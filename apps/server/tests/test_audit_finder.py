"""audit.finder: each check on both sides of its boundary, values pinned as numbers
(a test written against the constant would follow a changed constant silently)."""

from audit import finder
from audit.finder import (
    cluster,
    controls,
    ear_events,
    moved_events,
    qa_events,
    voice_events,
    word_events,
)


def _w(text, s, e):
    return {"text": text, "start_ms": s, "end_ms": e}


def _doc(*lines, sync="word"):
    out = []
    for words in lines:
        out.append({"text": " ".join(w["text"] for w in words), "start_ms": words[0]["start_ms"],
                    "end_ms": words[-1]["end_ms"], "words": words})
    return {"sync": sync, "lines": out}


def tags(events):
    return sorted(e.tag for e in events)


def test_thresholds_are_the_calibrated_numbers():
    assert (finder.PILE_SPAN_MS, finder.SHORT_WORD_MS, finder.SHORT_RUN) == (60, 60, 3)
    assert (finder.ORPHAN_MS, finder.LINE_GAP_MS) == (250, 500)
    assert (finder.ORPHAN_MIN_PEAK_DB, finder.ORPHAN_PEAK_WINDOW_MS) == (-19.0, 250)
    assert (finder.LINE_VOICE_MIN_MS, finder.LINE_VOICE_MAX_MS) == (250, 1500)
    assert (finder.MOVED_MS, finder.CLUSTER_MS, finder.CLUSTER_MAX_SPAN_MS) == (250, 1500, 3000)
    assert (finder.TAG_COUNT_CAP, finder.CONTROL_CLEARANCE_MS) == (3, (3000, 2000, 1000))


def test_pile_counts_starts_within_60_ms_also_across_a_line_seam():
    doc = _doc([_w("a", 1000, 1300), _w("b", 1500, 1500)], [_w("c", 1560, 1900)])
    assert [e.tag for e in word_events(doc, "v")].count("pile") == 1
    doc = _doc([_w("a", 1000, 1300), _w("b", 1500, 1600)], [_w("c", 1561, 1900)])
    assert "pile" not in tags(word_events(doc, "v"))


def test_short_run_needs_three_words_under_60_ms():
    three = _doc([_w("a", 0, 59), _w("b", 100, 159), _w("c", 200, 259), _w("d", 300, 700)])
    assert tags(word_events(three, "v")) == ["short_run"]
    at_60 = _doc([_w("a", 0, 59), _w("b", 100, 159), _w("c", 200, 260), _w("d", 300, 700)])
    assert "short_run" not in tags(word_events(at_60, "v"))


def test_a_lone_zero_word_is_its_own_light_tag():
    doc = _doc([_w("a", 0, 300), _w("b", 400, 400), _w("c", 500, 900)])
    assert tags(word_events(doc, "v")) == ["zero"]


def test_orphan_voice_is_a_return_with_no_word_start_within_250_ms():
    doc = _doc([_w("a", 1000, 1400), _w("b", 3000, 3400)])
    assert tags(voice_events(doc, "v", [1250, 2000])) == ["orphan_voice"]
    assert tags(voice_events(doc, "v", [1251])) == ["orphan_voice"]
    assert tags(voice_events(doc, "v", [1250])) == []


def test_line_vs_voice_only_after_a_500_ms_silence_and_between_250_and_1500_ms():
    first = [_w("a", 0, 1000)]
    late = _doc(first, [_w("b", 1500, 1800)])           # gap 500, start 250 after voice
    assert "line_vs_voice" in tags(voice_events(late, "v", [1250]))
    near = _doc(first, [_w("b", 1500, 1800)])
    assert "line_vs_voice" not in tags(voice_events(near, "v", [1251]))
    short_gap = _doc(first, [_w("b", 1499, 1800)])
    assert "line_vs_voice" not in tags(voice_events(short_gap, "v", [1249]))
    far = _doc(first, [_w("b", 3000, 3300)])
    assert "line_vs_voice" in tags(voice_events(far, "v", [1500]))      # exactly 1500: counts
    assert "line_vs_voice" not in tags(voice_events(far, "v", [1499]))


def _ear(tokens, probs=None, stride=20.0):
    return {"tokens": tokens, "letter_prob": probs or [0.0] * 500, "stride_ms": stride,
            "start_ms": 0}


def _t(text, ms):
    return {"text": text, "start_ms": ms, "end_ms": ms + 20}


def test_sung_unboxed_needs_four_letters_away_from_every_box():
    doc = _doc([_w("a", 0, 200)], [_w("b", 5000, 5200)])
    loose = [_t(c, 2000 + 60 * i) for i, c in enumerate("abcd")]
    assert tags(ear_events(doc, "v", _ear(loose))) == ["sung_unboxed"]
    assert tags(ear_events(doc, "v", _ear(loose[:3]))) == []
    # a box covers its word +-150 ms, inclusive: 350 is still "a"'s, 351 is not
    at_edge = [_t(c, 350 + 60 * i) for i, c in enumerate("abcd")]
    assert tags(ear_events(doc, "v", _ear(at_edge))) == []
    past_edge = [_t(c, 351 + 60 * i) for i, c in enumerate("abcd")]
    assert tags(ear_events(doc, "v", _ear(past_edge))) == ["sung_unboxed"]


