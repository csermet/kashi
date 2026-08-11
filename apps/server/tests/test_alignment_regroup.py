"""The regroup rules are where word-level alignment lives or dies.
Pure function, fake segments — no torch needed."""

import pytest

from kashi_server.pipeline.alignment import (
    STAR_TOKEN,
    _fit_to_vocab,
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


def test_japanese_line_is_counted_in_characters_and_displayed_in_kanji():
    """Faz 8 P-B3 end to end through the pure path. The aligner is given the
    kana reading (uroman reads kanji as Chinese and would hand the model
    pinyin), but the document must still display what was written."""
    from kashi_server.pipeline.alignment import regroup_words_into_lines
    from kashi_server.pipeline.japanese import prepare_line

    line = "宇宙を駆ける"
    plan = prepare_line(line)
    assert plan is not None
    # 8 characters -> 8 segments. The whitespace path expected 1 and bailed to
    # line mode, which is exactly today's Japanese failure.
    assert len(line.split()) == 1 and len(plan.units) == 8

    regrouped = regroup_words_into_lines([line], _segments(plan.units), [plan])
    assert regrouped is not None
    lines, words_per_line = regrouped
    words = words_per_line[0]

    # Displayed as written, timed as sung.
    assert [w.text for w in words] == ["宇宙", "を", "駆ける"]
    assert words[0].start_ms == 0  # 宇宙 starts at う
    assert words[0].end_ms == 1150  # …and ends at ー, its fourth character
    assert words[1].start_ms == 1200  # を picks up at character 5
    assert words[2].start_ms == 1500  # 駆ける at character 6
    assert lines[0].text == line
    starts = [w.start_ms for w in words]
    assert starts == sorted(starts)


def test_a_surface_is_only_as_trustworthy_as_its_weakest_mora():
    """Averaging would let one confident kana hide a lost one, and the whole
    point of the score is to notice damage."""
    from kashi_server.pipeline.alignment import AlignedWord, _fold_units_onto_surfaces
    from kashi_server.pipeline.japanese import PreparedLine

    plan = PreparedLine(surfaces=["宇宙"], units=list("うちゅー"), units_per_surface=[4])
    chunk = [
        AlignedWord(0, 300, "う", 0.9),
        AlignedWord(300, 600, "ち", 0.02),  # the aligner lost this one
        AlignedWord(600, 750, "ゅ", 0.9),
        AlignedWord(750, 900, "ー", 0.9),
    ]
    folded = _fold_units_onto_surfaces(chunk, plan)
    assert len(folded) == 1
    assert folded[0].prob == 0.02  # weakest, not the 0.607 mean
    assert (folded[0].start_ms, folded[0].end_ms) == (0, 900)


def test_english_line_inside_a_japanese_job_is_also_split_per_character():
    """The guess that was wrong, pinned. A Japanese job splits EVERYTHING per
    character — measured: language="jpn", "hello world" -> ['h','e','l',…].
    So the English line in a J-pop document takes the character path too, and
    routing per line would have desynchronised exactly these documents."""
    from kashi_server.pipeline.alignment import regroup_words_into_lines
    from kashi_server.pipeline.japanese import prepare_line

    ja, en = "紅蓮華", "burning bright"
    plans = [prepare_line(ja), prepare_line(en)]
    assert plans[0] is not None and plans[1] is not None
    assert plans[1].units == list("burningbright")  # per character, no space
    assert plans[1].surfaces == ["burning", "bright"]  # …still displayed whole

    tokens = plans[0].units + plans[1].units
    regrouped = regroup_words_into_lines([ja, en], _segments(tokens), plans)
    assert regrouped is not None
    _, words_per_line = regrouped
    # UniDic splits 紅蓮華 into 紅蓮 + 華 — finer than a whole-line unit and
    # exactly the granularity a karaoke sweep wants. What must hold is that
    # the surfaces reassemble the line with nothing invented or lost.
    assert "".join(w.text for w in words_per_line[0]) == ja
    assert [w.text for w in words_per_line[1]] == ["burning", "bright"]


def test_blank_segments_are_dropped_like_stars():
    """A Japanese job splits per character, so the space that " ".join puts
    between lines becomes a segment of its own with an empty romanization.
    An English job never produces one — measured on the worker — so filtering
    them is a no-op outside Japanese and load-bearing inside it."""
    from kashi_server.pipeline.alignment import regroup_words_into_lines

    texts = ["hello world"]
    with_blanks = _segments(["hello", " ", "world"])
    assert regroup_words_into_lines(texts, with_blanks) is not None


def test_plans_are_optional_and_the_default_path_is_untouched():
    from kashi_server.pipeline.alignment import regroup_words_into_lines

    texts = ["hello world", "again"]
    segs = _segments(["hello", "world", "again"])
    assert regroup_words_into_lines(texts, segs) == regroup_words_into_lines(
        texts, segs, [None, None]
    )


def test_vocab_fitting_keeps_the_letters_the_model_learned():
    """romanize=False yolunda temizlik kimse yapmıyordu: uroman devrede
    olmadığı için noktalama modelin sözlüğüne çarpıyor ve hizalayıcı assert'e
    düşüyordu — ilk Türkçe ölçümünde 10 şarkının 5'i böyle kayboldu."""
    turkish = set("abcdefghijklmnopqrstuvwxyzçğışöü' ")
    # Türkçe harfler modelin ÖĞRENDİĞİ harfler — sadeleştirilirse model
    # hiç görmediği bir ses kümesiyle çalışmaya zorlanır.
    assert _fit_to_vocab("Bi' açıldım, bi' kapandım", turkish) == "Bi' açıldım bi' kapandım"
    # Sözlükte olmayan aksan gerçek bir kelimenin parçası: düşürülür, atılmaz.
    assert _fit_to_vocab("hâlim hikâye,", turkish) == "halim hikaye"


def test_vocab_fitting_never_changes_the_token_count():
    """Regroup, satır metnindeki kelime sayısının hizalanan segment sayısına
    EŞİT olmasına dayanıyor. Tamamen elenen bir token (♪ gibi) yer tutmazsa o
    özdeşlik kırılır ve şarkı satır moduna düşer."""
    turkish = set("abcdefghijklmnopqrstuvwxyzçğışöü' ")
    for text in ["Şampiyon (Ooh) ♪", "♪ ♪ ♪", "a, b! c?"]:
        assert len(_fit_to_vocab(text, turkish).split()) == len(text.split())


def test_an_unknown_vocabulary_leaves_the_text_alone():
    """Sözlük okunamazsa (tokenizer türü değişti) metne dokunma — sessizce
    bozmaktansa eski davranışta kal."""
    assert _fit_to_vocab("a,b ♪", set()) == "a,b ♪"


def test_the_non_romanized_path_actually_applies_the_fitting():
    """Saf fonksiyonu sınamak yetmiyor: çağrı yeri silinirse hiçbir test
    düşmez ve ölçüm yine yarım şarkıyla döner. Gerçek yol `ctc_forced_aligner`
    olmadan koşamadığı için kapı kaynak düzeyinde — bu koşulun kaybolması
    sessiz kalmasın."""
    from pathlib import Path

    from kashi_server.pipeline import alignment

    source = Path(alignment.__file__).read_text(encoding="utf-8")
    assert "if not romanize:" in source
    assert "_fit_to_vocab(joined, _vocab_chars(tokenizer))" in source
    # ...ve romanize=True yolunda ASLA çalışmamalı: uroman zaten temizliyor,
    # üstüne bir de sözlüğe indirmek MMS'in beklediği metni bozardı.
    fitted = source.index("_fit_to_vocab(joined")
    guard = source.rindex("if not romanize:", 0, fitted)
    assert fitted - guard < 120, "sözlüğe indirgeme romanize koşulundan koptu"
