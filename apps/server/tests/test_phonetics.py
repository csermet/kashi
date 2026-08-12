"""Word-initial sound classes (Faz 9 P2).

Four broad buckets, because English spelling is not phonemic and a finer table
would be measuring orthography. What matters here is that the buckets are
stable and that an unknown script says "unknown" instead of guessing.
"""

from kashi_server.pipeline.phonetics import (
    FRICATIVE,
    PLOSIVE,
    SONORANT,
    VOWEL,
    initial_class,
)


def test_the_four_classes():
    assert initial_class("apple") == VOWEL
    assert initial_class("baby") == PLOSIVE
    assert initial_class("sing") == FRICATIVE
    assert initial_class("moon") == SONORANT


def test_case_does_not_decide_a_class():
    assert initial_class("Apple") == initial_class("apple")
    assert initial_class("İZMİR".lower()) == VOWEL


def test_leading_punctuation_is_skipped():
    """The aligner is handed text as written: quotes, brackets and the ♪ the
    lyrics carry all sit in front of real words."""
    assert initial_class('("Hello') == FRICATIVE  # h
    assert initial_class("...and") == VOWEL
    assert initial_class("♪ time") == PLOSIVE  # t


def test_a_token_with_no_letters_has_no_class():
    """A bare ♪ or a bar number must fall back to the plain offset rather than
    land in whichever bucket happens to be first."""
    assert initial_class("♪") is None
    assert initial_class("123") is None
    assert initial_class("") is None


def test_turkish_letters_land_in_their_own_class():
    """Turkish carries no measured offsets yet, but a classified language must
    not fall into "unknown" over its own alphabet."""
    assert initial_class("ısırgan") == VOWEL
    assert initial_class("özlem") == VOWEL
    assert initial_class("üzgün") == VOWEL
    assert initial_class("çiçek") == PLOSIVE  # affricate: same sharp onset
    assert initial_class("şarkı") == FRICATIVE
    assert initial_class("göz") == PLOSIVE
    assert initial_class("ğ") == SONORANT


def test_an_unmapped_script_is_unknown_not_a_guess():
    """Japanese kana reach the aligner on the Faz 8 P-B3 path. Bucketing them
    by accident would apply English phonetics to a language that never
    measured any."""
    assert initial_class("うちゅう") is None
    assert initial_class("Москва") is None


def test_the_measured_english_ordering_is_representable():
    """The measurement that justifies the feature: vowel-initial words are the
    latest class (+112 ms median) and plosive-initial the earliest (+62 ms).
    These four names are the keys an operator writes in config."""
    assert {VOWEL, FRICATIVE, PLOSIVE, SONORANT} == {
        "vowel",
        "fricative",
        "plosive",
        "sonorant",
    }
