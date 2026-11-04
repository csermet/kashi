"""A second, independent ear: an ASR that never sees the lyrics, segment-timed.

The audit's own ear is the production CTC checkpoint (``audit.ear``): for
Turkish and the MMS languages its letter times equal the word boxes by
construction, and it hears through the document's eyes only in the sense that
it shares the aligner's model. An ASR that takes no lyrics can see a sheet that
is wrong (``docs/research/yerel-model-denetim-2026-11.md`` section 1):
Qwen3-ASR-1.7B on the stem (Apache-2.0), Whisper large-v3-turbo through
faster-whisper on the mix (MIT).

Neither returns times by default, so the time axis comes from the stem. It is
cut at the finder's silences - at least MIN_SILENCE_MS below SILENCE_DB on the
vocal loudness contour (``audit.analysis``: the production
``measure_vocal_energy`` on the 16 kHz stem, line QA's -25 dB) - into voiced
pieces of at most MAX_PIECE_MS; each piece is transcribed on its own and
stamped with its start, and its letters are spread evenly over it (the letter
rate is the piece's length). Where Qwen3-ForcedAligner covers the language (en,
ja, es, de, fr) ``--word-times`` adds word times inside the piece; marked.

The output has the whole-song ear's shape (``ear/<id>/whole.json``), so
``audit.textmatch``, ``audit.finder`` and ``audit.anchors`` read it unchanged,
plus ``source`` (backend, model, version, settings), ``timing: "segment"``,
``word_times`` and the ``segments`` themselves. Japanese is kept as written
(script), never romanised here.

    python -m audit.ext_ear <id> --backend qwen3-asr|faster-whisper [--mix M]
        [--language en] [--word-times] [--model M] [--device D] [--out PATH]

The backends are imported lazily and are NOT in the lock (README: local
install lines, pinned).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from audit.analysis import SILENCE_DB
from audit.ear import WORD_DELIMITER
from audit.finder import LINE_GAP_MS

logger = logging.getLogger("audit.ext_ear")

# The finder's silence: LINE_GAP_MS (500 ms) below line QA's -25 dB.
MIN_SILENCE_MS = LINE_GAP_MS
MAX_PIECE_MS = 30_000
STRIDE_MS = 20.0
SAMPLE_RATE = 16_000
# Qwen3-ForcedAligner languages this adapter asks for word times in.
WORD_TIME_LANGUAGES = frozenset({"en", "ja", "es", "de", "fr"})
# ISO 639-1 / 639-3 -> (639-1, the name Qwen3-ASR takes)
LANGUAGES = {
    "en": ("en", "English"), "eng": ("en", "English"),
    "ja": ("ja", "Japanese"), "jpn": ("ja", "Japanese"),
    "es": ("es", "Spanish"), "spa": ("es", "Spanish"),
    "de": ("de", "German"), "deu": ("de", "German"),
    "fr": ("fr", "French"), "fra": ("fr", "French"),
    "tr": ("tr", "Turkish"), "tur": ("tr", "Turkish"),
}


# --- cutting the stem ------------------------------------------------------------

def voiced_spans(frames: Sequence[tuple[int, float]], hop_ms: float,
                 silence_db: float = SILENCE_DB,
                 min_silence_ms: int = MIN_SILENCE_MS) -> list[tuple[int, int]]:
    """[start, end) of the stretches between silences: a silence is a run of
    frames below ``silence_db`` lasting at least ``min_silence_ms`` (from its
    first quiet frame to the next loud one). Shorter dips stay inside."""
    if not frames:
        return []
    end_of_song = round(frames[-1][0] + hop_ms)
    out: list[tuple[int, int]] = []
    span_start: int | None = None
    quiet_since: int | None = None
    for t, db in frames:
        if db < silence_db:
            if quiet_since is None:
                quiet_since = t
            continue
        if quiet_since is not None and span_start is not None \
                and t - quiet_since >= min_silence_ms:
            out.append((span_start, quiet_since))
            span_start = None
        if span_start is None:
            span_start = t
        quiet_since = None
    if span_start is not None:
        stop = quiet_since if quiet_since is not None \
            and end_of_song - quiet_since >= min_silence_ms else end_of_song
        out.append((span_start, stop))
    return out


def split_long(span: tuple[int, int], frames: Sequence[tuple[int, float]],
               max_ms: int = MAX_PIECE_MS) -> list[tuple[int, int]]:
    """A span longer than ``max_ms`` is cut at its quietest frame inside its
    middle half, again and again, until every piece is at most ``max_ms``."""
    s, e = span
    if e - s <= max_ms:
        return [span]
    lo, hi = s + (e - s) // 4, e - (e - s) // 4
    inside = [(db, t) for t, db in frames if lo <= t <= hi]
    cut = min(inside)[1] if inside else (s + e) // 2
    return split_long((s, cut), frames, max_ms) + split_long((cut, e), frames, max_ms)


def pieces(frames: Sequence[tuple[int, float]], hop_ms: float,
           max_ms: int = MAX_PIECE_MS) -> list[tuple[int, int]]:
    return [p for span in voiced_spans(frames, hop_ms) for p in split_long(span, frames, max_ms)]


# --- transcribing ------------------------------------------------------------------

@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None = None
    # (text, start_ms, end_ms) relative to the piece; only from a forced aligner
    words: list[tuple[str, int, int]] | None = None


class Transcriber(Protocol):
    name: str

    def describe(self) -> dict: ...

    def transcribe_many(self, audio: Sequence[Any], sr: int,
                        language: str | None) -> list[Transcript]: ...


def _version(package: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(package)
    except PackageNotFoundError:
        return None


class QwenASR:
    """Qwen3-ASR through the ``qwen-asr`` package (transformers backend)."""

    name = "qwen3-asr"
    MIN_INPUT_S = 0.5   # qwen_asr MIN_ASR_INPUT_SECONDS: shorter pieces are padded

    def __init__(self, model: str = "Qwen/Qwen3-ASR-1.7B", device: str = "cuda:0",
                 word_times: bool = False, batch: int = 16, max_new_tokens: int = 512,
                 aligner: str = "Qwen/Qwen3-ForcedAligner-0.6B"):
        import torch  # pyright: ignore[reportMissingImports]
        from qwen_asr import Qwen3ASRModel  # pyright: ignore[reportMissingImports]

        self.settings = {"model": model, "device": device, "dtype": "bfloat16", "batch": batch,
                         "max_new_tokens": max_new_tokens,
                         "aligner": aligner if word_times else None}
        kwargs: dict = {"dtype": torch.bfloat16, "device_map": device,
                        "max_inference_batch_size": batch, "max_new_tokens": max_new_tokens}
        if word_times:
            kwargs |= {"forced_aligner": aligner,
                       "forced_aligner_kwargs": {"dtype": torch.bfloat16, "device_map": device}}
        self.word_times = word_times
        self.model = Qwen3ASRModel.from_pretrained(model, **kwargs)

    def describe(self) -> dict:
        return {"backend": self.name, "model": self.settings["model"],
                "version": _version("qwen-asr"), "settings": self.settings}

    def transcribe_many(self, audio: Sequence[Any], sr: int,
                        language: str | None) -> list[Transcript]:
        import numpy as np

        code, name = LANGUAGES.get(language or "", (None, None))
        minimum = round(self.MIN_INPUT_S * sr)
        batch = [(np.pad(a, (0, max(0, minimum - len(a)))), sr) for a in audio]
        stamps = self.word_times and code in WORD_TIME_LANGUAGES
        results = self.model.transcribe(audio=batch, language=name, return_time_stamps=stamps)
        out = []
        for r in results:
            words = None
            if stamps and r.time_stamps is not None:
                words = [(it.text, round(it.start_time * 1000), round(it.end_time * 1000))
                         for it in r.time_stamps]
            out.append(Transcript(r.text, r.language or None, words))
        return out


class FasterWhisper:
    """Whisper through faster-whisper (CTranslate2), CPU INT8 by default."""

    name = "faster-whisper"

    def __init__(self, model: str = "large-v3-turbo", device: str = "cpu",
                 compute_type: str = "int8", beam_size: int = 5):
        from faster_whisper import WhisperModel  # pyright: ignore[reportMissingImports]

        self.settings = {"model": model, "device": device, "compute_type": compute_type,
                         "beam_size": beam_size, "temperature": 0.0, "vad_filter": False,
                         "condition_on_previous_text": False}
        self.model = WhisperModel(model, device=device, compute_type=compute_type)

    def describe(self) -> dict:
        return {"backend": self.name, "model": self.settings["model"],
                "version": _version("faster-whisper"), "settings": self.settings}

    def transcribe_many(self, audio: Sequence[Any], sr: int,
                        language: str | None) -> list[Transcript]:
        code = LANGUAGES.get(language or "", (None, None))[0]
        out = []
        for a in audio:
            segments, info = self.model.transcribe(
                a, language=code, beam_size=self.settings["beam_size"], temperature=0.0,
                vad_filter=False, condition_on_previous_text=False, word_timestamps=False)
            text = " ".join(s.text.strip() for s in segments)
            out.append(Transcript(text, getattr(info, "language", None)))
        return out


BACKENDS: dict[str, Callable[..., Transcriber]] = {
    "qwen3-asr": QwenASR,
    "faster-whisper": FasterWhisper,
}


# --- the ear file ------------------------------------------------------------------

def _keep(ch: str) -> bool:
    """Letters (any script) and apostrophes; digits and punctuation carry no sound
    the matcher can use."""
    return ch == "'" or unicodedata.category(ch)[0] == "L"


def _spread(words: Sequence[str], s: int, e: int) -> list[dict]:
    """Tokens of ``words`` with their letters evenly over [s, e); a delimiter
    token between words at the next letter's time."""
    n = sum(len(w) for w in words)
    if not n:
        return []
    step = (e - s) / n
    out: list[dict] = []
    k = 0
    for wi, w in enumerate(words):
        if wi:
            t = s + round(k * step)
            out.append({"text": WORD_DELIMITER, "start_ms": t, "end_ms": t})
        for ch in w:
            t = s + round(k * step)
            out.append({"text": ch, "start_ms": t, "end_ms": s + round((k + 1) * step)})
            k += 1
    return out


