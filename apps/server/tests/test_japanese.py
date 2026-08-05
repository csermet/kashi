"""Japanese lyrics -> their kana reading (Faz 8 P-B3).

Every expectation here was MEASURED against the shipped aligner in the worker
pod on 2026-08-05, not reasoned about. Two guesses turned out wrong and both
are pinned below: the split granularity is decided by the `language` argument
rather than by the text, and the unit the aligner emits is the CHARACTER, not
the mora.
"""

from kashi_server.pipeline.japanese import (
    handles,
    katakana_to_hiragana,
    looks_japanese,
    prepare_line,
    to_alignment_units,
)


def test_kanji_gets_its_japanese_reading_not_pinyin():
    """The damage this closes. Measured on the worker, 空に光る romanized to
    ['k o n g', 'n i', 'g u a n g', 'r u'] — Mandarin. The model was being
    asked to find Chinese in audio sung in Japanese."""
    units = to_alignment_units("空に光る星")
    assert units is not None
    joined = "".join(units)
    assert joined.startswith("そら")  # sora — not kong
    assert "ひかる" in joined
    assert "ほし" in joined


def test_the_unit_is_the_character_the_aligner_emits():
    """Measured: a Japanese job emits one segment per character, so a unit is
    one character. Morae are the right unit for a human reading kana — きゃ is
    one mora — but pinning that here would desynchronise every count."""
    assert to_alignment_units("ギミチョコ") == ["ぎ", "み", "ち", "ょ", "こ"]
    # がっこー, not がっこう: `pron` is the SPOKEN form and writes the long
    # vowel as ー, which is what is actually sung. That is why it wins over
    # the citation reading `kana`.
    assert to_alignment_units("学校") == ["が", "っ", "こ", "ー"]
    prepared = prepare_line("宇宙")
    assert prepared is not None
    assert prepared.units == ["う", "ち", "ゅ", "ー"]
    assert prepared.align_text == "うちゅー"  # no separators: each char is a segment


def test_language_decides_the_split_not_the_text():
    """The guess that was wrong. Measured:
        language="eng", "hello world" -> ['hello', 'world']
        language="jpn", "hello world" -> ['h','e','l','l','o',' ','w',…]
    So an English line inside a Japanese job is still split per character, and
    routing line by line would desynchronise exactly the mixed documents J-pop
    is full of."""
    assert handles("jpn")
    assert handles("ja")
    assert handles("JPN")
    assert not handles("eng")
    assert not handles("tur")
    assert not handles("")


def test_katakana_folds_to_hiragana():
    assert katakana_to_hiragana("ソラ") == "そら"
    assert katakana_to_hiragana("ギミチョコ") == "ぎみちょこ"
    assert katakana_to_hiragana("ー") == "ー"  # not in the katakana block
    assert katakana_to_hiragana("hello") == "hello"


def test_katakana_surface_is_its_own_reading():
    """UniDic carries no reading field for katakana — the surface already IS
    the reading. Field case: BABYMETAL's "ギミチョコ！！", a line-mode document
    in the archive today."""
    prepared = prepare_line("ギミチョコ")
    assert prepared is not None
    # UniDic reads it as two morphemes — finer than the whole phrase, which is
    # the granularity a karaoke sweep wants. What must hold is that the
    # surfaces reassemble the line with nothing invented or lost.
    assert "".join(prepared.surfaces) == "ギミチョコ"
    assert prepared.align_text == "ぎみちょこ"


def test_punctuation_carries_no_sound():
    assert to_alignment_units("ギミチョコ！！") == ["ぎ", "み", "ち", "ょ", "こ"]
    assert prepare_line("！！！") is None
    assert prepare_line("") is None


def test_latin_words_keep_their_spelling_and_own_their_characters():
    """A Japanese job splits Latin per character too, so `forever` contributes
    seven units while still displaying as one word."""
    prepared = prepare_line("空 forever 光る")
    assert prepared is not None
    index = prepared.surfaces.index("forever")
    assert prepared.units_per_surface[index] == len("forever")
    start = sum(prepared.units_per_surface[:index])
    assert prepared.units[start : start + 7] == list("forever")
    assert " " not in prepared.align_text  # spaces would only add empty segments


def test_surface_and_sound_stay_paired():
    """The trap: the listener READS 宇宙 while the model HEARS うちゅー. Both
    survive, held together by the ownership counts — the aligner times the
    units and those times fold back onto the surfaces that own them."""
    prepared = prepare_line("宇宙を駆ける")
    assert prepared is not None
    assert prepared.surfaces == ["宇宙", "を", "駆ける"]  # as written
    assert prepared.align_text == "うちゅーおかける"  # as sung (を is read お)
    assert prepared.units_per_surface == [4, 1, 3]  # うちゅー / お / かける
    assert sum(prepared.units_per_surface) == len(prepared.units)


def test_ownership_invariant_holds_on_real_archive_lines():
    """A desynchronised mapping produces confident nonsense rather than a
    visible failure, so the invariant is asserted in the type itself and
    pinned here on lines taken from documents that are line-mode today."""
    for line in [
        "ギミチョコ",
        "空に光る星",
        "紅蓮華",
        "空 forever 光る",
        "ヤラララ！！",
        "あたたたたた ずっきゅん！",
        "でもね ちょっと weight ちょっと最近 心配なんです",
    ]:
        prepared = prepare_line(line)
        assert prepared is not None, line
        assert len(prepared.surfaces) == len(prepared.units_per_surface)
        assert sum(prepared.units_per_surface) == len(prepared.units)
        assert len(prepared.align_text) == len(prepared.units)
        assert all(count > 0 for count in prepared.units_per_surface)


def test_output_is_deterministic():
    """Determinism is a contract, not a nicety: the document promises
    byte-identical output for identical input, which is exactly why this is a
    dictionary and not an LLM."""
    line = "宇宙を駆ける"
    first = to_alignment_units(line)
    assert first == to_alignment_units(line)
    assert first is not None and len(first) > 1


def test_script_test_is_cheap_and_correct():
    assert looks_japanese("空")
    assert looks_japanese("ソラ")
    assert looks_japanese("そら")
    assert not looks_japanese("hello world")
    assert not looks_japanese("")
