"""2.35.0: a line's first word starts at most FIRST_HEARD_LEAD_MS before the earliest place a pass
hears its first letter (windows.start_at_heard_onset; the evidence from alignment._first_letters and
_heard_at).

Field case (the owner, 2026-11-14, GOSSIP 2:00 and 2:23): the display jumped to the next line as
soon as one ended, lit its first word and waited seconds for the rest. Values sit exactly on the
lead (499 / 500 / 501 ms), the short-dwell guard (119 / 120 / 121 ms), the letter floor and line
QA's drift band (2 499 / 2 500 / 2 501 ms past the stamp).
"""

import math
import sys
import types
from dataclasses import dataclass

import numpy as np
import pytest

from kashi_server.pipeline import alignment
from kashi_server.pipeline.level import Envelope
from kashi_server.pipeline.windows import Window, start_at_heard_onset

FLOOR = alignment.FIRST_LETTER_FLOOR


def _row(text, start, end=None, heard=None, **extra):
    row = {"text": text, "start": start, "end": start + 0.2 if end is None else end, "score": -0.1}
    if heard is not None:
        row["heard_at"] = heard
    return {**row, **extra}


def _whole(texts_starts, lp=-0.5):
    return [{"text": t, "start": s, "end": s + 0.1, "score": -0.1, "first_letter": [s, lp]}
            for t, s in texts_starts]


# Two lines: "ab ab" / "ab ab". The second line's first word (row 2) is the one under test.
def _stream(first_start, heard, second_start=2.0):
    return [_row("ab", 0.2, heard=[0.2]), _row("ab", 0.5, heard=[0.5]),
            _row("ab", first_start, heard=heard), _row("ab", second_start, heard=[second_start])]


def _agreeing_whole(rows, lead_s=0.07):
    """A whole-track pass that hears every word where the window rows do (or at its start)."""
    words = [r for r in rows if r["text"] not in ("<star>", " ")]
    return _whole([(r["text"], (r.get("heard_at") or [r["start"]])[0] + lead_s) for r in words])


def _run(rows, whole=None, line_words=(2, 2), lead=500, edges_lead_ms=70, **kwargs):
    whole = _agreeing_whole(rows) if whole is None else whole
    return start_at_heard_onset(list(rows), list(whole), list(line_words), lead, edges_lead_ms,
                                FLOOR, **kwargs)


@pytest.mark.parametrize(("start_ms", "moved"), [(701, False), (700, False), (699, True)])
def test_a_start_more_than_the_lead_before_the_heard_letter_moves_onto_it(start_ms, moved):
    out, n = _run(_stream(start_ms / 1000, [1.2]))
    assert n == int(moved)
    assert out[2]["start"] == pytest.approx(1.2 if moved else start_ms / 1000)
    assert [r["start"] for r in out[:2] + out[3:]] == pytest.approx([0.2, 0.5, 2.0])


@pytest.mark.parametrize(("second_ms", "moved"), [(1_319, False), (1_320, True), (1_321, True)])
def test_the_new_start_leaves_the_next_word_its_short_dwell(second_ms, moved):
    out, n = _run(_stream(0.6, [1.2], second_start=second_ms / 1000))
    assert n == int(moved)
    assert out[2]["start"] == pytest.approx(1.2 if moved else 0.6)


def test_the_padded_start_of_a_silence_takes_the_letter_every_pass_hears():
    # GOSSIP line 38 in miniature: an edges start 6.8 s before a letter all three passes hear.
    rows = _stream(1.0, [7.84, 7.77], second_start=8.2)
    whole = _whole([("ab", 0.27), ("ab", 0.57), ("ab", 7.84), ("ab", 8.27)])
    out, n = _run(rows, whole)
    assert n == 1 and out[2]["start"] == pytest.approx(7.77)


def test_the_earliest_pass_that_hears_the_letter_decides():
    # GOSSIP line 33 in miniature: the windows' first letter is parked (no heard time), the whole
    # track hears it at 3.27 s = 3.20 on the edges clock; the edges cluster's 3.3 is later.
    rows = _stream(1.0, [3.3], second_start=3.5)
    whole = _whole([("ab", 0.27), ("ab", 0.57), ("ab", 3.27), ("ab", 3.57)])
    out, n = _run(rows, whole)
    assert n == 1 and out[2]["start"] == pytest.approx(3.2)