def piece_tokens(transcript: Transcript, s: int, e: int) -> list[dict]:
    """A piece's tokens on the song clock: letters spread over the piece, or
    over each word's own span when the transcript has word times."""
    if transcript.words:
        out: list[dict] = []
        for i, (text, ws, we) in enumerate(transcript.words):
            letters = "".join(ch for ch in text.lower() if _keep(ch))
            if not letters:
                continue
            if i and out:
                out.append({"text": WORD_DELIMITER, "start_ms": s + ws, "end_ms": s + ws})
            out += _spread([letters], s + ws, s + max(ws + 1, we))
        return out
    words = ["".join(ch for ch in w.lower() if _keep(ch)) for w in transcript.text.split()]
    return _spread([w for w in words if w], s, e)


def build(video_id: str, spans: Sequence[tuple[int, int]], transcripts: Sequence[Transcript],
          duration_ms: int, source: dict, audio: str, word_times: str | None = None) -> dict:
    """The whole-song ear file of the external ear."""
    tokens: list[dict] = []
    segments = []
    frames = int(duration_ms / STRIDE_MS)
    prob = [0.0] * frames
    for (s, e), tr in zip(spans, transcripts, strict=True):
        toks = piece_tokens(tr, s, e)
        if tokens and toks:
            tokens.append({"text": WORD_DELIMITER, "start_ms": s, "end_ms": s})
        tokens += toks
        segments.append({"start_ms": s, "end_ms": e, "text": tr.text.strip(),
                         "language": tr.language, "word_times": bool(tr.words)})
        if toks:
            for i in range(max(0, int(s / STRIDE_MS)), min(frames, int(e / STRIDE_MS))):
                prob[i] = 1.0
    text = "".join(t["text"] for t in tokens).replace(WORD_DELIMITER, " ").strip()
    return {
        "id": video_id,
        "model": source.get("model"),
        "start_ms": 0,
        "stride_ms": STRIDE_MS,
        "text": text,
        "tokens": tokens,
        "letter_prob": prob,
        "letter_prob_max": max(prob) if prob else 0.0,
        "frames": frames,
        "finite": True,
        "source": source,
        "timing": "segment",
        "word_times": word_times,
        "input": {"audio": audio, "silence_db": SILENCE_DB, "min_silence_ms": MIN_SILENCE_MS,
                  "max_piece_ms": MAX_PIECE_MS},
        "segments": segments,
    }


