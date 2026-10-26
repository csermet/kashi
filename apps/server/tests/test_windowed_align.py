"""Windowed alignment clock and seam wiring (Faz 9, 2026-10), without torch.

ctc_forced_aligner rounds its frame step up: every window slice is shorter
than 30 s, where the model's real 20 ms step reads as 20.03 and is returned as
21. Inside a window every time then ran 4.8 % slow from the slice start.
"""

import sys
import types

import pytest

from kashi_server.pipeline import alignment
from kashi_server.pipeline.alignment import MODEL_HOP_SAMPLES, exact_stride_ms

SLICE_MS = 6_210  # a real window slice from the Stressed Out field case
SLICE_SAMPLES = SLICE_MS * 16
SLICE_FRAMES = (SLICE_SAMPLES - 400) // MODEL_HOP_SAMPLES + 1  # what the model emits: 310


def test_a_window_slice_steps_20_ms_not_21():
    assert SLICE_FRAMES == 310
    assert exact_stride_ms(SLICE_SAMPLES, SLICE_FRAMES, 21) == 20.0
    # Frame 301 of that slice: 6.020 s on the real clock, 6.321 s on the library's.
    assert 301 * exact_stride_ms(SLICE_SAMPLES, SLICE_FRAMES, 21) / 1000 == pytest.approx(6.02)


def test_long_audio_steps_20_ms_too():
    # The batched branch crops to exactly 1500 frames per 30 s.
    assert exact_stride_ms(60 * 16_000, 3_000, 20) == 20.0


def test_a_frame_count_that_fits_no_320_sample_hop_keeps_the_library_stride():
    # Twice the frames (a 160-sample hop) is not this model family.
    assert exact_stride_ms(SLICE_SAMPLES, 2 * SLICE_FRAMES, 11) == 11.0
    assert exact_stride_ms(SLICE_SAMPLES, 0, 21) == 21.0


def test_the_hop_check_boundary():
    n_frames = 300
    inside = n_frames * MODEL_HOP_SAMPLES - (2 * MODEL_HOP_SAMPLES - 1)
    outside = n_frames * MODEL_HOP_SAMPLES - 2 * MODEL_HOP_SAMPLES
    assert exact_stride_ms(inside, n_frames, 21) == 20.0
    assert exact_stride_ms(outside, n_frames, 21) == 21.0


class FakeAudio:
    """Just enough of a 16 kHz mono tensor for slicing and sizing."""

    def __init__(self, n_samples: int):
        self.shape = (n_samples,)

    def __getitem__(self, key):
        _, window = key
        return FakeAudio(min(window.stop, self.shape[0]) - window.start)


def fake_library(record: dict) -> types.SimpleNamespace:
    """ctc_forced_aligner with every call stubbed; postprocess sees the stride."""

    def generate_emissions(model, audio, batch_size=4):
        frames = (audio.shape[-1] - 400) // MODEL_HOP_SAMPLES + 1
        return types.SimpleNamespace(shape=(frames, 32)), 21

    def postprocess_results(text_starred, spans, stride, scores):
        record["stride"] = stride
        return [{"text": "w", "start": 301 * stride / 1000, "end": 302 * stride / 1000}]

    return types.SimpleNamespace(
        generate_emissions=generate_emissions,
        preprocess_text=lambda *a, **k: (["w"], ["w"]),
        get_alignments=lambda *a, **k: ([], [], 0),
        get_spans=lambda *a, **k: [],
        postprocess_results=postprocess_results,
    )


def test_align_texts_forwards_the_library_stride_by_default(monkeypatch):
    record: dict = {}
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", fake_library(record))
    out = alignment._align_texts(None, None, FakeAudio(SLICE_SAMPLES), ["w"], "eng")
    assert record["stride"] == 21
    assert out[0]["start"] == pytest.approx(6.321)


def test_align_texts_uses_the_real_step_when_asked(monkeypatch):
    record: dict = {}
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", fake_library(record))
    out = alignment._align_texts(
        None, None, FakeAudio(SLICE_SAMPLES), ["w"], "eng", exact_stride=True
    )
    assert record["stride"] == 20.0
    assert out[0]["start"] == pytest.approx(6.02)


# --- align(): the windowed wiring ------------------------------------------

