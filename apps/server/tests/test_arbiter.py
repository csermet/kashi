"""The arbiter: does a flagged line deserve to lose its words?

The rule being corrected deleted them unconditionally. Measured on 3383 lines
with ground truth, the signals that could justify that deletion are seven
times chance and nowhere near a separator — good enough to warn with, not to
destroy with. So every test here is really one assertion: **deletion carries
the burden of proof.**
"""

from kashi_server.pipeline.alignment import AlignedWord
from kashi_server.pipeline.arbiter import (
    MIN_ONSET_SUPPORT,
    ONSET_TOLERANCE_MS,
    judge_line,
    onset_support,
)


def _words(starts_ms: list[int], *, dur: int = 300) -> list[AlignedWord]:
    return [AlignedWord(s, s + dur, f"w{i}", 0.5) for i, s in enumerate(starts_ms)]


def test_audio_backing_the_words_saves_them():
    """The case the old rule got wrong: the aligner's words land on real vocal
    onsets and fill their line, but the anchor says the line is misplaced.
    That is a clock disagreement, not bad word timing — the words survive."""
    words = _words([1000, 1400, 1800, 2200])
    verdict = judge_line(words, line_span_ms=1500, onset_ms=[1000, 1400, 1800, 2200])
    assert not verdict.drop_words
    assert verdict.onset_support == 1.0
    assert "backs the words up" in verdict.reason


def test_audio_agreeing_with_the_anchor_still_deletes():
    """The other half. Words that sit nowhere near an onset AND leave most of
    their line hollow are what the old rule was built for, and it keeps them."""
    words = _words([1000, 1400, 1800], dur=50)  # 150ms of sound in a 9s line
    verdict = judge_line(words, line_span_ms=9000, onset_ms=[40_000, 41_000, 42_000])
    assert verdict.drop_words
    assert verdict.onset_support == 0.0
    assert verdict.span_coverage < 0.1


def test_one_signal_alone_is_never_enough_to_delete():
    """Either signal alone is a seven-times-chance predictor. Corroboration is
    the whole design — a single suspicious number must not destroy timings."""
    # Hollow line, but every word lands on an onset.
    onsets = [1000, 1400, 1800]
    hollow_but_supported = judge_line(_words([1000, 1400, 1800], dur=20), 9000, onsets)
    assert not hollow_but_supported.drop_words
    # Well-filled line, but no onset supports it.
    filled_but_unsupported = judge_line(_words([1000, 1400, 1800], dur=400), 1500, [50_000])
    assert not filled_but_unsupported.drop_words


def test_too_few_words_falls_back_to_todays_behaviour():
    """Neither signal means anything on a two-word line, and no new behaviour
    is introduced where there is no new evidence."""
    verdict = judge_line(_words([1000, 1400]), 800, [1000, 1400])
    assert verdict.drop_words  # unchanged from the pre-arbiter rule


def test_missing_onsets_leave_coverage_to_rule_alone():
    """librosa unavailable or audio unreadable: the arbiter must still decide,
    and it only rescues the unambiguous case."""
    filled = judge_line(_words([1000, 1400, 1800], dur=400), 1500, None)
    assert not filled.drop_words and filled.onset_support is None
    hollow = judge_line(_words([1000, 1400, 1800], dur=20), 9000, None)
    assert hollow.drop_words and hollow.onset_support is None


def test_onset_support_tolerates_the_consonant_offset():
    """In singing a note onset lands on the syllable's VOWEL, so a word start
    sits early of it by the leading consonant. The tolerance absorbs that bias
    rather than punishing every consonant-initial word."""
    onsets = [1000, 2000]
    early = _words([1000 - ONSET_TOLERANCE_MS + 20, 2000 - ONSET_TOLERANCE_MS + 20])
    assert onset_support(early, onsets) == 1.0
    far = _words([1000 - ONSET_TOLERANCE_MS - 50, 2000 - ONSET_TOLERANCE_MS - 50])
    assert onset_support(far, onsets) == 0.0


def test_onset_support_is_none_when_unmeasurable():
    """An unmeasurable signal must never read as a bad one."""
    assert onset_support(_words([1000]), None) is None
    assert onset_support(_words([1000]), []) is None
    assert onset_support([], [1000]) is None


def test_partial_support_lands_on_the_measured_operating_point():
    """The threshold is not a round number pulled from the air: it sits where
    the line-level sweep put the useful operating point."""
    onsets = [1000, 2000, 3000]
    # Two of three words supported — above the bar, so no deletion.
    ok = judge_line(_words([1000, 2000, 50_000], dur=20), 9000, onsets)
    assert ok.onset_support is not None and ok.onset_support > MIN_ONSET_SUPPORT
    assert not ok.drop_words
    # One of four — below the bar, and the line is hollow too.
    bad = judge_line(_words([1000, 50_000, 51_000, 52_000], dur=20), 9000, onsets)
    assert bad.onset_support is not None and bad.onset_support < MIN_ONSET_SUPPORT
    assert bad.drop_words
