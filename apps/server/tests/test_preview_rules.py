"""The preview's measures and verdict (worker/preview_rules.py): every threshold exactly on its
value and one millisecond (or one step) either side, the witness constants pinned to the audit
voice witness, and the envelope witness checked against the audit code itself."""

import random

import pytest

from kashi_server.pipeline.level import Envelope
from kashi_server.worker import preview_rules as r

VOICED, QUIET = 50, 51  # -25.0 dB is at the silence line (voiced), -25.5 dB under it


def _line(start, words=None, end=None, text="la la"):
    """A line from its word starts (each word ends where the next starts, the last 300 ms on)."""
    line = {"start_ms": start, "text": text}
    if words:
        line["words"] = [
            {"start_ms": w, "end_ms": (words[k + 1] if k + 1 < len(words) else w + 300),
             "text": "w"}
            for k, w in enumerate(words)
        ]
        line["end_ms"] = end if end is not None else words[-1] + 300
    else:
        line["end_ms"] = end if end is not None else start + 1000
    return line


def _doc(lines, version="2.34.0", **alignment):
    return {"pipeline_version": version, "generated_at": "x", "sync": "word",
            "alignment": {"lyrics_source": "lrclib", "lyrics_source_id": 7, "speed_factor": 1.0,
                          **alignment},
            "lines": lines}


def _env(levels, step_ms=20):
    return Envelope(bytes(levels), step_ms=step_ms)


# --- the audit witness's constants -----------------------------------------------------------


def test_the_witness_constants_are_the_audit_voice_witness_own():
    from audit import analysis, finder

    assert r.LINE_GAP_MS == finder.LINE_GAP_MS == 500
    assert r.LINE_VOICE_MAX_MS == finder.LINE_VOICE_MAX_MS == 1500
    assert r.SILENCE_DB == analysis.SILENCE_DB == -25.0
    assert r.MIN_SILENCE_MS == analysis.MIN_SILENCE_MS == 150


def test_the_veto_thresholds_are_the_archive_rig_s():
    assert (r.RUN_STEP_MS, r.VETO_RUN_WORDS, r.VETO_AWAY_MS, r.SHORT_DWELL_MS, r.MOVED_MS) == (
        60, 4, 1500, 120, 100)


# --- counts --------------------------------------------------------------------------------------


@pytest.mark.parametrize("gap,u120,u60,zero", [
    (121, 0, 0, 0), (120, 0, 0, 0), (119, 1, 0, 0), (61, 1, 0, 0), (60, 1, 0, 0), (59, 1, 1, 0),
    (1, 1, 1, 0), (0, 1, 1, 1), (-1, 1, 1, 1),
])
def test_dwell_counts_on_their_thresholds(gap, u120, u60, zero):
    s = r.shape(_doc([_line(1000, [1000, 1000 + gap])]))
    assert (s["dwell_under_120"], s["dwell_under_60"], s["dwell_0"]) == (u120, u60, zero)
    assert s["disorder"] == (gap < 0)


def test_the_last_word_of_a_line_has_no_dwell_to_count():
    s = r.shape(_doc([_line(1000, [1000, 1500]), _line(1510, [1510, 2000])]))
    assert s["dwell_under_120"] == 0  # 1 500 -> 1 510 crosses a line


@pytest.mark.parametrize("end,overlap", [(1999, 0), (2000, 0), (2001, 1)])
def test_a_line_ending_after_the_next_starts(end, overlap):
    s = r.shape(_doc([_line(1000, [1000, 1200], end=end), _line(2000, [2000, 2300])]))
    assert s["overlap"] == overlap


def test_disorder_reaches_across_lines_and_lines_are_counted():
    s = r.shape(_doc([_line(1000, [1000, 2000]), _line(1999, [1999, 2500]), _line(3000)]))
    assert (s["disorder"], s["lines"], s["worded"], s["wordless"], s["words"]) == (1, 3, 2, 1, 4)


# --- the sheet ----------------------------------------------------------------------------------


def test_the_same_sheet():
    assert r.sheet_changes(_doc([_line(1000, [1000])]), _doc([_line(9000, [9000])])) == []