def test_any_pass_that_hears_the_letter_at_the_start_keeps_it():
    # nOjpbNatUY8 0:08 in miniature: the edges pass hears the word 2 s late, the whole track and
    # the segment pass where it starts. A genuine pick-up word keeps its place.
    rows = _stream(1.0, [3.0, 1.03])
    whole = _whole([("ab", 0.27), ("ab", 0.57), ("ab", 1.07), ("ab", 2.07)])
    out, n = _run(rows, whole)
    assert n == 0 and out[2]["start"] == pytest.approx(1.0)


def test_a_held_first_word_keeps_its_start():
    out, n = _run(_stream(1.0, [1.0], second_start=3.0))
    assert n == 0 and out[2]["start"] == pytest.approx(1.0)


def test_a_word_no_pass_hears_keeps_its_start():
    whole = _whole([("ab", 0.27), ("ab", 0.57), ("ab", 1.77), ("ab", 2.07)], lp=-9.0)
    out, n = _run(_stream(1.0, []), whole)
    assert n == 0 and out[2]["start"] == pytest.approx(1.0)


def test_the_window_passes_alone_move_nothing():
    # Q_mQxKviRJM 4:00 in miniature: the window passes hear the word 1.2 s in, the whole track
    # hears nothing there (its pass is lost elsewhere). Without a second emission pass: no move.
    rows = _stream(0.6, [1.2])
    lost = _whole([("ab", 0.27), ("ab", 0.57), ("ab", 9.0), ("ab", 9.5)])
    deaf = _whole([("ab", 0.27), ("ab", 0.57), ("ab", 1.27), ("ab", 2.07)], lp=FLOOR - 1)
    for whole in (lost, deaf, []):
        out, n = _run(rows, whole)
        assert n == 0 and out[2]["start"] == pytest.approx(0.6)


@pytest.mark.parametrize(("whole_ms", "moved"), [(1_699, True), (1_700, True), (1_701, False)])
def test_the_whole_track_hears_it_at_most_the_lead_after_the_earliest_pass(whole_ms, moved):
    rows = _stream(0.6, [1.2])
    whole = _whole([("ab", 0.27), ("ab", 0.57), ("ab", whole_ms / 1000 + 0.07), ("ab", 2.07)])
    out, n = _run(rows, whole)
    assert n == int(moved)
    assert out[2]["start"] == pytest.approx(1.2 if moved else 0.6)


@pytest.mark.parametrize(("lp", "moved"), [(FLOOR - 1e-6, False), (FLOOR, True), (-0.1, True)])
def test_the_whole_track_letter_counts_from_the_floor(lp, moved):
    rows = _stream(1.0, [])
    whole = _whole([("ab", 0.27), ("ab", 0.57), ("ab", 1.77), ("ab", 2.07)], lp=lp)
    out, n = _run(rows, whole)
    assert n == int(moved)
    assert out[2]["start"] == pytest.approx(1.7 if moved else 1.0)


@pytest.mark.parametrize("whole", [
    _whole([("ab", 0.27), ("ab", 0.57), ("ab", 1.77)]),  # one word short
    _whole([("ab", 0.27), ("ab", 0.57), ("ba", 1.77), ("ab", 2.07)]),  # another text
])
def test_a_whole_track_pass_that_does_not_pair_is_not_read(whole):
    out, n = _run(_stream(1.0, []), whole)
    assert n == 0 and out[2]["start"] == pytest.approx(1.0)


def test_a_word_inside_a_line_is_not_touched():
    rows = [_row("ab", 0.2, heard=[0.2]), _row("ab", 0.5, heard=[3.0]),
            _row("ab", 4.0, heard=[4.0]), _row("ab", 4.5, heard=[4.5])]
    out, n = _run(rows)
    assert n == 0 and out == rows


def test_the_first_line_counts_too_and_an_empty_line_is_skipped():
    rows = [_row("ab", 0.2, heard=[1.5]), _row("ab", 2.0, heard=[2.0]),
            _row("ab", 3.0, heard=[3.0]), _row("ab", 3.5, heard=[3.5])]
    out, n = _run(rows, line_words=(2, 0, 2))
    assert n == 1 and out[0]["start"] == pytest.approx(1.5)


def test_rows_that_are_no_word_are_not_counted():
    rows = [{"text": "<star>", "start": 0.0, "end": 0.1}, _row("ab", 0.2, heard=[0.2]),
            {"text": " ", "start": 0.3, "end": 0.3}, _row("ab", 0.5, heard=[0.5]),
            _row("ab", 0.6, heard=[1.2]), _row("ab", 2.0, heard=[2.0])]
    out, n = _run(rows)
    assert n == 1 and out[4]["start"] == pytest.approx(1.2) and out[2] == rows[2]


