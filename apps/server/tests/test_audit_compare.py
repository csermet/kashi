"""audit.compare: before/after documents against moment verdicts. Synthetic
documents only (no real ids or lyrics); every rule on both sides of its
boundary, values pinned as numbers."""

import copy
import json

import pytest

from audit import compare
from audit.compare import (
    classify,
    moment_diff,
    pair_documents,
    same_within,
    song_numbers,
)
from audit.score import LineTiming

VID = "synthVid001"


def _w(text, s, dur=300):
    return {"text": text, "start_ms": s, "end_ms": s + dur}


def _line(text, starts, dur=300):
    words = [_w(t, s, dur) for t, s in zip(text.split(), starts, strict=True)]
    return {"text": text, "start_ms": starts[0], "end_ms": starts[-1] + dur, "score": 0.9,
            "words": words}


def _doc(pv="2.29.0", lines=None, qa=None):
    return {
        "pipeline_version": pv,
        "sync": "word",
        "track": {"source": {"type": "youtube", "id": VID}},
        "alignment": {"method": "synthetic", "quality_score": 0.8, "qa": qa or {}},
        "lines": lines if lines is not None else [
            _line("alpha beta gamma", [1000, 1500, 2000]),
            _line("delta epsilon zeta", [6000, 6500, 7000]),
            _line("eta theta iota", [11000, 11500, 12000]),
        ],
    }


def _moved(doc, line, delta, words=None):
    """A copy with `line`'s words (all, or the given indices) moved by delta."""
    out = copy.deepcopy(doc)
    ln = out["lines"][line]
    for i, w in enumerate(ln["words"]):
        if words is None or i in words:
            w["start_ms"] += delta
            w["end_ms"] += delta
    ln["start_ms"] = ln["words"][0]["start_ms"]
    ln["end_ms"] = max(ln["end_ms"], ln["words"][-1]["end_ms"])
    return out


def _class(verdict, before, after, t=6000):
    return classify(verdict, moment_diff(before, after, t))


def test_thresholds_are_pinned_numbers():
    assert compare.UNCHANGED_MS == 20      # one model frame
    assert compare.WINDOW_MS == 2500       # the picture's +-2.5 s
    assert compare.VERDICTS_DESCRIBE == "2.29"
    assert compare.BAD == {"pile", "late", "early", "missing", "wrong_text"}
    assert compare.GOOD == {"ok"}
    assert compare.AGREEING == {"none", "either"}
    assert compare.REWORDED_SIMILARITY == 0.6


def test_unequal_replaced_runs_pair_alike_items_in_order():
    # "abcde" vs "abcxy": ratio 0.6 pairs, "abcde" vs "abxyz": 0.4 does not
    assert compare._pair(["q", "abcde", "z"], ["q", "abcxy", "new", "z"]) == \
        [(0, 0), (1, 1), (None, 2), (2, 3)]
    assert compare._pair(["q", "abcde", "z"], ["q", "abxyz", "new", "z"]) == \
        [(0, 0), (1, None), (None, 1), (None, 2), (2, 3)]


# --- pairing ------------------------------------------------------------------

def test_a_reworded_line_keeps_its_slot_and_an_inserted_line_is_unpaired():
    before = _doc()
    after = copy.deepcopy(before)
    after["lines"][1]["text"] = "delta epsilon ZETA!"
    after["lines"].insert(2, _line("new words here", [9000, 9300, 9600]))
    lines, _ = pair_documents(before, after)
    assert [(p.before is not None, p.after is not None) for p in lines] == \
        [(True, True), (True, True), (False, True), (True, True)]
    assert [p.text_changed for p in lines] == [False, True, False, False]


def test_words_pair_by_text_inside_a_line():
    before = _doc()
    after = copy.deepcopy(before)
    del after["lines"][1]["words"][1]  # "epsilon" lost
    _, words = pair_documents(before, after)
    lost = [w.before["text"] for w in words if w.after is None]
    assert lost == ["epsilon"]


# --- change or not --------------------------------------------------------------

def test_a_frame_of_jitter_is_no_change_one_ms_more_is():
    base = _doc()
    assert _class("late", base, _moved(base, 1, -20)) == "bad:unchanged"
    assert _class("late", base, _moved(base, 1, 20)) == "bad:unchanged"
    assert _class("late", base, _moved(base, 1, -21)) == "bad:fixed?"
    assert _class("ok", base, _moved(base, 1, 21)) == "good:moved"


