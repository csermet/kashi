"""Beat grid extraction (librosa) — optional enrichment for Faz 4's effects.

Runs on the FULL MIX (drums carry the beat; separated vocals would not).
Any failure returns None: a song without a beat grid is still a valid document.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_MIN_BEATS = 8


@dataclass(frozen=True)
class Beats:
    bpm: float
    confidence: float
    times_ms: list[int]
    downbeat_indices: list[int]


def _downbeat_phase(onset_env, beat_frames) -> int:
    """librosa has no downbeat tracker; assume 4/4 and pick the phase whose
    beats carry the most onset energy."""
    best_phase, best_energy = 0, -1.0
    for phase in range(4):
        energy = float(onset_env[beat_frames[phase::4]].sum())
        if energy > best_energy:
            best_phase, best_energy = phase, energy
    return best_phase


def extract_beats(wav_path: Path) -> Beats | None:
    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        if len(beat_frames) < _MIN_BEATS:
            logger.info("beats: only %d beats found, omitting grid", len(beat_frames))
            return None

        times = librosa.frames_to_time(beat_frames, sr=sr)
        times_ms = [round(float(t) * 1000) for t in times]

        intervals = np.diff(times)
        mean_interval = float(intervals.mean())
        confidence = 0.0
        if mean_interval > 0:
            # Steady grids have low inter-beat jitter (documented heuristic).
            confidence = max(0.0, min(1.0, 1.0 - float(intervals.std()) / mean_interval))

        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        phase = _downbeat_phase(onset_env, beat_frames)
        downbeats = list(range(phase, len(times_ms), 4))

        bpm = float(tempo if np.isscalar(tempo) else tempo.item())
        if bpm <= 0:  # schema: exclusiveMinimum 0
            logger.info("beats: non-positive tempo, omitting grid")
            return None
        logger.info(
            "beats: %d beats, %.1f bpm, confidence %.2f, downbeat phase %d",
            len(times_ms),
            bpm,
            confidence,
            phase,
        )
        return Beats(
            bpm=round(bpm, 2),
            confidence=round(confidence, 3),
            times_ms=times_ms,
            downbeat_indices=downbeats,
        )
    except Exception as exc:
        logger.warning("beat extraction failed (%s) — document ships without beats", exc)
        return None
