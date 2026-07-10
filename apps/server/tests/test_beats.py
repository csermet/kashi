"""Beat extraction against a synthetic click track (librosa, no network)."""

import wave

import numpy as np
import pytest

from kashi_server.pipeline.beats import extract_beats

BPM = 120.0


@pytest.fixture(scope="module")
def click_track(tmp_path_factory):
    """20 s of clicks at 120 BPM (every 0.5 s), 22050 Hz mono."""
    rate = 22050
    seconds = 20
    samples = np.zeros(rate * seconds, dtype=np.float32)
    step = int(rate * 60 / BPM)
    click = (np.sin(2 * np.pi * 1000 * np.arange(rate // 50) / rate) * 0.9).astype(np.float32)
    for start in range(0, len(samples) - len(click), step):
        samples[start : start + len(click)] += click
    path = tmp_path_factory.mktemp("beats") / "clicks.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((samples * 32767).astype(np.int16).tobytes())
    return path


def test_click_track_tempo_and_grid(click_track):
    beats = extract_beats(click_track)
    assert beats is not None
    assert beats.bpm == pytest.approx(BPM, rel=0.06)
    assert len(beats.times_ms) >= 30
    intervals = np.diff(beats.times_ms)
    assert abs(float(np.median(intervals)) - 500) < 40  # 120 BPM -> 500 ms
    assert beats.confidence > 0.8  # metronome-steady
    assert beats.downbeat_indices[0] < 4
    pairs = zip(beats.downbeat_indices, beats.downbeat_indices[1:], strict=False)
    assert all(b - a == 4 for a, b in pairs)
    assert all(isinstance(t, int) for t in beats.times_ms)


def test_silence_yields_no_grid(tmp_path):
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 22050)
    assert extract_beats(path) is None  # 1 s of silence: <8 beats


def test_garbage_file_never_raises(tmp_path):
    path = tmp_path / "junk.wav"
    path.write_bytes(b"not audio at all")
    assert extract_beats(path) is None
