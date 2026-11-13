"""Pure metric computations for alignment benchmarks.

No torch, no I/O, no kashi_server imports — everything here runs on plain
lists and is unit-tested (tests/test_benchmark_metrics.py). match_refs walks
by text with a forward cursor like production line_qa, but more forgivingly:
a line missing from the reference yields None for THAT line only, where
line_qa._match_references gives up on the whole document (its full-doc gate
needs that strictness; a benchmark wants every comparable line).

Conventions: times in ms; a "deviation" is hypothesis − reference (signed);
summary statistics are computed over absolute deviations.
"""

import math
from dataclasses import dataclass
from statistics import median


def match_refs(
    hyp_texts: list[str], ref_entries: list[tuple[int | None, str]]
) -> list[int | None]:
    """Reference start per hypothesis line, matched by text with a forward
    cursor: order is preserved, repeated chorus lines resolve positionally,
    and lines missing from either side leave their neighbours untouched."""
    texts = [text for _, text in ref_entries]
    starts = [start for start, _ in ref_entries]
    refs: list[int | None] = []
    cursor = 0
    for text in hyp_texts:
        probe = cursor
        while probe < len(texts) and texts[probe] != text:
            probe += 1
        if probe < len(texts):
            refs.append(starts[probe])
            cursor = probe + 1
        else:  # not found ahead — DON'T burn the cursor for later lines
            refs.append(None)
    return refs


@dataclass(frozen=True)
class ErrorStats:
    count: int
    mae_ms: float
    medae_ms: float
    p95_ms: float
    pcs: dict[str, float]  # tolerance in seconds (str key) -> fraction within


def error_stats(
    deviations_ms: list[float], tolerances_ms: tuple[int, ...] = (300,)
) -> ErrorStats | None:
    """MAE / MedAE / p95 over |deviation|, plus PCS@tolerance (the fraction of
    items within the tolerance — the "percentage of correct onsets" metric the
    lyrics-alignment literature reports at 0.3 s)."""
    if not deviations_ms:
        return None
    magnitudes = sorted(abs(dev) for dev in deviations_ms)
    n = len(magnitudes)
    p95 = magnitudes[max(0, math.ceil(0.95 * n) - 1)]  # nearest-rank
    return ErrorStats(
        count=n,
        mae_ms=sum(magnitudes) / n,
        medae_ms=median(magnitudes),
        p95_ms=p95,
        pcs={
            f"{tol / 1000:g}": sum(m <= tol for m in magnitudes) / n for tol in tolerances_ms
        },
    )


def word_start_deviations(
    hyp_words: list[tuple[int, str]], ref_words: list[tuple[int, str]]
) -> list[float] | None:
    """Signed start deviations, paired positionally. The harness aligns the
    SAME text the ground truth annotates, so differing token counts mean the
    case is broken — return None and let the runner record a skip, never pair
    words that don't correspond. Works for END times too — the pairing only
    cares that the (time, token) lists correspond."""
    if len(hyp_words) != len(ref_words):
        return None
    return [float(h - r) for (h, _), (r, _) in zip(hyp_words, ref_words, strict=True)]


def over_extension_rate(
    signed_end_deviations_ms: list[float], threshold_ms: int = 250
) -> float:
    """Fraction of words whose hypothesis END runs past the reference end by
    more than the threshold — the "hanging word" the Faz 5 ear test
    complains about. Input is SIGNED end deviations (hyp − ref); early ends
    do not count (at these magnitudes they read as crisp, not wrong)."""
    if not signed_end_deviations_ms:
        return 0.0
    n = len(signed_end_deviations_ms)
    return sum(d > threshold_ms for d in signed_end_deviations_ms) / n


def end_collapse_rate(
    signed_end_deviations_ms: list[float], threshold_ms: int = 250
) -> float:
    """Fraction of words whose hypothesis END comes more than the threshold
    BEFORE the reference end: the twin of over_extension_rate. 2.29 collapsed
    word durations (0 ms words 2.0 % -> 7.6 % on the owner's pilot set) while
    over_extension IMPROVED, because it only counts late ends."""
    if not signed_end_deviations_ms:
        return 0.0
    n = len(signed_end_deviations_ms)
    return sum(d < -threshold_ms for d in signed_end_deviations_ms) / n


# Duration hygiene thresholds (the 2026-10-31 field finding's own measures).
SHORT_WORD_MS = 60
BUNCH_SPAN_MS = 60
BUNCH_MIN_WORDS = 3