def test_boxed_silent_is_a_long_box_with_no_letters_and_no_loudness():
    doc = _doc([_w("a", 1000, 1250), _w("b", 3000, 3249)])
    assert tags(ear_events(doc, "v", _ear([]), level_at=lambda t: -40.0)) == ["boxed_silent"]
    assert tags(ear_events(doc, "v", _ear([]), level_at=lambda t: -25.0)) == []
    probs = [0.0] * 500
    probs[55] = 0.2  # frame 55 = 1100 ms, inside "a"
    assert tags(ear_events(doc, "v", _ear([], probs), level_at=lambda t: -40.0)) == []


def test_qa_marks_uncertain_derived_and_wordless_lines_of_a_word_document():
    doc = {"sync": "word", "lines": [
        {"text": "x", "start_ms": 0, "end_ms": 1, "uncertain": True, "words": [_w("x", 0, 1)]},
        {"text": "y", "start_ms": 5, "end_ms": 9},
        {"text": "z", "start_ms": 10, "end_ms": 20, "words": [_w("z", 10, 20)]},
    ]}
    assert [e.detail.split(":")[0] for e in qa_events(doc, "v")] == ["uncertain", "no_words"]
    assert qa_events({**doc, "sync": "line"}, "v")[-1].detail.startswith("uncertain")


def test_moved_is_more_than_250_ms_on_the_same_word():
    old = _doc([_w("a", 1000, 1200), _w("b", 1300, 1500)])
    assert moved_events(old, _doc([_w("a", 1250, 1400), _w("b", 1300, 1500)]), "v") == []
    assert len(moved_events(old, _doc([_w("a", 1251, 1400), _w("b", 1300, 1500)]), "v")) == 1


def test_cluster_merges_within_1500_ms_and_ranks_by_weight():
    ev = finder.Event
    moments = cluster([ev(0, "zero", "v"), ev(1500, "pile", "v"), ev(3001, "orphan_voice", "v"),
                       ev(3001, "pile", "v")])
    assert [(m.t_ms, m.score) for m in moments] == [(3001, 1.6), (1500, 0.9)]


def test_controls_are_seeded_and_stay_clear_of_events():
    doc = _doc([_w(str(i), 1000 * i, 1000 * i + 300) for i in range(20)])
    busy = cluster([finder.Event(5000, "pile", "v")])
    picks = [m.t_ms for m in controls(doc, busy, "seed")]
    assert picks == [m.t_ms for m in controls(doc, busy, "seed")]
    assert all(abs(p - 5000) > 3000 for p in picks) and len(picks) == 2


def test_cluster_stops_at_a_3000_ms_span_and_caps_each_tag_at_three():
    ev = finder.Event
    chain = [ev(1000 * i, "zero", "v") for i in range(5)]   # 0..4000, gaps of 1000
    moments = cluster(chain)
    assert sorted(m.t_ms for m in moments) == [0, 4000]
    assert sorted(m.score for m in moments) == [0.3, 0.9]    # 4 zeros count as 3
    exact = cluster([ev(0, "zero", "v"), ev(1500, "zero", "v"), ev(3000, "zero", "v")])
    assert len(exact) == 1                                  # span exactly 3000 stays one


def test_controls_relax_their_clearance_in_dense_songs():
    doc = _doc([_w(str(i), 1000 * i, 1000 * i + 300) for i in range(10)])
    busy = cluster([finder.Event(t, "pile", "v") for t in (0, 4000, 8000)])
    # nothing is 3000 or 2000 clear here; at 1000 only 2000 and 6000 are
    picks = [m.t_ms for m in controls(doc, busy, "seed", n=1)]
    assert len(picks) == 1 and picks[0] in (2000, 6000)


def test_a_pile_weighs_per_extra_word_up_to_three():
    four = _doc([_w(t, 1000, 1000) for t in "abc"] + [_w("d", 1000, 1300)])
    (ev,) = [e for e in word_events(four, "v") if e.tag == "pile"]
    assert ev.size == 3 and round(ev.weight, 6) == 1.8
    six = _doc([_w(t, 1000, 1000) for t in "abcde"] + [_w("f", 1000, 1300)])
    assert [e.size for e in word_events(six, "v") if e.tag == "pile"] == [3]
    two = _doc([_w("a", 1000, 1000), _w("b", 1000, 1300)])
    assert [e.weight for e in word_events(two, "v") if e.tag == "pile"] == [0.6]


def test_controls_keep_5000_ms_apart():
    # ten candidates inside one second and one far away: the second pick must
    # be the far one, whatever the shuffle puts first
    doc = _doc([_w(str(i), 100 * i, 100 * i + 50) for i in range(10)] + [_w("x", 20000, 20300)])
    for seed in ("a", "b", "c", "seed"):
        picks = [m.t_ms for m in controls(doc, [], seed)]
        assert len(picks) == 2 and picks[1] - picks[0] >= 5000


def test_a_quiet_voice_return_is_not_an_orphan():
    doc = _doc([_w("a", 1000, 1400), _w("b", 3000, 3400)])
    loud = voice_events(doc, "v", [2000], peak_after=lambda t, w: -19.0)
    assert tags(loud) == ["orphan_voice"]                       # exactly -19 counts
    quiet = voice_events(doc, "v", [2000], peak_after=lambda t, w: -19.1)
    assert tags(quiet) == []
    seen = []
    voice_events(doc, "v", [2000], peak_after=lambda t, w: seen.append((t, w)) or -10.0)
    assert seen == [(2000, 250)]
