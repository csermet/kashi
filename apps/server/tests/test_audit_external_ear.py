"""audit.external_ear (Faz 3.5 Step 2): pieces, the segment-timed ear file and the
Step 2 gates, on synthetic songs; the ASR backends are replaced by fakes (no
model, no GPU)."""

import json

import audit_synth as syn
import pytest

from audit import anchors, external_ear
from audit.external_ear import (
    Piece,
    in_silence,
    pieces,
    segment_ear,
    split_long,
    step2,
    transcribe_pieces,
    voiced_regions,
)
from audit.textmatch import letters

HOP = 10.0


def _frames(levels):
    return [(round(k * HOP), db) for k, db in enumerate(levels)]


def test_parameters_are_pinned():
    assert (external_ear.SILENCE_DB, external_ear.SILENCE_MS, external_ear.MAX_PIECE_MS) == \
        (-25.0, 500, 30_000)
    assert (external_ear.SPLIT_SEARCH_FROM_MS, external_ear.MIN_PIECE_MS) == (15_000, 300)
    assert (external_ear.FRAME_STRIDE_MS, external_ear.CTC_SILENT_PROB,
            external_ear.MIN_PLACED_FOR_MEDIAN) == (20.0, 0.2, 3)
    assert external_ear.BACKENDS == {"qwen3-asr": "Qwen/Qwen3-ASR-1.7B",
                                     "whisper": "large-v3-turbo"}
    assert (external_ear.QWEN_MAX_NEW_TOKENS, external_ear.QWEN_BATCH) == (512, 8)


def test_qwen_loads_on_the_card_in_bf16_when_there_is_one():
    on_card = external_ear.qwen3_load_kwargs(cuda=True)
    assert (on_card["device_map"], on_card["dtype"]) == ("cuda:0", "bfloat16")
    assert "device_map" not in external_ear.qwen3_load_kwargs(cuda=False)
    for kwargs in (on_card, external_ear.qwen3_load_kwargs(cuda=False)):
        assert (kwargs["max_new_tokens"], kwargs["max_inference_batch_size"]) == (512, 8)


def test_half_a_second_of_silence_cuts_a_shorter_dip_does_not():
    loud, quiet = -5.0, -40.0
    cut = _frames([loud] * 100 + [quiet] * 50 + [loud] * 100)      # 500 ms
    assert voiced_regions(cut, HOP) == [Piece(0, 1000), Piece(1500, 2500)]
    dip = _frames([loud] * 100 + [quiet] * 49 + [loud] * 100)      # 490 ms
    assert voiced_regions(dip, HOP) == [Piece(0, 2490)]
    # the threshold itself is voice
    assert voiced_regions(_frames([quiet] * 60 + [-25.0] * 40), HOP) == [Piece(600, 1000)]


def test_a_long_stretch_is_cut_at_its_quietest_frame_within_thirty_seconds():
    levels = [-5.0] * 7000                    # 70 s voiced
    levels[2000] = -20.0                      # 20 s: the quietest in [15 s, 30 s]
    levels[4500] = -21.0                      # 45 s: quietest in [35 s, 50 s]
    frames = _frames(levels)
    out = split_long(Piece(0, 70_000), frames)
    assert out == [Piece(0, 20_000), Piece(20_000, 45_000), Piece(45_000, 70_000)]
    assert split_long(Piece(0, 30_000), frames) == [Piece(0, 30_000)]
    assert len(split_long(Piece(0, 30_001), frames)) == 2


def test_breaths_are_not_transcribed():
    loud, quiet = -5.0, -40.0
    frames = _frames([quiet] * 60 + [loud] * 30 + [quiet] * 60 + [loud] * 29 + [quiet] * 60)
    assert pieces(frames, HOP) == [Piece(600, 900)]                # 300 kept, 290 dropped


def test_the_ear_file_has_the_whole_song_ears_shape():
    ear = segment_ear([{"start_ms": 1000, "end_ms": 2000, "text": "ab  cd"},
                       {"start_ms": 3000, "end_ms": 3500, "text": " "},
                       {"start_ms": 4000, "end_ms": 4400, "text": "夜空"}], 6000, "fake")
    ctc_keys = {"id", "model", "start_ms", "stride_ms", "text", "tokens", "letter_prob",
                "letter_prob_max", "frames", "finite"}
    assert ctc_keys <= set(ear) and ear["timing"] == "segment"
    assert [t["text"] for t in ear["tokens"]] == ["a", "b", "|", "c", "d", "夜", "空"]
    assert [t["start_ms"] for t in ear["tokens"][:5]] == [1000, 1200, 1400, 1600, 1800]
    assert ear["frames"] == 300
    prob = ear["letter_prob"]
    assert prob[49] == 0.0 and prob[50] == 1.0 and prob[99] == 1.0 and prob[100] == 0.0
    assert prob[160] == 0.0                   # a piece with no text is not "sung"