def duration_hygiene(starts_ms: list[int], ends_ms: list[int]) -> dict:
    """Hypothesis-only health of word durations, no truth needed:
    zero_len_frac (end <= start), short_frac (shorter than SHORT_WORD_MS) and
    bunched_runs (runs of >= BUNCH_MIN_WORDS consecutive words that all start
    within BUNCH_SPAN_MS of the run's first word)."""
    n = len(starts_ms)
    if n == 0:
        return {"words": 0, "zero_len_frac": 0.0, "short_frac": 0.0, "bunched_runs": 0}
    durations = [e - s for s, e in zip(starts_ms, ends_ms, strict=True)]
    runs = 0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and starts_ms[j + 1] - starts_ms[i] <= BUNCH_SPAN_MS:
            j += 1
        if j - i + 1 >= BUNCH_MIN_WORDS:
            runs += 1
        i = j + 1
    return {
        "words": n,
        "zero_len_frac": round(sum(d <= 0 for d in durations) / n, 4),
        "short_frac": round(sum(d < SHORT_WORD_MS for d in durations) / n, 4),
        "bunched_runs": runs,
    }


@dataclass(frozen=True)
class LineRow:
    index: int
    hyp_ms: int
    ref_ms: int | None
    dev_ms: float | None  # hyp - ref (signed)
    corrected_ms: float | None  # dev - median offset (None when uncorrected)
    in_window: bool
    over_threshold: bool
    text: str


@dataclass(frozen=True)
class LineReport:
    rows: list[LineRow]
    offset_ms: float  # 0.0 when median_correction is off
    failures: int  # rows in window over the threshold
    stats: ErrorStats | None  # over corrected deviations of referenced rows


def line_start_report(
    hyp_lines: list[tuple[int, str]],
    ref_entries: list[tuple[int | None, str]],
    *,
    threshold_ms: float = 2500,
    window_ms: tuple[float, float] | None = None,
    median_correction: bool = True,
) -> LineReport:
    """Per-line start deviation against a reference.

    median_correction compensates a systematic clock offset and belongs to
    CROSS-SOURCE references (lrclib stamps vs our download — this is what
    line_qa does in production). Against same-audio ground truth (Jamendo)
    it must be off: an aligner that is globally shifted IS wrong.
    """
    refs = match_refs([text for _, text in hyp_lines], ref_entries)
    deviations = [
        hyp - ref for (hyp, _), ref in zip(hyp_lines, refs, strict=True) if ref is not None
    ]
    offset = median(deviations) if deviations and median_correction else 0.0

    rows: list[LineRow] = []
    corrected_all: list[float] = []
    failures = 0
    for index, ((hyp_ms, text), ref) in enumerate(zip(hyp_lines, refs, strict=True)):
        dev = float(hyp_ms - ref) if ref is not None else None
        corrected = dev - offset if dev is not None else None
        in_window = window_ms is None or (
            ref is not None and window_ms[0] <= ref <= window_ms[1]
        )
        over = corrected is not None and in_window and abs(corrected) > threshold_ms
        failures += over
        if corrected is not None:
            corrected_all.append(corrected)
        rows.append(
            LineRow(
                index=index,
                hyp_ms=hyp_ms,
                ref_ms=ref,
                dev_ms=dev,
                corrected_ms=corrected,
                in_window=in_window,
                over_threshold=over,
                text=text,
            )
        )
    return LineReport(
        rows=rows,
        offset_ms=float(offset),
        failures=failures,
        stats=error_stats(corrected_all),
    )


# --- what the screen shows: line starts and dwell (review step B.3) -----------
#
# Defined by the 2026-11-13 GPU bench pass BEFORE its runs were read
# (~/Documents/krediler/kashi-gozden-gecirme/bench-gpu/notes.md, extra_metrics.py)
# and used for every human-truth decision since (the 2.32.0 vote): the owner's
# priority is that a line comes in on its voice and that words do not rush past.

LINE_LATE_MS = 220  # a line start later than this against the human start is noticeable
LINE_EARLY_MS = -330  # ... and earlier than this (Faz 5 plan note, acceptance layer 1)
DWELL_SHORT_MS = 120  # a word lit for less than this reads as rushed
GROSS_MS = 500  # a word start off by more than this is a gross error


@dataclass(frozen=True)
class WordRow:
    """One annotated word in annotation order: its line (the dataset's), its
    human start, the pipeline's start (None when the word got no time: line QA
    left its line wordless) and whether a human checked it."""

    line: int
    truth_ms: int
    hyp_ms: int | None
    verified: bool = True


@dataclass(frozen=True)
class Fluency:
    lines: int  # lines whose start was judged
    line_late: int
    line_early: int
    pairs: int  # non-final word pairs judged for dwell
    hyp_short: int  # pairs the pipeline shows for under DWELL_SHORT_MS
    truth_short: int  # pairs the singer sings that fast
    both_short: int
    words: int  # verified words with a time
    gross: int  # of those, off by more than GROSS_MS
    untimed: int  # verified words without a time (wordless lines)


