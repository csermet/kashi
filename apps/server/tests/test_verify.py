"""verify_audio_file against real files produced by ffmpeg/stdlib wave."""

import subprocess
import wave

import pytest

from kashi_server.vdl_kit.verify import run_ffprobe, verify_audio_file

pytestmark = pytest.mark.skipif(
    subprocess.run(["which", "ffprobe"], capture_output=True, check=False).returncode != 0,
    reason="ffprobe not installed",
)


def _write_wav(path, seconds=6.0, rate=44100):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(rate * seconds))
    return path


def test_healthy_wav_passes(tmp_path):
    ok, reason, size = verify_audio_file(_write_wav(tmp_path / "a.wav"))
    assert ok and reason is None and size > 200 * 1024


def test_missing_file(tmp_path):
    ok, reason, size = verify_audio_file(tmp_path / "nope.wav")
    assert not ok and size == 0 and "missing" in reason


def test_truncated_file_rejected(tmp_path):
    tiny = tmp_path / "tiny.wav"
    _write_wav(tiny, seconds=0.1)
    ok, reason, _ = verify_audio_file(tiny)
    assert not ok and "too small" in reason


def test_garbage_file_rejected(tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"\x00" * (300 * 1024))
    ok, reason, _ = verify_audio_file(junk)
    assert not ok and reason in ("ffprobe failed", "no audio stream")


def test_ffprobe_returns_none_for_garbage(tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not media")
    assert run_ffprobe(junk) is None