@pytest.mark.parametrize("field,value", [("lyrics_source", "caller"), ("lyrics_source_id", 8),
                                          ("speed_factor", 1.25)])
def test_another_record_source_or_clock_is_another_sheet(field, value):
    old, new = _doc([_line(1000, [1000])]), _doc([_line(1000, [1000])], **{field: value})
    assert len(r.sheet_changes(old, new)) == 1
    comparison = r.compare(old, new, None)
    assert r.verdict(comparison)[0] == r.SHEET
    assert "moved" not in comparison  # never judged by the timing measures


def test_a_line_count_or_a_line_text_is_another_sheet():
    old = _doc([_line(1000, [1000]), _line(2000, [2000])])
    assert r.sheet_changes(old, _doc([_line(1000, [1000])])) == ["line count 2 -> 1"]
    longer = _doc([_line(1000, [1000]), _line(2000, [2000]), _line(3000, [3000])])
    assert r.sheet_changes(old, longer) == ["line count 2 -> 3"]
    other = _doc([_line(1000, [1000]), _line(2000, [2000], text="la lo")])
    assert r.sheet_changes(old, other) == ["text of 1 line(s), first line 1"]
    assert r.verdict(r.compare(old, other, None)) == (r.SHEET, "text of 1 line(s), first line 1")


# --- W1 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("step,run", [(59, True), (60, False), (61, False)])
def test_a_run_needs_each_word_under_60_ms_after_the_last(step, run):
    words = [{"start_ms": 1000 + k * step} for k in range(3)]
    assert r.runs(words) == ([(0, 2)] if run else [])


@pytest.mark.parametrize("n,veto", [(3, False), (4, True), (5, True)])
def test_w1_vetoes_a_new_run_of_four_words(n, veto):
    old = _doc([_line(1000, [1000 + 200 * k for k in range(6)])])
    new = _doc([_line(1000, [1000 + 10 * k for k in range(n)]
                      + [3000 + 200 * k for k in range(6 - n)])])
    comparison = r.compare(old, new, None)
    assert comparison["w1"]["words"] == n
    assert ("W1" in r.vetoes(comparison)) is veto


def test_w1_a_run_the_stored_line_already_had_is_not_new():
    words = [1000, 1010, 1020, 1030, 1040, 2000]
    old = _doc([_line(1000, words)])
    same = r.compare(old, _doc([_line(1000, words)], version="2.35.0"), None)
    assert same["w1"]["words"] == 0
    inside = r.compare(old, _doc([_line(1000, [1000, 1010, 1020, 1030, 1500, 2000])]), None)
    assert inside["w1"]["words"] == 0  # 0-3 lies inside the stored run 0-4
    longer = r.compare(old, _doc([_line(1000, [1000, 1010, 1020, 1030, 1040, 1050])]), None)
    assert longer["w1"]["words"] == 6


def test_w1_without_a_word_pairing_every_run_is_new():
    old = _doc([_line(1000, [1000, 1010, 1020, 1030, 1040])])  # five words
    new = _doc([_line(1000, [1000, 1010, 1020, 1030])])  # four: no pairing
    assert r.compare(old, new, None)["w1"]["words"] == 4
    wordless = _doc([_line(1000)])
    assert r.compare(wordless, new, None)["w1"]["words"] == 4


# --- W3 -----------------------------------------------------------------------------------------


def test_w3_a_line_that_lost_its_words():
    old = _doc([_line(1000, [1000, 1500]), _line(3000, [3000, 3500])])
    new = _doc([_line(1000, [1000, 1500]), _line(3000)])
    comparison = r.compare(old, new, None)
    assert comparison["w3"] == [1] and r.vetoes(comparison) == ["W3"]
    back = r.compare(new, old, None)
    assert back["w3"] == [] and back["gained_words"] == [1] and r.vetoes(back) == []


def test_w3_a_line_wordless_on_both_sides_lost_nothing():
    old = _doc([_line(1000, [1000, 1500]), _line(3000)])
    comparison = r.compare(old, _doc([_line(1000, [1000, 1500]), _line(3000)]), None)
    assert comparison["w3"] == [] and comparison["gained_words"] == []


