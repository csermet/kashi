"""Japanese lyrics -> kana morae, so the aligner is fed text it can hear.

The problem this exists for (Faz 8 P-B3, measured 2026-08-05). Two failures
stack on Japanese documents:

1. `regroup_words_into_lines` requires `sum(len(line.split())) == len(segments)`.
   Japanese does not delimit words with spaces, so that identity cannot hold
   and every document takes the line-mode exit — nine of the ten line-mode
   documents in the archive are non-Latin.
2. The deeper one: MMS romanizes through **uroman**, which reads kanji as
   CHINESE and emits pinyin. 空 becomes "kong", not "sora". So even with the
   token counts fixed, Japanese was being aligned against text that does not
   sound like the audio. It is a documented uroman limitation, not a bug.

Both dissolve at the same point: convert each morpheme to its **kana reading**
and hand the aligner morae. uroman romanizes kana correctly, so the acoustic
side becomes right, and one mora per token makes the count identity hold by
construction.

The pattern is Nightingale's (GPL-3 — read for the idea, no code taken).
Dictionary segmentation rather than an LLM on purpose: it is deterministic,
which the byte-identical document contract requires, and measurably more
accurate on this task.

Pure module: no torch, no I/O, no network. fugashi's tagger is the only
dependency and it is loaded lazily so importing this costs nothing.
"""

import logging
import re

logger = logging.getLogger(__name__)

_tagger = None

# Kana that cannot stand alone as a mora: they fuse with the kana before them.
# きゃ is one mora, not two — feeding the aligner two would desynchronise every
# count downstream. The small vowels matter for loanwords (ファ, ティ), which
# J-pop is full of.
_SMALL_KANA = "ゃゅょぁぃぅぇぉゎ"
# The long-vowel mark and the geminate stop DO stand alone (ラーメン is 4).
_STANDALONE = "ーっ"

_KATAKANA = re.compile(r"[ァ-ヶ]")
_KANA_ONLY = re.compile(r"^[ぁ-ゟ゠-ヿー]+$")
# A line worth routing through this module at all: contains kana or kanji.
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def looks_japanese(text: str) -> bool:
    """Cheap script test. Pure — the routing decision, not the language id."""
    return bool(_JAPANESE.search(text))


def katakana_to_hiragana(text: str) -> str:
    """Readings come out of UniDic in katakana; the aligner sees one script."""
    return _KATAKANA.sub(lambda m: chr(ord(m.group()) - 0x60), text)


def split_morae(kana: str) -> list[str]:
    """Kana string -> morae.

    Pure. The rule that matters: small ya/yu/yo and the small vowels attach to
    the kana before them, while the long mark and the small tsu stand alone.
    A leading small kana has nothing to attach to and is kept as its own mora
    rather than dropped — losing a character would desynchronise the count
    this whole module exists to keep.
    """
    morae: list[str] = []
    for char in kana:
        if char in _SMALL_KANA and morae and morae[-1] not in _STANDALONE:
            morae[-1] += char
        else:
            morae.append(char)
    return morae


def _reading(word) -> str | None:
    """A morpheme's kana reading, or None when the dictionary has none.

    UniDic exposes `kana` (the citation reading) and `pron` (the spoken one);
    `pron` writes long vowels as ー, which is what is actually sung, so it
    wins. Katakana and Latin surfaces carry no reading field at all — the
    katakana surface IS its reading, and Latin text is left to the caller.
    """
    for field in ("pron", "kana", "pronBase", "kanaBase"):
        value = getattr(word.feature, field, None)
        if value and value != "*":
            return value
    surface = word.surface
    return surface if _KANA_ONLY.match(surface) else None


def to_alignment_units(text: str) -> list[str] | None:
    """Japanese line -> the units to hand the aligner, or None to leave it be.

    Returns None when the line is not Japanese, or when any morpheme has no
    reading we can trust (a bare Latin word, an unknown kanji). Partial
    conversion is deliberately NOT attempted: half-converted text is worse
    than the honest fallback, because the caller can no longer tell which
    units correspond to which surface.
    """
    if not looks_japanese(text):
        return None
    units: list[str] = []
    for word in _tagger_instance()(text):
        surface = word.surface
        if not surface.strip():
            continue
        if not re.search(r"\w", surface, re.UNICODE):
            continue  # punctuation carries no sound
        if not looks_japanese(surface):
            # A Latin word inside a Japanese line (very common in J-pop). It
            # already survives whitespace tokenisation, so it passes through
            # whole rather than being forced into morae.
            units.append(surface)
            continue
        reading = _reading(word)
        if reading is None:
            logger.debug("no reading for %r — leaving the line to the caller", surface)
            return None
        units.extend(split_morae(katakana_to_hiragana(reading)))
    return units or None


def _tagger_instance():
    """Loaded once; ~50 MB of dictionary, so never at import time."""
    global _tagger
    if _tagger is None:
        import fugashi  # pyright: ignore[reportMissingImports]

        _tagger = fugashi.Tagger()  # pyright: ignore[reportAttributeAccessIssue]
    return _tagger
