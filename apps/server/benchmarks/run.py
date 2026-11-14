"""Benchmark runner (manual, never in CI — see benchmarks/README.md).

One invocation = one dataset sweep under ONE pipeline configuration.
Results land in benchmarks/results/YYYY-MM-DD-<label>.json and are committed —
they are the evidence behind separation/windowing decisions.

Two modes (2.33.0, review step B.2):

- default, the SHIPPED pipeline: each song goes through the worker's own chain
  after the lyrics are chosen (`process.align_and_review`: the sheet shift,
  windowed alignment resolved as production resolves it — the aligner table
  with its offset table, the separator, the research switches at their
  defaults — then line QA on the aligned audio's onsets and the held-end
  restore). The human line starts, jittered, are the lrclib sheet. Each stage
  is scored (`stages`: aligned / line_qa / shipped), so what line QA and the
  restore do on human truth reads off one run.
- `--legacy`: the bench as it was before: `align()` plus the end trim
  production dropped in 2.32.0, the old defaults (full mix, mixback 0.15, not
  windowed, trim on). Every result file before 2026-11-14 was made this way;
  `--legacy` with the same flags reproduces them.

Needs the align (+ separate, unless --separation full-mix) extras — in
practice: run inside the bench container, wall-clock numbers stay comparable.

    python -m benchmarks.run --dataset jamendo --languages eng --dump-words
    python -m benchmarks.run --legacy --dataset jamendo --separation kim-base --mixback 0 --windowed
"""

import argparse
import json
import logging
import os
import platform
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, cast

from benchmarks import datasets, metrics
from benchmarks.trim import trim_word_ends
from kashi_server.pipeline.alignment import (
    EXACT_STRIDE_DEFAULT,
    HYBRID_T_MS,
    LINE_REGION_DEFAULT,
    SEAM_POLICIES,
    SEAM_POLICY_DEFAULT,
    UNPILE_DEFAULT,
    WHOLE_VOTE_DEFAULT,
    WHOLE_VOTE_T_MS,
    WINDOW_STAR_DEFAULT,
    WINDOW_STARS,
    WINDOWED_LEAD_MS,
    AlignResult,
)

logger = logging.getLogger("benchmarks")

BENCH_DIR = Path(__file__).resolve().parent
DATA_DIR = BENCH_DIR / "data"  # gitignored (CC NC/ND audio — never commit)
RESULTS_DIR = BENCH_DIR / "results"

SEPARATION_MODELS = {
    "full-mix": None,
    "bs-roformer": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
    "htdemucs_ft": "htdemucs_ft.yaml",
    "voc_ft": "UVR-MDX-NET-Voc_FT.onnx",
    # Research round 2026-07-11 (MVSEP Multisong rankings): Kim MelBand family
    # scores >= ep_317 at ~2.5-3.5x less CPU (dim 384/depth 6 vs 512/12);
    # resurrection/revive are same-cost ep_317 upgrades (revive_v2 = best
    # "bleedless" score, the metric most correlated with CTC lock losses).
    "kim-melband": "mel_band_roformer_kim_ft_unwa.ckpt",
    "kim-melband-bleedless": "mel_band_roformer_kim_ft2_bleedless_unwa.ckpt",
    # License hunt 2026-08-09: the unwa FTs above carry NO license at all
    # (default all-rights-reserved), but their PARENT — Kimberley Jensen's
    # base vocal model — is MIT on HF. If it lands within noise of the FT,
    # the commercial chain gets its separator for free.
    "kim-base": "vocals_mel_band_roformer.ckpt",
    "bsr-resurrection": "bs_roformer_vocals_resurrection_unwa.ckpt",
    "bsr-revive-v2": "bs_roformer_vocals_revive_v2_unwa.ckpt",
}
LINE_THRESHOLD_MS = 2500  # production line_qa drift threshold (case pass/fail)


def _decode_16k(src: Path, dest: Path) -> Path:
    from kashi_server.worker.process import _decode

    return _decode(src, dest, rate=16000)


def _separated_audio(
    song_audio: Path, cache_key: str, separation: str, mixback: float
) -> tuple[Path, float]:
    """Vocal stem (+mixback) for `song_audio`, cached under data/stems/.

    The RAW stem (pre-mixback) is cached separately from the blend: separation
    costs double-digit minutes on CPU while blending is one ffmpeg pass, so a
    mixback ablation (mb0 vs mb0.15) and the P3 windowed re-sweep only pay
    alignment. Returns (path, wall_seconds); wall_seconds = 0.0 on a raw-stem
    cache hit (a blend pass is never counted as separation time)."""
    from kashi_server.worker.process import _mix_back, _separate_vocals

    raw = DATA_DIR / "stems" / separation / f"{cache_key}.wav"
    blend_dir = DATA_DIR / "stems" / f"{separation}-mb{mixback:g}"
    blended = blend_dir / f"{cache_key}.wav"
    if mixback > 0 and blended.exists():  # pre-raw-cache runs left only blends
        return blended, 0.0
    elapsed = 0.0
    if not raw.exists():
        raw.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="kashi-bench-sep-") as tmp:
            out = _separate_vocals(
                song_audio,
                Path(tmp),
                model_filename=SEPARATION_MODELS[separation],
                mixback=0,  # raw stem; the blend below is cached per-mixback
            )
            elapsed = time.monotonic() - started
            shutil.move(str(out), raw)  # handles tmpfs -> disk (cross-device)
    if mixback <= 0:
        return raw, elapsed
    blend_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kashi-bench-mix-") as tmp:
        out = _mix_back(raw, song_audio, Path(tmp) / "blend.wav", mixback)
        shutil.move(str(out), blended)
    return blended, elapsed


def _by_initial(args) -> dict[str, int] | None:
    """--align-offset-by-initial as a class->ms table (Faz 9 P2).

    Accepts `vowel=-110,plosive=-80` as well as JSON: the runs that need this
    are typed into a Windows cmd window, where quoting JSON is its own failure
    mode and a failed quote costs a whole GPU round trip.
    """
    raw = args.align_offset_by_initial
    if not raw:
        return None
    if raw.strip().lower() == "none":  # explicitly no class table
        return {}
    if raw.lstrip().startswith("{"):
        return json.loads(raw)
    return {
        key.strip(): int(value) for key, value in (p.split("=", 1) for p in raw.split(","))
    }


def _align_song(
    audio: Path,
    line_texts: list[str],
    language: str,
    anchors: list[int | None] | None = None,
    model_name: str | None = None,
    romanize: bool | None = None,
    offset_ms: int | None = None,
    by_initial: dict[str, int] | None = None,
    *,
    exact_stride: bool = EXACT_STRIDE_DEFAULT,
    seam_policy: str = SEAM_POLICY_DEFAULT,
    window_star: str = WINDOW_STAR_DEFAULT,
    whole_vote: bool = WHOLE_VOTE_DEFAULT,
    hybrid_t_ms: int = HYBRID_T_MS,
    whole_vote_t_ms: int = WHOLE_VOTE_T_MS,
    unpile: bool = UNPILE_DEFAULT,
    line_region_ms: int | None = LINE_REGION_DEFAULT,
) -> tuple[AlignResult, float]:
    from kashi_server.pipeline.alignment import align

    with tempfile.TemporaryDirectory(prefix="kashi-bench-dec-") as tmp:
        wav = _decode_16k(audio, Path(tmp) / "align.wav")
        started = time.monotonic()
        result = align(
            wav, line_texts, language, anchors, model_name, romanize, offset_ms, by_initial,
            exact_stride=exact_stride, seam_policy=seam_policy, window_star=window_star,
            whole_vote=whole_vote, hybrid_t_ms=hybrid_t_ms, whole_vote_t_ms=whole_vote_t_ms,
            unpile=unpile, line_region_ms=line_region_ms,
        )
    return result, time.monotonic() - started