LINES = ["one two three", "four five six", "seven eight nine", "ten eleven twelve"]
STAMPS = [1_000, 6_000, 11_000, 16_000]
TOTAL_S = 20


def run_align(monkeypatch, tmp_path, **kwargs):
    """Four windows. The aligner parks line 0's last word in window 0's pad
    (6.55 s, past the next stamp at 6.0 s) while window 1 places line 1's first
    word correctly at 6.15 s: the Stressed Out shape. `line_mode` loses a word
    in window 1, so the word count no longer matches and alignment drops to
    line timings."""
    line_mode = kwargs.pop("line_mode", False)
    # The edges mechanics (2.28's windows). The shipped hybrid + whole-track
    # vote run through run_hybrid below.
    kwargs.setdefault("window_star", "edges")
    kwargs.setdefault("whole_vote", False)
    seen: dict = {"exact": [], "star": []}
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: FakeAudio(TOTAL_S * 16_000)),
    )
    fake_model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (fake_model, object()))

    def fake_align_texts(model, tokenizer, piece, texts, language, star, *, romanize, exact_stride):
        seen["exact"].append(exact_stride)
        seen["star"].append(star)
        words = texts[0].split()
        if texts[0] == LINES[0]:  # window 0: slice starts at 0.65 s
            rel = [0.5, 2.0, 5.9]  # "three" -> 6.55 s absolute
        elif texts[0] == LINES[1]:  # window 1: slice starts at 5.65 s
            rel = [0.5, 1.5, 3.0]  # "four" -> 6.15 s absolute
        else:
            rel = [0.5, 1.5, 3.0]
        if line_mode and texts[0] == LINES[1]:
            words, rel = words[:2], rel[:2]
        return [
            {"text": t, "start": r, "end": r + 0.2, "score": -0.1}
            for t, r in zip(words, rel, strict=True)
        ]

    monkeypatch.setattr(alignment, "_align_texts", fake_align_texts)
    offset_ms = kwargs.pop("offset_ms", 0)
    by_initial = kwargs.pop("offset_by_initial", None)
    result = alignment.align(
        tmp_path / "x.wav", LINES, "eng", STAMPS, "m", True, offset_ms, by_initial, **kwargs
    )
    return result, seen


def test_switched_off_the_2_27_pile_is_reproduced(monkeypatch, tmp_path):
    result, seen = run_align(monkeypatch, tmp_path, exact_stride=False, seam_policy="clamp")
    assert result.windowed
    assert seen["exact"] == [False] * 4
    four, five = result.words_per_line[1][:2]
    assert four.start_ms == 6_550  # pushed onto the stray "three"
    assert four.end_ms == four.start_ms  # ...and zero-length


def test_seam_policy_yield_keeps_the_next_line_in_place(monkeypatch, tmp_path):
    result, _ = run_align(monkeypatch, tmp_path, exact_stride=False, seam_policy="yield")
    three = result.words_per_line[0][-1]
    four = result.words_per_line[1][0]
    assert four.start_ms == 6_150
    assert (three.start_ms, three.end_ms) == (6_130, 6_150)


def test_exact_stride_reaches_every_window(monkeypatch, tmp_path):
    _, seen = run_align(monkeypatch, tmp_path, exact_stride=True)
    assert seen["exact"] == [True] * 4


# --- 2.28.0: the shipped defaults and the windowed lead ----------------------


def test_the_shipped_clock_seams_and_lead(monkeypatch, tmp_path):
    result, seen = run_align(monkeypatch, tmp_path)
    assert seen["exact"] == [True] * 4
    three = result.words_per_line[0][-1]
    four = result.words_per_line[1][0]
    lead = alignment.WINDOWED_LEAD_MS
    assert lead == 70
    assert four.start_ms == 6_150 + lead
    assert (three.start_ms, three.end_ms) == (6_130 + lead, 6_150 + lead)
    # The arbiter undoes shift_ms to judge the aligner's own clock.
    assert four.start_ms - four.shift_ms == 6_150


def test_the_library_clock_takes_no_lead(monkeypatch, tmp_path):
    # 2.27's tables were fitted on the 21 ms clock: the lead belongs to the exact one.
    result, _ = run_align(monkeypatch, tmp_path, exact_stride=False)
    assert result.words_per_line[1][0].start_ms == 6_150