def test_w3_a_line_without_text_cannot_lose_words():
    old = _doc([_line(1000, [1000], text=" ")])
    assert r.compare(old, _doc([_line(1000, text=" ")]), None)["w3"] == []


# --- moved lines, W4 ------------------------------------------------------------------------------


@pytest.mark.parametrize("d,moved", [(99, 0), (100, 0), (101, 1), (-100, 0), (-101, 1)])
def test_a_line_moves_past_100_ms(d, moved):
    old = _doc([_line(10_000, [10_000, 10_500])])
    new = _doc([_line(10_000 + d, [10_000 + d, 10_500])])
    assert r.compare(old, new, None)["moved"]["over_100"] == moved


@pytest.mark.parametrize("d,over_500,over_1500", [(500, 0, 0), (501, 1, 0), (1500, 1, 0),
                                                   (1501, 1, 1), (-1501, 1, 1)])
def test_moves_past_500_and_1500_ms_and_the_largest(d, over_500, over_1500):
    old = _doc([_line(10_000, [10_000, 12_000]), _line(20_000, [20_000, 22_000])])
    new = _doc([_line(10_000 + d, [10_000 + d, 12_000]), _line(20_200, [20_200, 22_000])])
    moved = r.compare(old, new, None)["moved"]
    assert (moved["over_500"], moved["over_1500"]) == (over_500, over_1500)
    assert moved["largest"]["line"] == 0 and moved["largest"]["d_ms"] == d


@pytest.mark.parametrize("new_dwell,old_dwell,veto", [
    (119, 120, True), (120, 120, False), (119, 119, False), (119, 121, True), (121, 500, False),
])
def test_w4_a_moved_first_word_that_loses_its_dwell(new_dwell, old_dwell, veto):
    old = _doc([_line(10_000, [10_000, 10_000 + old_dwell, 11_000])])
    new = _doc([_line(10_500, [10_500, 10_500 + new_dwell, 11_000])])
    assert ("W4" in r.vetoes(r.compare(old, new, None))) is veto


def test_w4_only_on_a_moved_line():
    old = _doc([_line(10_000, [10_000, 10_200, 11_000])])
    new = _doc([_line(10_100, [10_100, 10_200, 11_000])])  # 100 ms: not moved
    assert r.compare(old, new, None)["w4"] == []


# --- the envelope: silent lead, the voice's return, W2 -------------------------------------------


@pytest.mark.parametrize("level,voiced", [(49, True), (50, True), (51, False)])
def test_the_silence_line_is_minus_25_db(level, voiced):
    assert r.voiced(_env([level]), 0) is voiced


@pytest.mark.parametrize("t,lead", [(0, 60), (1, 59), (59, 1), (60, 0), (61, None)])
def test_silent_lead_reaches_the_first_voiced_frame_starting_at_or_after_the_start(t, lead):
    env = _env([QUIET, QUIET, QUIET, VOICED], step_ms=20)  # the voice in the frame at 60 ms
    assert r.silent_lead(env, t) == lead


@pytest.mark.parametrize("quiet,returns", [(149, False), (150, True), (151, True)])
def test_the_voice_returns_after_150_ms_of_silence(quiet, returns):
    env = _env([VOICED] * 10 + [QUIET] * quiet + [VOICED] * 10, step_ms=1)
    assert (r.voice_returns(env) == [10 + quiet]) is returns


def test_the_track_starts_in_silence():
    assert r.voice_returns(_env([QUIET] * 150 + [VOICED], step_ms=1)) == [150]
    assert r.voice_returns(_env([QUIET] * 149 + [VOICED], step_ms=1)) == []


@pytest.mark.parametrize("gap,placed", [(499, False), (500, True), (501, True)])
def test_a_line_is_placed_after_500_ms_of_document_silence(gap, placed):
    doc = _doc([_line(1000, [1000, 1500], end=2000), _line(2000 + gap, [2000 + gap, 3000])])
    assert (1 in r.placed_on_voice(doc, [2000 + gap])) is placed
    assert 0 not in r.placed_on_voice(doc, [1000])  # the first line has nothing before it


