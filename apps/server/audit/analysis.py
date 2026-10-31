"""Per-song audio evidence, measured once and cached next to the stem.

Measured on the 16 kHz stem with the production functions, on the same audio
the worker's arbiter reads (``worker/process.py`` ``_align_with_ctc``):

- ``energy``: vocal loudness in dB relative to the song's loudest frame
  (``measure_vocal_energy``);
- ``onsets``: librosa onsets (``detect_onsets``). On pop they are dense (one
  every ~344 ms, measured 2026-09-04), so an onset near a word proves little;
- ``voice_onsets``: where the voice comes back after a real silence, read off
  the energy contour with line QA's own silence threshold. These are the
  load-bearing evidence for "this line starts late/early".
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass

from audit.paths import STEMS_DIR
from audit.stems import decode_16k

# line_qa.RESPONSE_SILENCE_DB: below this (dB re the song max) nobody sings.
SILENCE_DB = -25.0
# A gap must last this long to count as silence before a voice onset; shorter
# dips are consonants and breaths inside a phrase.
MIN_SILENCE_MS = 150
# Loudness bumps (sung syllables, roughly): peaks above the silence line that
# stand BUMP_PROMINENCE_DB over the dips on both sides, at least BUMP_GAP_MS
# apart, on a contour smoothed over BUMP_SMOOTH frames (~58 ms).
BUMP_PROMINENCE_DB = 6.0
BUMP_GAP_MS = 90
BUMP_SMOOTH = 5


@dataclass(frozen=True)
class SongAnalysis:
    hop_ms: float
    energy: list[tuple[int, float]]
    onsets: list[int]
    voice_onsets: list[int]

    def nearest(self, items: list[int], t_ms: int) -> int | None:
        if not items:
            return None
        i = bisect.bisect_left(items, t_ms)
        candidates = items[max(0, i - 1) : i + 1]
        return min(candidates, key=lambda x: abs(x - t_ms))

    def peak_after(self, t_ms: int, window_ms: int) -> float:
        """Loudest level in [t, t + window] (dB re the song max); -99 off-contour."""
        i0 = max(0, round(t_ms / self.hop_ms))
        i1 = min(len(self.energy), round((t_ms + window_ms) / self.hop_ms) + 1)
        return max((db for _, db in self.energy[i0:i1]), default=-99.0)

    def bumps(self, t0: int, t1: int) -> list[int]:
        """Loudness bump peaks in [t0, t1] (see BUMP_*)."""
        return loudness_bumps(self.energy, self.hop_ms, t0, t1)

    def level_at(self, t_ms: int) -> float | None:
        if not self.energy:
            return None
        i = min(len(self.energy) - 1, max(0, round(t_ms / self.hop_ms)))
        return self.energy[i][1]


def voice_onsets(frames: list[tuple[int, float]], hop_ms: float,
                 silence_db: float = SILENCE_DB, min_silence_ms: int = MIN_SILENCE_MS) -> list[int]:
    """Times where the level rises to >= ``silence_db`` after >= ``min_silence_ms`` below it."""
    out: list[int] = []
    quiet_since: int | None = 0  # the track starts in silence
    for t, db in frames:
        if db < silence_db:
            if quiet_since is None:
                quiet_since = t
        else:
            if quiet_since is not None and t - quiet_since >= min_silence_ms:
                out.append(t)
            quiet_since = None
    return out


def loudness_bumps(frames: list[tuple[int, float]], hop_ms: float, t0: int, t1: int,
                   prominence_db: float = BUMP_PROMINENCE_DB, gap_ms: int = BUMP_GAP_MS,
                   smooth: int = BUMP_SMOOTH) -> list[int]:
    """Peak times of the smoothed loudness contour inside [t0, t1]."""
    import numpy as np
    from scipy.signal import find_peaks

    window = [db for t, db in frames if t0 <= t <= t1]
    times = [t for t, _ in frames if t0 <= t <= t1]
    if len(window) < 3:
        return []
    level = np.convolve(np.asarray(window), np.ones(smooth) / smooth, mode="same") \
        if smooth > 1 else np.asarray(window)
    peaks, _ = find_peaks(level, height=SILENCE_DB, prominence=prominence_db,
                          distance=max(1, round(gap_ms / hop_ms)))
    return [times[i] for i in peaks]


def _cache_path(video_id: str):
    return STEMS_DIR / f"{video_id}.analysis.json"


def analyse(video_id: str) -> SongAnalysis:
    path = _cache_path(video_id)
    if path.exists():
        raw = json.loads(path.read_text())
    else:
        from kashi_server.pipeline.onsets import detect_onsets
        from kashi_server.pipeline.vocal_energy import measure_vocal_energy

        wav = decode_16k(video_id)
        energy = measure_vocal_energy(wav)
        if energy is None:
            raise RuntimeError(f"vocal energy failed for {video_id}")
        raw = {
            "hop_ms": energy.hop_ms,
            "energy": [list(f) for f in energy.frames],
            "onsets": detect_onsets(wav) or [],
        }
        path.write_text(json.dumps(raw) + "\n")
    frames = [(int(t), float(db)) for t, db in raw["energy"]]
    return SongAnalysis(
        hop_ms=float(raw["hop_ms"]),
        energy=frames,
        onsets=[int(x) for x in raw["onsets"]],
        voice_onsets=voice_onsets(frames, float(raw["hop_ms"])),
    )
