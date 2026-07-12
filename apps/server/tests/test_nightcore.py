"""Nightcore pure logic: title cleaning, factor detection, timeline rescale,
sanity gates and the ffmpeg filter formatting (all torch/IO-free)."""

from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.nightcore import (
    SLOW_DURATION_TOLERANCE_S,
    clean_title,
    detect_speed_factor,
    pick_record_for_factor,
    rescale_result,
    rubberband_filter,
    slow_duration_ok,
)


def test_clean_title_strips_markers_and_tidies():
    assert clean_title("Nightcore - Never Gonna Give You Up") == "Never Gonna Give You Up"
    assert clean_title("Never Gonna Give You Up (Nightcore)") == "Never Gonna Give You Up"
    assert clean_title("never gonna give you up sped up") == "never gonna give you up"
    assert clean_title("Song [Sped-Up Version]") == "Song [ Version]"
    assert clean_title("Song (speed up)") == "Song"
    # Upload noise is stripped too (field case: "Nightcore - X - (Lyrics)").
    cleaned = clean_title("Nightcore - We Don't Sleep At Night - (Lyrics)")
    assert cleaned == "We Don't Sleep At Night"
    assert clean_title("Nightcore - Come On Now (Lyrics)") == "Come On Now"


def test_clean_title_none_without_markers():
    # No marker → auto-detection must not run (normal songs stay untouched).
    assert clean_title("Never Gonna Give You Up") is None
    assert clean_title("TiK ToK") is None
    assert clean_title("") is None


def test_rubberband_filter_formats_six_decimals():
    # 1/1.2 = 0.8333... — float repr must never leak into the filter string.
    assert rubberband_filter(1 / 1.2) == "rubberband=tempo=0.833333:pitch=0.833333"
    assert rubberband_filter(1 / 1.25) == "rubberband=tempo=0.800000:pitch=0.800000"


def _rec(rid: int, duration: float, *, synced: bool = True, plain: bool = True) -> dict:
    rec: dict = {"id": rid, "duration": duration}
    if synced:
        rec["syncedLyrics"] = "[00:01.00] la"
    if plain:
        rec["plainLyrics"] = "la"
    return rec


def test_detect_single_candidate():
    # Original 240 s, nightcore 200 s → r = 1.2.
    got = detect_speed_factor([_rec(1, 240)], 200.0)
    assert got is not None
    r, record = got
    assert r == 1.2 and record["id"] == 1


def test_detect_rejects_out_of_range_and_empty():
    assert detect_speed_factor([_rec(1, 205)], 200.0) is None  # r=1.025 < min
    assert detect_speed_factor([_rec(1, 320)], 200.0) is None  # r=1.6 > max
    assert detect_speed_factor([], 200.0) is None
    assert detect_speed_factor([_rec(1, 240)], 0.0) is None  # defensive


def test_detect_largest_cluster_wins_and_prefers_synced():
    candidates = [
        _rec(1, 239.0, synced=False),  # cluster A (~1.2)
        _rec(2, 240.0),  # cluster A — synced, should be the record
        _rec(3, 241.0, synced=False),  # cluster A
        _rec(4, 260.0),  # lone outlier (~1.3)
    ]
    got = detect_speed_factor(candidates, 200.0)
    assert got is not None
    r, record = got
    assert abs(r - 1.2) < 0.01
    assert record["id"] == 2  # synced member preferred


def test_detect_skips_unusable_records_for_the_pick():
    instrumental = _rec(2, 240.0)
    instrumental["instrumental"] = True
    got = detect_speed_factor([_rec(1, 239.5, synced=False), instrumental], 200.0)
    assert got is not None
    _, record = got
    assert record["id"] == 1  # usable beats instrumental even without sync


def test_pick_record_for_factor_by_duration_distance():
    candidates = [_rec(1, 240.0), _rec(2, 250.0), _rec(3, 241.0, synced=False)]
    picked = pick_record_for_factor(candidates, 200.0, 1.2)  # wanted 240 s
    assert picked is not None and picked["id"] == 1
    assert pick_record_for_factor(candidates, 200.0, 1.4) is None  # wanted 280 s


def test_slow_duration_ok_edges():
    # Nightcore 200 s at r=1.2 → slowed must be ≈240 s.
    assert slow_duration_ok(240.0, 200.0, 1.2)
    assert slow_duration_ok(240.0 + SLOW_DURATION_TOLERANCE_S, 200.0, 1.2)
    assert not slow_duration_ok(240.0 + SLOW_DURATION_TOLERANCE_S + 0.01, 200.0, 1.2)
    assert not slow_duration_ok(200.0, 200.0, 1.2)  # r was wrong


def test_rescale_result_divides_and_stays_monotonic():
    result = AlignResult(
        sync="word",
        lines=[
            LineTiming(1200, 2400, "one", 0.5),
            LineTiming(2401, 2402, "two", 0.5),  # rounding collision fodder
        ],
        words_per_line=[
            [AlignedWord(1200, 1800, "one", 0.9)],
            [AlignedWord(2401, 2402, "two", 0.9)],
        ],
        quality_score=0.8,
        windowed=True,
    )
    scaled = rescale_result(result, 1.2)
    assert scaled.lines[0].start_ms == 1000  # 1200/1.2
    assert scaled.lines[0].end_ms == 2000
    assert scaled.words_per_line[0][0].end_ms == 1500
    # Monotonic after rounding: starts never go backwards, ends >= starts.
    assert scaled.lines[1].start_ms >= scaled.lines[0].start_ms
    assert scaled.lines[1].end_ms >= scaled.lines[1].start_ms
    # Provenance untouched.
    assert scaled.quality_score == 0.8 and scaled.windowed
    assert scaled.words_per_line[1][0].prob == 0.9


def test_rescale_r1_is_identity():
    result = AlignResult(
        sync="line", lines=[LineTiming(10, 20, "x", 0.1)], words_per_line=[], quality_score=0.2
    )
    assert rescale_result(result, 1.0) is result
