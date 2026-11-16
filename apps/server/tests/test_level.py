"""pipeline/level.py: the stem's level envelope - per 20 ms frame a half-dB step under the loudest
frame and a zero-crossing count, and the loudest frame's exact mean square. Values sit on the
frame edge (320 samples), the step edges, the crossing cap and the floor."""

import hashlib
import math
import wave
from fractions import Fraction

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
    assert (level.UNIT_DB, level.FLOOR_STEP, level.CROSSINGS_CAP) == (0.5, 255, 255)


def test_the_step_edges_are_pinned_and_sit_on_the_half_db_boundaries():
    edges = level.STEP_EDGES
    assert len(edges) == 255 and all(a > b > 0 for a, b in zip(edges, edges[1:], strict=False))
    for j in (0, 1, 49, 50, 254):  # the float formula agrees to ~1e-15 relative
        assert edges[j] / 2**96 == pytest.approx(10 ** (-(j + 0.5) / 20), rel=1e-14)
    digest = hashlib.sha256(",".join(map(str, edges)).encode()).hexdigest()[:16]
    assert digest == "d57c848457c69f23"


def test_frame_stats_are_exact_per_frame_and_the_last_frame_uses_what_it_has():
    samples = [3] * 320 + [-4] * 320 + [10] * 5
    sums, counts, crossings = level.frame_stats(samples)
    assert (sums, counts) == ([9 * 320, 16 * 320, 500], [320, 320, 5])
    assert crossings == bytes([0, 0, 0])
    assert level.frame_stats([]) == ([], [], b"")
    big = [-32_768] * 320
    assert level.frame_stats(big)[0] == [32_768**2 * 320]  # no int16 overflow


def test_crossings_count_sign_changes_inside_the_frame_with_zero_as_non_negative():
    # 1, -1, 0, -1, 1: pairs (1,-1) (-1,0) (0,-1) (-1,1) all change sign -> 4; a frame of zeros 0
    frame = [1, -1, 0, -1, 1] + [1] * 315
    assert level.frame_stats(frame)[2] == bytes([4])
    assert level.frame_stats([0] * 320)[2] == bytes([0])
    assert level.frame_stats([0, -1] * 160)[2] == bytes([255])  # 319 changes, capped
    assert level.frame_stats([0, -1] * 127 + [0, 0] + [0] * 64)[2] == bytes([254])
    assert level.frame_stats([0, -1] * 128 + [-1] * 64)[2] == bytes([255])  # 255 exactly
    assert level.frame_stats([0, -1] * 128 + [0] * 64)[2] == bytes([255])  # 256, capped
    # the pair across a frame edge belongs to neither frame
    assert level.frame_stats([1] * 320 + [-1] * 320)[2] == bytes([0, 0])
    # the last frame's pairs: 3 samples, 2 pairs; its zero is non-negative too
    assert level.frame_stats([1] * 320 + [1, -1, 1])[2] == bytes([0, 2])
    assert level.frame_stats([1] * 320 + [-1, 0, -1])[2] == bytes([0, 2])


def _steps_for(ratio: Fraction) -> int:
    """quantize on a two-frame track [loudest, ratio x loudest] with exact integer sums."""
    top = 2**40 * ratio.denominator
    return level.quantize([top, int(top * ratio)], [320, 320])[0][1]


@pytest.mark.parametrize(("ratio", "steps"), [
    (Fraction(1), 0),            # the loudest frame
    (Fraction(1, 10), 20),       # 10 dB under it
    (Fraction(1, 100), 40),      # 20 dB
    (Fraction(0), 255),          # no energy at all
])
def test_quantize_in_half_db_steps_under_the_loudest(ratio, steps):
    assert _steps_for(ratio) == steps


