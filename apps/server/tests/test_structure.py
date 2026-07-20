"""Structure v2 (Faz 6.5 P6): pure labeling rules fast; synthetic-audio
segmentation e2e under the slow marker (librosa CQT costs seconds)."""

import pytest

from kashi_server.pipeline.energy import Energy
from kashi_server.pipeline.structure import Segment, label_segments


def _energy(values: list[int], rate_hz: int = 2) -> Energy:
    return Energy(rate_hz=rate_hz, values=values)


def test_most_repeated_energetic_cluster_becomes_chorus():
    # Cluster 1 repeats (3 spans) and sits loud; cluster 0 repeats quietly.
    segments = [
        Segment(0, 10, 0),
        Segment(10, 20, 1),
        Segment(20, 30, 0),
        Segment(30, 40, 1),
        Segment(40, 50, 2),
        Segment(50, 60, 1),
    ]
    in_cluster_1 = lambda sec: 10 <= sec < 20 or 30 <= sec < 40 or 50 <= sec < 60  # noqa: E731
    loud_in_1 = [90 if in_cluster_1(i / 2) else 30 for i in range(120)]
    sections = label_segments(segments, _energy(loud_in_1))
    assert [s.type for s in sections] == ["chorus", "chorus", "chorus"]
    assert [(s.start_ms, s.end_ms) for s in sections] == [
        (10000, 20000),
        (30000, 40000),
        (50000, 60000),
    ]


def test_no_repetition_means_no_sections_honestly():
    segments = [Segment(0, 30, 0), Segment(30, 60, 1), Segment(60, 90, 2)]
    assert label_segments(segments, _energy([50] * 180)) == []


def test_short_spans_fall_below_the_noise_floor():
    segments = [
        Segment(0, 4, 1),  # < 8 s — dropped even from the winning cluster
        Segment(10, 30, 1),
        Segment(40, 44, 0),
        Segment(50, 54, 0),
    ]
    sections = label_segments(segments, _energy([50] * 120))
    assert [(s.start_ms, s.end_ms) for s in sections] == [(10000, 30000)]


def test_energy_breaks_repetition_ties_deterministically():
    # Two clusters, two spans each; cluster 1's spans are louder — it wins.
    segments = [
        Segment(0, 10, 0),
        Segment(10, 20, 1),
        Segment(20, 30, 0),
        Segment(30, 40, 1),
    ]
    values = [20] * 20 + [90] * 20 + [20] * 20 + [90] * 20  # 2 Hz over 40 s
    sections = label_segments(segments, _energy(values))
    assert [(s.start_ms, s.end_ms) for s in sections] == [(10000, 20000), (30000, 40000)]
    # No energy at all → count ties fall to the LOWER cluster id (stable).
    tied = label_segments(segments, None)
    assert [(s.start_ms, s.end_ms) for s in tied] == [(0, 10000), (20000, 30000)]


@pytest.mark.slow
def test_synthetic_ab_form_segments_and_repeats_deterministically(tmp_path):
    import numpy as np
    import soundfile as sf

    from kashi_server.pipeline.structure import extract_structure

    sr = 22050
    rng = np.random.default_rng(7)

    def block(freqs: list[float], seconds: float) -> "np.ndarray":
        t = np.arange(int(sr * seconds)) / sr
        sig = sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs)
        # Pure sines give beat_track nothing to hold onto — gate the signal
        # at 120 BPM (2 Hz) so a beat grid exists, plus a pinch of noise.
        gate = 0.55 + 0.45 * np.sign(np.sin(2 * np.pi * 2.0 * t))
        return (sig * gate + 0.02 * rng.standard_normal(t.shape)).astype(np.float32)

    verse = block([220.0, 277.2, 329.6], 12.0)  # A-major-ish
    chorus = 1.6 * block([261.6, 329.6, 392.0, 523.2], 12.0)  # louder C-major-ish
    y = np.concatenate([verse, chorus, verse, chorus, verse, chorus])
    wav = tmp_path / "ab.wav"
    sf.write(wav, y, sr)

    first = extract_structure(wav, None)
    second = extract_structure(wav, None)
    assert first == second  # seeded clustering — the determinism contract
    assert first is not None and len(first) >= 2  # the repeated block is found
    for section in first:
        assert section.type == "chorus"
        assert section.end_ms - section.start_ms >= 8000
