"""audit.kana and the Japanese option of audit.textmatch.

Synthetic Japanese sentences written for the test (plain everyday sentences,
no lyrics). Ears are hand-written the way MMS (Latin letters) and Qwen3-ASR
(script) would spell them, so the test is not the code agreeing with itself."""

import pytest

from audit import ear_gates, textmatch
from audit.kana import (
    japanese_items,
    japanese_letters,
    japanese_stream,
    simplify,
    simplify_items,
    to_romaji,
)
from audit.textmatch import locate, matcher_for

# (script line, how an MMS ear spells it, the same line written in romaji)
LINES = [
    ("今日は雨が降っている", "kyoo wa ame ga futte iru", "Kyō wa ame ga futte iru"),
    ("駅の前で会いましょう", "eki no mae de aimashoo", "Eki no mae de aimashō"),
    ("小さな花が咲いた", "chiisana hana ga saita", "Chiisana hana ga saita"),
    ("電車は駅に着いた", "densha wa eki ni tsuita", "densha wa eki ni tuita"),
]


def _r(kana):
    return "".join(c for c, _ in to_romaji([(ch, 0) for ch in kana]))


def test_kana_to_hepburn():
    assert _r("きょう") == "kyou" and _r("しゃしん") == "shashin" and _r("ちゃ") == "cha"
    assert _r("じゃ") == "ja" and _r("ぢゃ") == "ja" and _r("りょ") == "ryo"
    assert _r("がっこう") == "gakkou" and _r("まっちゃ") == "matcha"     # small tsu
    assert _r("コーヒー") == "koohii"                                    # long mark, katakana
    assert _r("ファン") == "fan" and _r("ティー") == "tii" and _r("ヴァ") == "va"
    assert _r("を") == "o" and _r("ん") == "n" and _r("ぁ") == "a"
    assert _r("x空") == "x空"                                            # not kana: passes


def test_romaji_keeps_each_kanas_payload():
    got = to_romaji([("き", 10), ("ょ", 20), ("う", 30), ("っ", 40), ("と", 50)])
    assert got == [("k", 10), ("y", 10), ("o", 10), ("u", 30), ("t", 40), ("t", 50), ("o", 50)]


@pytest.mark.parametrize("a, b", [
    ("Tōkyō", "toukyou"), ("Tōkyō", "tokyo"), ("Tōkyō", "Tohkyoh"), ("ookii", "ōkii"),
    ("shinbun", "shimbun"), ("shi", "si"), ("chi", "ti"), ("tsu", "tu"), ("fu", "hu"),
    ("ji", "zi"), ("sha", "sya"), ("cho", "tyo"), ("ja", "zya"), ("matcha", "maccha"),
    ("gakkou", "gakō"), ("kan'i", "kani"), ("sensei", "sensē"), ("wo", "o"), ("dzu", "zu"),
    ("Konnichi wa", "konichiwa"), ("botchi", "botti"), ("bocchi", "botti"), ("aa", "a"),
])
def test_one_simplified_form_for_every_romanisation(a, b):
    assert simplify(a) == simplify(b)


@pytest.mark.parametrize("a, b, same", [("kita", "kitta", True), ("oha", "oa", False),
                                        ("hana", "hama", False), ("koi", "koe", False)])
def test_the_simplifier_folds_length_and_spelling_not_words(a, b, same):
    # small tsu and doubled consonants fold (kita/kitta), other letters stay
    assert (simplify(a) == simplify(b)) is same


def test_oh_before_a_vowel_is_kept():
    assert simplify("ohayou") == "ohayo" and simplify("ohta") == "ota"


def test_simplified_letters_keep_the_first_source_payload():
    got = simplify_items([(c, t) for t, c in enumerate("shitte")])
    assert got == [("s", 0), ("i", 0), ("t", 3), ("e", 5)]   # "shi" -> "si" from its 's'


