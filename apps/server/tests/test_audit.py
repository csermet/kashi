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
    mms = "MahmoudAshraf/mms-300m-1130-forced-aligner"
    assert checkpoint_for("ctc-forced-aligner/mms-300m+line-windowed") == mms


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


def test_syllables_counts_vowel_groups_with_a_silent_final_e():
    from audit.syllables import doc_language, line_syllables, syllables

    cases = {"It's": 1, "so": 1, "crazy": 2, "right": 1, "now": 1, "love": 1, "make": 1,
             "the": 1, "be": 1, "little": 2, "remedy": 3, "gettin'": 2, "(woo!": 1, "Hova?": 2,
             "agree": 2, "goodbye": 2, "...": 0}
    assert {w: syllables(w) for w in cases} == cases
    assert line_syllables(["It's", "so", "crazy", "right", "now"]) == 6
    turkish = {"kalpte": 2, "aksesuar": 4, "acısı": 3, "da": 1}
    assert {w: syllables(w, "tur") for w in turkish} == turkish
    assert syllables("bueno", "other") == 2 and syllables("kalpte", "other") == 2
    method = "ctc-forced-aligner/wav2vec2-xls-r-300m-cv7-turkish+line-windowed"
    assert doc_language({"alignment": {"method": method}}) == "tur"
    assert doc_language({"alignment": {"method": "x/wav2vec2-xls-r-1b-english"}}) == "eng"
    assert doc_language({}) == "other"


def test_loudness_bumps_need_a_6_db_dip_on_both_sides():
    from audit.analysis import loudness_bumps

    def contour(levels):
        return [(i * 10, db) for i, db in enumerate(levels)]

    # the second, lower peak (-6) counts only when the dip before it is 6 dB under it
    deep = [-30.0] * 5 + [-5.0] * 5 + [-12.0] * 5 + [-6.0] * 5 + [-30.0] * 5
    assert len(loudness_bumps(contour(deep), 10.0, 0, 240, gap_ms=10, smooth=1)) == 2
    shallow = [-30.0] * 5 + [-5.0] * 5 + [-11.9] * 5 + [-6.0] * 5 + [-30.0] * 5
    assert len(loudness_bumps(contour(shallow), 10.0, 0, 240, gap_ms=10, smooth=1)) == 1
    quiet = [-40.0] * 5 + [-26.0] * 5 + [-40.0] * 5
    assert loudness_bumps(contour(quiet), 10.0, 0, 140, gap_ms=10, smooth=1) == []


def test_line_numbers_report_whether_bumps_can_be_mapped_in_order():
    from audit.moment import line_numbers

    line = {"text": "It's so crazy", "start_ms": 1000, "end_ms": 2000,
            "words": [{"text": "It's", "start_ms": 1000, "end_ms": 1200},
                      {"text": "so", "start_ms": 1300, "end_ms": 1500},
                      {"text": "crazy", "start_ms": 1600, "end_ms": 2000}]}
    four = line_numbers(line, 200, None, None, None, [1000, 1300, 1600, 1800])
    assert (four["syllables"], four["bump_mapping"]) == (4, "1:1")
    assert line_numbers(line, 200, None, None, None, [1000, 1300])["bump_mapping"] == "fewer bumps"
    assert line_numbers(line, 200, None, None, None, [1] * 5)["bump_mapping"] == "more bumps"
    assert "bump_mapping" not in line_numbers(line, 200, None, None, None)
