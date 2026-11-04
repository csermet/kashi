"""A second ear from an ASR model that never sees the lyrics (Faz 3.5 Step 2).

Qwen3-ASR (and, as the second arm, Whisper large-v3-turbo) transcribe without
the lyrics, so unlike the aligner's own checkpoint they can hear a sheet that
is wrong. They return text without times, so the stem is cut at the finder's
silences (>= SILENCE_MS below SILENCE_DB) into pieces of <= MAX_PIECE_MS; each
piece's transcript is spread over the piece at a uniform letter rate. The
result is written in the SAME shape as the whole-song CTC ear
(``ear/<id>/whole.json``): the ear-text match, the finder, the score and the
anchor check read it unchanged. Its times are segment-level: good for the
>= 1 s class it is for, never for word timing.

    python -m audit.external_ear pieces <id>
    python -m audit.external_ear transcribe <id> --backend qwen3-asr [--audio mix.wav]
    python -m audit.external_ear import <id> --transcript pieces.json --model NAME
    python -m audit.external_ear step2 --doc <2.29 dump> --ear qwen3-asr --expect expect.json

``transcribe`` needs the backend's package on a GPU host (``qwen-asr`` in the
bench GPU image, ``faster-whisper`` on cnr-intel); ``import`` takes pieces
transcribed anywhere: ``[{"start_ms", "end_ms", "text"}]``. Ears land in
``ear/<id>/<name>.json`` next to ``whole.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from audit import gates

logger = logging.getLogger("audit.external_ear")

# The finder's silence (finder.SILENCE_DB / LINE_GAP_MS) cuts the stem.
SILENCE_DB = -25.0
SILENCE_MS = 500
MAX_PIECE_MS = 30_000
# A long voiced stretch is cut at its quietest frame in the last part of the
# piece; shorter pieces than this are breaths and are not transcribed.
SPLIT_SEARCH_FROM_MS = 15_000
MIN_PIECE_MS = 300
FRAME_STRIDE_MS = 20.0           # the written letter curve's grid (the CTC ear's)
CTC_SILENT_PROB = 0.2            # finder.BOXED_SILENT_PROB: the CTC curve calls it silent
MIN_PLACED_FOR_MEDIAN = 3        # READING: a song's median needs this many placed lines
BACKENDS = {
    "qwen3-asr": "Qwen/Qwen3-ASR-1.7B",
    "whisper": "large-v3-turbo",
}


@dataclass(frozen=True)
class Piece:
    start_ms: int
    end_ms: int


# --- pieces ---------------------------------------------------------------------

def voiced_regions(frames: Sequence[tuple[int, float]], hop_ms: float,
                   silence_db: float = SILENCE_DB, silence_ms: int = SILENCE_MS
                   ) -> list[Piece]:
    """Stretches between silences of >= ``silence_ms`` (shorter dips stay inside)."""
    regions: list[Piece] = []
    start: int | None = None
    quiet_since: int | None = None
    for t, db in frames:
        if db >= silence_db:
            if start is None:
                start = t
            quiet_since = None
        elif start is not None:
            if quiet_since is None:
                quiet_since = t
            if t + hop_ms - quiet_since >= silence_ms:
                regions.append(Piece(start, quiet_since))
                start, quiet_since = None, None
    if start is not None:
        end = quiet_since if quiet_since is not None else round(frames[-1][0] + hop_ms)
        regions.append(Piece(start, end))
    return regions


def split_long(region: Piece, frames: Sequence[tuple[int, float]],
               max_ms: int = MAX_PIECE_MS) -> list[Piece]:
    """A region longer than ``max_ms`` cut at its quietest frame between
    SPLIT_SEARCH_FROM_MS and ``max_ms`` from its start, repeatedly."""
    out: list[Piece] = []
    start = region.start_ms
    while region.end_ms - start > max_ms:
        window = [(db, t) for t, db in frames
                  if start + SPLIT_SEARCH_FROM_MS <= t <= start + max_ms]
        cut = min(window)[1] if window else start + max_ms
        out.append(Piece(start, cut))
        start = cut
    out.append(Piece(start, region.end_ms))
    return out


def pieces(frames: Sequence[tuple[int, float]], hop_ms: float) -> list[Piece]:
    out = []
    for region in voiced_regions(frames, hop_ms):
        out += [p for p in split_long(region, frames) if p.end_ms - p.start_ms >= MIN_PIECE_MS]
    return out


# --- the ear file -------------------------------------------------------------------

def segment_ear(transcripts: Sequence[dict], total_ms: int, model: str,
                label: str | None = None) -> dict:
    """``transcripts``: [{start_ms, end_ms, text}]. Each piece's characters
    are spread evenly over it (spaces become the word delimiter, every other
    character - any script - one token); the letter curve is 1 inside pieces
    that carry text and 0 elsewhere."""
    tokens: list[dict] = []
    frames = max(0, int(total_ms / FRAME_STRIDE_MS))
    prob = [0.0] * frames
    for seg in sorted(transcripts, key=lambda s: s["start_ms"]):
        text = " ".join(str(seg.get("text") or "").split())
        if not text:
            continue
        lo, hi = int(seg["start_ms"]), int(seg["end_ms"])
        step = (hi - lo) / len(text)
        for k, ch in enumerate(text):
            t = lo + round(k * step)
            tokens.append({"text": "|" if ch == " " else ch, "start_ms": t,
                           "end_ms": lo + round((k + 1) * step)})
        for f in range(max(0, int(lo / FRAME_STRIDE_MS)), min(frames, int(hi / FRAME_STRIDE_MS))):
            prob[f] = 1.0
    return {
        "id": label,
        "model": model,
        "timing": "segment",
        "start_ms": 0,
        "stride_ms": FRAME_STRIDE_MS,
        "text": " ".join(" ".join(str(s.get("text") or "").split()) for s in transcripts).strip(),
        "tokens": tokens,
        "segments": [dict(s) for s in transcripts],
        "letter_prob": prob,
        "letter_prob_max": max(prob, default=0.0),
        "frames": frames,
        "finite": True,
    }


# --- backends ---------------------------------------------------------------------

Transcriber = Callable[[list[tuple["object", int]], str | None], list[str]]


def qwen3_asr(model_id: str = BACKENDS["qwen3-asr"]) -> Transcriber:
    """Qwen3-ASR via the ``qwen-asr`` package (the bench GPU image). The day-one
    gate of Step 2 is that this runs at all on the desktop's card."""
    from qwen_asr import Qwen3ASRModel  # pyright: ignore[reportMissingImports]

    model = Qwen3ASRModel.from_pretrained(model_id)

    def run(audio: list[tuple[object, int]], language: str | None) -> list[str]:
        results = model.transcribe(audio=audio, language=language)
        return [str(r.text) for r in results]

    return run


