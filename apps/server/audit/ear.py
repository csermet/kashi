"""What the aligner's own model hears around a moment.

The production checkpoint is a CTC speech recogniser before it is an aligner.
Its emissions over a short clip of the separated vocal, decoded greedily,
give a character transcript with frame times, and the per-frame probability
of "some letter" (neither CTC blank nor the word delimiter) is a curve of
where the model hears singing. No new model and no new licence: the same
checkpoint and the same emission pass alignment uses (``alignment._emit``).

Times here are the MODEL's raw clock. Documents carry the per-language offset
table on top (English: -50 ms base, -20..-80 ms by initial class), so a word
box sitting ~50 ms before the model's letters is the table, not an error.

    python -m audit.ear <video id> --doc <doc.json> --times 203060,16000
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from audit.docs import load_document
from audit.paths import EAR_DIR, stem_path

logger = logging.getLogger("audit.ear")

HALF_WINDOW_MS = 2500
# Checkpoint short names as they appear in alignment.method, to their HF ids
# (gitops deployment-worker.yaml ALIGN_MODELS + the MMS default).
CHECKPOINTS = {
    "wav2vec2-xls-r-1b-english": "jonatasgrosman/wav2vec2-xls-r-1b-english",
    "wav2vec2-xls-r-300m-cv7-turkish": "mpoyraz/wav2vec2-xls-r-300m-cv7-turkish",
    "mms-300m-1130-forced-aligner": "MahmoudAshraf/mms-300m-1130-forced-aligner",
    # the MMS default is recorded under its short name (Gasolina, BxMxC)
    "mms-300m": "MahmoudAshraf/mms-300m-1130-forced-aligner",
}
WORD_DELIMITER = "|"


def checkpoint_for(method: str) -> str:
    """HF id of the checkpoint named in ``alignment.method``."""
    for short, hf_id in CHECKPOINTS.items():
        if short in method:
            return hf_id
    raise ValueError(f"unknown checkpoint in alignment.method {method!r}")


def timed_tokens(
    ids: Sequence[int],
    id_to_token: Mapping[int, str],
    blank_id: int,
    stride_ms: float,
    t0_ms: int,
) -> list[dict]:
    """Greedy CTC collapse that keeps time: one entry per emitted token run.

    Same collapse rule as ``asr.greedy_ctc_decode`` (repeats first, then
    blanks); a run's span is its first to last frame, frame i starting at
    ``t0 + i * stride``.
    """
    out: list[dict] = []
    previous: int | None = None
    for frame, token_id in enumerate(ids):
        if token_id != previous:
            previous = token_id
            if token_id == blank_id:
                continue
            out.append({
                "text": id_to_token.get(token_id, ""),
                "start_ms": t0_ms + round(frame * stride_ms),
                "end_ms": t0_ms + round((frame + 1) * stride_ms),
            })
        elif token_id != blank_id and out:
            out[-1]["end_ms"] = t0_ms + round((frame + 1) * stride_ms)
    return out


def letters_text(tokens: Sequence[dict]) -> str:
    return "".join(t["text"] for t in tokens).replace(WORD_DELIMITER, " ").strip()


def hear(video_id: str, model_name: str, t_ms: int, half_ms: int = HALF_WINDOW_MS) -> dict:
    """Emissions over [t - half, t + half] of the stem: tokens and the letter curve."""
    start_ms = max(0, t_ms - half_ms)
    out = _hear_span(video_id, model_name, start_ms, 2 * half_ms)
    out["t_ms"] = t_ms
    return out


def hear_whole(video_id: str, model_name: str) -> dict:
    """The whole stem in one emission pass (the library batches it in 30 s windows)."""
    return _hear_span(video_id, model_name, 0, None)


def _hear_span(video_id: str, model_name: str, start_ms: int, length_ms: int | None) -> dict:
    import torch  # pyright: ignore[reportMissingImports]
    from ctc_forced_aligner import load_audio  # pyright: ignore[reportMissingImports]

    from kashi_server.pipeline.alignment import _emit, _load_model
    from kashi_server.pipeline.asr import usable_columns
    from kashi_server.worker.process import _decode

    model, tokenizer = _load_model(model_name)
    with tempfile.TemporaryDirectory(prefix="kashi-ear-") as tmp:
        clip = _decode(stem_path(video_id), Path(tmp) / "clip.wav", rate=16000,
                       start_s=start_ms / 1000 if start_ms else None,
                       length_s=length_ms / 1000 if length_ms else None)
        audio = load_audio(str(clip), model.dtype, model.device)
        with torch.inference_mode():
            emissions, stride = _emit(model, audio, exact_stride=True)
    vocab = tokenizer.get_vocab()
    keep = usable_columns(int(emissions.shape[-1]), len(vocab))
    logp = emissions[:, :keep]
    if logp.dim() == 3:
        logp = logp.squeeze(0)
    probs = torch.exp(logp)
    blank_id = vocab.get("<pad>", 0)
    delim_id = vocab.get(WORD_DELIMITER)
    silent = probs[:, blank_id] + (probs[:, delim_id] if delim_id is not None else 0)
    letter = (1 - silent).clamp(0, 1).tolist()
    ids = probs.argmax(dim=-1).tolist()
    id_to_token = {i: tok for tok, i in vocab.items()}
    tokens = timed_tokens(ids, id_to_token, blank_id, float(stride), start_ms)
    return {
        "id": video_id,
        "model": model_name,
        "start_ms": start_ms,
        "stride_ms": round(float(stride), 4),
        "text": letters_text(tokens),
        "tokens": tokens,
        "letter_prob": [round(p, 3) for p in letter],
        "letter_prob_max": round(max(letter), 3) if letter else 0.0,
        "frames": len(letter),
        "finite": all(math.isfinite(p) for p in letter),
    }


def whole_path(video_id: str) -> Path:
    return EAR_DIR / video_id / "whole.json"


def ear_path(video_id: str, t_ms: int) -> Path:
    return EAR_DIR / video_id / f"{t_ms}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What the aligner's model hears.")
    parser.add_argument("id")
    parser.add_argument("--doc", required=True, help="document JSON or a [{id, doc}] dump")
    parser.add_argument("--times", help="comma-separated ms (document clock)")
    parser.add_argument("--whole", action="store_true", help="whole song -> ear/<id>/whole.json")
    parser.add_argument("--model", help="override the checkpoint taken from alignment.method")
    parser.add_argument("--half-window-ms", type=int, default=HALF_WINDOW_MS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    doc = load_document(Path(args.doc), args.id)
    model_name = args.model or checkpoint_for(doc["alignment"]["method"])
    if args.whole:
        dest = whole_path(args.id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out = hear_whole(args.id, model_name)
        dest.write_text(json.dumps(out) + "\n")
        logger.info("%s whole: %d frames, %d tokens", args.id, out["frames"], len(out["tokens"]))
    for t in (int(x) for x in (args.times.split(",") if args.times else [])):
        out = hear(args.id, model_name, t, args.half_window_ms)
        dest = ear_path(args.id, t)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(out) + "\n")
        logger.info("%s @%d: %r", args.id, t, out["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