def test_backends_get_each_piece_and_their_texts_come_back_in_order():
    import numpy as np

    samples = np.arange(16_000 * 3, dtype=np.float32)
    seen = []

    def fake(audio, language):
        seen.extend((len(a), sr, language) for a, sr in audio)
        return [f"t{k}" for k in range(len(audio))]

    out = transcribe_pieces(samples, 16_000, [Piece(0, 1000), Piece(1500, 3000)], fake, "en")
    assert seen == [(16_000, 16_000, "en"), (24_000, 16_000, "en")]
    assert out == [{"start_ms": 0, "end_ms": 1000, "text": "t0"},
                   {"start_ms": 1500, "end_ms": 3000, "text": "t1"}]


def _ext_ear(texts, sung_at, total, extra=()):
    """What a perfect lyrics-free ASR returns, piece by sung line."""
    segs = [{"start_ms": t, "end_ms": t + len(letters(x)) * syn.MS_PER_LETTER, "text": x}
            for x, t in zip(texts, sung_at, strict=True)]
    return segment_ear(segs + list(extra), total, "fake")


def test_the_consumers_read_the_external_ear_unchanged():
    texts, true, _, total = syn.song(1, 16)
    ext = _ext_ear(texts, true, total)
    result = anchors.check(syn.sheet(texts, true), ext)
    assert result["flags"] == [] and len(result["anchors"]) == 16
    shifted = anchors.check(syn.sheet(texts, [s + 6000 for s in true]), ext)
    assert shifted["flags"] == ["shift"] and abs(shifted["sections"][0]["median_ms"] + 6000) < 200


def test_a_piece_inside_ctc_silence_longer_than_two_seconds_counts():
    ctc = {"letter_prob": [0.9] * 50 + [0.1] * 101 + [0.9] * 50 + [0.1] * 100 + [0.9] * 10,
           "stride_ms": 20.0, "start_ms": 0}
    # silent 1000-3020 (2020 ms > 2000) and 4020-6020 (exactly 2000 ms: not counted)
    ext = {"segments": [{"start_ms": 1500, "end_ms": 2500, "text": "ghost"},
                        {"start_ms": 4500, "end_ms": 5500, "text": "edge"},
                        {"start_ms": 0, "end_ms": 900, "text": "real"},
                        {"start_ms": 1600, "end_ms": 2400, "text": " "}]}
    assert in_silence(ext, ctc) == (1, 3)


def _doc(texts, stamps):
    return {"sync": "word", "lines": [
        {"text": t, "start_ms": s, "end_ms": s + 2000,
         "words": [{"text": w, "start_ms": s + 450 * k, "end_ms": s + 450 * k + 400}
                   for k, w in enumerate(t.split())]}
        for t, s in zip(texts, stamps, strict=True)]}


@pytest.fixture
def three():
    out = []
    for seed, late in ((1, 0), (2, 0), (3, 4000)):
        texts, true, ctc, total = syn.song(seed, 16)
        out.append((f"synth{seed}", _doc(texts, [s + late for s in true]), ctc,
                    _ext_ear(texts, true, total)))
    return out


EXPECT = {"synth3": {"expect": "shift", "offset_ms": -4000}}


def test_step2_passes_on_a_good_second_ear(three):
    result = step2(three, EXPECT)
    assert [r["kind"] for r in result["songs"]] == ["healthy", "healthy", "shifted"]
    assert result["songs"][2]["median_ms"] < -3500
    assert result["gates"] == {"placed_share": True, "healthy_median": True,
                               "shifted_median": True, "silent": True}


def test_step2_fails_each_gate_on_its_own(three):
    # an ear that hears fewer lines than the CTC ear
    deaf = [(v, d, c, _ext_ear([x for x in syn.song(s, 16)[0][:8]], syn.song(s, 16)[1][:8],
                                syn.song(s, 16)[3]))
            for (v, d, c, _), s in zip(three, (1, 2, 3), strict=True)]
    assert not step2(deaf, EXPECT)["gates"]["placed_share"]
    # a healthy song read 600 ms off
    texts, true, ctc, total = syn.song(1, 16)
    off = [(three[0][0], three[0][1], ctc, _ext_ear(texts, [s - 600 for s in true], total))]
    assert not step2(off + three[1:], EXPECT)["gates"]["healthy_median"]
    # the shifted song with the wrong sign expected
    assert not step2(three, {"synth3": {"expect": "shift", "offset_ms": 4000}})["gates"][
        "shifted_median"]
    # hallucinated pieces in the CTC ear's silence
    texts, true, ctc, total = syn.song(2, 16)
    ghosts = [{"start_ms": total - 6000 + 300 * k, "end_ms": total - 5800 + 300 * k,
               "text": "ghost words"} for k in range(3)]
    haunted = [three[1][:3] + (_ext_ear(texts, true, total, ghosts),)]
    assert not step2(three[:1] + haunted + three[2:], EXPECT)["gates"]["silent"]