def _jittered(starts: list[int], stem: str, jitter_ms: int) -> list[int | None]:
    """Deterministic per-song jitter. Jamendo's line starts double as ground
    truth, so feeding them back verbatim as window anchors would measure an
    upper bound; jitter simulates crowd-sourced lrclib stamp noise."""
    import random

    rng = random.Random(f"{stem}:{jitter_ms}")
    return [max(0, s + rng.randint(-jitter_ms, jitter_ms)) for s in starts]


def _verified_only(values: list[float], verified: list[bool] | None) -> list[float]:
    """Keep only the rows a human actually checked.

    A sampled eval set carries truth for a subset; the unchecked rows still
    hold their pre-mark, which came from the very pipeline under test. Letting
    those into the metric would score the pipeline against its own output —
    the self-certification the whole eval exists to prevent. `None` means the
    set is fully annotated (JamendoLyrics), so everything counts.
    """
    if verified is None:
        return values
    return [v for v, ok in zip(values, verified, strict=True) if ok]


def _word_line_indices(result: AlignResult) -> list[int]:
    """Owning line index for every word of the FLATTENED hypothesis stream —
    the order `_pair` pairs positionally in legacy mode, so a per-word row can
    name its line without any re-matching."""
    return [index for index, chunk in enumerate(result.words_per_line) for _ in chunk]


def _run_jamendo(args, tolerances_ms: tuple[int, ...]) -> tuple[list[dict], dict]:
    songs = _select_jamendo(args)
    rows: list[dict] = []
    for index, song in enumerate(songs, 1):
        rows.append(_score_jamendo_song(args, song, tolerances_ms, index, len(songs)))
    return rows, _jamendo_aggregate(rows)


def _select_jamendo(args) -> list:
    root = datasets.ensure_jamendo(DATA_DIR, args.jamendo_root)
    songs = datasets.load_jamendo(
        root,
        languages=set(args.languages.split(",")) if args.languages else None,
        stems=set(args.songs) if args.songs else None,
        limit=args.limit,
    )
    if not songs:
        raise SystemExit("no songs selected — check --languages/--songs/--limit")
    logger.info("jamendo: %d song(s), separation=%s", len(songs), args.separation)
    return songs


def _score_jamendo_song(args, song, tolerances_ms: tuple[int, ...], index: int, total: int) -> dict:
    """One song's row. Errors become data points; the sweep never stops."""
    entry: dict = {
        "stem": song.stem,
        "language": song.language,
        "duration_s": round(song.duration_hint_s, 1),
        "n_words": len(song.words),
        "n_lines": len(song.line_texts),
    }
    stages: dict[str, AlignResult] = {}
    anchors = None
    try:
        if args.legacy:
            audio, separate_s, anchors, result, align_s = _align_jamendo_song(args, song)
        else:
            audio, separate_s, result, align_s, stages = _ship_jamendo_song(args, song, entry)
    except Exception as exc:  # keep sweeping; a broken song is a data point
        logger.exception("%s failed", song.stem)
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry

    if args.legacy:
        result = _postprocess(args, result, anchors, entry)
        entry["aligner"] = _legacy_aligner(args, song.language)
    entry["separate_s"] = round(separate_s, 1)
    entry["align_s"] = round(align_s, 1)
    # Per song, because per-language routing (Faz 8.1) means one run can
    # use several checkpoints — the header can only name the default, and
    # a header that names a model no song ran is exactly the confusion
    # `model_name` was added to prevent.
    entry["align_model"] = result.model_name
    entry["sync"] = result.sync
    entry["quality_score"] = round(result.quality_score, 4)
    # Duration hygiene of the hypothesis itself (no truth needed): the field
    # finding of 2026-10-31 that onset PCO could not see.
    hyp_words = [w for chunk in result.words_per_line for w in chunk]
    entry["hygiene"] = metrics.duration_hygiene(
        [w.start_ms for w in hyp_words], [w.end_ms for w in hyp_words]
    )
    onset_ms = None
    if args.signals:
        # Candidate confidence signals, written NEXT TO the ground-truth
        # error below so "does this signal know anything?" is a
        # correlation rather than an opinion (Faz 8).
        from benchmarks import signals as signal_probes

        onset_ms = signal_probes.detect_onsets(audio)
        entry["signals"] = signal_probes.collect(
            result, audio, round(song.duration_hint_s * 1000), onset_ms
        )
    if not _word_metrics(args, song, result, onset_ms, tolerances_ms, entry):
        return entry  # no verified words: a row, but nothing to report
    if stages:
        # The same song before line QA and before the held-end restore: what
        # each shipped step does to it on human truth.
        stages["shipped"] = result
        entry["stages"] = {
            name: _stage_summary(song, staged, tolerances_ms) for name, staged in stages.items()
        }
    _log_jamendo_song(index, total, song, entry, align_s, separate_s)
    return entry


def _song_audio(args, song) -> tuple[Path, float]:
    return (
        (song.audio_path, 0.0)
        if args.separation == "full-mix"
        else _separated_audio(song.audio_path, song.stem, args.separation, args.mixback)
    )


def _sheet(song, args) -> list[int | None]:
    """The lrclib sheet the bench hands the pipeline: the human line starts with
    the seeded jitter (`random.Random(f"{stem}:{jitter}")`, the same stamps in
    every arm of one jitter), then the whole-sheet offset."""
    stamps = _jittered(song.line_starts_ms, song.stem, args.anchor_jitter_ms)
    if args.stamp_offset_ms:
        stamps = [None if s is None else max(0, s + args.stamp_offset_ms) for s in stamps]
    return stamps


def _ship_jamendo_song(args, song, entry: dict):
    """(audio, separate_s, result, align_s, stages): the song through the
    worker's own chain (process.align_and_review). May raise."""
    from kashi_server.pipeline.lrclib import LyricsText

    audio, separate_s = _song_audio(args, song)
    lyrics = LyricsText(
        line_texts=list(song.line_texts),
        full_text=" ".join(song.line_texts),  # as lrclib.lyrics_from_record builds it
        source_id=0,
        had_synced=True,
        synced_starts_ms=_sheet(song, args),
    )
    with tempfile.TemporaryDirectory(prefix="kashi-bench-ship-") as tmp:
        started = time.monotonic()
        reviewed = _align_and_review(song.stem, Path(tmp), audio, lyrics, args.align_kwargs)
        align_s = time.monotonic() - started
    facts = _shipped_facts(song, reviewed)
    entry["aligner"] = facts.pop("aligner")
    entry["shipped"] = facts
    return audio, separate_s, reviewed.result, align_s, {
        "aligned": reviewed.aligned,
        "line_qa": reviewed.qa.result,
    }


def _align_and_review(label: str, tmp: Path, audio: Path, lyrics, align_kwargs: dict):
    """The worker's chain with no database: a session is only used by the
    second-pass separation mode, which the shipped bench refuses."""
    import types

    from kashi_server.worker.process import align_and_review

    job = types.SimpleNamespace(id=label, options={})
    return align_and_review(
        cast(Any, None), cast(Any, job), tmp, audio, lyrics, align_kwargs=align_kwargs or None
    )


