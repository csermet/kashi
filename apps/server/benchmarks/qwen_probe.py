"""Qwen3-ForcedAligner probe — is a non-CTC aligner a REAL second opinion?

The measured problem this answers (Faz 8, 2026-08-06): every wav2vec2-family
aligner correlates +0.92..+0.945 with MMS at song level — they fail on the
same songs, so a same-family second opinion carries no information. Qwen3-FA
is the one permissively licensed aligner with a genuinely different mechanism
(LLM slot-filling, no CTC/Viterbi), which makes it the last candidate for the
arbiter's disagreement signal. It has also never been measured on singing.

So: same 20 English JamendoLyrics songs, same separated vocals, ground truth
on the table. Three numbers decide:
  - PCO/MAE — can it align singing at all?
  - correlation of its per-song PCO with MMS's — **below ~0.6 means the
    disagreement signal is real**; +0.9 means this door closes too.
  - token-mismatch count — how often its tokenizer diverges from the
    annotation (a practical adapter cost, not a quality signal).

Standalone by design: nothing here touches production code, and `qwen-asr` is
installed ephemerally into the bench container rather than added to the lock.

    docker run --rm --gpus all --ipc=host -v "%cd%:/repo" \
      -v kashi-bench-models:/models --entrypoint bash kashi-bench-gpu -lc \
      "pip install -q -U qwen-asr && python -m benchmarks.qwen_probe --label qwen-fa-en"

Output lands in benchmarks/results/ like every other sweep.
"""

import argparse
import json
import logging
import time
import unicodedata
from datetime import UTC, datetime

from benchmarks import datasets, metrics
from benchmarks.run import DATA_DIR, RESULTS_DIR

logger = logging.getLogger("benchmarks.qwen_probe")

MODEL_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
# Qwen rejects audio beyond 5 minutes; skipping is honest (and rare on Jamendo).
MAX_AUDIO_S = 295


def _clean_token(token: str) -> str:
    """Mirror of Qwen's `clean_token`: keep letters, digits and apostrophes.

    Mirrored so the probe can predict which annotation tokens Qwen's
    processor will drop (pure punctuation) and pair the rest positionally.
    """
    return "".join(
        ch
        for ch in token
        if ch == "'" or unicodedata.category(ch)[0] in ("L", "N")
    )


def _pair(items, ref_words: list[tuple[int, str]]) -> list[float] | None:
    """Qwen items ↔ ground-truth tokens, positionally over the kept subset.

    The annotation and Qwen tokenize the same text, but Qwen drops
    punctuation-only tokens. Pairing over the cleaned subset keeps the
    comparison honest; a residual count mismatch means the tokenizers truly
    diverged and the song is reported as such rather than force-paired.
    """
    kept = [(start, tok) for start, tok in ref_words if _clean_token(tok)]
    if len(items) != len(kept):
        return None
    return [
        float(round(item.start_time * 1000) - start)
        for item, (start, _) in zip(items, kept, strict=True)
    ]