def test_a_healthy_median_exactly_on_the_gate_passes(three):
    texts, true, ctc, total = syn.song(1, 16)
    at = [(three[0][0], three[0][1], ctc, _ext_ear(texts, [s - 500 for s in true], total))]
    result = step2(at + three[1:], EXPECT)
    assert result["songs"][0]["median_ms"] == -500 and result["gates"]["healthy_median"]


def test_the_cli_imports_a_transcript_and_runs_step2(three, tmp_path, monkeypatch, capsys):
    import audit.ear

    monkeypatch.setattr(audit.ear, "EAR_DIR", tmp_path / "ear")
    frames = _frames([-5.0] * 100)
    monkeypatch.setattr(external_ear, "_analysis_frames", lambda vid: (frames, HOP))
    dump = []
    for vid, doc, ctc, ext in three:
        (tmp_path / "ear" / vid).mkdir(parents=True)
        (tmp_path / "ear" / vid / "whole.json").write_text(json.dumps(ctc))
        transcript = tmp_path / f"{vid}.json"
        transcript.write_text(json.dumps(ext["segments"]))
        assert external_ear.main(["import", vid, "--transcript", str(transcript),
                                  "--model", "qwen3-asr"]) == 0
        dump.append({"id": vid, "doc": doc})
    written = json.loads((tmp_path / "ear" / "synth1" / "qwen3-asr.json").read_text())
    assert written["timing"] == "segment" and written["id"] == "synth1"
    (tmp_path / "dump.json").write_text(json.dumps(dump))
    (tmp_path / "expect.json").write_text(json.dumps(EXPECT))
    assert external_ear.main(["step2", "--doc", str(tmp_path / "dump.json"), "--ear",
                              "qwen3-asr", "--expect", str(tmp_path / "expect.json")]) == 0
    assert "Step 2 (qwen3-asr, 3 songs): passed" in capsys.readouterr().out
    assert external_ear.main(["step2", "--doc", str(tmp_path / "dump.json"), "--ear",
                              "nothing"]) == 2


def test_placements_an_alike_neighbour_owns_are_left_out(monkeypatch):
    import audit.finder

    monkeypatch.setattr(audit.finder, "line_offsets",
                        lambda doc, ear: ({0: 40, 1: -3000, 2: 60}, {1}))
    assert external_ear.placed_offsets({"lines": [{}, {}, {}]}, {}) == (3, [40, 60])


def test_songs_marked_unhealthy_and_wrong_sheets_stay_out_of_the_healthy_gate(three):
    texts, true, ctc, total = syn.song(1, 16)
    off = (three[0][0], three[0][1], ctc, _ext_ear(texts, [s - 2000 for s in true], total))
    songs = [off] + three[1:]
    assert not step2(songs, EXPECT)["gates"]["healthy_median"]
    for mark in ({"healthy": False}, {"expect": "wrong_sheet"}):
        result = step2(songs, {**EXPECT, "synth1": mark})
        assert result["songs"][0]["kind"] == "other" and result["gates"]["healthy_median"]


def test_a_median_needs_three_placed_lines(three):
    texts, true, ctc, total = syn.song(1, 16)
    for n, has in ((3, True), (2, False)):
        few = [(three[0][0], three[0][1], ctc, _ext_ear(texts[:n], true[:n], total))]
        row = step2(few + three[1:], EXPECT)["songs"][0]
        assert (row["placed_external"], row["median_ms"] is not None) == (n, has)


def test_a_japanese_sheet_is_reported_and_kept_out_of_the_gates(three):
    jp_doc = {"sync": "word", "lines": [
        {"text": "今日は天気がいいですね", "start_ms": 5000, "end_ms": 7000,
         "words": [{"text": "今日は天気がいいですね", "start_ms": 5000, "end_ms": 7000}]}]}
    # the CTC ear (romaji) places the line, the second ear hears nothing: pooled
    # into the gate, the placed share would fail
    romaji = "kyou wa tenki ga ii desu ne"
    ctc = {"tokens": [{"text": "|" if c == " " else c, "start_ms": 5050 + 90 * n}
                      for n, c in enumerate(romaji)],
           "letter_prob": [0.9] * 500, "stride_ms": 20.0, "start_ms": 0}
    nothing = segment_ear([], 10_000, "fake")
    result = step2(three + [("synthJp01", jp_doc, ctc, nothing)], EXPECT)
    assert result["songs"][-1]["kind"] == "japanese"
    assert result["japanese"] == [{"id": "synthJp01", "lines": 1, "placed_ctc": 1,
                                   "placed_external": 0, "median_ms": None}]
    assert result["passed"]