def test_an_end_that_moved_is_a_change():
    base = _doc()
    after = copy.deepcopy(base)
    after["lines"][1]["words"][0]["end_ms"] += 21
    assert moment_diff(base, after, 6000).changed
    after["lines"][1]["words"][0]["end_ms"] -= 1
    assert not moment_diff(base, after, 6000).changed


def test_the_window_is_two_and_a_half_seconds_inclusive():
    base = _doc()
    # line 2's first word at 6000: a moment at 3500 still sees it, at 3499 not
    assert moment_diff(base, _moved(base, 1, 300, words={0}), 3500).changed
    assert not moment_diff(base, _moved(base, 1, 300, words={0}), 3499).changed
    # the lower edge: moved to 5700, it is still this moment's word from 8500
    earlier = _moved(base, 1, -300, words={0})
    assert moment_diff(base, earlier, 8500).start_shifts == [-300, 0, 0, 0]
    assert moment_diff(base, earlier, 8501).start_shifts == [0, 0, 0]


# --- known-bad moments ----------------------------------------------------------

def test_late_needs_earlier_starts_early_needs_later_ones():
    base = _doc()
    earlier, later = _moved(base, 1, -400), _moved(base, 1, 400)
    assert _class("late", base, earlier) == "bad:fixed?"
    assert _class("late", base, later) == "bad:worse?"
    assert _class("early", base, later) == "bad:fixed?"
    assert _class("early", base, earlier) == "bad:worse?"
    assert _class("late", base, base) == "bad:unchanged"


def test_late_reads_only_the_words_that_moved():
    # one word 400 ms earlier, two unmoved: the median of ALL shifts is 0,
    # the median of the moved ones says which way the line went
    base = _doc()
    assert moment_diff(base, _moved(base, 1, -400, words={0}), 6000).median_moved_ms == -400
    assert _class("late", base, _moved(base, 1, -400, words={0})) == "bad:fixed?"


def _piled(doc, line, n_zero=0):
    """`line`'s words all start at its first word (a pile); n_zero of them 0 ms."""
    out = copy.deepcopy(doc)
    words = out["lines"][line]["words"]
    for i, w in enumerate(words):
        w["start_ms"] = words[0]["start_ms"] + 20 * i
        w["end_ms"] = w["start_ms"] if i < n_zero else w["start_ms"] + 200
    return out


def test_pile_fixed_worse_and_mixed():
    base = _doc()
    pile = _piled(base, 1)
    assert moment_diff(pile, base, 6000).before.piled == 3
    assert _class("pile", pile, base) == "bad:fixed?"
    assert _class("pile", base, pile) == "bad:worse?"
    # the pile spread out but its first word went to 0 ms: no clear direction
    spread_zero = copy.deepcopy(base)
    spread_zero["lines"][1]["words"][0]["end_ms"] = 6000
    assert _class("pile", pile, spread_zero) == "bad:changed"


def test_pile_span_is_the_finders_sixty_ms():
    base = _doc()
    two = copy.deepcopy(base)
    two["lines"][1]["words"][1]["start_ms"] = 6060
    assert compare.window_stats(two, 3500, 8500).piled == 2
    two["lines"][1]["words"][1]["start_ms"] = 6061
    assert compare.window_stats(two, 3500, 8500).piled == 0


def test_missing_fixed_when_words_appear_worse_when_they_go_dark():
    base = _doc()
    wordless = copy.deepcopy(base)
    del wordless["lines"][1]["words"]
    assert _class("missing", wordless, base) == "bad:fixed?"
    assert _class("missing", base, wordless) == "bad:worse?"
    dark = copy.deepcopy(base)
    dark["lines"][1]["words"][2]["end_ms"] = dark["lines"][1]["words"][2]["start_ms"]
    assert _class("missing", base, dark) == "bad:worse?"
    assert _class("missing", dark, base) == "bad:fixed?"
    # 0 ms words for a wordless line: still dark, no direction
    all_dark = copy.deepcopy(base)
    for w in all_dark["lines"][1]["words"]:
        w["end_ms"] = w["start_ms"]
    assert _class("missing", wordless, all_dark) == "bad:changed"


