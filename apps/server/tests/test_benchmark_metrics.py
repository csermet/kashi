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
    assert report.stats is not None
    assert report.stats.mae_ms == 0.0


def test_line_report_without_correction_keeps_absolute_errors():
    hyp = [(1000, "a"), (6000, "b"), (11000, "c")]
    ref = [(0, "a"), (5000, "b"), (10000, "c")]
    report = line_start_report(hyp, ref, threshold_ms=500, median_correction=False)
    assert report.offset_ms == 0.0
    assert report.failures == 3
    assert report.stats is not None
    assert report.stats.mae_ms == pytest.approx(1000.0)


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


def test_end_collapse_rate_counts_only_early_ends():
    from benchmarks.metrics import end_collapse_rate

    assert end_collapse_rate([-300.0, 400.0, -100.0, -900.0]) == 0.5
    assert end_collapse_rate([-250.0]) == 0.0  # threshold is exclusive
    assert end_collapse_rate([-251.0]) == 1.0
    assert end_collapse_rate([]) == 0.0


def test_duration_hygiene_zero_short_and_bunched_runs():
    from benchmarks.metrics import duration_hygiene

    starts = [0, 0, 30, 60, 500, 1000, 1059, 1061]
    ends = [0, 30, 60, 160, 559, 1060, 1100, 1200]
    h = duration_hygiene(starts, ends)
    assert h["words"] == 8
    assert h["zero_len_frac"] == round(1 / 8, 4)            # only the first word
    # durations 0, 30, 30, 100, 59, 60, 41, 139 -> five under 60 ms (60 is not)
    assert h["short_frac"] == 0.625
    # 0/0/30/60 all start within 60 ms of the first: one run of 4; the
    # 1000/1059 pair is only two words (1061 is 61 ms after 1000)
    assert h["bunched_runs"] == 1
    assert duration_hygiene([], [])["words"] == 0


def test_bunched_run_needs_three_words_within_60_ms_inclusive():
    from benchmarks.metrics import duration_hygiene

    assert duration_hygiene([0, 30, 60], [100, 130, 160])["bunched_runs"] == 1
    assert duration_hygiene([0, 30, 61], [100, 130, 161])["bunched_runs"] == 0


# --- line starts and dwell (review step B.3), hand-made songs -----------------


def _line(line: int, truth: list[int], hyp: list[int | None], verified=None):
    from benchmarks.metrics import WordRow

    verified = verified or [True] * len(truth)
    return [WordRow(line, t, h, v) for t, h, v in zip(truth, hyp, verified, strict=True)]


def test_a_line_start_is_late_past_220_and_early_past_minus_330_ms():
    from benchmarks.metrics import fluency

    rows = (
        _line(0, [1000, 1500], [1220, 1700])  # +220: on time
        + _line(1, [3000, 3500], [3221, 3700])  # +221: late
        + _line(2, [5000, 5500], [4670, 5600])  # -330: on time
        + _line(3, [7000, 7500], [6669, 7600])  # -331: early
    )
    f = fluency(rows)
    assert (f.lines, f.line_late, f.line_early) == (4, 1, 1)


def test_only_the_first_word_judges_its_line_and_an_unverified_one_skips_it():
    from benchmarks.metrics import fluency

    rows = (
        _line(0, [1000, 1500], [1000, 9000])  # a late SECOND word does not make the line late
        + _line(1, [3000, 3500], [9000, 3500], verified=[False, True])  # skipped, not 2nd word
    )
    f = fluency(rows)
    assert (f.lines, f.line_late, f.line_early) == (1, 0, 0)


def test_a_wordless_line_is_judged_at_its_line_start_when_one_is_given():
    from benchmarks.metrics import fluency

    rows = _line(0, [1000, 1500], [1000, 1400]) + _line(1, [3000, 3500], [None, None])
    assert fluency(rows).lines == 1  # nothing to read the line at
    f = fluency(rows, {1: 3400})
    assert (f.lines, f.line_late, f.untimed) == (2, 1, 2)
    assert fluency(rows, {1: 3200}).line_late == 0


def test_dwell_is_next_start_minus_own_start_inside_a_line_under_120_ms():
    from benchmarks.metrics import fluency

    rows = (
        _line(0, [0, 200, 400, 600], [0, 119, 239, 600])  # 119 short, 120 not, 361 not
        + _line(1, [700, 800], [650, 700])  # the cross-line pair 600->650 is not a dwell
    )
    f = fluency(rows)
    assert f.pairs == 4
    assert f.hyp_short == 2  # 0->119 and 650->700
    assert f.truth_short == 1  # 700->800 is 100 ms in the singing too
    assert f.both_short == 1  # so only 0->119 is rushed by the pipeline alone


def test_dwell_pairs_need_both_words_verified_and_timed():
    from benchmarks.metrics import fluency

    rows = (
        _line(0, [0, 50, 100], [0, 10, 20], verified=[True, False, True])
        + _line(1, [1000, 1050, 1100], [None, None, None])
        + _line(2, [2000, 2050], [2000, 2010])
    )
    f = fluency(rows)
    assert (f.pairs, f.hyp_short, f.truth_short, f.both_short) == (1, 1, 1, 1)


def test_gross_errors_are_beyond_500_ms_among_timed_verified_words():
    from benchmarks.metrics import fluency

    rows = _line(0, [1000, 2000, 3000, 4000], [1500, 2501, None, 3000],
                 verified=[True, True, True, False])
    f = fluency(rows)
    assert (f.words, f.gross, f.untimed) == (2, 1, 1)


def test_the_summary_gives_per_song_means_beside_pooled_shares():
    from benchmarks.metrics import Fluency, fluency_summary

    small = Fluency(lines=2, line_late=1, line_early=0, pairs=4, hyp_short=2, truth_short=1,
                    both_short=1, words=10, gross=1, untimed=0)
    big = Fluency(lines=18, line_late=0, line_early=1, pairs=36, hyp_short=2, truth_short=4,
                  both_short=0, words=90, gross=0, untimed=3)
    s = fluency_summary([small, big])
    assert s["line_starts"] == {
        "noticeable_per_song": round((1 / 2 + 1 / 18) / 2, 4), "noticeable_pooled": 0.1,
        "late_pooled": 0.05, "early_pooled": 0.05, "late": 1, "early": 1, "lines": 20,
    }
    assert s["dwell"] == {
        "pipeline_short_pooled": 0.1, "truth_short_pooled": 0.125, "pipeline_only_pooled": 0.075,
        "real_of_pipeline_short": 0.25, "pipeline_short_per_song": round((0.5 + 2 / 36) / 2, 4),
        "truth_short_per_song": round((0.25 + 4 / 36) / 2, 4), "pairs": 40,
    }
    assert s["gross"] == {"per_song": 0.05, "pooled": 0.01, "words": 1, "of": 100,
                          "untimed_words": 3}
    assert fluency_summary([])["line_starts"]["noticeable_per_song"] is None