def _shipped_facts(song, reviewed) -> dict:
    """What the shipped steps did to one song: the sheet shift, line QA, the restore."""
    from kashi_server.config import settings
    from kashi_server.pipeline.alignment import resolve_aligner
    from kashi_server.pipeline.langid import detect_language

    qa = reviewed.qa
    language = detect_language(reviewed.lyrics.full_text)  # as _align_stage does
    spec = asdict(resolve_aligner(language))
    final = reviewed.result.words_per_line
    worded = [i for i, text in enumerate(song.line_texts) if text.split()]
    wordless = [i for i in worded if i >= len(final) or not final[i]]
    return {
        "language": language,
        "aligner": {"language": language, **spec,
                    "revision": settings.align_model_revisions.get(spec["model_name"])},
        "sheet_shifts": [
            {"median_ms": s.median_ms, "first_line": s.first_line, "last_line": s.last_line,
             "anchors": s.anchors, "carried": s.carried}
            for s in reviewed.sheet_shifts
        ],
        "line_qa": {
            "offset_ms": qa.offset_ms,
            "degraded_to_line": qa.degraded_to_line,
            "flagged": qa.flagged,
            "density_dropped": qa.density_dropped,
            # Flagged lines the arbiter did not rescue lose their words (a
            # document degraded to line sync loses all of them instead).
            "arbiter_dropped": [] if qa.degraded_to_line else [
                i for i in qa.flagged if i not in set(qa.uncertain)
            ],
            "rescued": qa.uncertain,
            "adlib_shifted": qa.adlib_shifted,
            "adlib_rederived": qa.adlib_rederived,
        },
        "wordless_lines": wordless,
        "restored_ends": reviewed.restored,
    }


def _legacy_aligner(args, language: str) -> dict:
    """What `align()` resolved for this song in legacy mode (the same call)."""
    from kashi_server.config import settings
    from kashi_server.pipeline.alignment import resolve_aligner

    spec = asdict(resolve_aligner(language, args.align_model, args.align_romanize,
                                  args.align_offset_ms, _by_initial(args)))
    return {"language": language, **spec,
            "revision": settings.align_model_revisions.get(spec["model_name"])}


def _align_jamendo_song(args, song):
    """(audio, separate_s, anchors, result, align_s) — may raise; the caller records it."""
    audio, separate_s = _song_audio(args, song)
    anchors = _sheet(song, args) if args.windowed else None
    result, align_s = _align_song(
        audio, song.line_texts, song.language, anchors, args.align_model,
        args.align_romanize, args.align_offset_ms, _by_initial(args),
        exact_stride=args.exact_stride, seam_policy=args.seam_policy, window_star=args.window_star,
        whole_vote=args.whole_vote, hybrid_t_ms=args.hybrid_t_ms,
        whole_vote_t_ms=args.whole_vote_t_ms, unpile=args.unpile,
        line_region_ms=args.line_region_ms or None,
    )
    return audio, separate_s, anchors, result, align_s


def _postprocess(args, result: AlignResult, anchors, entry: dict) -> AlignResult:
    """The bench-only experiments that run between alignment and scoring."""
    if args.line_postprocess != "none" and anchors is not None:  # P2 experiment
        from benchmarks.softoffset import apply_soft_offset

        result = apply_soft_offset(result, anchors, mode=args.line_postprocess)
    # The end trim production ran until 2.31.0 (benchmarks/trim.py). Line
    # starts are untouched, so the case suite only records it (for the P1 A/B).
    if args.trim_ends:
        result, trimmed = trim_word_ends(result)
        entry["trimmed_ends"] = trimmed
    return result


@dataclass(frozen=True)
class _Paired:
    """The pipeline's word for every annotated word, in annotation order. A
    word without a time (line QA left its line wordless) is None."""

    starts: list[int | None]
    ends: list[int | None]
    sources: list[str | None]
    lines: list[int]  # the line each annotated word belongs to
    wordless_starts: dict[int, int]  # line -> the start a wordless line is shown at


def _pair(result: AlignResult, song, by_line: bool) -> _Paired | None:
    """Pair hypothesis words with the annotation, or None when they cannot be.

    Positionally over the whole song (`by_line=False`, the legacy bench: the
    aligner returns every token, so the counts must match). Line by line in the
    shipped mode: line QA may empty a line's words (density drop, the arbiter,
    degrade-to-line), and a dataset line keeps its index in the result, so a
    line's words pair with its tokens or, when it has none, its tokens get no
    time and the line is read at its own start."""
    flat = [w for chunk in result.words_per_line for w in chunk]
    if not by_line:
        if len(flat) != len(song.words):
            return None
        return _Paired(
            starts=[w.start_ms for w in flat], ends=[w.end_ms for w in flat],
            sources=[w.source for w in flat], lines=_word_line_indices(result),
            wordless_starts={},
        )
    counts = [len(text.split()) for text in song.line_texts]
    if sum(counts) != len(song.words) or len(result.lines) != len(counts):
        return None
    starts: list[int | None] = []
    ends: list[int | None] = []
    sources: list[str | None] = []
    wordless: dict[int, int] = {}
    for i, n in enumerate(counts):
        chunk = result.words_per_line[i] if i < len(result.words_per_line) else []
        if len(chunk) == n:
            starts += [w.start_ms for w in chunk]
            ends += [w.end_ms for w in chunk]
            sources += [w.source for w in chunk]
        elif not chunk:
            starts += [None] * n
            ends += [None] * n
            sources += [None] * n
            if n:
                wordless[i] = result.lines[i].start_ms
        else:
            return None  # a line whose word count changed: nothing to pair
    lines = [i for i, n in enumerate(counts) for _ in range(n)]
    return _Paired(starts, ends, sources, lines, wordless)


@dataclass(frozen=True)
class _Scores:
    deviations: list[float | None]  # hyp - truth per annotated word
    stats: metrics.ErrorStats | None  # verified words with a time
    untimed: int  # verified words without a time
    pco_all: dict[str, float] | None  # untimed verified words counted as misses
    end_stats: metrics.ErrorStats | None
    verified_ends: list[float]
    fluency: metrics.Fluency


def _score_words(song, paired: _Paired, tolerances_ms) -> _Scores:
    verified = song.verified or [True] * len(song.words)
    deviations = [
        None if hyp is None else float(hyp - truth)
        for hyp, (truth, _) in zip(paired.starts, song.words, strict=True)
    ]
    scored = [d for d, ok in zip(deviations, verified, strict=True) if ok and d is not None]
    untimed = sum(ok and d is None for d, ok in zip(deviations, verified, strict=True))
    judged = len(scored) + untimed
    end_deviations = [
        None if hyp is None else float(hyp - truth)
        for hyp, truth in zip(paired.ends, song.word_ends_ms, strict=True)
    ]
    verified_ends = [
        d for d, ok in zip(end_deviations, verified, strict=True) if ok and d is not None
    ]
    rows = [
        metrics.WordRow(paired.lines[i], truth, paired.starts[i], verified[i])
        for i, (truth, _) in enumerate(song.words)
    ]
    return _Scores(
        deviations=deviations,
        stats=metrics.error_stats(scored, tolerances_ms),
        untimed=untimed,
        pco_all=(
            {f"{tol / 1000:g}": sum(abs(d) <= tol for d in scored) / judged
             for tol in tolerances_ms}
            if judged else None
        ),
        end_stats=metrics.error_stats(verified_ends, tolerances_ms),
        verified_ends=verified_ends,
        fluency=metrics.fluency(rows, paired.wordless_starts),
    )