def fluency(rows: list[WordRow], wordless_line_starts: dict[int, int] | None = None) -> Fluency:
    """Line starts, dwell and gross errors of one song.

    Line start: each line's FIRST word (lowest annotation index carrying the
    line), hyp − truth; late above LINE_LATE_MS, early below LINE_EARLY_MS. A
    line whose first word is unverified is skipped. A first word without a time
    is read at its line's own start when `wordless_line_starts` gives it (the
    overlay brings a wordless line in at that start), else skipped.

    Dwell: the overlay keeps a word lit until the next word starts, so a
    non-line-final word is on screen for next start − own start. Pairs of
    consecutive words of one line, both verified and both timed; the pipeline's
    and the singer's dwell are judged on the SAME pairs. `hyp_short` and not
    `truth_short` is a word the pipeline alone rushes."""
    starts = wordless_line_starts or {}
    firsts: dict[int, WordRow] = {}
    for row in rows:
        firsts.setdefault(row.line, row)
    late = early = judged = 0
    for line, row in firsts.items():
        if not row.verified:
            continue
        hyp = row.hyp_ms if row.hyp_ms is not None else starts.get(line)
        if hyp is None:
            continue
        judged += 1
        dev = hyp - row.truth_ms
        late += dev > LINE_LATE_MS
        early += dev < LINE_EARLY_MS
    pairs = hyp_short = truth_short = both = 0
    for a, b in zip(rows, rows[1:], strict=False):
        if a.line != b.line or not (a.verified and b.verified):
            continue
        if a.hyp_ms is None or b.hyp_ms is None:
            continue
        pairs += 1
        h = b.hyp_ms - a.hyp_ms < DWELL_SHORT_MS
        t = b.truth_ms - a.truth_ms < DWELL_SHORT_MS
        hyp_short += h
        truth_short += t
        both += h and t
    timed = [r for r in rows if r.verified and r.hyp_ms is not None]
    return Fluency(
        lines=judged, line_late=late, line_early=early, pairs=pairs, hyp_short=hyp_short,
        truth_short=truth_short, both_short=both, words=len(timed),
        gross=sum(abs(r.hyp_ms - r.truth_ms) > GROSS_MS for r in timed if r.hyp_ms is not None),
        untimed=sum(r.verified and r.hyp_ms is None for r in rows),
    )


def fluency_summary(songs: list[Fluency]) -> dict:
    """Run-level numbers: per-song means (the MIREX convention every bench
    aggregate follows) beside pooled shares and the raw counts."""

    def per_song(num: str, den: str) -> float | None:
        values = [getattr(s, num) / getattr(s, den) for s in songs if getattr(s, den)]
        return round(sum(values) / len(values), 4) if values else None

    def pooled(num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None

    lines = sum(s.lines for s in songs)
    late = sum(s.line_late for s in songs)
    early = sum(s.line_early for s in songs)
    pairs = sum(s.pairs for s in songs)
    hyp_short = sum(s.hyp_short for s in songs)
    both = sum(s.both_short for s in songs)
    words = sum(s.words for s in songs)
    gross = sum(s.gross for s in songs)
    noticeable = [(s.line_late + s.line_early) / s.lines for s in songs if s.lines]
    return {
        "line_starts": {
            "noticeable_per_song": (
                round(sum(noticeable) / len(noticeable), 4) if noticeable else None
            ),
            "noticeable_pooled": pooled(late + early, lines),
            "late_pooled": pooled(late, lines),
            "early_pooled": pooled(early, lines),
            "late": late,
            "early": early,
            "lines": lines,
        },
        "dwell": {
            "pipeline_short_pooled": pooled(hyp_short, pairs),
            "truth_short_pooled": pooled(sum(s.truth_short for s in songs), pairs),
            "pipeline_only_pooled": pooled(hyp_short - both, pairs),
            "real_of_pipeline_short": pooled(both, hyp_short),
            "pipeline_short_per_song": per_song("hyp_short", "pairs"),
            "truth_short_per_song": per_song("truth_short", "pairs"),
            "pairs": pairs,
        },
        "gross": {
            "per_song": per_song("gross", "words"),
            "pooled": pooled(gross, words),
            "words": gross,
            "of": words,
            "untimed_words": sum(s.untimed for s in songs),
        },
    }


def format_line_report(report: LineReport, threshold_ms: float) -> str:
    """The compare_lrclib table, as a string (the script prints it)."""
    out = [f"{'ln':>3} {'aligner':>8} {'lrclib':>8} {'dev':>7} {'dev-med':>8}  text"]
    for row in report.rows:
        if row.ref_ms is None or row.dev_ms is None or row.corrected_ms is None:
            continue
        flag = " <<< FAIL" if row.over_threshold else ""
        out.append(
            f"{row.index:>3} {row.hyp_ms / 1000:>8.2f} {row.ref_ms / 1000:>8.2f} "
            f"{row.dev_ms / 1000:>+7.1f} {row.corrected_ms / 1000:>+8.1f}  {row.text[:40]}{flag}"
        )
    out.append(
        f"\nmedian offset {report.offset_ms / 1000:+.2f}s, "
        f"{report.failures} line(s) over {threshold_ms / 1000:g}s"
    )
    return "\n".join(out)