@pytest.mark.parametrize("far,placed", [(1499, True), (1500, True), (1501, False)])
def test_a_line_is_placed_on_a_return_within_1500_ms(far, placed):
    doc = _doc([_line(1000, [1000], end=1300), _line(5000, [5000, 6000])])
    assert (r.placed_on_voice(doc, [5000 + far]) == {1: 5000 + far}) is placed
    assert (r.placed_on_voice(doc, [5000 - far]) == {1: 5000 - far}) is placed


def test_on_a_tie_the_earlier_return():
    doc = _doc([_line(1000, [1000], end=1300), _line(5000, [5000, 6000])])
    assert r.placed_on_voice(doc, [4900, 5100]) == {1: 4900}


def test_a_wordless_line_keeps_the_silence_open_but_its_end_counts():
    doc = _doc([_line(1000, [1000], end=1300), _line(1400, end=4800), _line(5000, [5000])])
    assert r.placed_on_voice(doc, [5000]) == {}  # 5 000 - 4 800 < 500


def test_a_wordless_line_ending_early_does_not_shorten_the_stretch():
    doc = _doc([_line(1000, [1000], end=4800), _line(1400, end=2000), _line(5000, [5000])])
    assert r.placed_on_voice(doc, [5000]) == {}  # 5 000 - 4 800 < 500, the wordless 2 000 aside


def _w2(away_old, away_new):
    """Line 1 placed on a voice return at 5 000 ms on the stored document; its start moves from
    5 000 - away_old to 5 000 - away_new (both earlier than the voice)."""
    levels = [QUIET] * 250 + [VOICED] * 50 + [QUIET] * 200 + [VOICED] * 100  # returns at 5 000
    env = _env(levels)
    old = _doc([_line(1000, [1000], end=1300), _line(5000 - away_old, [5000 - away_old, 6500])])
    new = _doc([_line(1000, [1000], end=1300), _line(5000 - away_new, [5000 - away_new, 6500])])
    return r.compare(old, new, env)


@pytest.mark.parametrize("extra,veto", [(1499, False), (1500, False), (1501, True)])
def test_w2_a_line_start_moved_more_than_1500_ms_farther_from_the_voice(extra, veto):
    comparison = _w2(0, extra)
    assert comparison["w2"]["away_ms"] == extra
    assert comparison["w2"]["voice_ms"] == 5000
    assert ("W2" in r.vetoes(comparison)) is veto


def test_w2_a_move_toward_the_voice_is_no_veto_and_its_lead_shrinks():
    comparison = _w2(3000, 0)
    assert comparison["w2"]["away_ms"] == 0 and r.vetoes(comparison) == []
    (row,) = comparison["moved_lines"]
    assert (row["lead_old_ms"], row["lead_new_ms"]) == (3000, 0)


def test_w2_reads_the_reference_on_the_new_document_when_the_old_is_not_placed():
    levels = [QUIET] * 250 + [VOICED] * 300
    env = _env(levels)  # returns at 5 000
    old = _doc([_line(1000, [1000], end=4700), _line(4800, [4800, 6500])])  # 100 ms gap: no
    new = _doc([_line(1000, [1000], end=1300), _line(3600, [3600, 6500])])  # placed at 5 000
    comparison = r.compare(old, new, env)
    assert comparison["w2"]["away_ms"] == 1400 - 200
    assert comparison["voice"]["placed_old"] == 0 and comparison["voice"]["placed_new"] == 1


def test_w2_the_reference_is_the_stored_document_s_when_both_are_placed():
    levels = [QUIET] * 250 + [VOICED] * 50 + [QUIET] * 150 + [VOICED] * 100  # 5 000 and 9 000
    env = _env(levels)
    old = _doc([_line(1000, [1000], end=1300), _line(5000, [5000, 5600])])
    new = _doc([_line(1000, [1000], end=1300), _line(9000, [9000, 9600])])
    comparison = r.compare(old, new, env)
    assert (comparison["voice"]["placed_old"], comparison["voice"]["placed_new"]) == (1, 1)
    assert comparison["w2"]["voice_ms"] == 5000 and comparison["w2"]["away_ms"] == 4000
    assert "W2" in r.vetoes(comparison)


