"""benchmarks.metrics is pure — unit tests only, no fixtures needed."""

import pytest

from benchmarks.metrics import (
    error_stats,
    format_line_report,
    line_start_report,
    match_refs,
    word_start_deviations,
)


def test_match_refs_walks_repeats_positionally():
    refs = match_refs(
        ["la", "verse", "la"],
        [(0, "la"), (1000, "verse"), (2000, "la")],
    )
    assert refs == [0, 1000, 2000]


def test_match_refs_skips_lines_missing_from_reference():
    # "adlib" is not in the reference; later lines must still match.
    refs = match_refs(
        ["verse", "adlib", "chorus"],
        [(0, "verse"), (5000, "chorus")],
    )
    assert refs == [0, None, 5000]


def test_match_refs_handles_stampless_and_extra_reference_lines():
    refs = match_refs(
        ["a", "c"],
        [(0, "a"), (1000, "b"), (None, "c")],
    )
    assert refs == [0, None]


def test_error_stats_known_values():
    stats = error_stats([100.0, -200.0, 300.0, -400.0], tolerances_ms=(300,))
    assert stats is not None
    assert stats.count == 4
    assert stats.mae_ms == pytest.approx(250.0)
    assert stats.medae_ms == pytest.approx(250.0)
    assert stats.p95_ms == 400.0  # nearest-rank: ceil(0.95*4)=4th of sorted
    assert stats.pcs["0.3"] == pytest.approx(0.75)


def test_error_stats_empty_is_none():
    assert error_stats([]) is None


def test_word_start_deviations_positional_pairing():
    devs = word_start_deviations(
        [(100, "a"), (600, "b")],
        [(0, "a"), (500, "b")],
    )
    assert devs == [100.0, 100.0]


def test_word_start_deviations_count_mismatch_is_none():
    assert word_start_deviations([(0, "a")], [(0, "a"), (1, "b")]) is None


def test_line_report_median_correction_absorbs_systematic_offset():
    # Every line is +1s late -> corrected deviations are 0, nothing fails.
    hyp = [(1000, "a"), (6000, "b"), (11000, "c")]
    ref = [(0, "a"), (5000, "b"), (10000, "c")]
    report = line_start_report(hyp, ref, threshold_ms=500)
    assert report.offset_ms == 1000.0
    assert report.failures == 0
    assert report.stats is not None and report.stats.mae_ms == 0.0


def test_line_report_without_correction_keeps_absolute_errors():
    hyp = [(1000, "a"), (6000, "b"), (11000, "c")]
    ref = [(0, "a"), (5000, "b"), (10000, "c")]
    report = line_start_report(hyp, ref, threshold_ms=500, median_correction=False)
    assert report.offset_ms == 0.0
    assert report.failures == 3
    assert report.stats is not None and report.stats.mae_ms == pytest.approx(1000.0)


def test_line_report_window_limits_failures():
    hyp = [(0, "a"), (20_000, "b"), (40_000, "c")]
    ref = [(0, "a"), (10_000, "b"), (30_000, "c")]
    # deviations 0 / +10s / +10s, offset(median)=+10s -> corrected -10/0/0...
    # correction would hide everything, so turn it off and window on [5s,15s].
    report = line_start_report(
        hyp, ref, threshold_ms=2500, window_ms=(5_000, 15_000), median_correction=False
    )
    assert [row.over_threshold for row in report.rows] == [False, True, False]
    assert report.failures == 1


def test_format_line_report_marks_failures():
    hyp = [(0, "a"), (20_000, "b")]
    ref = [(0, "a"), (10_000, "b")]
    report = line_start_report(hyp, ref, threshold_ms=2500, median_correction=False)
    text = format_line_report(report, threshold_ms=2500)
    assert "<<< FAIL" in text
    assert "1 line(s) over 2.5s" in text


def test_over_extension_rate_counts_only_late_ends():
    from benchmarks.metrics import over_extension_rate

    # 300 and 900 hang past the 250 ms threshold; early/exact ends never count.
    assert over_extension_rate([300.0, -400.0, 100.0, 900.0]) == 0.5
    assert over_extension_rate([250.0]) == 0.0  # threshold is exclusive
    assert over_extension_rate([]) == 0.0
