"""Faz 6 P3: energy envelope + energy-derived sections (synthesized audio —
librosa is a base dep, so these run in the fast CI job)."""

import math
import struct
import wave

from kashi_server.pipeline.energy import RATE_HZ, extract_energy

_SR = 22050


def _write_wav(path, segments: list[tuple[float, float]]) -> None:
    """segments: (duration_s, amplitude 0..1) of 220 Hz sine, 16-bit mono."""
    frames = bytearray()
    t = 0
    for duration_s, amp in segments:
        for _ in range(int(duration_s * _SR)):
            sample = amp * math.sin(2 * math.pi * 220 * t / _SR)
            frames += struct.pack("<h", int(sample * 32000))
            t += 1
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(_SR)
        f.writeframes(bytes(frames))


def test_quiet_loud_quiet_yields_one_high_section(tmp_path):
    wav = tmp_path / "song.wav"
    _write_wav(wav, [(12, 0.05), (12, 0.9), (12, 0.05)])
    out = extract_energy(wav)
    assert out is not None
    energy, sections = out
    assert energy.rate_hz == RATE_HZ
    assert len(energy.values) in range(int(36 * RATE_HZ) - 2, int(36 * RATE_HZ) + 3)
    assert all(0 <= v <= 100 for v in energy.values)
    # The loud middle third reads hotter than the quiet edges.
    third = len(energy.values) // 3
    assert min(energy.values[third + 2 : 2 * third - 2]) > max(
        energy.values[2 : third - 4]
    )
    assert len(sections) == 1
    section = sections[0]
    assert section.type == "high"
    # Section brackets the loud block (smoothing blurs the edges a little).
    assert 9_000 <= section.start_ms <= 15_000
    assert 21_000 <= section.end_ms <= 27_000


def test_extraction_is_deterministic(tmp_path):
    wav = tmp_path / "song.wav"
    _write_wav(wav, [(10, 0.1), (10, 0.8)])
    assert extract_energy(wav) == extract_energy(wav)


def test_too_short_clip_is_omitted(tmp_path):
    wav = tmp_path / "blip.wav"
    _write_wav(wav, [(0.5, 0.5)])
    assert extract_energy(wav) is None


def test_missing_file_never_raises(tmp_path):
    assert extract_energy(tmp_path / "nope.wav") is None
