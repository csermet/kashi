"""Structure sections v2 (Faz 6.5 P6) — real boundaries, honest labels.

allin1 turned out uninstallable (NATTEN removed the API it imports —
docs/research/allin1-viability-2026-07.md), so structure comes from
librosa's Laplacian segmentation (McFee & Ellis 2014) over beat-synchronous
chroma: recurrence + path affinity → normalized Laplacian → spectral
clustering. Zero new dependencies (librosa + scipy are base), CPU-cheap,
and deterministic PER MACHINE (pinned k-means rng; two runs on the same
box → identical sections). Cross-machine identity is best-effort only:
near-degenerate eigenpairs at the evecs[:, :k] cut and BLAS reduction
order can move a boundary beat on a different LAPACK build — acceptable,
nothing keys document identity to section bytes (reviewer risk note).

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
# ...and one LONGER than this is not a chorus either. Clustering finds the
# most-repeated texture; on some tracks that texture IS most of the song, and
# labeling a 2.3-minute block "chorus" both misnames it and flattens the very
# dynamic the label exists to drive (field: Beggin shipped a 139.5 s span,
# 80% of the track, leaving the overlay's ramp permanently on).
_MAX_SECTION_S = 60.0
# Even after that filter, a cluster covering most of the track is the song's
# general texture, not its chorus. Past this share we emit NOTHING — "no
# distinct chorus" is an honest answer, and the energy-derived "high" blocks
# still carry the intensity ramp.
_MAX_COVERAGE = 0.55
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

    # Derive the boundaries THE WAY sync() itself does. sync() aggregates
    # between boundaries built by `fix_frames`, which pads with 0 and the
    # final frame and then DEDUPLICATES — so a beat landing exactly on
    # either edge swallows one boundary and yields one FEWER column than a
    # naive len(beats)+1. Assuming that count silently dropped the whole
    # structure pass on such tracks (field: GOT IT MAID, "350 labels, 352
    # bounds"). Building bounds from the same helper makes
    # len(bounds) == len(labels) + 1 true BY CONSTRUCTION; the guard below
    # is now a belt that should never tighten.
    bound_frames = librosa.util.fix_frames(beats, x_min=0, x_max=chroma.shape[1])
    duration = len(y) / sr
    bounds = [
        min(float(t), duration) for t in librosa.frames_to_time(bound_frames, sr=sr)
    ]
    if len(bounds) != len(labels) + 1:
        logger.warning(
            "structure: label/boundary mismatch (%d labels, %d bounds) — skipping",
            len(labels),
            len(bounds),
        )
        return []
    segments: list[Segment] = []
    start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or int(labels[i]) != int(labels[start]):
            segments.append(Segment(bounds[start], bounds[i], int(labels[start])))
            start = i
    return segments


def label_segments(segments: list[Segment], energy: Energy | None) -> list[Section]:
    """Pure labeling: the most-repeated, most-energetic cluster = chorus.

    Honesty rules: a cluster must REPEAT (≥2 spans) to be a chorus candidate
    — a track with no repetition yields NO sections (that is a valid
    outcome, not a failure); spans outside [_MIN_SECTION_S, _MAX_SECTION_S]
    are dropped (too short = noise, too long = a structural block, not a
    chorus); a winner still covering more than _MAX_COVERAGE of the track is
    the song's texture rather than its chorus and yields NOTHING; ties break
    toward the higher mean energy then the lower cluster id (deterministic).
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
    chorus_spans = [
        span
        for span in scored[0][1]
        if _MIN_SECTION_S <= (span.end_s - span.start_s) <= _MAX_SECTION_S
    ]
    if not chorus_spans:
        return []

    # Coverage sanity: the segments tile the whole track, so their extent IS
    # the duration. A "chorus" filling most of the song is the track's
    # general texture under a wrong name — say nothing rather than flatten
    # the client's ramp across the entire song.
    track_s = max((seg.end_s for seg in segments), default=0.0)
    covered_s = sum(span.end_s - span.start_s for span in chorus_spans)
    if track_s > 0 and covered_s / track_s > _MAX_COVERAGE:
        logger.info(
            "structure: winning cluster covers %.0f%% of the track — no distinct chorus",
            100 * covered_s / track_s,
        )
        return []

    return [
        Section(
            type="chorus",
            start_ms=int(round(span.start_s * 1000)),
            end_ms=int(round(span.end_s * 1000)),
        )
        for span in chorus_spans
    ]