def test_wrong_text_is_fixed_only_by_a_text_change():
    base = _doc()
    reworded = copy.deepcopy(base)
    reworded["lines"][1]["text"] = "delta epsilon eta"
    reworded["lines"][1]["words"][2]["text"] = "eta"
    assert _class("wrong_text", base, reworded) == "bad:fixed?"
    assert _class("wrong_text", base, _moved(base, 1, 300)) == "bad:changed"
    assert _class("wrong_text", base, base) == "bad:unchanged"


# --- known-good moments ---------------------------------------------------------

def test_a_good_moment_breaks_past_the_noticeable_thresholds():
    base = _doc()
    assert _class("ok", base, base) == "good:kept"
    assert _class("ok", base, _moved(base, 1, 220)) == "good:moved"
    assert _class("ok", base, _moved(base, 1, 221)) == "good:broken?"
    assert _class("ok", base, _moved(base, 1, -330)) == "good:moved"
    assert _class("ok", base, _moved(base, 1, -331)) == "good:broken?"


def test_a_good_moment_breaks_on_hygiene_and_lost_words():
    base = _doc()
    assert _class("ok", base, _piled(base, 1)) == "good:broken?"
    zero = copy.deepcopy(base)
    zero["lines"][1]["words"][1]["end_ms"] = zero["lines"][1]["words"][1]["start_ms"]
    assert _class("ok", base, zero) == "good:broken?"
    lost = copy.deepcopy(base)
    del lost["lines"][1]["words"][1]
    assert _class("ok", base, lost) == "good:broken?"
    wordless = copy.deepcopy(base)
    del wordless["lines"][1]["words"]
    assert _class("ok", base, wordless) == "good:broken?"


def test_unsure_and_unknown_verdicts():
    base = _doc()
    assert _class("unsure", base, base) == "unsure:unchanged"
    assert _class("unsure", base, _moved(base, 1, 300)) == "unsure:changed"
    assert _class("render_bug", base, _moved(base, 1, 300)) == "other"


# --- verdict records ------------------------------------------------------------

def test_verdicts_apply_to_their_own_version_or_where_versions_agreed():
    on_229, on_228 = _doc("2.29.0"), _doc("2.28.3")
    rec = {"id": VID, "t_ms": 6000, "verdict": "late", "better_version": "2.28"}
    assert compare.applies(rec, on_229)
    assert not compare.applies(rec, on_228)
    assert compare.applies({**rec, "better_version": "none"}, on_228)
    assert compare.applies({**rec, "better_version": "either"}, on_228)
    assert not compare.applies({**rec, "better_version": "mixed"}, on_228)
    assert not compare.applies({k: v for k, v in rec.items() if k != "better_version"}, on_228)
    assert compare.applies(rec, on_228, describes="2.28")


def test_the_owners_listening_verdict_wins():
    rec = {"id": VID, "t_ms": 1, "verdict": "pile", "caner": "ok"}
    assert compare.effective_verdict(rec) == ("ok", "owner")
    assert compare.effective_verdict({**rec, "caner": "agree"}) == ("pile", "agent")
    assert compare.effective_verdict({**rec, "caner": None}) == ("pile", "agent")
    assert compare.effective_verdict({**rec, "caner": "pile"}) == ("pile", "agent")


def test_compare_classifies_each_verdict_and_counts_them():
    before = _doc("2.29.0")
    after = _moved(before, 1, -400)
    verdicts = [
        {"id": VID, "t_ms": 6000, "verdict": "late", "better_version": "2.28"},
        {"id": VID, "t_ms": 1000, "verdict": "ok", "better_version": "none"},
        {"id": "otherVid001", "t_ms": 1000, "verdict": "ok"},
    ]
    result = compare.compare({VID: before}, {VID: after}, verdicts)
    assert [m["class"] for m in result["moments"]] == ["bad:fixed?", "good:kept", "n/a:no_doc"]
    assert result["totals"]["classes"] == {"bad:fixed?": 1, "good:kept": 1, "n/a:no_doc": 1}
    stale = compare.compare({VID: _doc("2.28.0")}, {VID: after}, verdicts[:1])
    assert stale["moments"][0]["class"] == "n/a:version"


# --- song level -----------------------------------------------------------------

