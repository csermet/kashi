"""The regroup rules are where word-level alignment lives or dies.
Pure function, fake segments — no torch needed."""

import pytest

from kashi_server.pipeline.alignment import (
    STAR_TOKEN,
    quality_from_probs,
    regroup_words_into_lines,
)


def _seg(text, start, end, score=-0.1):
    return {"text": text, "start": start, "end": end, "score": score}


def test_two_lines_regrouped_with_ms_integers():
    lines = ["hello world", "second line here"]
    results = [
        _seg("hello", 0.0, 0.5),
        _seg("world", 0.5, 1.0),
        _seg("second", 2.0, 2.4),
        _seg("line", 2.4, 2.8),
        _seg("here", 2.8, 3.2),
    ]
    timings, words = regroup_words_into_lines(lines, results)
    assert [t.text for t in timings] == lines
    assert (timings[0].start_ms, timings[0].end_ms) == (0, 1000)
    assert (timings[1].start_ms, timings[1].end_ms) == (2000, 3200)
    assert [len(w) for w in words] == [2, 3]
    assert all(isinstance(w.start_ms, int) for chunk in words for w in chunk)


def test_star_tokens_are_dropped_not_counted():
    lines = ["one two"]
    results = [_seg(STAR_TOKEN, 0.0, 0.1), _seg("one", 0.1, 0.4), _seg("two", 0.4, 0.9)]
    timings, words = regroup_words_into_lines(lines, results)
    assert len(words[0]) == 2 and timings[0].start_ms == 100


def test_overlapping_words_are_clipped_monotone():
    lines = ["a b"]
    results = [_seg("a", 0.0, 0.60), _seg("b", 0.50, 1.0)]
    _, words = regroup_words_into_lines(lines, results)
    assert words[0][0].end_ms == 500  # clipped to the next word's start
    assert words[0][1].start_ms == 500


def test_zero_length_word_never_goes_negative():
    lines = ["a b"]
    results = [_seg("a", 0.9, 0.5), _seg("b", 0.9, 1.2)]
    _, words = regroup_words_into_lines(lines, results)
    assert words[0][0].end_ms >= words[0][0].start_ms


def test_token_count_mismatch_returns_none():
    """The caller degrades to line mode instead of emitting bogus timings."""
    assert regroup_words_into_lines(["three words here"], [_seg("three", 0, 1)]) is None
    assert regroup_words_into_lines(["one"], [_seg("one", 0, 1), _seg("extra", 1, 2)]) is None


def test_scores_become_probabilities():
    timings, words = regroup_words_into_lines(
        ["x y"], [_seg("x", 0, 1, 0.0), _seg("y", 1, 2, -5.0)]
    )
    assert words[0][0].prob == 1.0  # exp(0) clamped to 1
    assert 0 < words[0][1].prob < 0.01  # exp(-5)
    expected = quality_from_probs([words[0][0].prob, words[0][1].prob])
    assert timings[0].score == expected


def test_quality_mapping_matches_calibration_anchors():
    """Measured 2026-07-10: correct lyrics mean 0.078, wrong lyrics mean 0.029,
    clean speech 0.32. The 0.5 client gate must separate the first two."""
    correct_song = quality_from_probs([0.078])
    wrong_lyrics = quality_from_probs([0.029])
    clean_speech = quality_from_probs([0.32])
    assert correct_song > 0.5, correct_song  # ~0.68
    assert wrong_lyrics < 0.5, wrong_lyrics  # ~0.18
    assert clean_speech == 1.0
    assert quality_from_probs([]) == 0.0
    assert quality_from_probs([0.0]) == 0.0
    assert quality_from_probs([1.0]) == 1.0
    # Monotone in the mean.
    assert quality_from_probs([0.05]) < quality_from_probs([0.10]) < quality_from_probs([0.14])
    assert correct_song == pytest.approx(0.677, abs=0.01)
    assert wrong_lyrics == pytest.approx(0.185, abs=0.01)


def test_line_end_never_precedes_its_start():
    timings, _ = regroup_words_into_lines(["solo"], [_seg("solo", 1.5, 1.5)])
    assert timings[0].end_ms == timings[0].start_ms == 1500