def test_a_stream_regroup_cannot_walk_is_left_alone():
    rows = _stream(0.6, [1.2])
    out, n = start_at_heard_onset(rows, [], [2, 3], 500, 70, FLOOR)
    assert n == 0 and out is rows


def test_the_end_follows_only_a_start_that_passes_it():
    rows = _stream(0.6, [1.2])
    rows[2] = {**rows[2], "end": 0.9}
    out, _ = _run(rows)
    assert out[2]["start"] == pytest.approx(1.2) and out[2]["end"] == pytest.approx(1.2)
    rows[2] = {**rows[2], "end": 1.5}
    out, _ = _run(rows)
    assert out[2]["end"] == pytest.approx(1.5)


@pytest.mark.parametrize(("stamp", "moved"), [(None, True), (4_499, False), (4_500, True),
                                               (4_501, True)])
def test_a_stamped_line_is_not_moved_past_line_qa_s_band(stamp, moved):
    # Bwsr24D7HWY 0:11 in miniature: the onset 7.0 s sits 2 501 / 2 500 / 2 499 ms past the stamp.
    rows = _stream(1.0, [7.0], second_start=8.0)
    out, n = _run(rows, stamps=[0, stamp], drift_ms=2_500)
    assert n == int(moved)
    assert out[2]["start"] == pytest.approx(7.0 if moved else 1.0)


def test_without_a_drift_bound_the_stamps_are_not_read():
    rows = _stream(1.0, [7.0], second_start=8.0)
    out, n = _run(rows, stamps=[0, 0])
    assert n == 1 and out[2]["start"] == pytest.approx(7.0)


# --- v5: only out of silence, the band on the document clock, ad-libs left to line QA ---------


def _env(quiet_frames: set[int], n: int = 400, quiet: int = 60, loud: int = 10) -> Envelope:
    """20 ms frames: `quiet_frames` at -30 dB (60 half-dB steps), the rest at -5 dB."""
    return Envelope(bytes(quiet if k in quiet_frames else loud for k in range(n)))


def _v5(rows, env, **kwargs):
    return _run(rows, envelope=env, silence_db=-25.0, silence_share=0.9, **kwargs)


def test_v5_moves_a_start_that_sits_in_silence():
    # start 300 ms, onset 1 000 ms: the span [300, 500) is frames 15..24
    rows = _stream(0.3, [1.0])
    out, n = _v5(rows, _env(set(range(15, 25))))
    assert n == 1 and out[2]["start"] == pytest.approx(1.0)


@pytest.mark.parametrize(("quiet", "moved"), [(9, True), (8, False)])
def test_v5_needs_nine_tenths_of_the_span_quiet(quiet, moved):
    rows = _stream(0.3, [1.0])
    out, n = _v5(rows, _env(set(range(15, 15 + quiet))))  # 10 frames in the span
    assert n == int(moved)
    assert out[2]["start"] == pytest.approx(1.0 if moved else 0.3)


def test_v5_reads_the_span_up_to_the_lead_before_the_new_start():
    # frames from 500 ms (onset 1 000 - lead 500) on are the word's own onset region: voiced there
    # is fine; one more voiced frame inside the span, at 480 ms, makes it 9 of 10
    rows = _stream(0.3, [1.0])
    voiced_after = _env(set(range(15, 25)) | set())
    assert _v5(rows, Envelope(voiced_after.levels[:25] + bytes([10] * 375)))[1] == 1
    assert _v5(rows, _env(set(range(15, 24))))[1] == 1  # frame 24 (480 ms) voiced: 0.9
    assert _v5(rows, _env(set(range(15, 23))))[1] == 0  # 23 and 24 voiced: 0.8


def test_v5_a_frame_exactly_on_the_threshold_is_not_silence():
    rows = _stream(0.3, [1.0])
    on_line = Envelope(bytes(50 if 15 <= k < 25 else 10 for k in range(400)))  # -25.0 dB
    under = Envelope(bytes(51 if 15 <= k < 25 else 10 for k in range(400)))  # -25.5 dB
    assert _v5(rows, on_line)[1] == 0
    assert _v5(rows, under)[1] == 1


def test_v5_without_an_envelope_moves_nothing():
    rows = _stream(0.3, [1.0])
    out, n = _v5(rows, None)
    assert n == 0 and out[2]["start"] == pytest.approx(0.3)