def test_song_numbers_seam_piles_hygiene_and_qa():
    doc = _doc(qa={"flagged": 2, "nudged": 1, "uncertain": 1, "density_dropped": 0})
    # line 2 opens with two 0 ms words where line 1's last word starts: a seam pile
    doc["lines"][1]["words"][0] = _w("delta", 2000, 0)
    doc["lines"][1]["words"][1] = _w("epsilon", 2000, 0)
    doc["lines"][1]["start_ms"] = 2000
    n = song_numbers(doc)
    assert (n["worded"], n["words"], n["seam_piles"], n["zero"]) == (3, 9, 1, 2)
    assert n["qa"] == {"flagged": 2, "density_dropped": 0, "nudged": 1, "uncertain": 1}
    assert n["zero_frac"] == round(2 / 9, 4) and n["short_frac"] == round(2 / 9, 4)
    assert n["bunched"] == 1  # "gamma" at 2000 and both zeros: three starts within 60 ms


def test_same_within_is_inclusive_and_strict_on_structure():
    base = _doc()
    def ident(after):
        return compare.identity(*pair_documents(base, after))

    assert same_within(ident(base), 20)
    assert same_within(ident(_moved(base, 1, 20, words={1})), 20)
    assert not same_within(ident(_moved(base, 1, 21, words={1})), 20)
    reworded = copy.deepcopy(base)
    reworded["lines"][0]["text"] = "alpha beta"
    assert not same_within(ident(reworded), 20)
    line_only = copy.deepcopy(base)
    line_only["lines"][2]["end_ms"] += 21
    assert not same_within(ident(line_only), 20)


# --- audio evidence ---------------------------------------------------------------

def test_ear_reading_pairs_lines_and_counts_threshold_crossings():
    before, after = _doc(), _doc()
    late = [LineTiming(1, 6000, 400, "ear")]
    fine = [LineTiming(1, 6000, 30, "ear")]
    assert compare.ear_reading(before, after, late, fine, 3500, 8500)["reading"] == "better"
    assert compare.ear_reading(before, after, fine, late, 3500, 8500)["reading"] == "worse"
    assert compare.ear_reading(before, after, fine, fine, 3500, 8500)["reading"] == "same"
    assert compare.ear_reading(before, after, [], [], 3500, 8500)["reading"] == "none"
    # a line outside the window is not this moment's evidence
    assert compare.ear_reading(before, after, late, fine, 0, 5999)["reading"] == "none"


def test_audio_flag_adds_score_and_ear_reading(monkeypatch):
    before = _moved(_doc(), 1, 600)            # line 2 sung at 6000, documented at 6600
    after = _doc()
    monkeypatch.setattr(compare, "song_witnesses", lambda vid: (None, [1000, 6000, 11000]))
    verdicts = [{"id": VID, "t_ms": 6000, "verdict": "late"}]
    result = compare.compare({VID: before}, {VID: after}, verdicts, audio=True)
    song = result["songs"][0]["score"]
    assert (song["before"]["noticeable"], song["after"]["noticeable"]) == (1, 0)
    moment = result["moments"][0]
    assert moment["class"] == "bad:fixed?" and moment["ear"]["reading"] == "better"


# --- command line -----------------------------------------------------------------

def test_cli_reads_a_dump_and_a_single_document(tmp_path, capsys):
    before = tmp_path / "before.json"
    before.write_text(json.dumps([{"id": VID, "doc": _doc()}, {"id": "noDocVid01", "doc": None}]))
    after = tmp_path / "after.json"
    after.write_text(json.dumps(_moved(_doc("2.29.1"), 1, 21, words={0})))
    verdicts = tmp_path / "review.jsonl"
    verdicts.write_text(json.dumps({"id": VID, "t_ms": 6000, "verdict": "ok"}) + "\n\n")
    out = tmp_path / "result.json"
    assert compare.main([str(before), str(after), "--verdicts", str(verdicts),
                         "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "2.29.0→2.29.1" in printed and "good:moved" in printed
    assert json.loads(out.read_text())["moments"][0]["class"] == "good:moved"
    assert compare.main([str(before), str(after), "--same-within-ms", "21"]) == 0
    assert compare.main([str(before), str(after), "--same-within-ms", "20"]) == 1


def test_a_verdict_without_its_fields_is_refused(tmp_path):
    bad = tmp_path / "review.jsonl"
    bad.write_text(json.dumps({"id": VID, "verdict": "ok"}) + "\n")
    with pytest.raises(ValueError, match="t_ms"):
        compare.load_verdicts(bad)
