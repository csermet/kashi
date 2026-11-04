"""Script-aware matching for Japanese: both sides to kana (Faz 3.5, item c).

The ear-text match reduces text to a-z, so a Japanese sheet in script never
matched (Faz 3: 0 lines), and a script ear (Qwen3-ASR writes Japanese in
script) could not meet a romaji sheet either. Here every text, whatever its
script, becomes hiragana by ONE function, applied to both sides alike:

- Japanese script: the production module's UniDic readings
  (``pipeline.japanese``: ``pron`` first, so long vowels come out as ー);
- Latin letters: romaji to kana (Hepburn, Kunrei and Nihon-shiki spellings,
  l read as r, doubled consonants as っ); an English word in a J-pop line
  becomes nonsense kana, the same nonsense on both sides;
- then long vowels fold (ー dropped, a vowel kana repeating the previous
  kana's vowel dropped, おう -> お, えい -> え), so ありがとう, アリガトー and
  "arigato" are one string.

The matcher itself is the finder's (``textmatch.scan``/``locate``); only the
unit and its rates change (``JAPANESE``). Until three Japanese songs have
checked lines, Japanese placement is reported, never gated (the plan).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from audit.textmatch import Script

# romaji -> hiragana, longest key first at every position
_ROMAJI = {
    "a": "あ", "i": "い", "u": "う", "e": "え", "o": "お",
    "ka": "か", "ki": "き", "ku": "く", "ke": "け", "ko": "こ",
    "kya": "きゃ", "kyu": "きゅ", "kyo": "きょ",
    "ga": "が", "gi": "ぎ", "gu": "ぐ", "ge": "げ", "go": "ご",
    "gya": "ぎゃ", "gyu": "ぎゅ", "gyo": "ぎょ",
    "sa": "さ", "shi": "し", "si": "し", "su": "す", "se": "せ", "so": "そ",
    "sha": "しゃ", "shu": "しゅ", "sho": "しょ", "she": "しぇ",
    "sya": "しゃ", "syu": "しゅ", "syo": "しょ",
    "za": "ざ", "ji": "じ", "zi": "じ", "zu": "ず", "ze": "ぜ", "zo": "ぞ",
    "ja": "じゃ", "ju": "じゅ", "jo": "じょ", "je": "じぇ",
    "zya": "じゃ", "zyu": "じゅ", "zyo": "じょ",
    "ta": "た", "chi": "ち", "ti": "ち", "tsu": "つ", "tu": "つ", "te": "て", "to": "と",
    "cha": "ちゃ", "chu": "ちゅ", "cho": "ちょ", "che": "ちぇ",
    "tya": "ちゃ", "tyu": "ちゅ", "tyo": "ちょ",
    "da": "だ", "di": "ぢ", "du": "づ", "de": "で", "do": "ど",
    "na": "な", "ni": "に", "nu": "ぬ", "ne": "ね", "no": "の",
    "nya": "にゃ", "nyu": "にゅ", "nyo": "にょ",
    "ha": "は", "hi": "ひ", "fu": "ふ", "hu": "ふ", "he": "へ", "ho": "ほ",
    "hya": "ひゃ", "hyu": "ひゅ", "hyo": "ひょ",
    "fa": "ふぁ", "fi": "ふぃ", "fe": "ふぇ", "fo": "ふぉ",
    "ba": "ば", "bi": "び", "bu": "ぶ", "be": "べ", "bo": "ぼ",
    "bya": "びゃ", "byu": "びゅ", "byo": "びょ",
    "pa": "ぱ", "pi": "ぴ", "pu": "ぷ", "pe": "ぺ", "po": "ぽ",
    "pya": "ぴゃ", "pyu": "ぴゅ", "pyo": "ぴょ",
    "ma": "ま", "mi": "み", "mu": "む", "me": "め", "mo": "も",
    "mya": "みゃ", "myu": "みゅ", "myo": "みょ",
    "ya": "や", "yu": "ゆ", "yo": "よ",
    "ra": "ら", "ri": "り", "ru": "る", "re": "れ", "ro": "ろ",
    "rya": "りゃ", "ryu": "りゅ", "ryo": "りょ",
    "wa": "わ", "wo": "を", "we": "うぇ", "wi": "うぃ",
    "va": "ゔぁ", "vi": "ゔぃ", "vu": "ゔ", "ve": "ゔぇ", "vo": "ゔぉ",
}
# uroman / ASR spellings with l for r
_ROMAJI |= {"l" + k[1:]: v for k, v in _ROMAJI.items() if k.startswith("r")}
_KEYS = sorted(_ROMAJI, key=len, reverse=True)
_VOWEL_OF = {}
for _k, _v in _ROMAJI.items():
    for _ch in _v:
        if _ch not in "ゃゅょぁぃぅぇぉ":
            _VOWEL_OF.setdefault(_ch, _k[-1])
    if len(_v) == 2:  # きゃ: the small kana carries the vowel
        _VOWEL_OF[_v[1]] = _k[-1]
_VOWEL_KANA = {"a": "あ", "i": "い", "u": "う", "e": "え", "o": "お"}
_CONSONANTS = set("bcdfghjklmpqrstvwxyz")
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
_KATAKANA = re.compile(r"[ァ-ヶ]")
# A text is Japanese for matching when this share of its characters is in
# Japanese script (an ear that wrote a stray kana stays Latin).
JAPANESE_SHARE = 0.2


def romaji_to_kana(word: str) -> list[tuple[str, int]]:
    """(kana, index of the first letter it came from) for one Latin word."""
    w = word.lower()
    out: list[tuple[str, int]] = []
    i = 0
    while i < len(w):
        ch = w[i]
        if not ch.isalpha():
            i += 1
            continue
        if ch == "n":
            nxt = w[i + 1] if i + 1 < len(w) else ""
            after = w[i + 2] if i + 2 < len(w) else ""
            if nxt == "n":
                # konnichiwa: ん, and the second n opens the next mora; a
                # final or pre-consonant nn is one ん
                out.append(("ん", i))
                i += 1 if after and after in "aeiouy" else 2
                continue
            if not nxt or nxt not in "aeiouy":  # ren'ai: the apostrophe is skipped next
                out.append(("ん", i))
                i += 1
                continue
        if ch == "m" and i + 1 < len(w) and w[i + 1] in "bpm":
            out.append(("ん", i))  # traditional Hepburn: shimbun, sampo
            i += 1
            continue
        if ch in _CONSONANTS and ch != "n" and i + 1 < len(w) and (
                w[i + 1] == ch or (ch == "t" and w[i + 1] == "c")):
            out.append(("っ", i))  # kk, ss, tt, tch
            i += 1
            continue
        key = next((k for k in _KEYS if w.startswith(k, i)), None)
        if key is None:
            i += 1  # a letter romaji has no use for
            continue
        for kana in _ROMAJI[key]:
            out.append((kana, i))
        i += len(key)
    return out


def _hiragana(text: str) -> str:
    return _KATAKANA.sub(lambda m: chr(ord(m.group()) - 0x60), text)


def _script_readings(text: str) -> list[tuple[str, int]]:
    """(kana, index of the morpheme's first character) for Japanese script;
    Latin morphemes go through romaji, a morpheme without a reading is dropped."""
    from kashi_server.pipeline.japanese import _reading, _tagger_instance

    out: list[tuple[str, int]] = []
    pos = 0
    for word in _tagger_instance()(text):
        surface = word.surface
        start = text.find(surface, pos)
        start = pos if start < 0 else start
        pos = start + len(surface)
        if _JAPANESE.search(surface):
            reading = _reading(word)
            if reading:
                out += [(k, start) for k in _hiragana(reading)]
        else:
            out += [(k, start + i) for k, i in romaji_to_kana(surface)]
    return out


def fold_long(units: Sequence[tuple[str, int]]) -> list[tuple[str, int]]:
    """Long vowels as short ones: ー dropped; a vowel kana repeating the
    previous kana's vowel dropped, and う after an o, い after an e."""
    out: list[tuple[str, int]] = []
    for kana, at in units:
        if kana == "ー":
            continue
        if out and kana in _VOWEL_KANA.values():
            prev = _VOWEL_OF.get(out[-1][0])
            mine = _VOWEL_OF.get(kana)
            if prev is not None and (prev == mine or (prev, mine) in (("o", "u"), ("e", "i"))):
                continue
        out.append((kana, at))
    return out


def raw_units(text: str) -> list[tuple[str, int]]:
    """Any text -> hiragana (not yet folded), each with its source character's index."""
    out: list[tuple[str, int]] = []
    for m in re.finditer(r"\S+", text):
        word = m.group()
        if _JAPANESE.search(word):
            out += [(k, m.start() + i) for k, i in _script_readings(word)]
        else:
            out += [(k, m.start() + i) for k, i in romaji_to_kana(word)]
    return out


def kana_units(text: str) -> list[tuple[str, int]]:
    """Any text -> folded hiragana, each with the index of its source character."""
    return fold_long(raw_units(text))


def to_kana(text: str) -> str:
    return "".join(k for k, _ in kana_units(text))


def kana_stream(tokens: Sequence[dict]) -> tuple[str, list[int]]:
    """The ear's tokens as one folded kana string with times. Words (between
    delimiters) are converted whole, so romaji letters meet their partners; a
    unit takes the time of the character it came from; long vowels fold over
    the whole stream, as ``to_kana`` folds a whole line."""
    units: list[tuple[str, int]] = []
    word: list[dict] = []

    def flush() -> None:
        if not word:
            return
        text = "".join(tok["text"] for tok in word)
        starts: list[int] = []
        for tok in word:
            starts += [tok["start_ms"]] * len(tok["text"])
        units.extend((kana, starts[min(at, len(starts) - 1)]) for kana, at in raw_units(text))
        word.clear()

    for tok in tokens:
        if tok["text"] in ("|", " ") or not tok["text"].strip():
            flush()
        else:
            word.append(tok)
    flush()
    folded = fold_long(units)
    return "".join(k for k, _ in folded), [t for _, t in folded]


def japanese_share(text: str) -> float:
    chars = [c for c in text if not c.isspace() and c != "|"]
    return sum(1 for c in chars if _JAPANESE.match(c)) / len(chars) if chars else 0.0


# A kana is about two romaji letters: half the Latin minimum length, twice
# its letter rates.
JAPANESE = Script(norm=to_kana, stream=kana_stream, min_units=6, ms_per_unit=180,
                  min_ms_per_unit=100, max_ms_per_unit=300)


def script_for(doc: dict, ear: dict | None = None) -> Script:
    """JAPANESE when the sheet or the ear is written in Japanese script."""
    from audit.textmatch import LATIN

    sheet = " ".join(ln.get("text", "") for ln in doc.get("lines", []))
    heard = "".join(t["text"] for t in (ear or {}).get("tokens", []))
    if japanese_share(sheet) >= JAPANESE_SHARE or japanese_share(heard) >= JAPANESE_SHARE:
        return JAPANESE
    return LATIN