def test_v5_leaves_ad_lib_lines_to_line_qa():
    rows = _stream(0.3, [1.0])
    out, n = _v5(rows, _env(set(range(15, 25))), adlib=[False, True])
    assert n == 0
    assert _v5(rows, _env(set(range(15, 25))), adlib=[False, False])[1] == 1


@pytest.mark.parametrize(("onset_s", "moved"), [(2.459, True), (2.460, True), (2.461, False)])
def test_the_band_is_read_on_the_document_clock(onset_s, moved):
    # KAWASAKI in miniature: the word's lateness correction (+40 ms) puts the document start 40 ms
    # later than the stream's; line QA's median offset is 0 here (two of the three stamped lines
    # sit on their stamps on the document clock)
    rows = [_row("ab", -0.04, heard=[-0.04]), _row("ab", 0.1, heard=[0.1]),
            _row("ab", 0.3, heard=[onset_s]), _row("ab", 2.8, heard=[2.8]),
            _row("ab", 3.0, heard=[3.0])]
    out, n = _run(rows, line_words=(2, 2, 1), stamps=[0, 0, 3_040], drift_ms=2_500,
                  doc_offset=lambda text: 40)
    assert n == int(moved)


def test_the_band_follows_line_qa_s_median_offset():
    # three stamped lines run 300 ms late on the document clock: QA's median offset is +300, so a
    # move to 2 700 ms past the stamp (2 400 net of the offset) is inside the band
    rows = [_row("ab", 1.3, heard=[1.3]), _row("ab", 1.4, heard=[1.4]),
            _row("ab", 0.3, heard=[2.7]), _row("ab", 3.0, heard=[3.0]),
            _row("ab", 5.3, heard=[5.3]), _row("ab", 5.4, heard=[5.4])]
    rows[2]["start"] = 0.3
    stamps = [1_000, 0, 5_000]
    out, n = _run(rows, line_words=(2, 2, 2), stamps=stamps, drift_ms=2_500,
                  doc_offset=lambda text: 0)
    assert n == 1 and out[2]["start"] == pytest.approx(2.7)
    out, n = _run(rows, line_words=(2, 2, 2), stamps=stamps, drift_ms=2_500)  # edges clock (v4)
    assert n == 0


def test_other_keys_ride_along():
    rows = _stream(0.6, [1.2])
    rows[2] = {**rows[2], "star": "edges", "held": 0.3, "window": 4}
    out, _ = _run(rows)
    assert {k: out[2][k] for k in ("star", "held", "window", "text")} == {
        "star": "edges", "held": 0.3, "window": 4, "text": "ab"}


# --- the evidence: alignment._first_letters, _heard_at ------------------------------------------


@dataclass
class Segment:
    label: str
    start: int
    end: int


class Tokenizer:
    pad_token_id = 0

    def get_vocab(self):
        return {"<pad>": 0, "a": 1, "b": 2, "y": 3}


def _log_probs(n, spikes):
    lp = np.full((n, 5), np.log(0.001), dtype=np.float32)
    lp[:, 0] = np.log(0.9)
    for frame, (label, p) in spikes.items():
        lp[frame, label] = np.log(p)
    lp[:, 4] = 0.0
    return lp


def test_first_letters_reads_each_word_s_first_letter_on_its_own_frames():
    lp = _log_probs(60, {10: (1, 0.5), 11: (1, 0.7), 12: (2, 0.9), 40: (2, 0.0004)})
    text_starred = ["<star>", "ab", "b", "<star>"]
    spans = [[Segment("<star>", 0, 0)],
             [Segment("<pad>", 5, 9), Segment("a", 10, 11), Segment("b", 12, 12)],
             [Segment("<pad>", 13, 39), Segment("b", 40, 40)],
             [Segment("<star>", 41, 59)]]
    rows = [{"text": "ab", "start": 0.1, "end": 0.24}, {"text": "b", "start": 0.52, "end": 0.8}]
    out = alignment._first_letters(lp, 20.0, Tokenizer(), text_starred, spans, "<pad>", rows)
    assert out[0]["first_letter"] == pytest.approx([0.2, math.log(0.7)], abs=1e-6)
    assert out[1]["first_letter"] == pytest.approx([0.8, math.log(0.0004)], abs=1e-6)
    assert "first_letter" not in rows[0]  # a copy