def _word_metrics(args, song, result: AlignResult, onset_ms, tolerances_ms, entry: dict) -> bool:
    """Ground-truth error rows into `entry`. False when no verified word is left."""
    paired = _pair(result, song, by_line=not args.legacy)
    if paired is None:
        entry["error"] = "word count mismatch (sync degraded or token drift)"
        return True
    if song.verified is not None:
        entry["verified_words"] = sum(song.verified)
    scores = _score_words(song, paired, tolerances_ms)
    if not args.legacy:
        # Words line QA left without a time: PCO above is over the timed ones,
        # pco_all counts the untimed as misses.
        entry["untimed_words"] = scores.untimed
        entry["pco_all"] = scores.pco_all
    stats = scores.stats
    if stats is None:
        if scores.untimed:  # every verified word lost its time: the line QA story
            entry["fluency"] = asdict(scores.fluency)
            entry["error"] = "no verified word has a time (every line left wordless)"
        else:
            entry["error"] = "no verified words in this song"
        return False
    entry["words"] = asdict(stats)
    # What the screen shows (review step B.3): line starts against the human
    # start, words the overlay lights for under 120 ms, gross errors.
    entry["fluency"] = asdict(scores.fluency)
    if args.dump_words:
        # Per-word rows keyed by ANNOTATION INDEX — the join key for
        # cross-model comparison. Everything else here reduces to
        # summary statistics, which is why the first Qwen correlation
        # could only be computed per song: the per-word errors were
        # thrown away as soon as they were counted. The hypothesis END too:
        # without it a dump cannot show a word the aligner squeezed to
        # nothing (benchmarks.seam_piles --dump). hyp_ms is None for a word
        # without a time (shipped mode).
        entry["word_detail"] = [
            {
                "i": i,
                "token": token,
                "truth_ms": truth_ms,
                "hyp_ms": paired.starts[i],
                "hyp_end_ms": paired.ends[i],
                "truth_end_ms": song.word_ends_ms[i],
                "line": paired.lines[i],
                # Which pass placed the word (windowed path: edges / segment /
                # whole), so per-source accuracy reads straight off a dump.
                "source": paired.sources[i],
                **({} if song.verified is None else {"verified": song.verified[i]}),
            }
            for i, (truth_ms, token) in enumerate(song.words)
        ]
    if args.signals:  # legacy only: every word has a time
        entry["lines_detail"] = _line_signal_truth(
            result, onset_ms, cast(list[float], scores.deviations), song.verified, tolerances_ms
        )
    _end_metrics(scores, entry)
    line_report = metrics.line_start_report(
        [(line.start_ms, line.text) for line in result.lines],
        list(zip(song.line_starts_ms, song.line_texts, strict=True)),
        threshold_ms=LINE_THRESHOLD_MS,
        median_correction=False,  # same-audio ground truth: absolute errors
    )
    entry["lines"] = asdict(line_report.stats) if line_report.stats else None
    return True


def _line_signal_truth(result: AlignResult, onset_ms, deviations, verified, tolerances_ms) -> list:
    # Per-LINE signals paired with per-line truth. The deviation
    # list is positional over the same flattened word stream the
    # signals are computed from, so slicing it by the per-line
    # word counts pairs them exactly — no re-matching, nothing to
    # drift.
    from benchmarks import signals as signal_probes

    lines = signal_probes.line_signals(result, onset_ms)
    cursor = 0
    for row in lines:
        count = row["n_words"]
        chunk = _verified_only(
            deviations[cursor : cursor + count],
            None if verified is None else verified[cursor : cursor + count],
        )
        cursor += count
        line_stats = metrics.error_stats(chunk, tolerances_ms) if chunk else None
        if line_stats is not None:
            row["truth"] = asdict(line_stats)
    return lines


def _end_metrics(scores: _Scores, entry: dict) -> None:
    # END-time metrics (Faz 5 P1): the same pairing, end axis.
    if scores.end_stats is not None:
        entry["word_ends"] = asdict(scores.end_stats)
        entry["over_extension"] = round(metrics.over_extension_rate(scores.verified_ends), 4)
        entry["end_collapse"] = round(metrics.end_collapse_rate(scores.verified_ends), 4)


def _stage_summary(song, result: AlignResult, tolerances_ms) -> dict:
    """One stage of the shipped chain against the truth, compactly."""
    paired = _pair(result, song, by_line=True)
    if paired is None:
        return {"error": "word count mismatch"}
    scores = _score_words(song, paired, tolerances_ms)
    words = [w for chunk in result.words_per_line for w in chunk]
    return {
        "pcs": scores.stats.pcs if scores.stats else None,
        "mae_ms": round(scores.stats.mae_ms, 1) if scores.stats else None,
        "pco_all": scores.pco_all,
        "untimed_words": scores.untimed,
        "word_end_mae_ms": round(scores.end_stats.mae_ms, 1) if scores.end_stats else None,
        "hygiene": metrics.duration_hygiene(
            [w.start_ms for w in words], [w.end_ms for w in words]
        ),
        "fluency": asdict(scores.fluency),
    }


def _log_jamendo_song(index, total, song, entry, align_s, separate_s) -> None:
    logger.info(
        "[%d/%d] %s: %s",
        index,
        total,
        song.stem,
        entry.get("error")
        or f"MAE {entry['words']['mae_ms']:.0f}ms PCO@0.3 {entry['words']['pcs']['0.3']:.2f}"
        + (
            f" endMAE {entry['word_ends']['mae_ms']:.0f}ms"
            f" ovx {entry['over_extension']:.2f}"
            if "word_ends" in entry
            else ""
        )
        + f" (align {align_s:.0f}s{f', sep {separate_s:.0f}s' if separate_s else ''})",
    )


def _fluency_of(rows: list[dict]) -> dict:
    return metrics.fluency_summary(
        [metrics.Fluency(**r["fluency"]) for r in rows if "fluency" in r]
    )


def _jamendo_aggregate(rows: list[dict]) -> dict:
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
            "trimmed_ends_total": sum(r.get("trimmed_ends", 0) for r in scored),
            # Line starts, dwell and gross errors (metrics.fluency_summary).
            "fluency": _fluency_of(scored),
            "per_language": {
                lang: {
                    "songs": len(group),
                    "word_mae_ms_mean": round(mean(r["words"]["mae_ms"] for r in group), 1),
                    "pco_0.3": round(mean(r["words"]["pcs"]["0.3"] for r in group), 4),
                    "fluency": _fluency_of(group),
                }
                for lang in sorted({r["language"] for r in scored})
                if (group := [r for r in scored if r["language"] == lang])
            },
        }
        with_ends = [r for r in scored if "word_ends" in r]
        if with_ends:
            aggregate |= {
                "word_end_mae_ms_mean": round(
                    mean(r["word_ends"]["mae_ms"] for r in with_ends), 1
                ),
                "word_end_mae_ms_median": round(
                    median(r["word_ends"]["mae_ms"] for r in with_ends), 1
                ),
                "over_extension_mean": round(
                    mean(r["over_extension"] for r in with_ends), 4
                ),
                "end_collapse_mean": round(mean(r["end_collapse"] for r in with_ends), 4),
            }
        with_hygiene = [r for r in scored if "hygiene" in r]
        if with_hygiene:
            aggregate |= {
                "zero_len_frac_mean": round(
                    mean(r["hygiene"]["zero_len_frac"] for r in with_hygiene), 4
                ),
                "short_frac_mean": round(
                    mean(r["hygiene"]["short_frac"] for r in with_hygiene), 4
                ),
                "bunched_runs_total": sum(r["hygiene"]["bunched_runs"] for r in with_hygiene),
            }
        separated = [r for r in scored if r.get("separate_s")]
        if separated:  # cache hits excluded — only measured runs count
            aggregate["separate_x_realtime"] = round(
                sum(r["separate_s"] for r in separated)
                / sum(r["duration_s"] for r in separated),
                3,
            )
    # Shipped mode: words without a time counted as misses, the stages, and
    # what the shipped steps did (over every row, failed songs included).
    with_all = [r for r in rows if r.get("pco_all")]
    if with_all:
        aggregate["pco_all"] = {
            tol: round(mean(r["pco_all"][tol] for r in with_all), 4)
            for tol in with_all[0]["pco_all"]
        }
    staged = [r for r in scored if "stages" in r]
    if staged:
        aggregate["stages"] = {
            name: _stage_aggregate([r["stages"][name] for r in staged])
            for name in staged[0]["stages"]
        }
    shipped = [r for r in rows if "shipped" in r]
    if shipped:
        aggregate["shipped"] = _shipped_totals(shipped)
    return aggregate


