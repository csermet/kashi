"""pipeline/level.py: the stem's level envelope, one byte per 20 ms frame, half-dB steps under the
loudest frame. Values sit on the frame edge (320 samples), the rounding edges and the floor."""

import math
import wave

import numpy as np
import pytest

from kashi_server.pipeline import evidence, level
from kashi_server.pipeline.level import Envelope


def _wav(path, samples, *, rate=16_000, channels=1, width=2):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(width)
        f.setframerate(rate)
        f.writeframes(np.asarray(samples, dtype="<i2" if width == 2 else "<i4").tobytes())
    return path


def test_a_frame_is_one_emission_frame():
    assert (level.FRAME_SAMPLES, level.STEP_MS) == (320, 20)
    assert (level.UNIT_DB, level.FLOOR_STEP) == (0.5, 255)


def test_mean_squares_are_exact_per_frame_and_the_last_frame_uses_what_it_has():
    samples = [3] * 320 + [-4] * 320 + [10] * 5
    assert level.mean_squares(samples) == [9.0, 16.0, 100.0]
    assert level.mean_squares([]) == []
    big = [-32_768] * 320
    assert level.mean_squares(big) == [float(32_768**2)]  # no int16 overflow


@pytest.mark.parametrize(("ratio", "steps"), [
    (1.0, 0),            # the loudest frame
    (0.1, 20),           # 10 dB under it
    (0.01, 40),          # 20 dB
    (10 ** -0.02, 0),    # 0.2 dB rounds down
    (10 ** -0.03, 1),    # 0.3 dB rounds up to one half-dB step
    (10 ** -12.7, 254),  # 127 dB
    (1e-13, 255),        # 130 dB: clipped to the floor
    (0.0, 255),          # no energy at all
])
def test_quantize_in_half_db_steps_under_the_loudest(ratio, steps):
    assert level.quantize([1.0, ratio])[1] == steps
    assert level.quantize([1.0, ratio])[0] == 0


def test_quantize_rounds_half_up():
    # -20 log10(r) = 0.5 exactly needs r = 10 ** -0.025; the float lands within an ulp of the edge,
    # so the edge itself is pinned through the formula: floor(x + 0.5)
    assert math.floor(0.5 + 0.5) == 1 and math.floor(0.49999999 + 0.5) == 0
    assert level.quantize([4.0, 4.0 * 10 ** -0.0255])[1] == 1
    assert level.quantize([4.0, 4.0 * 10 ** -0.0245])[1] == 0


def test_a_silent_track_is_a_value_of_floor_steps():
    assert level.quantize([0.0, 0.0]) == bytes([255, 255])
    assert level.quantize([]) == b""


def test_envelope_reads_frames_and_shares():
    env = Envelope(bytes([0, 60, 40, 51, 49, 255]))
    assert [env.db(k) for k in range(6)] == [0.0, -30.0, -20.0, -25.5, -24.5, -127.5]
    # frames starting in [20, 100): 1..4 -> below -25 dB: frames 1 (-30) and 3 (-25.5)
    assert env.quiet_share(20, 100, -25.0) == 0.5
    # a frame exactly at the threshold is not below it
    assert Envelope(bytes([50])).quiet_share(0, 20, -25.0) == 0.0
    # [21, 40): no frame starts there -> None; [19, 21): frame 1 only
    assert env.quiet_share(21, 40, -25.0) is None
    assert env.quiet_share(19, 21, -25.0) == 1.0
    assert env.quiet_share(-100, 20, -25.0) == 0.0  # clipped to the track
    assert env.quiet_share(100, 10_000, -25.0) == 1.0


def test_level_envelope_reads_the_aligner_wav(tmp_path):
    loud, quiet = 10_000, 100  # 40 dB apart
    samples = np.concatenate(
        [np.full(320, loud), np.full(320, quiet), np.zeros(320), np.full(7, loud)])
    env = level.level_envelope(_wav(tmp_path / "a.wav", samples))
    assert env is not None
    assert env.levels == bytes([0, 80, 255, 0])


@pytest.mark.parametrize("kw", [{"rate": 44_100}, {"channels": 2}, {"width": 4}])
def test_a_wav_that_is_not_the_aligner_s_fails_and_says_so(tmp_path, kw):
    path = _wav(tmp_path / "x.wav", np.zeros(640), **kw)
    assert level.level_envelope(path) is None
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        assert level.level_envelope(path) is None
    assert not recorder.usable and recorder.why_not == "level envelope failed"


def test_an_unreadable_wav_fails_and_says_so(tmp_path):
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        assert level.level_envelope(tmp_path / "missing.wav") is None
    assert not recorder.usable


def test_the_bytes_are_pinned(tmp_path):
    """The definition, end to end, on a fixed signal (a change here is an evidence code bump)."""
    t = np.arange(16_000, dtype=np.int64)  # integer arithmetic only: the input is exact too
    samples = (((t * 37) % 2_001 - 1_000) * (t // 160 + 1) // 4).astype(np.int16)
    env = level.level_envelope(_wav(tmp_path / "s.wav", samples))
    assert env is not None and len(env.levels) == 50
    assert env.levels.hex() == (
        "483a322d292522201e1c1b1918161514131211100f0e0e0d0c0b0b0a09090808070706060505040403030202"
        "020101010000"
    )