def test_without_an_envelope_w2_cannot_fire():
    comparison = r.compare(_doc([_line(0, [0]), _line(5000, [5000])]),
                           _doc([_line(0, [0]), _line(9000, [9000])]), None)
    assert comparison["w2"] == {"away_ms": 0} and comparison["voice"]["envelope"] is False
    assert "lead_old_ms" not in comparison["moved_lines"][0]


# --- the envelope witness IS the audit voice witness on the envelope's contour --------------------


def test_voice_returns_equal_the_audit_witness_on_the_same_frames():
    from audit.analysis import voice_onsets

    rng = random.Random(20261116)
    for _ in range(200):
        step = rng.choice([1, 5, 10, 20])
        levels = []
        while len(levels) < 400:
            levels += [rng.choice([0, 30, 49, 50, 51, 52, 90, 255])] * rng.randint(1, 40)
        env = _env(levels[:400], step_ms=step)
        frames = [(k * step, env.db(k)) for k in range(len(env.levels))]
        assert r.voice_returns(env) == voice_onsets(frames, float(step))


def test_line_placement_equals_the_audit_witness():
    from audit.score import line_timings

    rng = random.Random(1116)
    for _ in range(300):
        lines, t = [], rng.randint(0, 3000)
        for _ in range(rng.randint(1, 12)):
            if rng.random() < 0.2:
                lines.append(_line(t, end=t + rng.randint(0, 3000)))
            else:
                n = rng.randint(1, 4)
                starts = sorted(t + rng.randint(0, 2000) for _ in range(n))
                lines.append(_line(starts[0], starts, end=starts[-1] + rng.randint(0, 900)))
            t = lines[-1]["end_ms"] + rng.choice([0, 100, 499, 500, 501, 1500, 4000])
        returns = sorted({rng.randint(0, t + 2000) for _ in range(rng.randint(0, 12))})
        doc = _doc(lines)
        audit = {x.line: x.doc_ms - x.error_ms for x in line_timings(doc, None, returns)}
        assert r.placed_on_voice(doc, returns) == audit


# --- the verdict --------------------------------------------------------------------------------


def test_a_rebuild_that_changes_only_its_stamps_is_unchanged():
    old = _doc([_line(1000, [1000, 1500])])
    new = {**old, "pipeline_version": "2.35.0", "generated_at": "y"}
    assert r.verdict(r.compare(old, new, None)) == (r.UNCHANGED, "")
    moved = _doc([_line(1200, [1200, 1500])], version="2.35.0")
    assert r.verdict(r.compare(old, moved, None)) == (r.REFRESH, "")
    other_beats = {**new, "beats": {"bpm": 1}}
    comparison = r.compare(old, other_beats, None)
    assert comparison["differs"] == ["beats"] and r.verdict(comparison)[0] == r.REFRESH


def test_the_verdict_order_guard_sheet_veto():
    old = _doc([_line(1000, [1000, 1500]), _line(3000, [3000, 3500])])
    lost = _doc([_line(1000, [1000, 1500]), _line(3000)])
    comparison = r.compare(old, lost, None)
    assert r.verdict(comparison) == (r.VETOED, "veto: W3")
    assert r.verdict(comparison, ("regression", "worded lines 2 -> 1")) == (
        r.GUARDED, "guard: regression: worded lines 2 -> 1")
    other = r.compare(old, _doc([_line(1000, [1000, 1500])]), None)
    assert r.verdict(other) == (r.SHEET, "line count 2 -> 1")
    assert r.verdict(other, ("stale", "x"))[0] == r.GUARDED


def test_every_veto_is_named_in_the_detail():
    old = _doc([_line(1000, [1000 + 200 * k for k in range(5)]), _line(5000, [5000, 5500])])
    new = _doc([_line(1000, [1000 + 10 * k for k in range(5)]), _line(5000)])
    assert r.verdict(r.compare(old, new, None)) == (r.VETOED, "veto: W1, W3")


