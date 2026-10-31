"""Perceptual audit (apps/server/audit): the pure pieces.

Thresholds are Faz 0 guesses; they are pinned here only where the LOGIC is the
point (which side of the boundary counts), and get their calibrated values in
Faz 2.
"""

from audit.analysis import voice_onsets
from audit.ear import checkpoint_for, letters_text, timed_tokens
from audit.moment import (
    EVENT_LAG_MS,
    LEAD_IN_PROBE_MS,
    STRIP_MAX_FRAMES,
    STRIP_MIN_FRAMES,
    strip_times,
    word_flags,
)

VOCAB = {0: "<pad>", 1: "d", 2: "o", 3: "n", 4: "|", 5: "t"}


def test_timed_tokens_collapses_repeats_then_blanks_and_keeps_time():
    # d d _ o o | _ n n -> "do n"; a run spans its first to last frame
    ids = [1, 1, 0, 2, 2, 4, 0, 3, 3]
    out = timed_tokens(ids, VOCAB, 0, 20.0, 1000)
    assert [t["text"] for t in out] == ["d", "o", "|", "n"]
    assert out[0] == {"text": "d", "start_ms": 1000, "end_ms": 1040}
    assert out[1] == {"text": "o", "start_ms": 1060, "end_ms": 1100}
    assert out[3] == {"text": "n", "start_ms": 1140, "end_ms": 1180}
    assert letters_text(out) == "do n"


def test_timed_tokens_double_letter_needs_a_blank_between():
    assert [t["text"] for t in timed_tokens([5, 5], VOCAB, 0, 20.0, 0)] == ["t"]
    assert [t["text"] for t in timed_tokens([5, 0, 5], VOCAB, 0, 20.0, 0)] == ["t", "t"]


def test_timed_tokens_uses_the_exact_stride():
    # 100 frames at 20.03 ms is 2003 ms; a rounded 20 ms step would say 2000
    out = timed_tokens([0] * 100 + [1], VOCAB, 0, 20.03, 0)
    assert out[0]["start_ms"] == 2003


def test_checkpoint_for_reads_alignment_method():
    m = "ctc-forced-aligner/wav2vec2-xls-r-1b-english+line-windowed"
    assert checkpoint_for(m) == "jonatasgrosman/wav2vec2-xls-r-1b-english"


def _contour(levels, hop=10):
    return [(i * hop, db) for i, db in enumerate(levels)]


def test_voice_onset_needs_a_long_enough_silence():
    # 150 ms of silence (quiet from t=10 to t=160) counts; 140 ms does not.
    # Voice at t=0 has no silence before it, so it is no onset.
    loud, quiet = -5.0, -40.0
    long_gap = _contour([loud] + [quiet] * 15 + [loud])
    assert voice_onsets(long_gap, 10.0, -25.0, 150) == [160]
    short_gap = _contour([loud] + [quiet] * 14 + [loud])
    assert voice_onsets(short_gap, 10.0, -25.0, 150) == []


def test_voice_onset_threshold_is_inclusive():
    # exactly the threshold counts as voice
    assert voice_onsets(_contour([-40.0] * 20 + [-25.0]), 10.0, -25.0, 150) == [200]


def test_word_flags_zero_short_pile():
    words = [
        {"text": "a", "start_ms": 1000, "end_ms": 1000},  # zero, piles with b
        {"text": "b", "start_ms": 1060, "end_ms": 1119},  # short (59), 60 from a -> pile
        {"text": "c", "start_ms": 1121, "end_ms": 1181},  # 60 ms: not short; 61 from b
        {"text": "d", "start_ms": 1300, "end_ms": 1500},
    ]
    assert word_flags(words) == [["zero", "pile"], ["short", "pile"], [], []]


def _doc(words):
    return {"lines": [{"text": "x", "start_ms": words[0][0], "end_ms": words[-1][1],
                       "words": [{"text": "w", "start_ms": s, "end_ms": e} for s, e in words]}]}


def test_strip_times_takes_word_events_and_lead_in_then_fills_a_grid():
    # centre 10_010: the fill grid (8_760 + k*250) never lands on an event
    doc = _doc([(10_200, 10_500), (10_600, 10_900)])
    times = strip_times(doc, 10_010, 200)
    assert 10_200 - 200 + EVENT_LAG_MS in times
    assert 10_600 - 200 + EVENT_LAG_MS in times
    assert 10_200 - 200 - LEAD_IN_PROBE_MS in times
    assert len(times) >= STRIP_MIN_FRAMES
    assert times == sorted(times)


def test_strip_times_caps_dense_windows():
    doc = _doc([(9_000 + 100 * i, 9_050 + 100 * i) for i in range(25)])
    assert len(strip_times(doc, 10_000, 200)) == STRIP_MAX_FRAMES


def test_strip_times_merges_piled_words_into_one_frame():
    doc = _doc([(10_000, 10_000), (10_000, 10_000), (10_010, 10_400)])
    events = [t for t in strip_times(doc, 10_000, 200) if 9_800 <= t <= 9_850]
    assert events == [9_830]