def test_first_letters_skips_a_span_without_a_letter():
    lp = _log_probs(20, {})
    out = alignment._first_letters(lp, 20.0, Tokenizer(), ["<star>", "-", "<star>"],
                                   [[Segment("<star>", 0, 0)], [Segment("<pad>", 1, 5)],
                                    [Segment("<star>", 6, 19)]], "<pad>",
                                   [{"text": "-", "start": 0.0, "end": 0.1}])
    assert "first_letter" not in out[0]


def test_heard_at_keeps_letters_from_the_floor_on_the_edges_clock():
    rows = [{"first_letter": [1.0, FLOOR]}, {"first_letter": [1.1, FLOOR - 1e-6]},
            {"first_letter": [1.2, -0.1]}, {}]
    assert alignment._heard_at(rows, [0.0, 0.0, 0.07, 0.0]) == pytest.approx([1.0, 1.13])


def _fake_library(spans, results):
    def preprocess_text(text, romanize, language, split_size, star_frequency):
        words = text.split()
        return ["<star>", *[" ".join(w) for w in words], "<star>"], ["<star>", *words, "<star>"]

    return types.SimpleNamespace(
        preprocess_text=preprocess_text,
        get_alignments=lambda emissions, tokens, tokenizer: ([], None, "<pad>"),
        get_spans=lambda tokens, segments, blank: spans,
        postprocess_results=lambda text_starred, sp, stride, scores: [dict(r) for r in results],
    )


@pytest.mark.parametrize("letters", [False, True])
def test_viterbi_annotates_only_when_asked(monkeypatch, letters):
    lp = _log_probs(40, {10: (1, 0.9), 11: (2, 0.9)})
    spans = [[Segment("<star>", 0, 0)], [Segment("a", 10, 10), Segment("b", 11, 11)],
             [Segment("<star>", 12, 39)]]
    results = [{"text": "ab", "start": 0.2, "end": 0.22, "score": -0.1}]
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", _fake_library(spans, results))
    rows = alignment._viterbi(lp, 20.0, Tokenizer(), ["ab"], "eng", "edges", True, None, None,
                              letters)
    assert ("first_letter" in rows[0]) is letters
    assert {k: v for k, v in rows[0].items() if k != "first_letter"} == results[0]


# --- the wiring ---------------------------------------------------------------------------------


class FakeAudio:
    def __init__(self, n_samples: int):
        self.shape = (n_samples,)

    def __getitem__(self, key):
        _, window = key
        return FakeAudio(min(window.stop, self.shape[0]) - window.start)


def test_align_windows_carries_both_window_passes_letters_onto_the_edges_clock(monkeypatch):
    def fake_hybrid(model, tokenizer, piece, texts, language, *, romanize, exact_stride,
                    edges_starts=None, segment_starts=None, tally=None, letters=False):
        assert letters
        edges = [{"text": "ab", "start": 0.1, "end": 0.3, "score": -0.1,
                  "first_letter": [0.9, -0.2]},
                 {"text": "ab", "start": 1.5, "end": 1.7, "score": -0.1,
                  "first_letter": [1.5, FLOOR - 1]}]
        segment = [{"text": "ab", "start": 0.97, "end": 1.0, "score": -0.1,
                    "first_letter": [0.97, -0.3]},
                   {"text": "ab", "start": 1.57, "end": 1.6, "score": -0.1,
                    "first_letter": [1.57, -0.3]}]
        return edges, segment

    monkeypatch.setattr(alignment, "_align_slice_hybrid", fake_hybrid)
    window = Window(2_000, 6_000, [0, 1], 2_350, 5_650)
    rows = alignment._align_windows(None, None, FakeAudio(8 * 16_000), [window], ["ab", "ab"],
                                    "eng", True, exact_stride=True, letters=True)
    assert rows[0]["heard_at"] == pytest.approx([2.9, 2.9])
    assert rows[1]["heard_at"] == pytest.approx([3.5])


def test_align_windows_without_letters_carries_none(monkeypatch):
    def fake_hybrid(model, tokenizer, piece, texts, language, *, romanize, exact_stride,
                    edges_starts=None, segment_starts=None, tally=None, letters=False):
        assert not letters
        rows = [{"text": "ab", "start": 0.1, "end": 0.3, "score": -0.1}]
        return rows, rows

    monkeypatch.setattr(alignment, "_align_slice_hybrid", fake_hybrid)
    window = Window(2_000, 6_000, [0], 2_350, 5_650)
    rows = alignment._align_windows(None, None, FakeAudio(8 * 16_000), [window], ["ab"], "eng",
                                    True, exact_stride=True)
    assert "heard_at" not in rows[0]