def _stage_aggregate(stages: list[dict]) -> dict:
    ok = [s for s in stages if s.get("pcs")]
    ends = [s["word_end_mae_ms"] for s in ok if s.get("word_end_mae_ms") is not None]
    with_all = [s for s in stages if s.get("pco_all")]
    return {
        "songs": len(ok),
        "pco": {tol: round(mean(s["pcs"][tol] for s in ok), 4) for tol in ok[0]["pcs"]}
        if ok else None,
        "pco_all": {
            tol: round(mean(s["pco_all"][tol] for s in with_all), 4)
            for tol in with_all[0]["pco_all"]
        } if with_all else None,
        "untimed_words": sum(s.get("untimed_words", 0) for s in stages),
        "word_end_mae_ms_mean": round(mean(ends), 1) if ends else None,
        "zero_len_frac_mean": round(mean(s["hygiene"]["zero_len_frac"] for s in ok), 4)
        if ok else None,
        "short_frac_mean": round(mean(s["hygiene"]["short_frac"] for s in ok), 4)
        if ok else None,
        "bunched_runs_total": sum(s["hygiene"]["bunched_runs"] for s in ok),
        "fluency": metrics.fluency_summary(
            [metrics.Fluency(**s["fluency"]) for s in stages if "fluency" in s]
        ),
    }


def _shipped_totals(rows: list[dict]) -> dict:
    """What the sheet shift, line QA and the restore did, summed over songs."""
    facts = [r["shipped"] for r in rows]
    qa = [f["line_qa"] for f in facts]

    def lines(key: str) -> int:
        return sum(len(q[key]) for q in qa)

    return {
        "songs": len(facts),
        "lines": sum(r["n_lines"] for r in rows),
        "language_differs": [
            r["stem"] for r in rows if r["shipped"]["language"] != r["language"]
        ],
        "sheet_shifted_songs": sum(bool(f["sheet_shifts"]) for f in facts),
        "sheet_shift_sections": sum(len(f["sheet_shifts"]) for f in facts),
        "degraded_to_line_songs": sum(bool(q["degraded_to_line"]) for q in qa),
        "flagged_lines": lines("flagged"),
        "density_dropped_lines": lines("density_dropped"),
        "arbiter_dropped_lines": lines("arbiter_dropped"),
        "rescued_lines": lines("rescued"),
        "adlib_shifted_lines": lines("adlib_shifted"),
        "adlib_rederived_lines": lines("adlib_rederived"),
        "wordless_lines": sum(len(f["wordless_lines"]) for f in facts),
        "songs_with_wordless_lines": sum(bool(f["wordless_lines"]) for f in facts),
        "restored_ends": sum(f["restored_ends"] for f in facts),
    }


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

    cache = DATA_DIR / "cases" / case.id / f"lrclib-{case.lrclib_id}.json"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        response = None
        for attempt in (1, 2, 3):  # lrclib has slow days; a timeout is not a result
            try:
                response = httpx.get(
                    f"https://lrclib.net/api/get/{case.lrclib_id}",
                    headers={
                        "User-Agent": (
                            "kashi-server-dev/benchmarks (+https://github.com/csermet/kashi)"
                        )
                    },
                    timeout=30,
                )
                response.raise_for_status()
                break
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(5 * attempt)
        assert response is not None  # loop either broke with one or raised
        cache.write_text(json.dumps(response.json()), encoding="utf-8")
    synced = json.loads(cache.read_text(encoding="utf-8")).get("syncedLyrics") or ""
    entries = _parse_synced(synced)
    if not entries:
        raise RuntimeError(f"lrclib {case.lrclib_id} has no synced lyrics")
    return entries


def _run_cases(args, _tolerances_ms: tuple[int, ...]) -> tuple[list[dict], dict]:
    # Same signature as _run_jamendo (dispatched alike); the case suite reports
    # its own pass/fail and has no tolerance sweep.
    cases = datasets.load_cases(BENCH_DIR / "cases.yaml")
    rows = [_score_case(args, case) for case in cases]
    return rows, {
        "cases": len(rows),
        "passed": sum(bool(r.get("passed")) for r in rows),
        "trimmed_ends_total": sum(r.get("trimmed_ends", 0) for r in rows),
    }


def _score_case(args, case) -> dict:
    entry: dict = {"id": case.id, "title": case.title, "artist": case.artist}
    try:
        reference, separate_s, anchors, result, align_s = _align_case(args, case)
    except Exception as exc:
        logger.exception("case %s failed", case.id)
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry

    # NOTE: case metrics compare against the SAME lrclib anchors the soft
    # offset is derived from — circular by construction. Case rows under
    # soft-* are a monotonicity/sanity signal only; the honest word-level
    # evidence lives in the Jamendo rows.
    if args.legacy:
        result = _postprocess(args, result, anchors, entry)
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
    logger.info(
        "case %s: %s (%d over threshold, offset %+dms)",
        case.id,
        "PASS" if entry["passed"] else "FAIL",
        report.failures,
        report.offset_ms,
    )
    return entry


def _align_case(args, case):
    """(reference, separate_s, anchors, result, align_s) — may raise."""
    reference = _case_reference(case)
    line_texts = [text for _, text in reference]
    audio = _case_audio(case)
    source, separate_s = (
        (audio, 0.0)
        if args.separation == "full-mix"
        else _separated_audio(audio, case.id, args.separation, args.mixback)
    )
    if not args.legacy:  # the worker's chain on the record's own stamps
        from kashi_server.pipeline.lrclib import LyricsText

        lyrics = LyricsText(
            line_texts=line_texts, full_text=" ".join(line_texts), source_id=case.lrclib_id,
            had_synced=True, synced_starts_ms=[start for start, _ in reference],
        )
        with tempfile.TemporaryDirectory(prefix="kashi-bench-ship-") as tmp:
            started = time.monotonic()
            reviewed = _align_and_review(case.id, Path(tmp), source, lyrics, args.align_kwargs)
            align_s = time.monotonic() - started
        return reference, separate_s, None, reviewed.result, align_s
    anchors = [start for start, _ in reference] if args.windowed else None
    result, align_s = _align_song(
        source, line_texts, case.language, anchors, args.align_model,
        args.align_romanize, args.align_offset_ms, _by_initial(args),
        exact_stride=args.exact_stride, seam_policy=args.seam_policy, window_star=args.window_star,
        whole_vote=args.whole_vote, hybrid_t_ms=args.hybrid_t_ms,
        whole_vote_t_ms=args.whole_vote_t_ms, unpile=args.unpile,
        line_region_ms=args.line_region_ms or None,
    )
    return reference, separate_s, anchors, result, align_s


def main(argv: list[str] | None = None) -> int:
    args = _finalize_args(_parse_args(argv))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if not args.legacy:
        _configure_shipped(args)

    tolerances_ms = _tolerances_ms(args)
    config_name = _config_name(args)
    started = time.monotonic()
    report: dict = {"meta": _report_meta(args, config_name, tolerances_ms)}
    if args.dataset in ("jamendo", "both"):
        rows, aggregate = _run_jamendo(args, tolerances_ms)
        report["jamendo"] = {"aggregate": aggregate, "songs": rows}
        report["meta"] |= _aligner_meta(args, rows)
    if args.dataset in ("cases", "both"):
        rows, aggregate = _run_cases(args, tolerances_ms)
        report["cases"] = {"aggregate": aggregate, "results": rows}
    report["meta"]["wall_s"] = round(time.monotonic() - started, 1)
    _write_report(report)
    return 0