def test_the_lead_rides_on_top_of_the_configured_table(monkeypatch, tmp_path):
    # The table describes the model over the whole track; a windowed word adds
    # the lead to whatever its class takes. Window 0's slice starts at 0.65 s.
    result, _ = run_align(
        monkeypatch, tmp_path, offset_ms=-50, offset_by_initial={"plosive": -30}
    )
    one, two = result.words_per_line[0][:2]  # vowel (base), plosive
    assert one.start_ms == 1_150 - 50 + 70
    assert two.start_ms == 2_650 - 30 + 70


def test_line_timings_take_the_lead_too(monkeypatch, tmp_path):
    result, _ = run_align(monkeypatch, tmp_path, line_mode=True)
    assert result.sync == "line"
    assert result.lines[0].start_ms == 1_150 + alignment.WINDOWED_LEAD_MS


def _whole_track_align(monkeypatch, tmp_path, stamps):
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: FakeAudio(TOTAL_S * 16_000)),
    )
    fake_model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (fake_model, object()))

    def fake_align_texts(model, tokenizer, audio, texts, language, star="segment", romanize=True):
        words = " ".join(texts).split()
        return [
            {"text": t, "start": 1.0 + i, "end": 1.2 + i, "score": -0.1}
            for i, t in enumerate(words)
        ]

    monkeypatch.setattr(alignment, "_align_texts", fake_align_texts)
    return alignment.align(tmp_path / "x.wav", LINES, "eng", stamps, "m", True, -50)


def test_the_whole_track_path_takes_no_lead(monkeypatch, tmp_path):
    result = _whole_track_align(monkeypatch, tmp_path, None)
    assert not result.windowed
    assert [w.start_ms for w in result.words_per_line[0]] == [950, 1_950, 2_950]


def test_stamps_the_planner_declines_take_no_lead_either(monkeypatch, tmp_path):
    # The lead follows the PLAN, not the anchors: sparse stamps that
    # plan_windows refuses fall back to the whole-track clock.
    monkeypatch.setattr(alignment, "plan_windows", lambda *a, **k: None)
    result = _whole_track_align(monkeypatch, tmp_path, STAMPS)
    assert not result.windowed
    assert [w.start_ms for w in result.words_per_line[0]] == [950, 1_950, 2_950]


def test_edges_stars_reach_every_window(monkeypatch, tmp_path):
    _, seen = run_align(monkeypatch, tmp_path)
    assert seen["star"] == ["edges"] * 4


def test_segment_stars_reach_every_window_and_take_no_lead(monkeypatch, tmp_path):
    # Round 5: the lead is a property of the `edges` topology; `segment` words
    # want the whole-track table as it is.
    result, seen = run_align(monkeypatch, tmp_path, window_star="segment")
    assert seen["star"] == ["segment"] * 4
    assert result.words_per_line[1][0].start_ms == 6_150


def test_an_unknown_star_placement_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        run_align(monkeypatch, tmp_path, window_star="every-frame")


def test_an_unknown_seam_policy_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        run_align(monkeypatch, tmp_path, seam_policy="median")


# --- 2.29 groundwork: per-word hybrid stars and the whole-track vote ----------

from kashi_server.pipeline.windows import hybrid_star_words, whole_track_vote  # noqa: E402


def _row(text, start, score=-0.1, **extra):
    return {"text": text, "start": start, "end": start + 0.1, "score": score, **extra}


def test_hybrid_takes_segment_at_exactly_t_and_edges_one_ms_past():
    edges = [_row("a", 1.0), _row("b", 3.0), _row("c", 5.0)]
    segment = [_row("a", 1.4), _row("b", 3.401), _row("c", 4.6)]
    out, paired = hybrid_star_words(edges, segment, 400)
    assert paired
    assert [r["star"] for r in out] == ["segment", "edges", "segment"]
    assert [r["start"] for r in out] == [1.4, 3.0, 4.6]


def test_hybrid_compares_after_the_lead_and_returns_the_edges_clock():
    # An edges word lands 70 ms earlier than segment places the same sound:
    # 1.000 s edges meets segment 1.070, so 1.470 agrees and 0.669 does not.
    # The chosen segment time comes back moved onto the edges clock.
    edges = [_row("a", 1.0), _row("b", 3.0)]
    segment = [_row("a", 1.47), _row("b", 2.669)]
    out, _ = hybrid_star_words(edges, segment, 400, edges_lead_ms=70)
    assert [r["star"] for r in out] == ["segment", "edges"]
    assert out[0]["start"] == pytest.approx(1.40)
    assert out[0]["end"] == pytest.approx(1.50)
    assert out[1]["start"] == 3.0


