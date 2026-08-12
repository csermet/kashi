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
that noise, and the win is cross-validated at +0.013 PCO@0.1 (95% CI
[+0.006, +0.020], 14 songs of 20).
"""

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
        if char in _VOWELS:
            return VOWEL
        if char in _PLOSIVES:
            return PLOSIVE
        if char in _FRICATIVES:
            return FRICATIVE
        if char in _SONORANTS:
            return SONORANT
        if char.isalpha():
            # A letter outside the tables (Greek, Cyrillic, kana): known to be
            # a word, unknown to this classifier. None, not a wrong bucket.
            return None
    return None