def _research_kwargs(args) -> dict:
    """The windowed-path switches that differ from `align()`'s own defaults
    (the worker calls it with none of them)."""
    import inspect

    from kashi_server.pipeline.alignment import align

    chosen = {
        "exact_stride": args.exact_stride, "seam_policy": args.seam_policy,
        "window_star": args.window_star, "whole_vote": args.whole_vote,
        "hybrid_t_ms": args.hybrid_t_ms, "whole_vote_t_ms": args.whole_vote_t_ms,
        "unpile": args.unpile, "line_region_ms": args.line_region_ms or None,
    }
    defaults = inspect.signature(align).parameters
    return {name: value for name, value in chosen.items() if value != defaults[name].default}


def _finalize_args(args: argparse.Namespace) -> argparse.Namespace:
    """Defaults that depend on the mode, and what makes a shipped run differ
    from the worker (`args.deviations`, written to the result meta)."""
    if args.legacy:
        if args.no_density_drop:
            raise SystemExit("--no-density-drop needs the shipped mode (legacy runs no line QA)")
        args.windowed = bool(args.windowed)
        args.separation = args.separation or "full-mix"
        args.mixback = 0.15 if args.mixback is None else args.mixback
        args.trim_ends = True if args.trim_ends is None else args.trim_ends
        args.align_kwargs = {}
        args.deviations = None
        return args

    from kashi_server.config import settings

    if args.trim_ends:
        raise SystemExit("production has no end trim since 2.32.0: --trim-ends needs --legacy")
    if args.line_postprocess != "none" or args.signals:
        raise SystemExit("--line-postprocess and --signals are legacy-mode experiments")
    if settings.separation_mode != "always":
        raise SystemExit(f"the shipped bench replays separation_mode=always, the settings say "
                         f"{settings.separation_mode}")
    production = next((key for key, name in SEPARATION_MODELS.items()
                       if name == settings.separation_model_filename), None)
    if production is None:
        raise SystemExit(f"no bench separator key for {settings.separation_model_filename}")
    deviations: list[str] = []
    if args.separation is None:
        args.separation = production
    elif args.separation != production:
        deviations.append(f"separation {args.separation} (production: {production})")
    if args.mixback is None:
        args.mixback = settings.separation_mixback
    elif args.mixback != settings.separation_mixback and args.separation != "full-mix":
        deviations.append(f"mixback {args.mixback:g} (production: {settings.separation_mixback:g})")
    if args.windowed is None:
        args.windowed = settings.windowed_alignment
    elif args.windowed != settings.windowed_alignment:
        deviations.append(f"windowed {args.windowed}")
    args.trim_ends = False
    args.align_kwargs = _research_kwargs(args)
    deviations += [f"align {name}={value}" for name, value in args.align_kwargs.items()]
    for flag, value in (("--align-model", args.align_model),
                        ("--align-romanize", args.align_romanize),
                        ("--align-offset-ms", args.align_offset_ms),
                        ("--align-offset-by-initial", args.align_offset_by_initial)):
        if value is not None:
            deviations.append(f"{flag} {value}")
    if args.no_density_drop:
        deviations.append("line QA density drop off")
    args.deviations = deviations
    return args


def _configure_shipped(args) -> None:
    """Shipped mode never hands `align()` a model or an offset: it sets what the
    worker's own resolution reads (settings.align_models / align_model /
    align_offset_ms), so the aligner, the sheet shift's ear and every other
    reader see ONE configuration — production's, unless a flag changes it.
    `--align-model` replaces the checkpoint of every table entry and the
    fallback but keeps each language's offsets (the 2026-11-12 trap was the
    table silently bypassed); `--align-offset-ms` / `--align-offset-by-initial`
    (`none` zeroes the class table) replace them. Bench process only."""
    from kashi_server.config import settings

    settings.windowed_alignment = args.windowed
    if args.onset_cache:
        from kashi_server.worker import process

        process.detect_onsets = _cached_onsets(process.detect_onsets, Path(args.onset_cache))
    if args.no_density_drop:
        # The review's question (C.4): should a doubtful line light or stay
        # plain? Line QA's border-case density drop, and only it, is off.
        from kashi_server.pipeline import line_qa

        line_qa._border_case_drops = lambda result, refs, flagged: set()
    by_initial = _by_initial(args)
    table = {}
    for code, choice in settings.align_models.items():
        update: dict = {}
        if args.align_model:
            update |= {"checkpoint": args.align_model, "romanize": args.align_romanize}
        elif args.align_romanize is not None:
            update["romanize"] = args.align_romanize
        if args.align_offset_ms is not None:
            update["offset_ms"] = args.align_offset_ms
        if by_initial is not None:
            update["offset_by_initial"] = by_initial or None
        table[code] = choice.model_copy(update=update)
    settings.align_models = table
    if args.align_model:
        settings.align_model = args.align_model
    if args.align_romanize is not None:
        settings.align_romanize = args.align_romanize
    if args.align_offset_ms is not None:
        settings.align_offset_ms = args.align_offset_ms


def _cached_onsets(detect, cache_dir: Path):
    """`detect_onsets` remembered per audio CONTENT (sha256 of the wav): the
    onsets of a stem do not depend on the sheet, so the arms of one sweep share
    them, and librosa's ~10 s per song stops holding the GPU lock."""
    import hashlib

    def cached(wav: Path):
        key = hashlib.sha256(Path(wav).read_bytes()).hexdigest()
        hit = cache_dir / f"{key}.json"
        if hit.exists():
            return json.loads(hit.read_text())
        onsets = detect(wav)
        cache_dir.mkdir(parents=True, exist_ok=True)
        hit.write_text(json.dumps(onsets))
        return onsets

    return cached


def _offset_source(args) -> str:
    """In words, where the run's offsets came from."""
    flags = args.align_offset_ms is not None or args.align_offset_by_initial is not None
    if args.legacy and args.align_model:
        return ("none from the table: --align-model takes the decision out of it (legacy)"
                + ("; offsets from flags" if flags else "; settings.align_offset_ms only"))
    source = "production table (config.PRODUCTION_ALIGN_MODELS) as configured"
    if args.align_model:
        source += "; its checkpoints replaced by --align-model, its offsets kept"
    if flags:
        source += "; offsets replaced by flags"
    return source


