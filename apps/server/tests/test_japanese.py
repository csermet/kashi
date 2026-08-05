"""Japanese lyrics -> kana morae (Faz 8 P-B3).

The two failures this closes, both measured on the archive 2026-08-05:
whitespace tokenisation cannot hold its count identity on a script without
word delimiters, and uroman reads kanji as Chinese so the aligner was hearing
pinyin. Nine of the ten line-mode documents in the archive are non-Latin.
"""

from kashi_server.pipeline.japanese import (
    katakana_to_hiragana,
    looks_japanese,
    split_morae,
    to_alignment_units,
)


def test_kanji_gets_its_japanese_reading_not_pinyin():
    """The whole point. uroman romanizes 空 as "kong" (Chinese); sung, it is
    "sora". Feeding the aligner the reading is what makes the audio and the
    text describe the same sound."""
    units = to_alignment_units("空に光る星")
    assert units is not None
    joined = "".join(units)
    assert joined.startswith("そら")  # sora — not kong
    assert "ひかる" in joined
    assert "ほし" in joined


def test_morae_are_the_unit_not_characters():
    # きゃ is ONE mora. Splitting it would desynchronise every count after it.
    assert split_morae("きゃく") == ["きゃ", "く"]
    assert split_morae("ちょこ") == ["ちょ", "こ"]
    # …but the long mark and the small tsu stand alone: ラーメン is four.
    assert split_morae("らーめん") == ["ら", "ー", "め", "ん"]
    assert split_morae("がっこう") == ["が", "っ", "こ", "う"]
    # Loanword clusters, which J-pop is full of.
    assert split_morae("ふぁいと") == ["ふぁ", "い", "と"]
    # A small kana with nothing before it keeps its own slot rather than
    # vanishing — a dropped character is a desynchronised document.
    assert split_morae("ゃあ") == ["ゃ", "あ"]
    assert split_morae("") == []


def test_katakana_folds_to_hiragana():
    assert katakana_to_hiragana("ソラ") == "そら"
    assert katakana_to_hiragana("ギミチョコ") == "ぎみちょこ"
    assert katakana_to_hiragana("ー") == "ー"  # not in the katakana block
    assert katakana_to_hiragana("hello") == "hello"


def test_katakana_surface_is_its_own_reading():
    """UniDic carries no reading field for katakana — the surface already IS
    the reading. Field case: BABYMETAL's "ギミチョコ！！", which sits in the
    archive as a line-mode document today."""
    units = to_alignment_units("ギミチョコ")
    assert units == ["ぎ", "み", "ちょ", "こ"]


def test_punctuation_carries_no_sound():
    assert to_alignment_units("ギミチョコ！！") == ["ぎ", "み", "ちょ", "こ"]
    assert to_alignment_units("空、光る") is not None


def test_latin_words_inside_a_japanese_line_pass_through_whole():
    # Very common in J-pop. Latin already survives whitespace tokenisation, so
    # forcing it into morae would be inventing a unit that does not exist.
    units = to_alignment_units("空 forever 光る")
    assert units is not None
    assert "forever" in units


def test_non_japanese_lines_are_left_alone():
    """Routing, not translation: an English line must come back untouched so
    the existing whitespace path keeps handling it."""
    assert to_alignment_units("hello world") is None
    assert to_alignment_units("") is None
    assert to_alignment_units("!!!") is None
    assert not looks_japanese("hello world")
    assert looks_japanese("空")
    assert looks_japanese("ソラ")
    assert looks_japanese("そら")


def test_mora_count_is_stable_across_calls():
    """Determinism is a contract, not a nicety: the document promises
    byte-identical output for identical input, which is exactly why this is a
    dictionary and not an LLM."""
    line = "宇宙を駆ける"
    first = to_alignment_units(line)
    assert first == to_alignment_units(line)
    assert first is not None and len(first) > 1


def test_every_unit_is_a_single_mora_worth_of_kana():
    """The count identity downstream only holds if no unit hides a second
    mora inside it."""
    units = to_alignment_units("宇宙を駆ける空に光る星")
    assert units is not None
    for unit in units:
        assert len(unit) <= 2, unit  # base kana + at most one small kana
        if len(unit) == 2:
            assert unit[1] in "ゃゅょぁぃぅぇぉゎ", unit


def test_surface_and_sound_stay_paired():
    """The trap this exists to avoid: the listener READS 宇宙 while the model
    HEARS うちゅー. Both have to survive, held together by the ownership
    counts — the aligner times the units and those times fold back onto the
    surfaces that own them."""
    from kashi_server.pipeline.japanese import prepare_line

    prepared = prepare_line("宇宙を駆ける")
    assert prepared is not None
    # What the screen shows is still the written form, not kana.
    assert "宇宙" in prepared.surfaces
    assert prepared.surfaces == ["宇宙", "を", "駆ける"]
    # What the aligner hears is the reading.
    assert prepared.units[:3] == ["う", "ちゅ", "ー"]
    # …and the two are held together: 宇宙 owns exactly its three morae.
    assert prepared.units_per_surface == [3, 1, 3]
    assert sum(prepared.units_per_surface) == len(prepared.units)


def test_latin_inside_japanese_owns_exactly_one_unit():
    from kashi_server.pipeline.japanese import prepare_line

    prepared = prepare_line("空 forever 光る")
    assert prepared is not None
    index = prepared.surfaces.index("forever")
    assert prepared.units_per_surface[index] == 1
    start = sum(prepared.units_per_surface[:index])
    assert prepared.units[start] == "forever"


def test_ownership_invariant_holds_on_every_prepared_line():
    """A desynchronised mapping produces confident nonsense rather than a
    visible failure, so the invariant is asserted in the type itself and
    pinned here across the shapes that actually occur."""
    from kashi_server.pipeline.japanese import prepare_line

    for line in ["ギミチョコ", "空に光る星", "紅蓮華", "空 forever 光る", "ヤラララ！！"]:
        prepared = prepare_line(line)
        assert prepared is not None, line
        assert len(prepared.surfaces) == len(prepared.units_per_surface)
        assert sum(prepared.units_per_surface) == len(prepared.units)
        assert all(count > 0 for count in prepared.units_per_surface)