def transcribe_song(video_id: str, frames: Sequence[tuple[int, float]], hop_ms: float,
                    samples: Any, transcriber: Transcriber, language: str | None,
                    audio: str = "stem", sr: int = SAMPLE_RATE) -> dict:
    """Cut, transcribe, assemble. ``samples``: the audio the transcriber hears
    (the stem, or the mix with ``--mix``) as mono float at ``sr``; the cut
    points always come from the stem's ``frames``."""
    spans = pieces(frames, hop_ms)
    clips = [samples[round(s * sr / 1000):round(e * sr / 1000)] for s, e in spans]
    transcripts = transcriber.transcribe_many(clips, sr, language) if clips else []
    duration = max(round(len(samples) * 1000 / sr), round(frames[-1][0] + hop_ms) if frames else 0)
    source = transcriber.describe()
    word_times = None
    if any(t.words for t in transcripts):
        word_times = (source.get("settings") or {}).get("aligner") or "forced-aligner"
    return build(video_id, spans, transcripts, duration, source, audio, word_times)


def ext_path(video_id: str, backend: str) -> Path:
    from audit.paths import EAR_DIR

    return EAR_DIR / video_id / f"whole-{backend}.json"


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _read_audio(path: Path) -> Any:
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise ValueError(f"{path}: {sr} Hz, expected {SAMPLE_RATE}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A segment-timed external ear.")
    parser.add_argument("id")
    parser.add_argument("--backend", required=True, choices=sorted(BACKENDS))
    parser.add_argument("--mix", type=Path, help="transcribe this full mix instead of the stem "
                                                 "(the cut points still come from the stem)")
    parser.add_argument("--language", help="ISO 639-1/-3 (en, ja, es, de, fr, tr); default: auto")
    parser.add_argument("--word-times", action="store_true",
                        help="Qwen3-ForcedAligner word times (en, ja, es, de, fr)")
    parser.add_argument("--model", help="override the backend's default model")
    parser.add_argument("--device", help="cuda:0 (qwen3-asr) / cpu (faster-whisper)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.language and args.language not in LANGUAGES:
        parser.error(f"unknown language {args.language!r}")
    from audit.analysis import analyse
    from audit.stems import decode_16k

    song = analyse(args.id)
    kwargs: dict = {}
    if args.model:
        kwargs["model"] = args.model
    if args.device:
        kwargs["device"] = args.device
    if args.word_times:
        if args.backend != "qwen3-asr":
            parser.error("--word-times needs --backend qwen3-asr")
        kwargs["word_times"] = True
    transcriber = BACKENDS[args.backend](**kwargs)
    if args.mix:
        from kashi_server.worker.process import _decode

        with tempfile.TemporaryDirectory(prefix="kashi-ext-ear-") as tmp:
            samples = _read_audio(_decode(args.mix, Path(tmp) / "mix.wav", rate=SAMPLE_RATE))
        audio = "mix"
    else:
        samples = _read_audio(decode_16k(args.id))
        audio = "stem"
    out = transcribe_song(args.id, song.energy, song.hop_ms, samples, transcriber,
                          args.language, audio)
    dest = args.out or ext_path(args.id, args.backend)
    write_atomic(dest, out)
    logger.info("%s: %d pieces, %d tokens -> %s", args.id, len(out["segments"]),
                len(out["tokens"]), dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
