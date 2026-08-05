"""Japanese lyrics -> their kana reading, so the aligner is fed what is sung.

The problem this exists for (Faz 8 P-B3). Two failures stack on Japanese
documents, and the second one is the real damage:

1. `regroup_words_into_lines` requires the text token count to match the
   segment count. In a Japanese job the aligner emits ONE SEGMENT PER
   CHARACTER, while `line.split()` sees a line with no spaces as a single
   token — so the identity could never hold and every document took the
   line-mode exit. Nine of the ten line-mode documents in the archive are
   non-Latin.
2. MMS romanizes through **uroman**, which reads kanji as CHINESE. Measured
   directly against the shipped aligner (worker pod, 2026-08-05):

       空に光る  ->  ['k o n g', 'n i', 'g u a n g', 'r u']

   "kong", "guang" — Mandarin. The model was being asked to find Chinese in
   audio sung in Japanese. Fixing the counts alone would have produced
   confidently wrong timings instead of no timings, which is worse.

Both dissolve at one point: replace each morpheme with its **kana reading**
before the text ever reaches the aligner. uroman romanizes kana correctly, so
the acoustic side becomes true, and the character count becomes predictable.

Everything here was measured rather than assumed, because the obvious guesses
were wrong twice: the split granularity is set by the `language` ARGUMENT and
not by the text (see `handles`), and the unit is the CHARACTER and not the
mora. Morae are the right unit for a human reading kana; they are not what
this aligner emits.

The pattern is Nightingale's (GPL-3 — read for the idea, no code taken).
Dictionary segmentation rather than an LLM on purpose: it is deterministic,
which the byte-identical document contract requires, and measurably more
accurate on this task.

Pure module: no torch, no I/O, no network. fugashi's tagger is the only
dependency and it is loaded lazily so importing this costs nothing.
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_tagger = None

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


# Languages whose lines this module rewrites. `langid` reports ISO 639-3.
JAPANESE_LANGUAGES = frozenset({"jpn", "ja"})


def handles(language: str) -> bool:
    """Is this a job we rewrite? The decision is per JOB, not per line.

    MEASURED against the shipped aligner (worker pod, 2026-08-05), because the
    obvious guess was wrong. `preprocess_text`'s split granularity is decided
    by the `language` argument, not by what the text contains:

        language="eng", "hello world"        -> ['hello', 'world']
        language="jpn", "hello world"        -> ['h','e','l','l','o',' ','w',…]

    So in a Japanese job EVERY line is split per character — including a line
    of pure English — and the spaces become segments of their own. Routing
    line by line would have desynchronised the count on exactly the mixed
    documents J-pop is full of.
    """
    return language.lower() in JAPANESE_LANGUAGES


@dataclass(frozen=True)
class PreparedLine:
    """What the SCREEN shows against what the ALIGNER hears.

    These are different things in Japanese and conflating them is the whole
    trap: the listener reads 宇宙, the model hears うちゅー. The surfaces stay
    as written, the units carry the sound, and `units_per_surface` holds them
    together — the aligner times the units, those times fold back onto the
    surfaces that own them.

    A unit is one CHARACTER, not one mora, because that is what the aligner
    actually emits for Japanese (measured, above). Morae remain the right
    reading unit for a human; they are not the unit of this contract.
    """

    surfaces: list[str]  # what the document displays
    units: list[str]  # what the aligner is given, flattened
    units_per_surface: list[int]  # units[i] ownership, sums to len(units)

    def __post_init__(self) -> None:
        assert len(self.surfaces) == len(self.units_per_surface)
        assert sum(self.units_per_surface) == len(self.units)

    @property
    def align_text(self) -> str:
        """The string to hand the aligner. No separators: each character is
        already its own segment, so a space would only add an empty one."""
        return "".join(self.units)


def prepare_line(text: str) -> PreparedLine | None:
    """Line -> (display surfaces, alignment units), or None to leave it be.

    None when nothing sounded (punctuation only) or when a Japanese morpheme
    has no reading we can trust. Partial conversion is deliberately NOT
    attempted: half-converted text is worse than the honest fallback, because
    the caller can no longer tell which units belong to which surface — and a
    desynchronised mapping produces confident nonsense rather than a visible
    failure.

    Latin words are kept as written and contribute their own characters, since
    a Japanese job splits them per character too.
    """
    surfaces: list[str] = []
    units: list[str] = []
    counts: list[int] = []
    for word in _tagger_instance()(text):
        surface = word.surface
        if not surface.strip() or not re.search(r"\w", surface, re.UNICODE):
            continue  # whitespace and punctuation carry no sound
        if looks_japanese(surface):
            reading = _reading(word)
            if reading is None:
                logger.debug("no reading for %r — leaving the line alone", surface)
                return None
            sound = katakana_to_hiragana(reading)
        else:
            sound = surface  # a Latin word inside a Japanese line
        if not sound:
            continue
        surfaces.append(surface)
        units.extend(sound)
        counts.append(len(sound))
    if not units:
        return None
    return PreparedLine(surfaces=surfaces, units=units, units_per_surface=counts)


def to_alignment_units(text: str) -> list[str] | None:
    """Flattened view of `prepare_line` — the units alone."""
    prepared = prepare_line(text)
    return prepared.units if prepared else None


def _tagger_instance():
    """Loaded once; ~50 MB of dictionary, so never at import time."""
    global _tagger
    if _tagger is None:
        import fugashi  # pyright: ignore[reportMissingImports]

        _tagger = fugashi.Tagger()  # pyright: ignore[reportAttributeAccessIssue]
    return _tagger
