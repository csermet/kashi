"""Script-aware Japanese matching: lyrics and ears in one simplified romaji.

The ear-text match reduces text to a-z, so a sheet in kanji and kana never
matches anything (the pilot set's Japanese song placed 0 lines). Four writings
meet here: the sheet in kanji/kana, the sheet in romaji (lrclib has both), the
MMS ear in Latin letters (uroman romanises the kana it hears) and Qwen3-ASR in
Japanese script.

**The common form is simplified romaji, not kana.** Reading the noisy MMS
greedy letters back into kana is ambiguous and lossy (``kng``, ``tsh`` are no
kana), while kana to romaji is a deterministic table; romaji also keeps
``audit.textmatch`` working on the a-z letters its thresholds were calibrated
on (12 letters, ratio 0.55, 50-150 ms per letter). So: kanji -> kana with the
production analyzer (``kashi_server.pipeline.japanese``: fugashi + UniDic,
already a base dependency, MIT / BSD), kana -> Hepburn romaji (the table below,
by hand), then every Latin writing through one simplifier that folds the
romanisation systems and lrclib's spellings together:

- long vowels: ``ō``/``ou``/``oo``/``oh`` (before a consonant), ``ei``, ``ー`` -> one vowel;
- small tsu / doubled consonants (``kk``, ``tch``, ``cch``) -> one consonant;
- ``n'``, ``nn``, ``m`` before b/p/m -> ``n``;
- Hepburn and Kunrei: ``shi``/``si`` -> ``si``, ``chi``/``ti`` -> ``ti``,
  ``tsu``/``tu`` -> ``tu``, ``fu``/``hu`` -> ``hu``, ``ji``/``zi`` -> ``zi``,
  ``sh``/``sy`` -> ``sy``, ``ch``/``ty`` -> ``ty``, ``j``/``zy`` -> ``zy``,
  ``dzu``/``du`` -> ``zu``, ``di`` -> ``zi``, ``wo`` -> ``o``.

Every step keeps each output letter's time (the first source letter's), so an
ear stream stays timed. Japanese is reported, never gated, until at least three
Japanese songs have checked lines.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

# --- kana -> Hepburn ------------------------------------------------------------

_BASE = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n", "ゔ": "vu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゃ": "ya", "ゅ": "yu", "ょ": "yo",
    "ゎ": "wa",
}
_SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
_SMALL_V = {"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o"}
# i-row kana before a small ya/yu/yo: the consonant they keep
_Y_HEAD = {"き": "ky", "ぎ": "gy", "し": "sh", "じ": "j", "ち": "ch", "ぢ": "j", "に": "ny",
           "ひ": "hy", "び": "by", "ぴ": "py", "み": "my", "り": "ry"}
# kana before a small vowel (loanword spellings): the consonant they keep
_V_HEAD = {"ふ": "f", "ゔ": "v", "て": "t", "で": "d", "と": "t", "ど": "d", "う": "w",
           "し": "sh", "じ": "j", "ち": "ch", "い": "y", "く": "kw", "ぐ": "gw"}
VOWELS = "aeiou"


def katakana_to_hiragana(text: str) -> str:
    from kashi_server.pipeline.japanese import katakana_to_hiragana as convert

    return convert(text)


def to_romaji[T](items: Sequence[tuple[str, T]]) -> list[tuple[str, T]]:
    """Kana (hiragana or katakana) to Hepburn letters, each with its kana's
    payload; characters that are not kana pass through."""
    kana = [(katakana_to_hiragana(ch), p) for ch, p in items]
    out: list[tuple[str, T]] = []
    i = 0
    while i < len(kana):
        ch, payload = kana[i]
        nxt = kana[i + 1][0] if i + 1 < len(kana) else ""
        if ch == "っ":                       # small tsu: the next consonant doubled
            following = "".join(c for c, _ in to_romaji(kana[i + 1:i + 3]))
            if following[:2] == "ch":
                out.append(("t", payload))    # Hepburn: matcha, not maccha
            elif following and following[0] not in VOWELS:
                out.append((following[0], payload))
            i += 1
            continue
        if ch == "ー":                       # long vowel mark: the previous vowel again
            prev = next((c for c, _ in reversed(out) if c in VOWELS), None)
            if prev:
                out.append((prev, payload))
            i += 1
            continue
        if nxt in _SMALL_Y and ch in _Y_HEAD:
            roma, i = _Y_HEAD[ch] + _SMALL_Y[nxt], i + 2
        elif nxt in _SMALL_V and ch in _V_HEAD:
            roma, i = _V_HEAD[ch] + _SMALL_V[nxt], i + 2
        elif ch in _BASE:
            roma, i = _BASE[ch], i + 1
        else:
            roma, i = ch, i + 1
        out += [(c, payload) for c in roma]
    return out


# --- the simplifier --------------------------------------------------------------

# (pattern, replacement), longest first at each position
_REWRITES = [
    ("tchi", "ti"), ("cchi", "ti"), ("tch", "ty"), ("cch", "ty"), ("dzu", "zu"),
    ("shi", "si"), ("chi", "ti"), ("tsu", "tu"),
    ("sh", "sy"), ("ch", "ty"),
    ("fu", "hu"), ("ji", "zi"), ("du", "zu"), ("di", "zi"), ("wo", "o"),
    ("j", "zy"),
]
# a doubled letter is one letter (below); these two pairs are long vowels too
_LONG = {("o", "u"), ("e", "i")}


def _latin[T](items: Sequence[tuple[str, T]]) -> list[tuple[str, T]]:
    """Lowercase a-z only; accents and macrons dropped (``ō`` -> ``o``)."""
    out = []
    for ch, p in items:
        for c in unicodedata.normalize("NFKD", ch.lower()):
            if "a" <= c <= "z":
                out.append((c, p))
    return out


def simplify_items[T](items: Sequence[tuple[str, T]]) -> list[tuple[str, T]]:
    """The simplifier over (letter, payload) pairs; an output letter carries the
    payload of the first source letter it came from."""
    src = _latin(items)
    text = "".join(c for c, _ in src)
    out: list[tuple[str, T]] = []
    i = 0
    while i < len(src):
        for pat, rep in _REWRITES:
            if text.startswith(pat, i):
                out += [(c, src[i][1]) for c in rep]
                i += len(pat)
                break
        else:
            c, nxt = text[i], text[i + 1:i + 2]
            if c == "m" and nxt in ("b", "p", "m") and nxt:
                out.append(("n", src[i][1]))
            elif c == "h" and out and out[-1][0] == "o" and (not nxt or nxt not in VOWELS):
                pass                          # "oh" before a consonant: a long o
            else:
                out.append((c, src[i][1]))
            i += 1
    folded: list[tuple[str, T]] = []
    for c, p in out:
        if folded:
            prev = folded[-1][0]
            if c == prev or (prev, c) in _LONG:
                continue                      # long vowels, small tsu, doubled n: one letter
        folded.append((c, p))
    return folded


def simplify(text: str) -> str:
    return "".join(c for c, _ in simplify_items([(ch, 0) for ch in text]))


# --- readings --------------------------------------------------------------------

_SCRIPT = re.compile(r"[぀-ヿ一-鿿々〆ヵヶ]")


def has_script(text: str) -> bool:
    return bool(_SCRIPT.search(text))


def reading_items[T](items: Sequence[tuple[str, T]]) -> list[tuple[str, T]]:
    """Kanji and kana to their kana reading (production analyzer: UniDic's
    spoken form, so long vowels arrive as ー), each reading character with the
    payload of the first character of its morpheme; Latin text passes through;
    a morpheme without a reading is dropped."""
    from kashi_server.pipeline.japanese import _reading, _tagger_instance

    text = "".join(ch for ch, _ in items)
    payloads = [p for ch, p in items for _ in ch]
    out: list[tuple[str, T]] = []
    pos = 0
    for word in _tagger_instance()(text):
        surface = word.surface
        at = text.find(surface, pos)
        if at < 0:
            continue
        pos = at + len(surface)
        if has_script(surface):
            sound = _reading(word)
            if not sound:
                continue
        else:
            sound = surface
        out += [(ch, payloads[at]) for ch in sound]
    return out


def japanese_items[T](items: Sequence[tuple[str, T]]) -> list[tuple[str, T]]:
    """Any writing (script, kana, romaji, an ear's Latin letters) to the
    simplified romaji, payloads kept."""
    items = list(items)
    if any(has_script(ch) for ch, _ in items):
        items = to_romaji(reading_items(items))
    return simplify_items(items)


def japanese_letters(text: str) -> str:
    """The line normalizer for ``audit.textmatch.locate``."""
    return "".join(c for c, _ in japanese_items([(ch, 0) for ch in text]))


def japanese_stream(tokens: Sequence[dict]) -> tuple[str, list[int]]:
    """The ear's letters in the simplified romaji, each with its time (the
    stream builder that goes with ``japanese_letters``). The stream is one run,
    word delimiters dropped, as a line is one run: a long vowel folds across a
    word boundary on both sides alike ("wa ame" and 'ワアメ' both read "wame")."""
    chars = [(ch, tok["start_ms"]) for tok in tokens
             if tok["text"] != "|" and tok["text"].strip() for ch in tok["text"]]
    out = japanese_items(chars)
    return "".join(c for c, _ in out), [t for _, t in out]
