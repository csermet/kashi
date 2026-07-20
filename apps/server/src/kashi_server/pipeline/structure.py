"""Structure sections v2 (Faz 6.5 P6) — real boundaries, honest labels.

allin1 turned out uninstallable (NATTEN removed the API it imports —
docs/research/allin1-viability-2026-07.md), so structure comes from
librosa's Laplacian segmentation (McFee & Ellis 2014) over beat-synchronous
chroma: recurrence + path affinity → normalized Laplacian → spectral
clustering. Zero new dependencies (librosa + scipy are base), CPU-cheap,
deterministic end to end (seeded k-means; two runs → identical sections).

Labeling claims ONLY what the math supports: clustering finds REPETITION,
not semantic roles. The most-repeated cluster with the highest mean energy
ships as "chorus"; nothing else is labeled — no fake verse/bridge. The
energy-derived "high" blocks (energy.py) continue unchanged alongside;
the overlay ramps on {high, chorus}.

This attacks the BAD GIRL class head-on: boundaries come from harmonic
repetition, not loudness, so a brickwalled master segments fine.

Failure posture mirrors palette/beats/energy: any error → no sections,
the document still ships.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from kashi_server.pipeline.energy import Energy, Section

logger = logging.getLogger(__name__)

STRUCTURE_METHOD = "librosa-laplacian/1.0"
# Spectral clustering target — clamped down for short/low-beat tracks.
_N_CLUSTERS = 5
# A chorus span shorter than this is noise, same floor as energy sections.
_MIN_SECTION_S = 8.0
# Below this length there is no structure to find (jingles, fragments).
_MIN_TRACK_S = 60.0


@dataclass(frozen=True)
class Segment:
    """A clustered span (seconds) — the pure labeling layer's input."""

    start_s: float
    end_s: float
    cluster: int


def extract_structure(wav_path: Path, energy: Energy | None) -> list[Section] | None:
    """Full-mix structure pass. Returns chorus sections or None (best-effort)."""
    try:
        import librosa

        y, sr_raw = librosa.load(str(wav_path), sr=22050, mono=True)
        sr = int(sr_raw)
        if len(y) < sr * _MIN_TRACK_S:
            logger.info("structure: track too short (%.0fs), omitting", len(y) / sr)
            return None
        segments = _segment(y, sr)
        if not segments:
            return None
        sections = label_segments(segments, energy)
        logger.info(
            "structure: %d segments -> %d chorus section(s) [%s]",
            len(segments),
            len(sections),
            STRUCTURE_METHOD,
        )
        return sections
    except Exception as exc:  # enrichment, never a job failure
        logger.warning("structure analysis failed (%s) — document ships without it", exc)
        return None


def _segment(y, sr: int) -> list[Segment]:
    """Laplacian segmentation over beat-synchronous chroma (librosa method)."""
    import librosa
    import numpy as np
    import scipy.linalg
    import scipy.ndimage
    import scipy.sparse.csgraph
    from scipy.cluster.vq import kmeans2

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    _tempo, beats = librosa.beat.beat_track(y=y, sr=sr, trim=False)
    if len(beats) < 2 * _N_CLUSTERS:
        return []
    csync = librosa.util.sync(chroma, [int(b) for b in beats], aggregate=np.median)

    rec = librosa.segment.recurrence_matrix(csync, width=3, mode="affinity", sym=True)
    rec = librosa.segment.timelag_filter(scipy.ndimage.median_filter)(rec, size=(1, 7))

    path_distance = np.sum(np.diff(csync, axis=1) ** 2, axis=0)
    sigma = np.median(path_distance) or 1.0
    path_sim = np.exp(-path_distance / sigma)
    r_path = np.diag(path_sim, k=1) + np.diag(path_sim, k=-1)

    deg_path = r_path.sum(axis=1)
    deg_rec = rec.sum(axis=1)
    denom = float(np.sum((deg_path + deg_rec) ** 2)) or 1.0
    mu = float(deg_path.dot(deg_path + deg_rec)) / denom
    affinity = mu * rec + (1 - mu) * r_path

    laplacian = scipy.sparse.csgraph.laplacian(affinity, normed=True)
    _evals, evecs = scipy.linalg.eigh(laplacian)
    k = min(_N_CLUSTERS, evecs.shape[1])
    basis = librosa.util.normalize(evecs[:, :k], axis=1)
    # The pinned rng is the document determinism contract.
    _centroids, labels = kmeans2(basis, k, minit="++", rng=0)
    # Beat-level labels flicker at cluster borders; a median filter keeps
    # the coherent blocks and kills the one-beat slivers (the synthetic AB
    # test fragmented into 40 sub-floor pieces without this).
    labels = scipy.ndimage.median_filter(labels, size=9, mode="nearest")

    times = librosa.frames_to_time(beats, sr=sr)
    duration = len(y) / sr
    segments: list[Segment] = []
    start = float(times[0]) if len(times) else 0.0
    current = int(labels[0])
    for i in range(1, len(labels)):
        if int(labels[i]) != current:
            segments.append(Segment(start, float(times[i]), current))
            start = float(times[i])
            current = int(labels[i])
    segments.append(Segment(start, duration, current))
    return segments


def label_segments(segments: list[Segment], energy: Energy | None) -> list[Section]:
    """Pure labeling: the most-repeated, most-energetic cluster = chorus.

    Honesty rules: a cluster must REPEAT (≥2 spans) to be a chorus candidate
    — a track with no repetition yields NO sections (that is a valid
    outcome, not a failure); candidate spans shorter than the noise floor
    are dropped; ties break toward the higher mean energy then the lower
    cluster id (deterministic).
    """
    by_cluster: dict[int, list[Segment]] = {}
    for seg in segments:
        by_cluster.setdefault(seg.cluster, []).append(seg)

    candidates = {c: spans for c, spans in by_cluster.items() if len(spans) >= 2}
    if not candidates:
        return []

    def mean_energy(spans: list[Segment]) -> float:
        if energy is None or not energy.values or energy.rate_hz <= 0:
            return 0.0
        total = 0.0
        count = 0
        for span in spans:
            lo = int(span.start_s * energy.rate_hz)
            hi = max(lo + 1, int(span.end_s * energy.rate_hz))
            window = energy.values[lo:hi]
            total += sum(window)
            count += len(window)
        return total / count if count else 0.0

    def total_duration(spans: list[Segment]) -> float:
        return sum(span.end_s - span.start_s for span in spans)

    # Chorus-ness = repetition × occupied time × loudness. Duration matters:
    # two 4-second slivers repeat, but a chorus OCCUPIES the song.
    scored = sorted(
        candidates.items(),
        key=lambda item: (
            -(len(item[1]) * total_duration(item[1]) * (1.0 + mean_energy(item[1]))),
            item[0],
        ),
    )
    chorus_spans = scored[0][1]

    sections = [
        Section(
            type="chorus",
            start_ms=int(round(span.start_s * 1000)),
            end_ms=int(round(span.end_s * 1000)),
        )
        for span in chorus_spans
        if (span.end_s - span.start_s) >= _MIN_SECTION_S
    ]
    return sections