def _align(monkeypatch, **kwargs):
    seen: dict = {"hybrid": [], "whole": [], "rule": []}
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", types.SimpleNamespace(
        load_audio=lambda *a, **k: FakeAudio(12 * 16_000)))
    model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (model, object()))

    def fake_hybrid(model, tokenizer, piece, texts, language, *, romanize, exact_stride,
                    edges_starts=None, segment_starts=None, tally=None, letters=False):
        seen["hybrid"].append(letters)
        rows = [{"text": w, "start": 0.5 + 0.3 * k, "end": 0.7 + 0.3 * k, "score": -0.1}
                for k, w in enumerate(" ".join(texts).split())]
        return rows, rows

    def fake_texts(model, tokenizer, audio, texts, language, star="segment", romanize=True, *,
                   exact_stride=False, starts_ms=None, tally=None, letters=False):
        seen["whole"].append(letters)
        return [{"text": "x", "start": 0.0, "end": 0.1, "score": -0.1}]

    def fake_rule(results, whole, line_words, lead_ms, edges_lead_ms, floor, *, stamps, drift_ms,
                  envelope, silence_db, silence_share, doc_offset, adlib):
        seen["rule"].append((len(results), whole[0]["text"], line_words, lead_ms, edges_lead_ms,
                             floor, stamps, drift_ms))
        seen["v5"] = (envelope, silence_db, silence_share, doc_offset("you"), doc_offset("uh"),
                      adlib)
        return results, 0

    monkeypatch.setattr(alignment, "_align_slice_hybrid", fake_hybrid)
    monkeypatch.setattr(alignment, "_align_texts", fake_texts)
    monkeypatch.setattr(alignment, "start_at_heard_onset", fake_rule)
    alignment.align("x.wav", ["you ready", "you ready", "get your sexy on", "uh yes"], "eng",
                    [2_000, 4_000, 5_000, 9_000], "m", True, -50,
                    {"vowel": -80, "sonorant": -20, "plosive": -30, "fricative": -50}, **kwargs)
    return seen


def test_align_runs_the_rule_last_with_the_whole_track_pass_and_the_line_counts(monkeypatch):
    marker = Envelope(b"\x00")
    seen = _align(monkeypatch, envelope=marker)
    assert seen["hybrid"] == [True] and seen["whole"] == [True]
    assert seen["rule"] == [(10, "x", [2, 2, 4, 2], 500, 70, FLOOR, [2_000, 4_000, 5_000, 9_000],
                             2_500)]
    # the document clock: the path's 70 ms lead on top of each initial's offset
    assert seen["v5"] == (marker, -25.0, 0.9, -20 + 70, -80 + 70, [False, False, False, False])
    assert alignment.FIRST_HEARD_DEFAULT == alignment.FIRST_HEARD_LEAD_MS == 500
    assert alignment.FIRST_LETTER_FLOOR == pytest.approx(math.log(0.001))
    assert (alignment.FIRST_HEARD_SILENCE_DB, alignment.FIRST_HEARD_SILENCE_SHARE) == (-25.0, 0.9)


def test_align_marks_the_ad_lib_lines_for_the_rule(monkeypatch):
    seen: dict = {}
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", types.SimpleNamespace(
        load_audio=lambda *a, **k: FakeAudio(12 * 16_000)))
    model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (model, object()))
    def hybrid(*a, **k):
        rows = [{"text": w, "start": 0.5, "end": 0.6, "score": -0.1}
                for w in " ".join(a[3]).split()]
        return rows, rows

    def rule(results, *a, **k):
        seen["adlib"] = k["adlib"]
        return results, 0

    monkeypatch.setattr(alignment, "_align_slice_hybrid", hybrid)
    monkeypatch.setattr(alignment, "_align_texts", lambda *a, **k: [])
    monkeypatch.setattr(alignment, "start_at_heard_onset", rule)
    alignment.align("x.wav", ["oh oh oh", "you ready", "la la", "uh yes"], "eng",
                    [2_000, 4_000, 5_000, 9_000], "m", True, -50, None)
    assert seen["adlib"] == [True, False, True, False]


def test_none_reproduces_2_34_0(monkeypatch):
    seen = _align(monkeypatch, first_heard_ms=None)
    assert seen == {"hybrid": [False], "whole": [False], "rule": []}