def test_hybrid_compares_whole_milliseconds():
    # 400.4 ms rounds to 400: float noise must not flip the boundary.
    out, _ = hybrid_star_words([_row("a", 1.0)], [_row("a", 1.4004)], 400)
    assert out[0]["star"] == "segment"


def test_a_segment_word_keeps_the_edges_score():
    # Word probs feed line QA and the quality gate, calibrated on edges.
    out, _ = hybrid_star_words([_row("a", 1.0, score=-3.0)], [_row("a", 1.1, score=-0.1)], 400)
    assert out[0]["star"] == "segment"
    assert out[0]["score"] == -3.0
    assert out[0]["end"] == pytest.approx(1.2)


@pytest.mark.parametrize(
    "segment",
    [[_row("a", 1.0)], [_row("a", 1.0), _row("x", 2.0)]],
    ids=["count", "text"],
)
def test_passes_that_disagree_on_the_words_keep_edges(segment):
    edges = [_row("a", 1.0), _row("b", 2.0)]
    out, paired = hybrid_star_words(edges, segment, 400)
    assert not paired
    assert [(r["start"], r["star"]) for r in out] == [(1.0, "edges"), (2.0, "edges")]


def test_whole_vote_takes_the_whole_time_within_t_after_the_lead():
    results = [_row("a", 1.0), _row("b", 3.0), _row("c", 5.0)]
    whole = [_row("a", 1.47, score=-9.0), _row("b", 3.471), _row("c", 4.67)]
    out, taken, paired = whole_track_vote(results, whole, 400, edges_lead_ms=70)
    assert paired
    assert taken == 2
    assert [r.get("star") for r in out] == ["whole", None, "whole"]
    # Moved back onto the edges clock; the windowed score stays.
    assert [round(r["start"], 3) for r in out] == [1.4, 3.0, 4.6]
    assert out[0]["score"] == -0.1


@pytest.mark.parametrize(
    "whole",
    [[_row("a", 1.0)], [_row("a", 1.0), _row("x", 2.0)]],
    ids=["count", "text"],
)
def test_whole_vote_skips_streams_it_cannot_pair(whole):
    results = [_row("a", 1.0), _row("b", 2.0)]
    out, taken, paired = whole_track_vote(results, whole, 400)
    assert (out, taken, paired) == (results, 0, False)


def test_whole_vote_pairs_words_not_blank_segments():
    # A Japanese job splits per character: the whole track joins every line
    # with a space (a blank segment of its own), a window only its own lines.
    results = [_row("う", 1.0), _row("ち", 1.2)]
    whole = [_row("う", 1.05), _row(" ", 1.1), _row("ち", 1.25)]
    out, taken, paired = whole_track_vote(results, whole, 400)
    assert paired and taken == 2
    assert [r["start"] for r in out] == [1.05, 1.25]


def run_hybrid(monkeypatch, tmp_path, seg_deltas, whole=None, **kwargs):
    """run_align's four windows, with `_align_slice_hybrid` faked: the segment
    pass is the edges pass moved by `seg_deltas[line][word]` seconds. `whole`
    (12 absolute starts) answers the full-length pass of the vote."""
    line_mode = kwargs.pop("line_mode", False)
    seen: dict = {"lengths": []}
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: FakeAudio(TOTAL_S * 16_000)),
    )
    fake_model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (fake_model, object()))
    rels = {LINES[0]: [0.5, 2.0, 5.9], LINES[1]: [0.5, 1.5, 3.0]}

    def edges_rows(texts):
        words = texts[0].split()
        rel = rels.get(texts[0], [0.5, 1.5, 3.0])
        if line_mode and texts[0] == LINES[1]:
            words, rel = words[:2], rel[:2]
        return [
            {"text": t, "start": r, "end": r + 0.2, "score": -0.1}
            for t, r in zip(words, rel, strict=True)
        ]

    def fake_hybrid(model, tokenizer, piece, texts, language, *, romanize, exact_stride):
        edges = edges_rows(texts)
        deltas = seg_deltas[LINES.index(texts[0])]
        segment = [
            {**r, "start": r["start"] + d, "end": r["end"] + d}
            for r, d in zip(edges, deltas, strict=False)
        ]
        return edges, segment

    def fake_align_texts(model, tokenizer, piece, texts, language, star, *, romanize, exact_stride):
        seen["lengths"].append(piece.shape[0])
        seen.setdefault("vote", []).append((star, exact_stride))
        words = " ".join(texts).split()
        return [
            {"text": t, "start": s, "end": s + 0.05, "score": -0.1}
            for t, s in zip(words, whole or [], strict=False)
        ]

    monkeypatch.setattr(alignment, "_align_slice_hybrid", fake_hybrid)
    monkeypatch.setattr(alignment, "_align_texts", fake_align_texts)
    offset_ms = kwargs.pop("offset_ms", 0)
    result = alignment.align(
        tmp_path / "x.wav", LINES, "eng", STAMPS, "m", True, offset_ms, None,
        window_star="hybrid", **kwargs,
    )
    return result, seen


