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


def test_resolve_model_name_prefers_the_explicit_override():
    """The seam's contract: an explicit argument wins, otherwise config, and
    config defaults to today's model so nothing changes by accident."""
    from kashi_server.config import settings
    from kashi_server.pipeline.alignment import MODEL_NAME, resolve_model_name

    assert resolve_model_name("Qwen/Qwen3-ForcedAligner-0.6B") == "Qwen/Qwen3-ForcedAligner-0.6B"
    assert resolve_model_name() == settings.align_model
    assert settings.align_model == MODEL_NAME  # default is a no-op swap
    assert resolve_model_name(None) == MODEL_NAME
    assert resolve_model_name("") == MODEL_NAME  # empty is not a selection


def _segments(texts, step_ms=300):
    """Aligner-shaped output: one segment per token, seconds, monotone."""
    return [
        {
            "start": i * step_ms / 1000,
            "end": (i * step_ms + step_ms - 50) / 1000,
            "text": t,
            "score": -1.0,
        }
        for i, t in enumerate(texts)
    ]


def test_japanese_line_is_counted_in_morae_and_displayed_in_kanji():
    """Faz 8 P-B3 end to end through the pure path. The aligner is given morae
    (because uroman reads kanji as Chinese and would hand the model pinyin),
    but the document must still display what was written."""
    from kashi_server.pipeline.alignment import regroup_words_into_lines
    from kashi_server.pipeline.japanese import prepare_line

    line = "宇宙を駆ける"
    plan = prepare_line(line)
    assert plan is not None
    # 7 morae -> 7 segments; the whitespace path would have expected 1 and
    # bailed out to line mode, which is exactly today's Japanese failure.
    assert len(line.split()) == 1 and len(plan.units) == 7

    regrouped = regroup_words_into_lines([line], _segments(plan.units), [plan])
    assert regrouped is not None
    lines, words_per_line = regrouped
    words = words_per_line[0]

    # Displayed as written, timed as sung.
    assert [w.text for w in words] == ["宇宙", "を", "駆ける"]
    assert words[0].start_ms == 0  # 宇宙 starts at its first mora
    assert words[0].end_ms == 850  # …and ends at its third (う ちゅ ー)
    assert words[1].start_ms == 900  # を picks up at mora 4
    assert words[2].start_ms == 1200  # 駆ける at mora 5
    assert lines[0].text == line
    starts = [w.start_ms for w in words]
    assert starts == sorted(starts)


def test_a_surface_is_only_as_trustworthy_as_its_weakest_mora():
    """Averaging would let one confident kana hide a lost one, and the whole
    point of the score is to notice damage."""
    from kashi_server.pipeline.alignment import AlignedWord, _fold_units_onto_surfaces
    from kashi_server.pipeline.japanese import PreparedLine

    plan = PreparedLine(surfaces=["宇宙"], units=["う", "ちゅ", "ー"], units_per_surface=[3])
    chunk = [
        AlignedWord(0, 300, "う", 0.9),
        AlignedWord(300, 600, "ちゅ", 0.02),  # the aligner lost this one
        AlignedWord(600, 900, "ー", 0.9),
    ]
    folded = _fold_units_onto_surfaces(chunk, plan)
    assert len(folded) == 1
    assert folded[0].prob == 0.02  # weakest, not the 0.607 mean
    assert (folded[0].start_ms, folded[0].end_ms) == (0, 900)


def test_mixed_language_document_counts_each_line_its_own_way():
    """A Japanese line and an English line in one document: morae for one,
    whitespace for the other, and the totals still have to line up."""
    from kashi_server.pipeline.alignment import regroup_words_into_lines
    from kashi_server.pipeline.japanese import prepare_line

    ja, en = "紅蓮華", "burning bright"
    plans = [prepare_line(ja), prepare_line(en)]
    assert plans[0] is not None and plans[1] is None  # English is left alone

    tokens = plans[0].units + en.split()
    regrouped = regroup_words_into_lines([ja, en], _segments(tokens), plans)
    assert regrouped is not None
    _, words_per_line = regrouped
    # UniDic splits 紅蓮華 into 紅蓮 + 華 — finer than a whole-line unit and
    # exactly the granularity a karaoke sweep wants. What must hold is that
    # the surfaces reassemble the line with nothing invented or lost.
    assert "".join(w.text for w in words_per_line[0]) == ja
    assert [w.text for w in words_per_line[1]] == ["burning", "bright"]


def test_plans_are_optional_and_the_default_path_is_untouched():
    from kashi_server.pipeline.alignment import regroup_words_into_lines

    texts = ["hello world", "again"]
    segs = _segments(["hello", "world", "again"])
    assert regroup_words_into_lines(texts, segs) == regroup_words_into_lines(
        texts, segs, [None, None]
    )
