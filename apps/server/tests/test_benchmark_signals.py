"""Candidate confidence signals and the correlation that judges them.

The correlation maths is tested harder than the signals themselves on
purpose: a wrong Spearman would not fail loudly, it would quietly recommend
the wrong signal — which is exactly the class of mistake this whole hunt
exists to undo.
"""

import pytest

from benchmarks.correlate import _pearson, _ranks, _spearman
from benchmarks.signals import plausibility_signals, probability_signals
from kashi_server.pipeline.alignment import AlignedWord


def _words(spans):
    return [AlignedWord(s, e, t, 0.5) for s, e, t in spans]


def test_spearman_is_exact_on_known_cases():
    perfect = [1, 2, 3, 4, 5]
    assert _spearman(perfect, [10, 20, 30, 40, 50]) == pytest.approx(1.0)
    assert _spearman(perfect, [50, 40, 30, 20, 10]) == pytest.approx(-1.0)
    # Monotone but wildly non-linear: Spearman sees through the scaling,
    # Pearson does not. That is the entire reason a gate is ranked, not scaled.
    curved = [1, 4, 9, 100, 10_000]
    assert _spearman(perfect, curved) == pytest.approx(1.0)
    assert _pearson(perfect, curved) < 0.8


def test_ties_get_shared_ranks_rather_than_an_invented_order():
    assert _ranks([5, 5, 5]) == [1.0, 1.0, 1.0]
    assert _ranks([1, 2, 2, 3]) == [0.0, 1.5, 1.5, 3.0]
    # A constant signal cannot rank anything, and must not fake a correlation.
    assert _spearman([1, 1, 1, 1], [4, 3, 2, 1]) == 0.0


def test_correlation_is_defined_on_degenerate_input():
    assert _pearson([], []) == 0.0
    assert _pearson([1.0], [2.0]) == 0.0  # too few points to mean anything


def test_probability_tail_separates_documents_the_mean_cannot():
    """The premise of looking at the tail at all: two documents with the SAME
    mean, one uniformly mediocre and one half-perfect half-lost. Today's ramp
    scores them identically; the tail does not."""
    uniform = probability_signals([0.3] * 10)
    split = probability_signals([0.6] * 5 + [0.0] * 5)
    assert uniform["prob_mean"] == pytest.approx(split["prob_mean"], abs=0.01)
    assert uniform["prob_frac_below_01"] == 0.0
    assert split["prob_frac_below_01"] == 0.5  # …and the tail says so
    assert split["prob_p10"] < uniform["prob_p10"]


def test_overlap_and_silence_are_measured_not_guessed():
    clean = plausibility_signals(_words([(0, 400, "aa"), (500, 900, "bb")]), 1000)
    assert clean["overlap_frac"] == 0.0
    overlapping = plausibility_signals(_words([(0, 600, "aa"), (300, 900, "bb")]), 1000)
    assert overlapping["overlap_frac"] == 1.0
    # A document covering a sliver of a long track left most of it silent.
    sparse = plausibility_signals(_words([(0, 200, "aa")]), 10_000)
    assert sparse["silence_frac"] == pytest.approx(0.98, abs=0.01)


def test_duration_outliers_are_relative_to_the_songs_own_pace():
    """An absolute threshold would flag every ballad. The comparison is
    against the document's own median per-character speed."""
    steady = [(i * 500, i * 500 + 400, "ab") for i in range(10)]
    assert plausibility_signals(_words(steady), 5000)["duration_outlier_frac"] == 0.0
    stretched = steady + [(5000, 12_000, "ab")]  # one word smeared over a gap
    assert plausibility_signals(_words(stretched), 12_000)["duration_outlier_frac"] > 0.0


def test_empty_input_reports_worst_case_rather_than_crashing():
    """A song that produced nothing is a data point, not an exception — the
    sweep has to keep going."""
    assert probability_signals([])["prob_frac_below_01"] == 1.0
    empty = plausibility_signals([], 1000)
    assert empty["overlap_frac"] == 1.0 and empty["silence_frac"] == 1.0
