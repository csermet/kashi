"""Benchmark runner (manual, never in CI — see benchmarks/README.md).

One invocation = one dataset sweep under ONE pipeline configuration
(separation model x mixback; windowed alignment joins the matrix with P3).
Results land in benchmarks/results/YYYY-MM-DD-<label>.json and are committed —
they are the evidence behind separation/windowing decisions.

Needs the align (+ separate, unless --separation full-mix) extras — in
practice: run inside the bench container, wall-clock numbers stay comparable.

    python -m benchmarks.run --dataset jamendo --separation full-mix --label baseline
    python -m benchmarks.run --dataset cases --separation bs-roformer --mixback 0.15
"""

import argparse
import json
import logging
import os
import platform
import shutil
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median

from benchmarks import datasets, metrics
from kashi_server.pipeline.alignment import AlignResult

logger = logging.getLogger("benchmarks")

BENCH_DIR = Path(__file__).resolve().parent
DATA_DIR = BENCH_DIR / "data"  # gitignored (CC NC/ND audio — never commit)
RESULTS_DIR = BENCH_DIR / "results"

SEPARATION_MODELS = {
    "full-mix": None,
    "bs-roformer": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
    "htdemucs_ft": "htdemucs_ft.yaml",
    "voc_ft": "UVR-MDX-NET-Voc_FT.onnx",
}
LINE_THRESHOLD_MS = 2500  # production line_qa drift threshold (case pass/fail)


def _decode_16k(src: Path, dest: Path) -> Path:
    from kashi_server.worker.process import _decode

    return _decode(src, dest, rate=16000)


def _separated_audio(
    song_audio: Path, cache_key: str, separation: str, mixback: float
) -> tuple[Path, float]:
    """Vocal stem (+mixback) for `song_audio`, cached under data/stems/ so a
    later windowed (P3) sweep reuses it. Returns (path, wall_seconds) with
    wall_seconds = 0.0 on a cache hit (never counted twice)."""
    from kashi_server.worker.process import _separate_vocals

    stem_dir = DATA_DIR / "stems" / f"{separation}-mb{mixback:g}"
    cached = stem_dir / f"{cache_key}.wav"
    if cached.exists():
        return cached, 0.0
    stem_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="kashi-bench-sep-") as tmp:
        out = _separate_vocals(
            song_audio,
            Path(tmp),
            model_filename=SEPARATION_MODELS[separation],
            mixback=mixback,
        )
        elapsed = time.monotonic() - started
        shutil.move(str(out), cached)  # handles tmpfs -> disk (cross-device)
    return cached, elapsed


def _align_song(
    audio: Path, line_texts: list[str], language: str
) -> tuple[AlignResult, float]:
    from kashi_server.pipeline.alignment import align

    with tempfile.TemporaryDirectory(prefix="kashi-bench-dec-") as tmp:
        wav = _decode_16k(audio, Path(tmp) / "align.wav")
        started = time.monotonic()
        result = align(wav, line_texts, language)
    return result, time.monotonic() - started


def _hyp_words(result: AlignResult) -> list[tuple[int, str]]:
    return [(w.start_ms, w.text) for chunk in result.words_per_line for w in chunk]


