"""Systematic lateness of a word-dump run, and what a constant shift buys.

Faz 9 P1. The alignment error on singing is not centred on zero: measured on
JamendoLyrics English (full-precision human annotation, 5569 words, MMS),
**76% of words are marked LATE** with a median signed error of +79 ms. The
mechanism was noted back in Faz 8 research — in singing, the note onset lands
on the syllable's VOWEL while the written word starts on a consonant, so a CTC
model that hears the vowel reports a start about one consonant late.

A bias is the cheapest thing in the world to fix: subtract it. On MMS that one
line moved PCO@0.1 from 0.492 to 0.588. But the bias belongs to the MODEL, not
to the pipeline, and the shipped English aligner is no longer MMS — so the
number has to be re-measured per checkpoint rather than inherited. That is what
this module is for.

Two habits it enforces, because both were easy to get wrong by hand:

1. **Per-song aggregation.** `run.py` reports PCO as the mean of per-song
   fractions (MIREX convention), so pooling every word into one bucket would
   produce a number that cannot be compared with any result file — long songs
   would quietly outvote short ones.
2. **Leave-one-song-out.** Picking the offset that maximises PCO on the same
   songs it was measured on is fitting the eval set. The honest number is the
   held-out one: fit on N-1 songs, score the song left out. When the bias is
   real those two agree; when it is noise, only the fitted one looks good.

CLI:  python -m benchmarks.lateness results/<run>.json [--tolerance 0.1] [--by-initial]

--by-initial fits one offset per class of the word's first sound (vowel,
fricative, plosive, sonorant — pipeline/phonetics.py), which is the shape of
the shipped English correction, and compares it held-out against the single
offset. Fit it on a run with the correction OFF (`--align-offset-ms 0`, no
`--align-offset-by-initial`), or the table comes out as a residual.

Since pipeline 2.28.0 the configured offsets describe the model over the WHOLE
track, and windowed alignment adds alignment.WINDOWED_LEAD_MS on top. So fit a
table for the config on a run WITHOUT --windowed; a windowed fit (with the
lead) lands within ~10 ms of it but answers a different question.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

from benchmarks.metrics import error_stats

# Applying an offset means hyp' = hyp + offset, so a LATE model (positive
# error) is corrected by a NEGATIVE offset. Symmetric since 2026-10-23: on the
# exact windowed clock models run EARLY (fra/spa want +80), and the old
# -200..+50 grid quietly returned its own edge, +50, for them. A best value
# on either edge is reported as such (`on_grid_edge`), never trusted.
DEFAULT_OFFSETS_MS = tuple(range(-200, 201, 10))
DEFAULT_TOLERANCES_MS = (100, 200, 300, 500)


@dataclass(frozen=True)
class Sweep:
    offset_ms: int
    pco: dict[str, float]  # tolerance (seconds, str) -> mean of per-song fractions


def signed_errors_by_song(report: dict) -> dict[str, list[float]]:
    """stem -> [hyp_ms - truth_ms], positive meaning the word was marked LATE.

    Words the ground truth does not vouch for are dropped. In the Turkish eval
    set an untouched word's "truth" is the pre-mark the model itself produced
    (`verified: false`), so counting those would measure the model against its
    own output and report a bias of zero no matter what it does.
    """
    songs: dict[str, list[float]] = {}
    for song in report.get("jamendo", {}).get("songs", []):
        detail = song.get("word_detail")
        if not detail:
            continue  # a run without --dump-words, or a failed song
        errors = [
            float(word["hyp_ms"]) - float(word["truth_ms"])
            for word in detail
            if word.get("verified", True)
        ]
        if errors:
            songs[song["stem"]] = errors
    return songs


def lateness_stats(errors_by_song: dict[str, list[float]]) -> dict:
    """The bias itself, before any correction is applied."""
    pooled = [error for errors in errors_by_song.values() for error in errors]
    if not pooled:
        return {"words": 0}
    return {
        "songs": len(errors_by_song),
        "words": len(pooled),
        "late_share": sum(error > 0 for error in pooled) / len(pooled),
        "median_signed_ms": median(pooled),
        "mean_signed_ms": mean(pooled),
        # Per-song medians: a bias carried by EVERY song is a property of the
        # model; one carried by a couple of broken songs is not, and the two
        # look identical in the pooled number.
        "song_median_ms": median([median(errors) for errors in errors_by_song.values()]),
        "songs_late": sum(median(errors) > 0 for errors in errors_by_song.values()),
    }


def pco_at_offset(
    errors_by_song: dict[str, list[float]],
    offset_ms: float,
    tolerances_ms: tuple[int, ...] = DEFAULT_TOLERANCES_MS,
) -> dict[str, float]:
    """Mean of per-song PCS after shifting every hypothesis by `offset_ms`."""
    per_song = []
    for errors in errors_by_song.values():
        stats = error_stats([error + offset_ms for error in errors], tolerances_ms)
        if stats:
            per_song.append(stats.pcs)
    if not per_song:
        return {}
    return {
        tol: mean(song[tol] for song in per_song) for tol in per_song[0]
    }


def sweep(
    errors_by_song: dict[str, list[float]],
    offsets_ms: tuple[int, ...] = DEFAULT_OFFSETS_MS,
    tolerances_ms: tuple[int, ...] = DEFAULT_TOLERANCES_MS,
) -> list[Sweep]:
    return [
        Sweep(offset, pco_at_offset(errors_by_song, offset, tolerances_ms))
        for offset in offsets_ms
    ]


def best_offset(
    errors_by_song: dict[str, list[float]],
    tolerance_ms: int = 100,
    offsets_ms: tuple[int, ...] = DEFAULT_OFFSETS_MS,
) -> int:
    """The offset maximising PCO at `tolerance_ms`.

    PCO is a step function, so the maximum is a PLATEAU rather than a point:
    any shift that brings the same words inside the tolerance scores the same.
    Which member of the plateau to ship is a real decision, and both obvious
    answers are wrong. The smallest correction sits at the plateau's EDGE, one
    bad song away from falling off it; the largest sits at the other edge.
    This takes the MIDDLE — the shift with the most margin on both sides, which
    is also where the bias itself lies when the plateau is the bias.

    Zero wins whenever it ties: if leaving the timings alone scores as well as
    moving them, the data has made no claim about a bias and shipping one
    anyway is invention.
    """
    key = f"{tolerance_ms / 1000:g}"
    rows = sweep(errors_by_song, offsets_ms, (tolerance_ms,))
    best = max(row.pco.get(key, 0.0) for row in rows)
    plateau = sorted(
        row.offset_ms for row in rows if row.pco.get(key, 0.0) >= best - 1e-12
    )
    return 0 if 0 in plateau else plateau[len(plateau) // 2]


def on_grid_edge(offset_ms: int, offsets_ms: tuple[int, ...] = DEFAULT_OFFSETS_MS) -> bool:
    """True when a fitted offset is the grid's own limit, i.e. the real
    optimum may lie beyond it."""
    return offset_ms in (min(offsets_ms), max(offsets_ms))


def held_out_gain(
    errors_by_song: dict[str, list[float]],
    tolerance_ms: int = 100,
    offsets_ms: tuple[int, ...] = DEFAULT_OFFSETS_MS,
) -> dict:
    """Leave-one-song-out: fit the offset on the rest, score the song held out.

    This is the number to quote. The fitted score is optimistic by
    construction; the gap between them is how much of the win was the eval set
    memorising itself.
    """
    key = f"{tolerance_ms / 1000:g}"
    if len(errors_by_song) < 2:
        return {}
    baseline, corrected, offsets = [], [], []
    for stem in errors_by_song:
        rest = {other: e for other, e in errors_by_song.items() if other != stem}
        offset = best_offset(rest, tolerance_ms, offsets_ms)
        offsets.append(offset)
        held = {stem: errors_by_song[stem]}
        baseline.append(pco_at_offset(held, 0, (tolerance_ms,))[key])
        corrected.append(pco_at_offset(held, offset, (tolerance_ms,))[key])
    return {
        "tolerance_ms": tolerance_ms,
        "baseline": mean(baseline),
        "held_out": mean(corrected),
        "gain": mean(corrected) - mean(baseline),
        "offset_min_ms": min(offsets),
        "offset_max_ms": max(offsets),
    }


def words_by_song(report: dict) -> dict[str, list[tuple[str, float]]]:
    """stem -> [(initial class, signed error)], verified words only.

    Tokens with no letter (a bare "♪") get the class "none" and are corrected
    by the plain offset, as the pipeline does.
    """
    from kashi_server.pipeline.phonetics import initial_class

    songs: dict[str, list[tuple[str, float]]] = {}
    for song in report.get("jamendo", {}).get("songs", []):
        rows = [
            (
                initial_class(word["token"]) or "none",
                float(word["hyp_ms"]) - float(word["truth_ms"]),
            )
            for word in song.get("word_detail") or []
            if word.get("verified", True)
        ]
        if rows:
            songs[song["stem"]] = rows
    return songs


def by_initial_table(
    songs: dict[str, list[tuple[str, float]]], tolerance_ms: int = 100
) -> tuple[int, dict[str, int]]:
    """(plain offset, class -> offset), each fitted on its own words."""
    base = best_offset({stem: [e for _, e in rows] for stem, rows in songs.items()}, tolerance_ms)
    classes = sorted({cls for rows in songs.values() for cls, _ in rows} - {"none"})
    table = {}
    for cls in classes:
        errors = {stem: [e for c, e in rows if c == cls] for stem, rows in songs.items()}
        table[cls] = best_offset({s: e for s, e in errors.items() if e}, tolerance_ms)
    return base, table


def held_out_by_initial(
    songs: dict[str, list[tuple[str, float]]], tolerance_ms: int = 100
) -> dict:
    """Leave-one-song-out for the class table, next to the single offset."""
    key = f"{tolerance_ms / 1000:g}"
    if len(songs) < 2:
        return {}
    none, single, table_scores = [], [], []
    for stem, rows in songs.items():
        rest = {other: r for other, r in songs.items() if other != stem}
        base, table = by_initial_table(rest, tolerance_ms)
        held = {stem: [e for _, e in rows]}
        none.append(pco_at_offset(held, 0, (tolerance_ms,))[key])
        single.append(pco_at_offset(held, base, (tolerance_ms,))[key])
        shifted = {stem: [e + table.get(cls, base) for cls, e in rows]}
        table_scores.append(pco_at_offset(shifted, 0, (tolerance_ms,))[key])
    return {
        "tolerance_ms": tolerance_ms,
        "none": mean(none),
        "single": mean(single),
        "by_initial": mean(table_scores),
    }


def format_by_initial(report: dict, tolerance_ms: int = 100) -> str:
    songs = words_by_song(report)
    if not songs:
        return "no word_detail in this run — re-run the benchmark with --dump-words"
    base, table = by_initial_table(songs, tolerance_ms)
    counts: dict[str, int] = {}
    for rows in songs.values():
        for cls, _ in rows:
            counts[cls] = counts.get(cls, 0) + 1
    lines = [
        f"run   : {report.get('meta', {}).get('label')}  "
        f"(fit at PCO@{tolerance_ms / 1000:g}, {len(songs)} songs)",
        f"plain offset (all words): {base:+d} ms",
    ]
    lines += [
        f"  {cls:10} {counts.get(cls, 0):6d} words  {off:+5d} ms" for cls, off in table.items()
    ]
    lines.append(f"  {'none':10} {counts.get('none', 0):6d} words  (plain)")
    flag = ",".join(f"{cls}={off}" for cls, off in table.items())
    lines.append(f"flags: --align-offset-ms {base} --align-offset-by-initial {flag}")
    edged = [name for name, off in [("plain", base), *table.items()] if on_grid_edge(off)]
    if edged:
        lines.append(
            f"WARNING: {', '.join(edged)} sit on the grid edge — the optimum may lie beyond it"
        )
    cv = held_out_by_initial(songs, tolerance_ms)
    if cv:
        lines += [
            "",
            f"held-out (leave-one-song-out) at PCO@{tolerance_ms / 1000:g}:",
            f"  no correction : {cv['none']:.4f}",
            f"  single offset : {cv['single']:.4f}   ({cv['single'] - cv['none']:+.4f})",
            f"  by initial    : {cv['by_initial']:.4f}   ({cv['by_initial'] - cv['none']:+.4f})",
        ]
    return "\n".join(lines)


def format_report(report: dict, tolerance_ms: int = 100) -> str:
    errors_by_song = signed_errors_by_song(report)
    if not errors_by_song:
        return "no word_detail in this run — re-run the benchmark with --dump-words"

    stats = lateness_stats(errors_by_song)
    meta = report.get("meta", {})
    lines = [
        f"run   : {meta.get('label')}  ({meta.get('alignment_model')})",
        f"chain : separation={meta.get('separation')} windowed={meta.get('windowed')} "
        f"jitter={meta.get('anchor_jitter_ms')} exact_stride={meta.get('exact_stride', False)} "
        f"lead={meta.get('windowed_lead_ms', 0)}",
        f"scope : {stats['songs']} songs, {stats['words']} verified words",
        "",
        f"late share      : {stats['late_share']:.1%}   (0.5 = no bias)",
        f"median signed   : {stats['median_signed_ms']:+.0f} ms",
        f"per-song median : {stats['song_median_ms']:+.0f} ms  "
        f"({stats['songs_late']}/{stats['songs']} songs late)",
        "",
        "offset      " + "  ".join(f"PCO@{t / 1000:g}" for t in DEFAULT_TOLERANCES_MS),
    ]
    fitted = best_offset(errors_by_song, tolerance_ms)
    for row in sweep(errors_by_song):
        mark = " <- best" if row.offset_ms == fitted else ""
        cells = "  ".join(f"{row.pco[f'{t / 1000:g}']:8.4f}" for t in DEFAULT_TOLERANCES_MS)
        lines.append(f"{row.offset_ms:+5d} ms   {cells}{mark}")
    if on_grid_edge(fitted):
        lines.append(f"WARNING: the best offset ({fitted:+d} ms) is the grid edge")

    cv = held_out_gain(errors_by_song, tolerance_ms)
    if cv:
        lines += [
            "",
            f"held-out (leave-one-song-out) at PCO@{tolerance_ms / 1000:g}:",
            f"  no correction : {cv['baseline']:.4f}",
            f"  corrected     : {cv['held_out']:.4f}   ({cv['gain']:+.4f})",
            f"  offsets chosen: {cv['offset_min_ms']:+d} .. {cv['offset_max_ms']:+d} ms",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, help="a --dump-words result JSON")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.1,
        help="seconds; which PCO the offset is chosen for (default 0.1 — the "
        "perceptual target Faz 9 exists for, not the 0.3 literature number)",
    )
    parser.add_argument(
        "--by-initial",
        action="store_true",
        help="fit one offset per first-sound class and compare it held-out "
        "against the single offset (run the benchmark with the correction OFF)",
    )
    args = parser.parse_args()
    report = json.loads(args.result.read_text())
    tolerance_ms = round(args.tolerance * 1000)
    print((format_by_initial if args.by_initial else format_report)(report, tolerance_ms))


if __name__ == "__main__":
    main()
