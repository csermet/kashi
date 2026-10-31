"""audit.score: line-level perceived misses, thresholds pinned as numbers."""

from audit import score
from audit.score import LineTiming, line_timings, song_score


def test_thresholds_are_the_roadmap_numbers():
    assert (score.NOTICE_LATE_MS, score.NOTICE_EARLY_MS, score.EAR_LAG_MS) == (220, -330, 50)


def test_noticeable_is_asymmetric_and_exclusive():
    assert not LineTiming(0, 0, 220, "ear").noticeable
    assert LineTiming(0, 0, 221, "ear").noticeable
    assert not LineTiming(0, 0, -330, "ear").noticeable
    assert LineTiming(0, 0, -331, "ear").noticeable


def test_voice_witness_needs_a_silence_and_a_return_within_1500_ms():
    def w(s, e):
        return {"text": "w", "start_ms": s, "end_ms": e}

    doc = {"lines": [
        {"text": "a", "start_ms": 0, "end_ms": 1000, "words": [w(0, 1000)]},
        {"text": "b", "start_ms": 1500, "end_ms": 2000, "words": [w(1500, 2000)]},  # gap 500
        {"text": "c", "start_ms": 2100, "end_ms": 2500, "words": [w(2100, 2500)]},  # gap 100
    ]}
    timings = line_timings(doc, None, [1200])
    assert [(t.line, t.error_ms, t.witness) for t in timings] == [(1, 300, "voice")]
    assert line_timings(doc, None, [3001]) == []            # 1501 ms away: no witness


def test_song_score_counts_late_and_early_misses():
    t = [LineTiming(0, 0, 300, "ear"), LineTiming(1, 0, -400, "voice"), LineTiming(2, 0, 10, "ear")]
    s = song_score(t, 10)
    assert (s["placed"], s["noticeable"], s["late"], s["early"]) == (3, 2, 1, 1)
    assert s["noticeable_share"] == round(2 / 3, 3) and s["median_error_ms"] == 10
    assert song_score([], 5) == {"lines": 5, "placed": 0}
