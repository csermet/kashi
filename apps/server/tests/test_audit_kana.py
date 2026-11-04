"""audit.kana: both sides to kana. Invented everyday sentences (no lyrics);
UniDic (unidic-lite) is a base dependency, so these run everywhere."""

import pytest

from audit import finder, kana
from audit.kana import JAPANESE, fold_long, kana_stream, romaji_to_kana, script_for, to_kana
from audit.textmatch import LATIN

SENTENCES = [
    ("今日は天気がいいですね", "kyou wa tenki ga ii desu ne"),
    ("毎朝コーヒーを飲みます", "maiasa koohii o nomimasu"),
    ("駅まで歩いて行きました", "eki made aruite ikimashita"),
    ("電車がとても混んでいる", "densha ga totemo konde iru"),
    ("図書館で本を読みました", "toshokan de hon o yomimashita"),
    ("友達と映画を見に行く", "tomodachi to eiga o mi ni iku"),
]


@pytest.mark.parametrize("a,b", [
    ("ありがとう", "アリガトー"), ("ありがとう", "arigato"), ("arigatou", "arigato"),
    ("東京", "tokyo"), ("toukyou", "tokyo"), ("先生", "sensei"), ("映画", "eiga"),
    ("こんにちは", "konnichiwa"), ("shimbun", "shinbun"), ("コーヒー", "koohii"),
] + SENTENCES)
def test_both_spellings_meet_in_one_kana_string(a, b):
    assert to_kana(a) == to_kana(b)


def test_romaji_rules():
    def k(word):
        return "".join(x for x, _ in romaji_to_kana(word))
    assert k("kitto") == "きっと" and k("matcha") == "まっちゃ"          # doubled consonants
    assert k("ren'ai") == "れんあい" and k("renai") == "れない"          # n' vs na
    assert k("minna") == "みんな" and k("hon") == "ほん" and k("onn") == "おん"
    assert k("sampo") == "さんぽ"                                        # m before p
    assert k("shi") == k("si") == "し" and k("tsu") == k("tu") == "つ"
    assert k("fu") == k("hu") == "ふ" and k("chi") == k("ti") == "ち"
    assert k("la") == k("ra") == "ら"                                    # l read as r
    assert k("kyo") == "きょ" and k("x") == ""
    assert [i for _, i in romaji_to_kana("kitto")] == [0, 2, 3]         # first source letter


def test_a_latin_word_inside_a_script_word_goes_through_romaji():
    assert to_kana("今日はlove") == to_kana("kyou wa love")


def test_delimiters_keep_romaji_words_apart():
    def stream(text):
        return kana_stream([{"text": "|" if c == " " else c, "start_ms": 0} for c in text])[0]
    assert stream("hon o") == "ほんお" and stream("hono") == "ほの"
    # long vowels fold across words on both sides alike
    for text in ("ii i", "kyou wa ii", "tou ou"):
        assert stream(text) == to_kana(text)


def test_long_vowels_fold():
    def f(text):
        return "".join(x for x, _ in fold_long([(c, n) for n, c in enumerate(text)]))
    assert f("とーきょー") == "ときょ" and f("とうきょう") == "ときょ"
    assert f("せんせい") == "せんせ" and f("おかあさん") == "おかさん"
    assert f("いいえ") == "いえ"                                         # same vowel again
    assert f("かい") == "かい"                                           # a then i: kept


def test_the_stream_keeps_each_unit_at_its_source_letter():
    tokens = [{"text": c, "start_ms": 1000 + 100 * n} for n, c in enumerate("kitto")]
    tokens += [{"text": "|", "start_ms": 1500}]
    tokens += [{"text": c, "start_ms": 2000 + 300 * n} for n, c in enumerate("空")]
    stream, times = kana_stream(tokens)
    assert stream.startswith("きっと") and times[:3] == [1000, 1200, 1300]
    assert len(stream) == len(times) and set(times[3:]) == {2000}


def test_the_script_follows_the_sheet_or_the_ear():
    jp = {"lines": [{"text": s} for s, _ in SENTENCES]}
    ro = {"lines": [{"text": r} for _, r in SENTENCES]}
    script_ear = {"tokens": [{"text": c, "start_ms": 0} for c in "今日は天気"]}
    latin_ear = {"tokens": [{"text": c, "start_ms": 0} for c in "kyou wa"]}
    assert script_for(jp) is JAPANESE and script_for(ro) is LATIN
    assert script_for(ro, script_ear) is JAPANESE and script_for(ro, latin_ear) is LATIN
    assert kana.JAPANESE_SHARE == 0.2
    assert kana.japanese_share("ab空") == pytest.approx(1 / 3)


def _doc(texts, starts):
    return {"lines": [{"text": t, "start_ms": s, "end_ms": s + 2500,
                       "words": [{"text": t, "start_ms": s, "end_ms": s + 2500}]}
                      for t, s in zip(texts, starts, strict=True)]}


def _ear(texts, starts, ms_per_char):
    tokens = []
    for text, start in zip(texts, starts, strict=True):
        t = start + 50
        for ch in text:
            tokens.append({"text": "|" if ch == " " else ch, "start_ms": t})
            t += ms_per_char
    return {"tokens": tokens}


STARTS = [5000 + 3500 * k for k in range(len(SENTENCES))]


def test_a_script_sheet_is_placed_on_a_romaji_ear_and_its_shift_read():
    script = [s for s, _ in SENTENCES]
    ear = _ear([r for _, r in SENTENCES], STARTS, 90)          # the MMS ear writes romaji
    offsets, borrowed = finder.line_offsets(_doc(script, STARTS), ear)
    assert len(offsets) == len(SENTENCES) and not borrowed
    assert all(abs(o - 50) <= 150 for o in offsets.values())
    late = finder.line_offsets(_doc(script, [s + 5000 for s in STARTS]), ear)[0]
    assert len(late) == len(SENTENCES)
    assert all(abs(o + 4950) <= 150 for o in late.values())


def test_a_romaji_sheet_is_placed_on_a_script_ear():
    romaji = [r for _, r in SENTENCES]
    ear = _ear([s for s, _ in SENTENCES], STARTS, 250)         # Qwen3-ASR writes script
    offsets, _ = finder.line_offsets(_doc(romaji, STARTS), ear)
    assert len(offsets) == len(SENTENCES)
    assert all(abs(o - 50) <= 300 for o in offsets.values())


def test_latin_songs_never_reach_the_kana_path(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("kana on a Latin song")

    monkeypatch.setattr(kana, "kana_stream", boom)
    doc = _doc(["one two three four five"], [1000])
    ear = _ear(["one two three four five"], [1000], 90)
    assert finder.line_offsets(doc, ear)[0] == {0: 50}
