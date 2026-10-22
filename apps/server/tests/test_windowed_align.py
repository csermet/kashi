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
    word correctly at 6.15 s: the Stressed Out shape."""
    seen: dict = {"exact": []}
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: FakeAudio(TOTAL_S * 16_000)),
    )
    fake_model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (fake_model, object()))

    def fake_align_texts(model, tokenizer, piece, texts, language, star, *, romanize, exact_stride):
        seen["exact"].append(exact_stride)
        words = texts[0].split()
        if texts[0] == LINES[0]:  # window 0: slice starts at 0.65 s
            rel = [0.5, 2.0, 5.9]  # "three" -> 6.55 s absolute
        elif texts[0] == LINES[1]:  # window 1: slice starts at 5.65 s
            rel = [0.5, 1.5, 3.0]  # "four" -> 6.15 s absolute
        else:
            rel = [0.5, 1.5, 3.0]
        return [
            {"text": t, "start": r, "end": r + 0.2, "score": -0.1}
            for t, r in zip(words, rel, strict=True)
        ]

    monkeypatch.setattr(alignment, "_align_texts", fake_align_texts)
    result = alignment.align(tmp_path / "x.wav", LINES, "eng", STAMPS, "m", True, 0, None, **kwargs)
    return result, seen


def test_the_shipped_defaults_still_pile_the_next_line_up(monkeypatch, tmp_path):
    result, seen = run_align(monkeypatch, tmp_path)
    assert result.windowed
    assert seen["exact"] == [False] * 4
    four, five = result.words_per_line[1][:2]
    assert four.start_ms == 6_550  # pushed onto the stray "three"
    assert four.end_ms == four.start_ms  # ...and zero-length


def test_seam_policy_yield_keeps_the_next_line_in_place(monkeypatch, tmp_path):
    result, _ = run_align(monkeypatch, tmp_path, seam_policy="yield")
    three = result.words_per_line[0][-1]
    four = result.words_per_line[1][0]
    assert four.start_ms == 6_150
    assert (three.start_ms, three.end_ms) == (6_130, 6_150)


def test_exact_stride_reaches_every_window(monkeypatch, tmp_path):
    _, seen = run_align(monkeypatch, tmp_path, exact_stride=True)
    assert seen["exact"] == [True] * 4


def test_an_unknown_seam_policy_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        run_align(monkeypatch, tmp_path, seam_policy="median")
