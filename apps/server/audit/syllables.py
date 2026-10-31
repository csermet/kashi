"""Rough syllable counts for lyric words: vowel groups, with English's silent
final 'e'. Good enough to say "six syllables against five loudness bumps -
the mapping is ambiguous", not to hyphenate. Turkish and Spanish are close to
phonetic, so vowel groups are their syllables."""

from __future__ import annotations

import re

VOWELS = "aeiouyáéíóúàèìòùâêîôûäëïöüıü"
_GROUPS = re.compile(f"[{VOWELS}]+")


def syllables(word: str, lang: str = "eng") -> int:
    """``lang``: "eng" (vowel groups, silent final e), "tur" (every vowel is a
    syllable), anything else: vowel groups."""
    w = re.sub(r"[^a-záéíóúàèìòùâêîôûäëïöüıüçğşñ]", "", word.lower())
    if not w:
        return 0
    if lang == "tur":
        return max(1, sum(1 for ch in w if ch in VOWELS))
    groups = len(_GROUPS.findall(w))
    # silent final e: "love", "make" - but not "the", "be", "little"
    if lang == "eng" and groups > 1 and w.endswith("e") and not w.endswith(("le", "ee", "ye")):
        groups -= 1
    return max(1, groups)


def line_syllables(words: list[str], lang: str = "eng") -> int:
    return sum(syllables(w, lang) for w in words)


def doc_language(doc: dict) -> str:
    """The language the document was aligned as, from its checkpoint name."""
    method = (doc.get("alignment") or {}).get("method", "")
    if "english" in method:
        return "eng"
    if "turkish" in method:
        return "tur"
    return "other"