@pytest.mark.parametrize("script, mms, romaji", LINES)
def test_script_mms_and_romaji_meet(script, mms, romaji):
    assert japanese_letters(script) == japanese_letters(mms) == japanese_letters(romaji)
    assert len(japanese_letters(script)) >= textmatch.MIN_LETTERS
    assert textmatch.letters(script) == ""           # why the a-z matcher never placed it


def test_script_readings_carry_their_morphemes_time():
    got = japanese_items([(ch, 100 * i) for i, ch in enumerate("雨が")])
    assert got == [("a", 0), ("m", 0), ("e", 0), ("g", 100), ("a", 100)]
    # a reading longer than its surface is spread over the surface's characters
    got = japanese_items([("光", 0), ("る", 400)])
    assert got == [("h", 0), ("i", 0), ("k", 0), ("a", 0), ("r", 400), ("u", 400)]


def _tokens(text, t0, step=90):
    out, t = [], t0
    for word in text.split():
        for ch in word:
            out.append({"text": ch, "start_ms": t, "end_ms": t + 20})
            t += step
        out.append({"text": "|", "start_ms": t, "end_ms": t})
    return out


def _ear(spoken, starts, step=90):
    toks = []
    for text, t in zip(spoken, starts, strict=True):
        toks += _tokens(text, t, step)
    return toks


STARTS = [5000, 12000, 19000, 26000]


def test_textmatch_places_a_script_line_in_an_mms_ear_with_the_japanese_option():
    toks = _ear([mms for _, mms, _ in LINES], [t + 3000 for t in STARTS])
    norm, build = matcher_for("jpn")
    stream, times = build(toks)
    for (script, _, _), t in zip(LINES, STARTS, strict=True):
        m = locate(script, t, stream, times, normalize=norm)
        assert m is not None and abs(m.heard_ms - (t + 3000)) <= 100
        # the default matcher cannot see it at all
        plain, plain_times = textmatch.ear_stream(toks)
        assert locate(script, t, plain, plain_times) is None


def test_textmatch_places_a_romaji_line_in_a_script_ear():
    """Qwen3-ASR writes Japanese in script: one token per character, spread
    evenly over a segment."""
    toks = []
    for (script, _, _), t in zip(LINES, STARTS, strict=True):
        for k, ch in enumerate(script):
            toks.append({"text": ch, "start_ms": t + 150 * k, "end_ms": t + 150 * k + 150})
        toks.append({"text": "|", "start_ms": t, "end_ms": t})
    norm, build = matcher_for("ja")
    stream, times = build(toks)
    for (_, _, romaji), t in zip(LINES, STARTS, strict=True):
        m = locate(romaji, t - 2000, stream, times, normalize=norm)
        assert m is not None and abs(m.heard_ms - t) <= 300


def test_other_languages_keep_the_a_z_matcher():
    assert matcher_for("eng") == (textmatch.letters, textmatch.ear_stream)
    assert matcher_for(None) == (textmatch.letters, textmatch.ear_stream)


def test_japanese_stream_folds_across_ear_word_boundaries_like_a_line():
    stream, times = japanese_stream(_tokens("wa ame", 0))
    assert stream == japanese_letters("ワアメ") == "wame"
    assert times == [0, 90, 270, 360]


def test_ear_gates_reads_a_japanese_song_with_the_japanese_matcher():
    lines = []
    for (script, _, _), t in zip(LINES, STARTS, strict=True):
        lines.append({"text": script, "start_ms": t, "end_ms": t + 2500,
                      "words": [{"text": script, "start_ms": t, "end_ms": t + 2500}]})
    doc = {"alignment": {"method": "ctc-forced-aligner/mms-300m"}, "lines": lines}
    ear = {"tokens": _ear([mms for _, mms, _ in LINES], [t + 40 for t in STARTS]),
           "stride_ms": 20.0, "start_ms": 0, "letter_prob": [0.9] * 1500}
    r = ear_gates.report(doc, ear, ear, "healthy")
    assert r["language"] == "jpn" and r["gated"] is False
    assert r["current"]["placed"] == 4 and abs(r["current"]["median_ms"] - 40) <= 100
