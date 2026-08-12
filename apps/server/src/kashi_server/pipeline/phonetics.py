"""Which broad sound a written word STARTS with. Pure, no dependencies.

Faz 9 P2. The constant lateness correction (P1) treats every word alike, and
the archive says words are not alike. Measured on the shipped English chain
(20 JamendoLyrics songs, 5693 verified words, before any correction), the
median signed error splits cleanly by the word's first sound:

    vowel-initial      +112 ms   (n=1376)
    fricative-initial   +87 ms   (n=828)
    plosive-initial     +62 ms   (n=1813)
    sonorant-initial    +59 ms   (n=1676)

**This refutes the mechanism Faz 8 predicted**, and the refutation is worth
more than the fix. The prediction was that a CONSONANT-initial word would be
the late one — the note lands on the syllable's vowel, so a model that hears
the vowel reports a start delayed by the consonant's duration, and a
vowel-initial word should therefore be roughly unbiased. The measurement puts
vowel-initial words at the TOP of the lateness ranking, nearly twice the
plosive figure. Whatever is happening, "the consonant's duration" is not it.

A mechanism that does fit the ordering: a CTC model needs an acoustic LANDMARK
to place a token, and the classes rank exactly by how sharp their landmark is.
A plosive burst is unmistakable; friction is long and its edge is soft; a word
that opens on a vowel, sung straight out of the previous word's voicing, has
no boundary in the signal at all — so the model drifts to the steady state it
can see. That is a hypothesis, not a measurement, and it is written here as
one.

The classes are broad on purpose. English orthography is not phonemic ("hour",
"one"), so a finer table would be measuring spelling; four buckets survive
that noise. The honest, leave-one-song-out win is +0.010 PCO@0.1 (95% CI
[+0.001, +0.018], 14 songs of 20). An earlier record called +0.013
"cross-validated"; the 2026-08-12 audit showed that number was the in-sample
fit — direction identical, magnitude ~30% flattered. The shipped table itself
is close to but not exactly the argmax (plosive -80 vs a fitted -60; the
difference is ~0.002 and errs toward the base offset, i.e. conservative).
"""

import unicodedata

# Latin letters, extended with the Turkish alphabet so a classified language
# does not silently fall into "unknown" over ç/ğ/ı/ö/ş/ü. Turkish carries no
# measured offsets yet — this only decides which bucket a word would land in
# once someone measures them.
_VOWELS = set("aeiouıöü")
_PLOSIVES = set("ptkbdgcqç")  # ç is an affricate; it has the same sharp onset
_FRICATIVES = set("fvszhxşj")
_SONORANTS = set("mnlrwyğ")

VOWEL = "vowel"
PLOSIVE = "plosive"
FRICATIVE = "fricative"
SONORANT = "sonorant"


def initial_class(token: str) -> str | None:
    """The broad class of `token`'s first letter, or None when it has none.

    Leading punctuation and quotes are skipped — the aligner is given text as
    written, and ("Hello) has to classify as its H. A token with no letter at
    all (a bare "♪", a number) returns None so the caller can fall back to the
    language's plain offset rather than guess.
    """
    for char in token.lower():
        cls = _class_of(char)
        if cls is not None:
            return cls
        if char.isalpha():
            # Legitimate orthography carries marks the tables don't list —
            # Turkish â/î/û, borrowed é — and those are just marked vowels, so
            # strip combining marks and try the base letter before giving up.
            base = "".join(
                c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)
            )
            if base and base != char:
                cls = _class_of(base)
                if cls is not None:
                    return cls
            # A letter outside the tables even after decomposition (Greek,
            # Cyrillic, kana): known to be a word, unknown to this classifier.
            # None, not a wrong bucket.
            return None
    return None


def _class_of(char: str) -> str | None:
    if char in _VOWELS:
        return VOWEL
    if char in _PLOSIVES:
        return PLOSIVE
    if char in _FRICATIVES:
        return FRICATIVE
    if char in _SONORANTS:
        return SONORANT
    return None