FAR = [2.0, 2.0, 2.0]


def test_hybrid_segment_words_net_no_lead_and_edges_words_seventy(monkeypatch, tmp_path):
    # Window 0: "two" 2.65 edges / 3.10 segment is 450 ms off raw but 380 after
    # the lead (2.72): segment. Window 1 (slice 5.65 s): "four" 6.15 / 6.48
    # (260 after the lead), "five" 7.15 / 7.621 (401), "six" 8.65 / 8.32 (400).
    result, seen = run_hybrid(
        monkeypatch, tmp_path, [[0, 0.45, 0], [0.33, 0.471, -0.33], FAR, FAR], whole_vote=False
    )
    assert seen["lengths"] == []  # no whole-track pass with the vote off
    one, two, three = result.words_per_line[0]
    four, five, six = result.words_per_line[1]
    assert [w.source for w in (one, two, four, five, six)] == [
        "segment", "segment", "segment", "edges", "segment"
    ]
    starts = (two.start_ms, four.start_ms, five.start_ms, six.start_ms)
    assert starts == (3_100, 6_480, 7_220, 8_320)
    assert (one.start_ms, three.start_ms, three.end_ms) == (1_150, 6_460, 6_480)
    # One raw clock for the arbiter: the edges aligner's, whoever placed the word.
    assert all(w.shift_ms == 70 for chunk in result.words_per_line for w in chunk)
    assert (four.start_ms - four.shift_ms, five.start_ms - five.shift_ms) == (6_410, 7_150)


def test_an_edges_word_yields_to_a_segment_word_on_one_clock(monkeypatch, tmp_path):
    # Review finding (2026-10-26): "three" (edges) parked in window 0's pad at
    # 6.55 s, "four" placed by segment at 6.15 s. On mixed clocks yield ended
    # "three" at four's raw start and the lead pushed it past "four" (a pile).
    result, _ = run_hybrid(
        monkeypatch, tmp_path, [[0, 0.45, 2.0], [0.0, 0.471, -0.33], FAR, FAR], whole_vote=False
    )
    three = result.words_per_line[0][-1]
    four = result.words_per_line[1][0]
    assert (three.source, four.source) == ("edges", "segment")
    assert (three.start_ms, three.end_ms) == (6_130, 6_150)
    assert four.start_ms == 6_150


def test_whole_vote_takes_agreeing_words_and_reclamps_seams(monkeypatch, tmp_path):
    # Edges everywhere (segment far off). Windowed starts after the lead: one
    # 1.22, two 2.72, three 6.20 (yielded to 6.13 raw), four 6.22. The whole
    # track says one 1.48 (260 ms: taken), two 3.121 (401: not), four 5.82 (400:
    # taken — but that lands before "three", so the second seam pass clamps it).
    far = [FAR, FAR, FAR, FAR]
    windowed = [1.15, 2.65, 6.13, 6.15, 7.15, 8.65, 11.15, 12.15, 13.65, 16.15, 17.15, 18.65]
    whole = [s + 2.0 for s in windowed]
    whole[0], whole[1], whole[3] = 1.48, 3.121, 5.82
    result, seen = run_hybrid(monkeypatch, tmp_path, far, whole=whole, whole_vote=True)
    assert seen["lengths"] == [TOTAL_S * 16_000]
    assert seen["vote"] == [("segment", True)]  # whole-track stars, the exact clock
    one, two, three = result.words_per_line[0]
    four = result.words_per_line[1][0]
    assert (one.source, one.start_ms) == ("whole", 1_480)
    assert (two.source, two.start_ms) == ("edges", 2_720)
    assert four.source == "whole"
    assert four.start_ms == three.start_ms == 6_200
    assert four.start_ms - four.shift_ms == 6_130  # clamped on the raw clock, not 5.75


