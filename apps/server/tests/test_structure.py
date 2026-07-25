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


def test_overlong_span_is_not_a_chorus():
    # Field (Beggin): a 139.5 s block shipped as "chorus", 80% of the track,
    # leaving the client ramp permanently on. Spans past _MAX_SECTION_S are
    # structural blocks, not choruses.
    segments = [
        Segment(0, 100, 1),  # 100 s — over the cap
        Segment(100, 120, 0),
        Segment(120, 220, 1),  # 100 s — over the cap
        Segment(220, 240, 0),
    ]
    assert label_segments(segments, None) == []


def test_dominant_cluster_yields_nothing_rather_than_a_song_wide_chorus():
    # Winner repeats and each span is individually legal, but together they
    # cover ~75% of the track: that is the song's texture, not its chorus.
    segments = [
        Segment(0, 40, 1),
        Segment(40, 50, 0),
        Segment(50, 90, 1),
        Segment(90, 100, 0),
        Segment(100, 140, 1),
        Segment(140, 160, 0),
    ]
    assert label_segments(segments, None) == []


def test_a_normal_chorus_still_survives_both_guards():
    # Textbook shape: verse/chorus alternating, chorus ~28 s x3 (42% of a
    # 200 s track). NOTE the energy argument is what makes the CHORUS win:
    # by repetition x occupied time alone the verse cluster is bigger (4
    # spans / 116 s vs 3 / 84 s), and loudness is the tie-breaker that picks
    # the chorus — which is exactly how the live pipeline calls this
    # (extract_structure always passes the energy curve).
    segments = [
        Segment(0, 30, 0),
        Segment(30, 58, 1),
        Segment(58, 90, 0),
        Segment(90, 118, 1),
        Segment(118, 150, 0),
        Segment(150, 178, 1),
        Segment(178, 200, 0),
    ]
    loud = lambda s: 30 <= s < 58 or 90 <= s < 118 or 150 <= s < 178  # noqa: E731
    energy = _energy([90 if loud(i / 2) else 30 for i in range(400)])
    sections = label_segments(segments, energy)
    assert [s.type for s in sections] == ["chorus"] * 3
    assert [(s.start_ms, s.end_ms) for s in sections] == [
        (30000, 58000),
        (90000, 118000),
        (150000, 178000),
    ]


@pytest.mark.slow
def test_beat_on_the_final_frame_still_produces_structure(tmp_path):
    """Regression: librosa's sync() de-duplicates boundary frames, so a beat
    landing on frame 0 or the last frame yields one FEWER column than a naive
    len(beats)+1 — which used to trip the guard and silently drop the whole
    structure pass (field: GOT IT MAID, "350 labels, 352 bounds"). Boundaries
    now come from librosa.util.fix_frames, the same helper sync() uses."""
    import numpy as np
    import soundfile as sf

    from kashi_server.pipeline.structure import _segment

    sr = 22050
    rng = np.random.default_rng(11)

    def block(freqs: list[float], seconds: float) -> "np.ndarray":
        t = np.arange(int(sr * seconds)) / sr
        sig = sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs)
        gate = 0.55 + 0.45 * np.sign(np.sin(2 * np.pi * 2.0 * t))
        return (sig * gate + 0.02 * rng.standard_normal(t.shape)).astype(np.float32)

    verse = block([220.0, 277.2, 329.6], 12.0)
    chorus = 1.6 * block([261.6, 329.6, 392.0, 523.2], 12.0)
    y = np.concatenate([verse, chorus, verse, chorus, verse, chorus])
    wav = tmp_path / "edge.wav"
    sf.write(wav, y, sr)

    segments = _segment(y, sr)
    # The bug's signature was an EMPTY result plus a mismatch warning.
    assert segments, "structure pass produced nothing — boundary math regressed"
    # Boundaries must tile the track from 0 to the full duration.
    assert segments[0].start_s == 0.0
    assert abs(segments[-1].end_s - len(y) / sr) < 0.2