def _run_jamendo(args, tolerances_ms: tuple[int, ...]) -> tuple[list[dict], dict]:
    root = datasets.ensure_jamendo(DATA_DIR)
    songs = datasets.load_jamendo(
        root,
        languages=set(args.languages.split(",")) if args.languages else None,
        stems=set(args.songs) if args.songs else None,
        limit=args.limit,
    )
    if not songs:
        raise SystemExit("no songs selected — check --languages/--songs/--limit")
    logger.info("jamendo: %d song(s), separation=%s", len(songs), args.separation)

    rows: list[dict] = []
    for index, song in enumerate(songs, 1):
        entry: dict = {
            "stem": song.stem,
            "language": song.language,
            "duration_s": round(song.duration_hint_s, 1),
            "n_words": len(song.words),
            "n_lines": len(song.line_texts),
        }
        try:
            audio, separate_s = (
                (song.audio_path, 0.0)
                if args.separation == "full-mix"
                else _separated_audio(song.audio_path, song.stem, args.separation, args.mixback)
            )
            result, align_s = _align_song(audio, song.line_texts, song.language)
        except Exception as exc:  # keep sweeping; a broken song is a data point
            logger.exception("%s failed", song.stem)
            entry["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(entry)
            continue

        entry["separate_s"] = round(separate_s, 1)
        entry["align_s"] = round(align_s, 1)
        entry["sync"] = result.sync
        entry["quality_score"] = round(result.quality_score, 4)
        deviations = metrics.word_start_deviations(_hyp_words(result), song.words)
        if deviations is None:
            entry["error"] = "word count mismatch (sync degraded or token drift)"
        else:
            stats = metrics.error_stats(deviations, tolerances_ms)
            assert stats is not None
            entry["words"] = asdict(stats)
            line_report = metrics.line_start_report(
                [(line.start_ms, line.text) for line in result.lines],
                list(zip(song.line_starts_ms, song.line_texts, strict=True)),
                threshold_ms=LINE_THRESHOLD_MS,
                median_correction=False,  # same-audio ground truth: absolute errors
            )
            entry["lines"] = asdict(line_report.stats) if line_report.stats else None
        rows.append(entry)
        logger.info(
            "[%d/%d] %s: %s",
            index,
            len(songs),
            song.stem,
            entry.get("error")
            or f"MAE {entry['words']['mae_ms']:.0f}ms PCO@0.3 {entry['words']['pcs']['0.3']:.2f} "
            f"(align {align_s:.0f}s{f', sep {separate_s:.0f}s' if separate_s else ''})",
        )

    scored = [r for r in rows if "words" in r]
    aggregate: dict = {
        "songs": len(rows),
        "scored": len(scored),
        "failed": [r["stem"] for r in rows if "words" not in r],
    }
    if scored:
        total_audio = sum(r["duration_s"] for r in scored)
        aggregate |= {
            # MIREX/JamendoLyrics convention: per-song values aggregated over songs
            "word_mae_ms_mean": round(mean(r["words"]["mae_ms"] for r in scored), 1),
            "word_mae_ms_median": round(median(r["words"]["mae_ms"] for r in scored), 1),
            "word_medae_ms_mean": round(mean(r["words"]["medae_ms"] for r in scored), 1),
            "pco": {
                tol: round(mean(r["words"]["pcs"][tol] for r in scored), 4)
                for tol in scored[0]["words"]["pcs"]
            },
            "align_s_total": round(sum(r["align_s"] for r in scored), 1),
            "align_x_realtime": round(sum(r["align_s"] for r in scored) / total_audio, 3),
            "per_language": {
                lang: {
                    "songs": len(group),
                    "word_mae_ms_mean": round(mean(r["words"]["mae_ms"] for r in group), 1),
                    "pco_0.3": round(mean(r["words"]["pcs"]["0.3"] for r in group), 4),
                }
                for lang in sorted({r["language"] for r in scored})
                if (group := [r for r in scored if r["language"] == lang])
            },
        }
        separated = [r for r in scored if r.get("separate_s")]
        if separated:  # cache hits excluded — only measured runs count
            aggregate["separate_x_realtime"] = round(
                sum(r["separate_s"] for r in separated)
                / sum(r["duration_s"] for r in separated),
                3,
            )
    return rows, aggregate


def _case_audio(case: datasets.KashiCase) -> Path:
    from kashi_server.pipeline.download import download_audio

    case_dir = DATA_DIR / "cases" / case.id
    existing = sorted(case_dir.glob("audio.*"))
    if existing:
        return existing[0]
    case_dir.mkdir(parents=True, exist_ok=True)
    return download_audio(case.youtube_id, case_dir, max_duration_s=1200).path


def _case_reference(case: datasets.KashiCase) -> list[tuple[int | None, str]]:
    import httpx

    from kashi_server.pipeline.lrclib import _parse_synced

    cache = DATA_DIR / "cases" / case.id / "lrclib.json"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        response = httpx.get(
            f"https://lrclib.net/api/get/{case.lrclib_id}",
            headers={"User-Agent": "kashi-server-dev/benchmarks (+https://github.com/csermet/kashi)"},
            timeout=15,
        )
        response.raise_for_status()
        cache.write_text(json.dumps(response.json()), encoding="utf-8")
    synced = json.loads(cache.read_text(encoding="utf-8")).get("syncedLyrics") or ""
    entries = _parse_synced(synced)
    if not entries:
        raise RuntimeError(f"lrclib {case.lrclib_id} has no synced lyrics")
    return entries


def _run_cases(args, tolerances_ms: tuple[int, ...]) -> tuple[list[dict], dict]:
    cases = datasets.load_cases(BENCH_DIR / "cases.yaml")
    rows: list[dict] = []
    for case in cases:
        entry: dict = {"id": case.id, "title": case.title, "artist": case.artist}
        try:
            reference = _case_reference(case)
            line_texts = [text for _, text in reference]
            audio = _case_audio(case)
            source, separate_s = (
                (audio, 0.0)
                if args.separation == "full-mix"
                else _separated_audio(audio, case.id, args.separation, args.mixback)
            )
            result, align_s = _align_song(source, line_texts, case.language)
        except Exception as exc:
            logger.exception("case %s failed", case.id)
            entry["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(entry)
            continue

        report = metrics.line_start_report(
            [(line.start_ms, line.text) for line in result.lines],
            reference,
            threshold_ms=LINE_THRESHOLD_MS,
            window_ms=(case.window_s[0] * 1000, case.window_s[1] * 1000)
            if case.window_s
            else None,
            median_correction=True,  # cross-source clocks, like production line_qa
        )
        entry |= {
            "separate_s": round(separate_s, 1),
            "align_s": round(align_s, 1),
            "sync": result.sync,
            "quality_score": round(result.quality_score, 4),
            "offset_ms": round(report.offset_ms),
            "lines_over_threshold": report.failures,
            "passed": report.failures == 0 and result.sync == "word",
            "lines": asdict(report.stats) if report.stats else None,
        }
        rows.append(entry)
        logger.info(
            "case %s: %s (%d over threshold, offset %+dms)",
            case.id,
            "PASS" if entry["passed"] else "FAIL",
            report.failures,
            report.offset_ms,
        )
    return rows, {
        "cases": len(rows),
        "passed": sum(bool(r.get("passed")) for r in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["jamendo", "cases", "both"], default="both")
    parser.add_argument("--separation", choices=sorted(SEPARATION_MODELS), default="full-mix")
    parser.add_argument("--mixback", type=float, default=0.15, help="ignored for full-mix")
    parser.add_argument("--languages", help="comma-separated ISO-639-3, e.g. eng,spa")
    parser.add_argument("--songs", nargs="*", help="jamendo stems to include")
    parser.add_argument("--limit", type=int, help="max jamendo songs (after filters)")
    parser.add_argument("--tolerances", default="0.1,0.2,0.3,0.5", help="PCO tolerances, seconds")
    parser.add_argument("--label", help="results filename label (default: config name)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    tolerances_ms = tuple(round(float(t) * 1000) for t in args.tolerances.split(","))
    if 300 not in tolerances_ms:  # summaries key off PCO@0.3 — always measure it
        tolerances_ms += (300,)
    config_name = args.separation + (
        f"-mb{args.mixback:g}" if args.separation != "full-mix" else ""
    )
    started = time.monotonic()

    from kashi_server.pipeline.alignment import MODEL_NAME
    from kashi_server.version import PIPELINE_VERSION

    report: dict = {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "label": args.label or config_name,
            "pipeline_version": PIPELINE_VERSION,
            "alignment_model": MODEL_NAME,
            "separation": args.separation,
            "mixback": args.mixback if args.separation != "full-mix" else None,
            "windowed": False,  # joins the matrix with P3
            "host": platform.node(),
            "cpus": os.cpu_count(),
            "tolerances_s": [t / 1000 for t in tolerances_ms],
        }
    }
    if args.dataset in ("jamendo", "both"):
        rows, aggregate = _run_jamendo(args, tolerances_ms)
        report["jamendo"] = {"aggregate": aggregate, "songs": rows}
    if args.dataset in ("cases", "both"):
        rows, aggregate = _run_cases(args, tolerances_ms)
        report["cases"] = {"aggregate": aggregate, "results": rows}
    report["meta"]["wall_s"] = round(time.monotonic() - started, 1)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now(UTC):%Y-%m-%d}-{report['meta']['label']}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    for section in ("jamendo", "cases"):
        if section in report:
            print(section, json.dumps(report[section]["aggregate"], indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