def test_line_mode_with_hybrid_stars_keeps_segment_lines_unshifted(monkeypatch, tmp_path):
    # Review finding: line mode applies the lead to every line, so a line whose
    # first word segment placed must already sit on the edges clock.
    result, _ = run_hybrid(
        monkeypatch, tmp_path, [[0.05, 0, 0], FAR, FAR, FAR], whole_vote=False, line_mode=True
    )
    assert result.sync == "line"
    assert result.lines[0].start_ms == 1_150 + 50  # segment's own time, net of the lead
    # (an all-edges line takes the lead: test_line_timings_take_the_lead_too)


def test_one_emission_pass_serves_both_star_placements(monkeypatch):
    calls: dict = {"emit": 0, "align": 0, "stars": []}

    def generate_emissions(model, audio, batch_size=4):
        calls["emit"] += 1
        frames = (audio.shape[-1] - 400) // MODEL_HOP_SAMPLES + 1
        return types.SimpleNamespace(shape=(frames, 32)), 21

    def preprocess_text(text, romanize, language, split_size, star_frequency):
        calls["stars"].append(star_frequency)
        return ["w"], ["w"]

    def get_alignments(emissions, tokens, tokenizer):
        calls["align"] += 1
        return [], [], 0

    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", types.SimpleNamespace(
        generate_emissions=generate_emissions,
        preprocess_text=preprocess_text,
        get_alignments=get_alignments,
        get_spans=lambda *a, **k: [],
        postprocess_results=lambda text, spans, stride, scores: [
            {"text": "w", "start": stride / 1000, "end": stride / 1000, "score": 0.0}
        ],
    ))
    edges, segment = alignment._align_slice_hybrid(
        None, None, FakeAudio(SLICE_SAMPLES), ["w"], "eng", romanize=True, exact_stride=True
    )
    assert (calls["emit"], calls["align"], calls["stars"]) == (1, 2, ["edges", "segment"])
    assert edges[0]["start"] == segment[0]["start"] == pytest.approx(0.02)  # exact stride on both


def test_hybrid_is_a_known_star_placement():
    assert "hybrid" in alignment.WINDOW_STARS
    assert alignment.HYBRID_T_MS == 400
    assert alignment.WHOLE_VOTE_T_MS == 400


def test_the_shipped_defaults_are_hybrid_stars_and_the_whole_track_vote(monkeypatch, tmp_path):
    # 2.29.0: align() called as the worker calls it — no star or vote arguments.
    assert alignment.WINDOW_STAR_DEFAULT == "hybrid"
    assert alignment.WHOLE_VOTE_DEFAULT is True
    calls: dict = {"hybrid": 0, "whole": 0}
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: FakeAudio(TOTAL_S * 16_000)),
    )
    fake_model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (fake_model, object()))

    def fake_hybrid(model, tokenizer, piece, texts, language, *, romanize, exact_stride):
        calls["hybrid"] += 1
        rows = [
            {"text": t, "start": 0.5 + i, "end": 0.7 + i, "score": -0.1}
            for i, t in enumerate(texts[0].split())
        ]
        return rows, rows

    def fake_align_texts(model, tokenizer, piece, texts, language, star, *, romanize, exact_stride):
        calls["whole"] += 1
        return []  # cannot pair: the vote is skipped, the windows stand

    monkeypatch.setattr(alignment, "_align_slice_hybrid", fake_hybrid)
    monkeypatch.setattr(alignment, "_align_texts", fake_align_texts)
    result = alignment.align(tmp_path / "x.wav", LINES, "eng", STAMPS)
    assert calls == {"hybrid": 4, "whole": 1}
    assert {w.source for chunk in result.words_per_line for w in chunk} == {"segment"}
