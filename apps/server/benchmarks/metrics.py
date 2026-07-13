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