def whisper(model_id: str = BACKENDS["whisper"]) -> Transcriber:
    """faster-whisper on CPU (cnr-intel): INT8 is disabled on Blackwell, and the
    CUDA 12 CTranslate2 stack stays out of the CUDA 13 image."""
    from faster_whisper import WhisperModel  # pyright: ignore[reportMissingImports]

    model = WhisperModel(model_id, device="cpu", compute_type="int8")

    def run(audio: list[tuple[object, int]], language: str | None) -> list[str]:
        out = []
        for samples, _sr in audio:
            segments, _info = model.transcribe(samples, language=language, vad_filter=False)
            out.append(" ".join(s.text.strip() for s in segments))
        return out

    return run


def read_pcm16(path: Path):
    """A 16 kHz mono 16-bit wav as float32 samples."""
    import numpy as np

    with wave.open(str(path), "rb") as f:
        raw = f.readframes(f.getnframes())
        rate = f.getframerate()
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768, rate


def transcribe_pieces(samples, rate: int, cuts: Sequence[Piece], run: Transcriber,
                      language: str | None) -> list[dict]:
    audio = [(samples[p.start_ms * rate // 1000: p.end_ms * rate // 1000], rate) for p in cuts]
    texts = run(audio, language) if audio else []
    return [{"start_ms": p.start_ms, "end_ms": p.end_ms, "text": t}
            for p, t in zip(cuts, texts, strict=True)]


# --- Step 2 -----------------------------------------------------------------------

def placed_offsets(doc: dict, ear: dict) -> tuple[int, list[int]]:
    """(lines, offsets of the placed lines): the finder's own ear-text match,
    borrowed placements left out."""
    from audit.finder import line_offsets

    offsets, borrowed = line_offsets(doc, ear)
    return len(doc.get("lines", [])), [o for li, o in offsets.items() if li not in borrowed]


def in_silence(external: dict, ctc: dict) -> tuple[int, int]:
    """(pieces with text whose middle lies in a stretch the CTC letter curve
    calls silent for > SECOND_EAR_SILENT_REGION_MS, pieces with text)."""
    probs, stride = ctc["letter_prob"], float(ctc["stride_ms"])
    t0 = int(ctc.get("start_ms", 0))
    silent: list[tuple[int, int]] = []
    run_start = None
    for k, p in enumerate(probs + [1.0]):
        t = round(t0 + k * stride)
        if p < CTC_SILENT_PROB:
            run_start = t if run_start is None else run_start
        elif run_start is not None:
            if t - run_start > gates.SECOND_EAR_SILENT_REGION_MS:
                silent.append((run_start, t))
            run_start = None
    with_text = [s for s in external.get("segments", []) if str(s.get("text") or "").strip()]
    inside = sum(1 for s in with_text
                 if any(lo <= (s["start_ms"] + s["end_ms"]) // 2 <= hi for lo, hi in silent))
    return inside, len(with_text)


def step2(songs: Sequence[tuple[str, dict, dict, dict]], expect: dict) -> dict:
    """``songs``: (id, document, CTC whole ear, external ear). ``expect``: the
    Step 1 expectations file; an expected shift is a shifted song (its sign
    from ``offset_ms``), ``"healthy": false`` keeps a song out of the healthy
    gate, every other song is healthy."""
    rows = []
    for vid, doc, ctc, ext in songs:
        n, ctc_off = placed_offsets(doc, ctc)
        _, ext_off = placed_offsets(doc, ext)
        inside, with_text = in_silence(ext, ctc)
        e = expect.get(vid) or {}
        kind = "shifted" if e.get("expect") == "shift" else (
            "healthy" if e.get("healthy", True) and e.get("expect", "none") == "none" else "other")
        rows.append({
            "id": vid, "kind": kind, "lines": n, "placed_ctc": len(ctc_off),
            "placed_external": len(ext_off),
            "median_ms": round(statistics.median(ext_off))
            if len(ext_off) >= MIN_PLACED_FOR_MEDIAN else None,
            "expected_sign": (1 if e["offset_ms"] > 0 else -1) if kind == "shifted" else None,
            "pieces_with_text": with_text, "pieces_in_silence": inside,
            "silent_share": round(inside / with_text, 3) if with_text else None,
        })
    lines = sum(r["lines"] for r in rows)
    share_ctc = sum(r["placed_ctc"] for r in rows) / lines if lines else 0.0
    share_ext = sum(r["placed_external"] for r in rows) / lines if lines else 0.0
    healthy = [r for r in rows if r["kind"] == "healthy" and r["median_ms"] is not None]
    shifted = [r for r in rows if r["kind"] == "shifted"]
    shares = [r["silent_share"] for r in rows if r["silent_share"] is not None]
    with_text = sum(r["pieces_with_text"] for r in rows)
    silent_pooled = sum(r["pieces_in_silence"] for r in rows) / with_text if with_text else None
    out = {
        "songs": rows,
        "placed_share_ctc": round(share_ctc, 3),
        "placed_share_external": round(share_ext, 3),
        "healthy_songs_with_a_median": len(healthy),
        "silent_share_pooled": None if silent_pooled is None else round(silent_pooled, 3),
        "silent_share_worst": max(shares, default=None),
    }
    out["gates"] = {
        "placed_share": share_ext >= share_ctc,
        "healthy_median": bool(healthy) and all(
            abs(r["median_ms"]) <= gates.SECOND_EAR_HEALTHY_MEDIAN_MS for r in healthy),
        "shifted_median": bool(shifted) and all(
            r["median_ms"] is not None and abs(r["median_ms"]) > gates.SECOND_EAR_SHIFTED_MEDIAN_MS
            and (r["median_ms"] > 0) == (r["expected_sign"] > 0) for r in shifted),
        "silent": silent_pooled is not None and silent_pooled <= gates.SECOND_EAR_SILENT_MAX,
    }
    out["passed"] = all(out["gates"].values())
    return out


# --- CLI ------------------------------------------------------------------------

def _analysis_frames(video_id: str) -> tuple[list[tuple[int, float]], float]:
    from audit.analysis import analyse

    song = analyse(video_id)
    return song.energy, song.hop_ms


def main(argv: list[str] | None = None) -> int:
    from audit.ear import EAR_DIR, whole_path

    parser = argparse.ArgumentParser(description="A second ear that never sees the lyrics.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_pieces = sub.add_parser("pieces", help="where the stem is cut")
    p_pieces.add_argument("id")
    p_tr = sub.add_parser("transcribe", help="run a backend over the pieces")
    p_tr.add_argument("id")
    p_tr.add_argument("--backend", choices=sorted(BACKENDS), required=True)
    p_tr.add_argument("--model", help="override the backend's checkpoint")
    p_tr.add_argument("--audio", type=Path,
                      help="transcribe this audio (the mix, for Whisper) cut at the stem's pieces")
    p_tr.add_argument("--language")
    p_im = sub.add_parser("import", help="pieces transcribed elsewhere")
    p_im.add_argument("id")
    p_im.add_argument("--transcript", type=Path, required=True)
    p_im.add_argument("--model", required=True)
    p_s2 = sub.add_parser("step2", help="the Step 2 gates for one ear name")
    p_s2.add_argument("--doc", type=Path, required=True)
    p_s2.add_argument("--ear", required=True, help="ear name, e.g. qwen3-asr")
    p_s2.add_argument("--expect", type=Path)
    p_s2.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.cmd == "step2":
        from audit.docs import documents_by_id

        docs = documents_by_id(json.loads(args.doc.read_text()))
        expect = json.loads(args.expect.read_text()) if args.expect else {}
        songs = []
        for vid, doc in sorted(docs.items()):
            ctc_path, ext_path = whole_path(vid), EAR_DIR / vid / f"{args.ear}.json"
            if ctc_path.exists() and ext_path.exists():
                songs.append((vid, doc, json.loads(ctc_path.read_text()),
                              json.loads(ext_path.read_text())))
        if not songs:
            print("no song has both ears", file=sys.stderr)
            return 2
        result = step2(songs, expect)
        for r in result["songs"]:
            print(f"  {r['id'][:14]:14} {r['kind']:8} placed {r['placed_external']}/{r['lines']} "
                  f"(ctc {r['placed_ctc']}), median {r['median_ms']} ms, "
                  f"in CTC silence {r['silent_share']}")
        g = result["gates"]
        print(f"placed-line share {result['placed_share_external']} vs the CTC ear's "
              f"{result['placed_share_ctc']} -> {gates.verdict(g['placed_share'])}")
        print(f"healthy |median| <= {gates.SECOND_EAR_HEALTHY_MEDIAN_MS} ms "
              f"({result['healthy_songs_with_a_median']} songs) -> "
              f"{gates.verdict(g['healthy_median'])}")
        print(f"shifted songs beyond {gates.SECOND_EAR_SHIFTED_MEDIAN_MS} ms with their sign -> "
              f"{gates.verdict(g['shifted_median'])}")
        print(f"pieces inside CTC silence > {gates.SECOND_EAR_SILENT_REGION_MS} ms: "
              f"{result['silent_share_pooled']} (gate <= {gates.SECOND_EAR_SILENT_MAX}) -> "
              f"{gates.verdict(g['silent'])}")
        print(f"Step 2 ({args.ear}, {len(songs)} songs): {gates.verdict(result['passed'])}")
        if args.json:
            args.json.write_text(json.dumps(result, indent=1) + "\n")
        return 0 if result["passed"] else 1

    frames, hop = _analysis_frames(args.id)
    total_ms = round(frames[-1][0] + hop) if frames else 0
    cuts = pieces(frames, hop)
    if args.cmd == "pieces":
        for p in cuts:
            print(f"{p.start_ms:>8} {p.end_ms:>8} {(p.end_ms - p.start_ms) / 1000:6.1f} s")
        print(f"{len(cuts)} pieces, {sum(p.end_ms - p.start_ms for p in cuts) / 1000:.0f} s")
        return 0
    if args.cmd == "import":
        transcripts = json.loads(args.transcript.read_text())
        name = args.model
    else:
        name = args.backend
        factory = qwen3_asr if args.backend == "qwen3-asr" else whisper
        run = factory(args.model) if args.model else factory()
        if args.audio:
            import tempfile

            from kashi_server.worker.process import _decode

            with tempfile.TemporaryDirectory(prefix="kashi-ear2-") as tmp:
                samples, rate = read_pcm16(_decode(args.audio, Path(tmp) / "a.wav", rate=16000))
        else:
            from audit.stems import decode_16k

            samples, rate = read_pcm16(decode_16k(args.id))
        transcripts = transcribe_pieces(samples, rate, cuts, run, args.language)
    ear = segment_ear(transcripts, total_ms, name, label=args.id)
    dest = EAR_DIR / args.id / f"{name.replace('/', '_')}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(ear, ensure_ascii=False) + "\n")
    logger.info("%s: %d pieces, %d with text -> %s", args.id, len(transcripts),
                sum(1 for t in transcripts if str(t.get("text") or "").strip()), dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