def _aligner_meta(args, rows: list[dict]) -> dict:
    """What the songs actually ran with, per language. Before 2.33.0 the
    summary fields named the fallback model and the settings' 0 ms offset even
    when the table moved every English word."""
    by_language: dict[str, dict] = {}
    for row in rows:
        spec = row.get("aligner")
        if spec:
            by_language.setdefault(spec["language"],
                                   {k: v for k, v in spec.items() if k != "language"})
    checkpoints = {spec["model_name"] for spec in by_language.values()}
    bases = {spec["offset_ms"] for spec in by_language.values()}
    tables = {json.dumps(spec["offset_by_initial"], sort_keys=True)
              for spec in by_language.values()}
    return {
        "aligners": by_language,
        "alignment_model": (
            next(iter(checkpoints)) if len(checkpoints) == 1 else "per-language (see aligners)"
        ),
        "align_offset_ms": next(iter(bases)) if len(bases) == 1 else None,
        "align_offset_by_initial": (
            json.loads(next(iter(tables))) if len(tables) == 1 else "per-language (see aligners)"
        ),
        "offset_table": _offset_source(args),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legacy",
        action="store_true",
        help=(
            "the bench before 2.33.0: align() plus the private end trim, the old defaults "
            "(full mix, mixback 0.15, not windowed, trim on), --align-model bypassing the "
            "offset table. Reproduces every earlier result file with its own flags."
        ),
    )
    parser.add_argument("--dataset", choices=["jamendo", "cases", "both"], default="both")
    parser.add_argument(
        "--separation",
        choices=sorted(SEPARATION_MODELS),
        help="vocal separator (default: shipped mode the production one, legacy full-mix)",
    )
    parser.add_argument(
        "--mixback",
        type=float,
        help="ignored for full-mix (default: shipped mode the production value, legacy 0.15)",
    )
    parser.add_argument("--languages", help="comma-separated ISO-639-3, e.g. eng,spa")
    parser.add_argument("--songs", nargs="*", help="jamendo stems to include")
    parser.add_argument("--limit", type=int, help="max jamendo songs (after filters)")
    parser.add_argument("--tolerances", default="0.1,0.2,0.3,0.5", help="PCO tolerances, seconds")
    parser.add_argument(
        "--jamendo-root",
        default="jamendolyrics",
        help=(
            "dataset directory under benchmarks/data/ (default: the downloaded "
            "JamendoLyrics). Use `jamendolyrics-tr` for the hand-built Turkish "
            "set — same schema, so every metric and flag applies unchanged, "
            "which is the whole reason it was written in this shape."
        ),
    )
    parser.add_argument(
        "--windowed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="line-anchored windowed alignment (default: shipped mode the production "
        "setting, legacy off)",
    )
    parser.add_argument(
        "--align-model",
        help=(
            "forced aligner checkpoint. The point of the Faz 8 P-B1 seam: a "
            "bake-off is a flag, not a code change. Shipped mode: replaces the "
            "checkpoint of every language and keeps each language's production "
            "offsets (zero them with --align-offset-ms 0 --align-offset-by-initial "
            "none); its romanize is --align-romanize or settings.align_romanize. "
            "Legacy: bypasses the table, offsets included."
        ),
    )
    parser.add_argument(
        "--align-romanize",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "uroman romanization before alignment (default: settings). MMS "
            "REQUIRES it — its vocabulary is romanized Latin. A model trained "
            "on the language's own alphabet wants --no-align-romanize: "
            "mpoyraz's Turkish vocab has ç ğ ı ö ş ü natively, and romanizing "
            "first hands it a phoneme set it never learned, which understates "
            "it. The flag rides with the checkpoint."
        ),
    )
    parser.add_argument(
        "--align-offset-ms",
        type=int,
        help=(
            "constant shift applied to every word and line, ms (default: "
            "settings). Negative moves timings EARLIER. Sung alignment is "
            "measurably LATE — 76%% of English words, median +80 ms, all 20 "
            "songs — so this is the cheapest accuracy in the pipeline. Fit it "
            "with `python -m benchmarks.lateness <run>.json`, then verify by "
            "re-running WITH it: a correct offset drives the fitted residual "
            "to zero. Pass 0 to measure a chain with the correction OFF."
        ),
    )
    parser.add_argument(
        "--align-offset-by-initial",
        help=(
            "JSON class->ms table refining --align-offset-ms by the word's "
            "first sound, e.g. "
            "vowel=-110,fricative=-100,plosive=-80,sonorant=-60 (JSON also "
            "accepted; `none` = no class table). "
            "Measured on English: a vowel-initial word is nearly twice as late "
            "as a plosive-initial one (+112 vs +62 ms), worth +0.013 PCO@0.1 "
            "cross-validated. Classes: pipeline/phonetics.py."
        ),
    )
    parser.add_argument(
        "--stamp-offset-ms",
        type=int,
        default=0,
        help=(
            "jamendo only: move the whole sheet by this much after the jitter "
            "(an lrclib record on another clock; the sheet shift's territory "
            "beyond 1 s)"
        ),
    )
    parser.add_argument(
        "--onset-cache",
        help="shipped mode: a directory remembering line QA's onsets per audio content, "
        "shared by the arms of a sweep (the result does not change; the CPU pass goes)",
    )
    parser.add_argument(
        "--no-density-drop",
        action="store_true",
        help="shipped mode: line QA keeps the words of the lines its border-case density "
        "drop would empty (review C.4: should a doubtful line light or stay plain?)",
    )
    parser.add_argument(
        "--line-postprocess",
        choices=("none", "soft-median", "soft-pl"),
        default="none",
        help="P2 experiment: shift every line (rigidly, words included) by its "
        "median-filtered anchor delta — step (soft-median) or piecewise-linear "
        "(soft-pl). Requires --windowed; bench-only, never production.",
    )
    parser.add_argument(
        "--trim-ends",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="legacy mode: apply the word-end sustain trim production ran until pipeline "
        "2.31.0 (benchmarks/trim.py; on by default there so the end axis stays comparable "
        "with earlier results; --no-trim-ends for the P1 baseline). The shipped mode runs "
        "the restore production runs instead.",
    )
    parser.add_argument(
        "--anchor-jitter-ms",
        type=int,
        default=400,
        help="jamendo only: simulate lrclib stamp noise on the anchors (0 = ground-truth anchors)",
    )
    parser.add_argument(
        "--signals",
        action="store_true",
        help=(
            "also emit candidate confidence signals per song (benchmarks/"
            "signals.py). The shipped quality_score correlates at r=0.36 with "
            "real accuracy; this is the rig for finding something that does "
            "better. Adds an audio pass per song for onset detection."
        ),
    )
    parser.add_argument(
        "--dump-words",
        action="store_true",
        help=(
            "also emit every word's truth/hypothesis time, keyed by annotation "
            "index (benchmarks/word_disagreement.py joins two such dumps). "
            "Without it the per-word errors are reduced to MAE/PCO and thrown "
            "away, which is why 'do two models fail on the same WORDS?' could "
            "not be answered from any sweep run so far. Adds ~100 KB/song."
        ),
    )
    parser.add_argument(
        "--exact-stride",
        action=argparse.BooleanOptionalAction,
        default=EXACT_STRIDE_DEFAULT,
        help=(
            "windowed path only: the model's real 20 ms frame step (plus "
            "alignment.WINDOWED_LEAD_MS) instead of the aligner library's rounded "
            "21 (every window ran 4.8%% slow from its slice start; Faz 9, "
            "2026-10). Default = what production ships (on since 2.28.0)."
        ),
    )
    parser.add_argument(
        "--seam-policy",
        choices=SEAM_POLICIES,
        default=SEAM_POLICY_DEFAULT,
        help=(
            "windowed path only: at a seam, 'yield' pulls a word parked in its "
            "window's pad back to its own line instead of pushing the next "
            "line's words onto it (pipeline/windows.yield_pad_words)."
        ),
    )
    parser.add_argument(
        "--window-star",
        choices=WINDOW_STARS,
        default=WINDOW_STAR_DEFAULT,
        help=(
            "windowed path only: star tokens at the slice edges (production) or "
            "before every word, as the whole-track path does "
            "(alignment.WINDOW_STAR_DEFAULT); 'hybrid' aligns both over one "
            "emission pass and keeps segment per word where it agrees with "
            "edges (--hybrid-t-ms; Faz 9 round 5)."
        ),
    )
    parser.add_argument(
        "--hybrid-t-ms",
        type=int,
        default=HYBRID_T_MS,
        help="windowed hybrid stars: agreement window, ms (alignment.HYBRID_T_MS)",
    )
    parser.add_argument(
        "--whole-vote",
        action=argparse.BooleanOptionalAction,
        default=WHOLE_VOTE_DEFAULT,
        help=(
            "windowed path only: one extra whole-track pass; a word takes its "
            "whole-track time where it agrees with the windowed one "
            "(--whole-vote-t-ms; Faz 9 round 5, 'Third vote')."
        ),
    )
    parser.add_argument(
        "--whole-vote-t-ms",
        type=int,
        default=WHOLE_VOTE_T_MS,
        help="whole-track vote: agreement window, ms (alignment.WHOLE_VOTE_T_MS)",
    )
    parser.add_argument(
        "--unpile",
        action=argparse.BooleanOptionalAction,
        default=UNPILE_DEFAULT,
        help=(
            "windowed path only: a run of 3+ starts within 60 ms that the hybrid "
            "or the vote made takes the edges starts back (2.30.0, timing v3 V3); "
            "--no-unpile reproduces 2.29.1."
        ),
    )
    parser.add_argument(
        "--line-region-ms",
        type=int,
        default=LINE_REGION_DEFAULT or 0,
        help=(
            "windowed path only: each line of a window with 2+ stamps starts within "
            "this many ms of its own stamp (2.31.0, timing v3 V1); 0 is off and "
            "reproduces 2.30.0."
        ),
    )
    parser.add_argument("--label", help="results filename label (default: config name)")
    return parser.parse_args(argv)