@pytest.mark.parametrize("j", [0, 1, 19, 20, 100, 253, 254])
def test_a_frame_exactly_on_an_edge_takes_the_lower_step_one_above_it_the_higher(j):
    # r <= edge_j / 2^96 is step >= j + 1 (half up); one unit above the edge stays at step j
    edge = level.STEP_EDGES[j]
    top = 2**96  # the loudest frame's sum, so a frame's sum IS the ratio's numerator
    on, above = level.quantize([top, edge, edge + 1], [320, 320, 320])[0][1:]
    assert (on, above) == (j + 1, j)


def test_the_loudest_is_compared_exactly_across_frame_sizes():
    # the tail frame (5 samples, mean 100) is louder than a full frame of mean 99
    assert level.quantize([99 * 320, 500], [320, 5])[1] == (500, 5)
    # mean 64 against 100: 1.94 dB, 4 half-dB steps (3.88 rounds up)
    assert level.quantize([64 * 320, 500], [320, 5]) == (bytes([4, 0]), (500, 5))
    # equal means: the first such frame is the loudest
    assert level.quantize([100 * 320, 500], [320, 5])[1] == (100 * 320, 320)


def test_a_silent_track_is_a_value_of_floor_steps():
    assert level.quantize([0, 0], [320, 320]) == (bytes([255, 255]), (0, 320))
    assert level.quantize([], []) == (b"", (0, 0))


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


def test_the_loudest_frame_puts_the_levels_on_a_dbfs_scale():
    assert Envelope(b"\x00", loudest=(32_768**2 * 320, 320)).loudest_dbfs() == 0.0
    assert Envelope(b"\x00", loudest=(32_768**2 * 32, 320)).loudest_dbfs() == pytest.approx(-10.0)
    assert Envelope(b"\xff", loudest=(0, 320)).loudest_dbfs() is None


def test_level_envelope_reads_the_aligner_wav(tmp_path):
    loud, quiet = 10_000, 100  # 40 dB apart
    samples = np.concatenate(
        [np.full(320, loud), np.full(320, quiet), np.zeros(320), np.full(7, loud)])
    env = level.level_envelope(_wav(tmp_path / "a.wav", samples))
    assert env is not None
    assert env.levels == bytes([0, 80, 255, 0])
    assert env.crossings == bytes([0, 0, 0, 0])
    assert env.loudest == (loud**2 * 320, 320)


def test_the_wav_is_read_in_chunks_with_the_same_result(tmp_path, monkeypatch):
    t = np.arange(5 * 320 + 77, dtype=np.int64)
    samples = ((t * 7919) % 4001 - 2000).astype(np.int16)
    path = _wav(tmp_path / "c.wav", samples)
    whole = level.level_envelope(path)
    monkeypatch.setattr(level, "CHUNK_FRAMES", 2)  # 3 reads of 640 samples, the last one short
    chunked = level.level_envelope(path)
    assert whole is not None and chunked == whole and len(whole.levels) == 6


def test_a_short_read_before_the_end_fails(tmp_path, monkeypatch):
    path = _wav(tmp_path / "s.wav", np.ones(3 * 320, dtype=np.int16))
    reads = iter([b"\x01\x00" * 300, b"\x01\x00" * 660, b""])
    real_open = wave.open

    def short_open(*a, **k):
        f = real_open(*a, **k)
        f.readframes = lambda n: next(reads)
        return f

    monkeypatch.setattr(level.wave, "open", short_open)
    assert level.level_envelope(path) is None


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
    assert env.crossings.hex() == (
        "0b0c0c0c0c0b0b0c0c0c0c0b0b0c0c0c0c0b0b0c0c0c0c0b0b0c0c0c0c0b0b0c0c0c0c0b0b0c0c0c0c0b0b0c"
        "0c0c0c0b0b0c"
    )
    assert env.loudest == (int((samples[-320:].astype(np.int64) ** 2).sum()), 320)
    assert env.loudest_dbfs() == pytest.approx(10 * math.log10(env.loudest[0] / 320 / 2**30))