# --- round 2: what W2 cannot judge, the palette, older documents ----------------------------------


@pytest.mark.parametrize("d,unjudged", [(1500, []), (1501, [1]), (-1500, []), (-1501, [1])])
def test_a_move_past_1500_ms_without_a_stored_reference_is_unjudged(d, unjudged):
    old = _doc([_line(1000, [1000], end=1300), _line(5000, [5000, 5600])])  # no envelope: unplaced
    new = _doc([_line(1000, [1000], end=1300), _line(5000 + d, [5000 + d, 5600 + d])])
    comparison = r.compare(old, new, None)
    assert comparison["w2_unjudged"] == unjudged
    assert r.verdict(comparison)[0] == (r.REVIEW if unjudged else r.REFRESH)


def test_a_line_the_stored_document_places_is_judged_not_reviewed():
    comparison = _w2(0, 1499)  # placed on the stored document, 1 499 ms farther: no veto
    assert comparison["w2_unjudged"] == [] and r.verdict(comparison)[0] == r.REFRESH
    reviewed = _w2(1600, 0)  # placed only on the new document (moved toward the voice)
    assert reviewed["w2_unjudged"] == [1]
    assert r.verdict(reviewed) == (r.REVIEW, "line(s) 1 moved > 1500 ms where W2 cannot judge")


def test_the_first_line_is_never_judged_by_w2():
    old = _doc([_line(1000, [1000, 1500]), _line(9000, [9000])])
    new = _doc([_line(3000, [3000, 3500]), _line(9000, [9000])])
    assert r.compare(old, new, _env([0] * 600))["w2_unjudged"] == [0]


@pytest.mark.parametrize("old_source,new_source,review", [
    ("album_art", "default", True), ("default", "default", False), (None, "default", False),
    ("album_art", "album_art", False),
])
def test_a_palette_falling_back_to_the_default_is_review(old_source, new_source, review):
    old = _doc([_line(1000, [1000])])
    if old_source:
        old["palette"] = {"source": old_source, "primary": "#000001"}
    new = {**old, "pipeline_version": "2.35.0",
           "palette": {"source": new_source, "primary": "#e84545"}}
    comparison = r.compare(old, new, None)
    assert comparison["palette_falls_back"] is review
    expected = (r.REVIEW, "palette would fall back to the default") if review else (r.REFRESH, "")
    assert r.verdict(comparison) == expected


def test_a_document_older_than_the_speed_factor_reads_as_one():
    old = _doc([_line(1000, [1000])])
    del old["alignment"]["speed_factor"]
    assert r.sheet_changes(old, _doc([_line(1000, [1000])])) == []
    assert r.sheet_changes(old, _doc([_line(1000, [1000])], speed_factor=1.25)) == [
        "speed factor None -> 1.25"]


def test_inline_tags_stripped_are_the_same_line():
    old = _doc([_line(1000, [1000], text="<00:01.00>la <00:01.50>la")])
    assert r.sheet_changes(old, _doc([_line(1000, [1000], text="la la")])) == []
    assert r.sheet_changes(old, _doc([_line(1000, [1000], text="la lo")])) == [
        "text of 1 line(s), first line 0"]
    tagged_new = _doc([_line(1000, [1000], text="<00:01.00>la la")])
    assert r.sheet_changes(_doc([_line(1000, [1000], text="la la")]), tagged_new) == [
        "text of 1 line(s), first line 0"]  # only the stored side is stripped


def test_the_review_comes_after_the_vetoes_and_before_refresh():
    old = _doc([_line(1000, [1000 + 200 * k for k in range(5)]), _line(5000, [5000, 5500])])
    piled = _doc([_line(1000, [1000 + 10 * k for k in range(5)]), _line(7000, [7000, 7500])])
    assert r.verdict(r.compare(old, piled, None)) == (r.VETOED, "veto: W1")
    moved = _doc([_line(1000, [1000 + 200 * k for k in range(5)]), _line(7000, [7000, 7500])])
    assert r.verdict(r.compare(old, moved, None))[0] == r.REVIEW