def _align_windowed(aligner, audio_path, song) -> list:
    """One Qwen call per lrclib-anchored window, timestamps offset back.

    Production would run Qwen exactly this way (the same windows MMS uses),
    and 15-40 s slices sit near its speech training distribution — against
    full songs it drifts up to 55 s with no way back. Ground-truth line starts
    serve as anchors unjittered: a probe measures the mode's ceiling, not its
    field robustness.
    """
    import soundfile as sf

    from kashi_server.pipeline.windows import plan_windows

    total_ms = round(song.duration_hint_s * 1000)
    plan = plan_windows(song.line_texts, song.line_starts_ms, total_ms)
    if plan is None:
        raise RuntimeError("no window plan (too few anchors)")

    data, sr = sf.read(str(audio_path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)

    pieces, texts = [], []
    for window in plan:
        lo = min(len(data), max(0, round(window.slice_start_ms / 1000 * sr)))
        hi = min(len(data), round(window.slice_end_ms / 1000 * sr))
        pieces.append((data[lo:hi], sr))
        texts.append(" ".join(song.line_texts[i] for i in window.line_indices))

    results = aligner.align(pieces, texts, "English")
    items: list = []
    for window, result in zip(plan, results, strict=True):
        offset_s = window.slice_start_ms / 1000
        for item in result.items:
            item.start_time += offset_s
            item.end_time += offset_s
            items.append(item)
    return items


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--languages", default="eng", help="ISO-639-3, comma-separated")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--separation",
        default="kim-melband",
        help="same stems the MMS baseline aligned, so the comparison is input-identical",
    )
    parser.add_argument(
        "--full-mix",
        action="store_true",
        help="feed the mix instead of separated vocals (Whisper-style models can prefer it)",
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        help=(
            "slice by lrclib-style line anchors (production's window shape). "
            "Full songs collapsed — MAE 18-24 s — while three songs aligned "
            "fine, so short windows near Qwen's speech training length are "
            "its last plausible operating mode."
        ),
    )
    parser.add_argument("--label", default="qwen-fa-probe")
    args = parser.parse_args()

    from qwen_asr import Qwen3ForcedAligner  # pyright: ignore[reportMissingImports]

    logger.info("loading %s", MODEL_ID)
    aligner = Qwen3ForcedAligner.from_pretrained(MODEL_ID)

    root = datasets.ensure_jamendo(DATA_DIR)
    songs = datasets.load_jamendo(
        root,
        languages={lang.strip() for lang in args.languages.split(",")},
        limit=args.limit,
    )
    logger.info("%d song(s)", len(songs))

    tolerances_ms = (100, 200, 300, 500)
    rows: list[dict] = []
    started = time.monotonic()
    for index, song in enumerate(songs, 1):
        entry: dict = {"stem": song.stem, "language": song.language}
        rows.append(entry)
        if song.duration_hint_s > MAX_AUDIO_S:
            entry["error"] = f"skipped: {song.duration_hint_s:.0f}s exceeds Qwen's 5-minute cap"
            continue
        try:
            if args.full_mix:
                audio = song.audio_path
            else:
                # Cached stem read DIRECTLY — importing the separation
                # machinery pulls in the worker module, whose dependencies the
                # ephemeral qwen-asr install can clobber (measured: the first
                # vocals run lost prometheus_client and all 20 songs errored).
                # The probe reads what previous sweeps cached and refuses
                # honestly when nothing is there.
                audio = DATA_DIR / "stems" / args.separation / f"{song.stem}.wav"
                if not audio.exists():
                    entry["error"] = (
                        f"stem not cached ({args.separation}) — run one "
                        "kim-melband sweep first; the probe never separates"
                    )
                    continue
            # The annotation's own token stream, so the counting matches the
            # ground truth by construction — the same trick the harness uses.
            t0 = time.monotonic()
            if args.windowed:
                items = _align_windowed(aligner, audio, song)
            else:
                text = " ".join(token for _, token in song.words)
                items = list(aligner.align(str(audio), text, "English")[0].items)
            entry["align_s"] = round(time.monotonic() - t0, 1)
        except Exception as exc:  # a broken song is a data point, not the end
            logger.exception("%s failed", song.stem)
            entry["error"] = f"{type(exc).__name__}: {exc}"
            continue

        deviations = _pair(items, song.words)
        if deviations is None:
            entry["error"] = (
                f"token mismatch: qwen={len(items)} "
                f"vs kept-annotation={sum(1 for _, t in song.words if _clean_token(t))}"
            )
            continue
        stats = metrics.error_stats(deviations, tolerances_ms)
        assert stats is not None
        entry["words"] = {
            "count": stats.count,
            "mae_ms": stats.mae_ms,
            "medae_ms": stats.medae_ms,
            "p95_ms": stats.p95_ms,
            "pcs": stats.pcs,  # tolerance-in-seconds string keys, harness convention
        }
        logger.info(
            "%2d/%d %s: MAE %.0f ms (%.1fs align)",
            index,
            len(songs),
            song.stem,
            stats.mae_ms,
            entry.get("align_s", -1),
        )

    scored = [r for r in rows if r.get("words")]
    aggregate: dict = {
        "songs": len(rows),
        "scored": len(scored),
        "errors": [
            {"stem": r["stem"], "error": r["error"]} for r in rows if r.get("error")
        ],
    }
    if scored:
        aggregate["word_mae_ms_mean"] = round(
            sum(r["words"]["mae_ms"] for r in scored) / len(scored), 1
        )
        first = scored[0]["words"]["pcs"]
        aggregate["pco"] = {
            tol: round(sum(r["words"]["pcs"][tol] for r in scored) / len(scored), 4)
            for tol in first
        }

    report = {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "label": args.label,
            "alignment_model": MODEL_ID,
            "separation": "full-mix" if args.full_mix else args.separation,
            "note": (
                "standalone probe — no windowing, no line QA, no star tokens; "
                "NOT comparable to windowed sweeps except per-song vs the same "
                "ground truth"
            ),
            "wall_s": round(time.monotonic() - started, 1),
        },
        "jamendo": {"aggregate": aggregate, "songs": rows},
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now(UTC):%Y-%m-%d}-{args.label}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(json.dumps(aggregate, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