def _tolerances_ms(args) -> tuple[int, ...]:
    tolerances_ms = tuple(round(float(t) * 1000) for t in args.tolerances.split(","))
    if 300 not in tolerances_ms:  # summaries key off PCO@0.3 — always measure it
        tolerances_ms += (300,)
    return tolerances_ms


def _config_name(args) -> str:
    config_name = args.separation + (
        f"-mb{args.mixback:g}" if args.separation != "full-mix" else ""
    )
    if args.windowed:
        config_name += "-win"
    # Suffixes name departures from what production ships (2.28.0 on); both
    # switches only exist on the windowed path. Labels alone do not compare
    # across 2.28.0: "-win" meant the library clock and clamp before it, so
    # read meta.exact_stride.
    if args.windowed and not args.exact_stride:
        config_name += "-libstride"
    if args.windowed and args.seam_policy != SEAM_POLICY_DEFAULT:
        config_name += f"-{args.seam_policy}"
    if args.windowed and args.window_star != WINDOW_STAR_DEFAULT:
        config_name += f"-star{args.window_star}"
    if args.windowed and args.window_star == "hybrid" and args.hybrid_t_ms != HYBRID_T_MS:
        config_name += f"-t{args.hybrid_t_ms}"
    if args.windowed and args.whole_vote != WHOLE_VOTE_DEFAULT:
        config_name += "-vote" if args.whole_vote else "-novote"
    if args.windowed and args.whole_vote and args.whole_vote_t_ms != WHOLE_VOTE_T_MS:
        config_name += f"-vt{args.whole_vote_t_ms}"
    if args.windowed and args.unpile != UNPILE_DEFAULT:
        config_name += "-unpile" if args.unpile else "-nounpile"
    if args.windowed and (args.line_region_ms or None) != LINE_REGION_DEFAULT:
        config_name += f"-region{args.line_region_ms}"
    if args.line_postprocess != "none":
        config_name += f"-{args.line_postprocess}"
    if args.legacy and not args.trim_ends:
        config_name += "-notrim"
    if args.stamp_offset_ms:
        config_name += f"-off{args.stamp_offset_ms}"
    if not args.legacy:
        config_name = "shipped-" + config_name + ("-nodensity" if args.no_density_drop else "")
    return config_name


def _report_meta(args, config_name: str, tolerances_ms: tuple[int, ...]) -> dict:
    from kashi_server.config import (
        PIPELINE_FIELDS,
        pipeline_overrides,
        pipeline_value,
        settings,
    )
    from kashi_server.pipeline.alignment import resolve_model_name
    from kashi_server.version import PIPELINE_VERSION

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": args.label or config_name,
        "pipeline_version": PIPELINE_VERSION,
        # "shipped": the worker's chain after the lyrics (process.align_and_review);
        # "legacy": align() + the private end trim, the bench before 2.33.0.
        "bench_mode": "legacy" if args.legacy else "shipped",
        # Shipped mode: everything that makes this run differ from the worker.
        "deviations": args.deviations,
        # Every pipeline setting in force (config.PIPELINE_FIELDS) and the ones
        # an env value moved off the code default.
        "pipeline_settings": {name: pipeline_value(name, getattr(settings, name))
                              for name in PIPELINE_FIELDS},
        "pipeline_overrides": pipeline_overrides(settings),
        # The command line's aligner flags as given; what ran is `aligners`
        # (written after the run, per language), and the summary fields below
        # are overwritten from it.
        "flags": {"align_model": args.align_model, "align_romanize": args.align_romanize,
                  "align_offset_ms": args.align_offset_ms,
                  "align_offset_by_initial": _by_initial(args)},
        "alignment_model": resolve_model_name(args.align_model),
        # The routing table that was in force, romanize flags included: a
        # checkpoint measured with and without uroman is two different
        # measurements, and the file has to be able to tell them apart.
        "align_models": (
            {code: choice.model_dump() for code, choice in settings.align_models.items()}
            if (not args.align_model or not args.legacy) and settings.align_models
            else None
        ),
        "align_romanize": args.align_romanize,
        # The correction is part of the chain being measured: two runs of
        # one checkpoint at different offsets are two different results.
        "align_offset_by_initial": _by_initial(args),
        "align_offset_ms": (
            args.align_offset_ms
            if args.align_offset_ms is not None
            else settings.align_offset_ms
        ),
        "separation": args.separation,
        "mixback": args.mixback if args.separation != "full-mix" else None,
        "windowed": args.windowed,
        # The sheet's errors: seeded jitter, then a whole-sheet offset. The
        # shipped mode always hands the pipeline the sheet (line QA reads it).
        "anchor_jitter_ms": args.anchor_jitter_ms if args.windowed or not args.legacy else None,
        "stamp_offset_ms": args.stamp_offset_ms,
        "density_drop": None if args.legacy else not args.no_density_drop,
        "exact_stride": args.exact_stride,
        "windowed_lead_ms": WINDOWED_LEAD_MS if args.exact_stride and args.windowed else 0,
        "window_star": args.window_star,
        # Since the 2.29 groundwork the lead is the EDGES topology's: words
        # placed by segment stars or the whole-track vote net no lead. The
        # 2026-10-25 `-starsegment` runs predate this (every word took it).
        "lead_rule": "edges-only",
        "hybrid_t_ms": (
            args.hybrid_t_ms if args.windowed and args.window_star == "hybrid" else None
        ),
        "whole_vote": args.whole_vote if args.windowed else None,
        "whole_vote_t_ms": args.whole_vote_t_ms if args.windowed and args.whole_vote else None,
        "unpile": args.unpile if args.windowed else None,
        "line_region_ms": (args.line_region_ms or None) if args.windowed else None,
        "seam_policy": args.seam_policy,
        "signals": args.signals,
        "line_postprocess": args.line_postprocess,
        "trim_ends": args.trim_ends,
        "host": platform.node(),
        "cpus": os.cpu_count(),
        "tolerances_s": [t / 1000 for t in tolerances_ms],
    }


def _write_report(report: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now(UTC):%Y-%m-%d}-{report['meta']['label']}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    for section in ("jamendo", "cases"):
        if section in report:
            print(section, json.dumps(report[section]["aggregate"], indent=1, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
