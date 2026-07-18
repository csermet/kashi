"""Energy envelope + energy-derived sections (Faz 6 P3) — effect fuel.

Runs on the FULL MIX like beats.py (musical intensity lives in the whole
arrangement, not the vocal stem). Any failure returns None: a song without
an energy curve is still a valid document.

The envelope is a 2 Hz, 0-100 quantized RMS curve — a few hundred ints per
song. Sections v1 are ENERGY-DERIVED high blocks (a chorus PROXY, not real
structure analysis): sustained stretches whose smoothed energy sits above
the 70th percentile. allin1-style functional labels (verse/chorus/bridge)
stay a Faz 6.5 candidate; the `type` field is an open string so they can
join additively later.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

RATE_HZ = 2
# Sections: smoothed-envelope threshold percentile + minimum block length.
_HIGH_PERCENTILE = 70
_MIN_SECTION_S = 8.0
_SMOOTH_S = 3.0  # moving-average window for section detection only


@dataclass(frozen=True)
class Energy:
    rate_hz: int
    values: list[int]  # 0-100, len == ceil(duration * rate_hz)


@dataclass(frozen=True)
class Section:
    type: str  # open vocabulary; v1 emits only "high"
    start_ms: int
    end_ms: int


def extract_energy(wav_path: Path) -> tuple[Energy, list[Section]] | None:
    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        if not len(y):
            return None
        hop = int(sr / RATE_HZ)
        rms = librosa.feature.rms(y=y, hop_length=hop, frame_length=hop * 2)[0]
        if len(rms) < 4:
            logger.info("energy: clip too short (%d frames), omitting", len(rms))
            return None

        # Perceptual-ish scale: dB relative to the track's own loud end,
        # clamped to a 40 dB window, mapped to 0-100. Per-track normalized —
        # the client ramps RELATIVE intensity, absolute loudness is noise.
        db = librosa.amplitude_to_db(rms, ref=float(np.percentile(rms, 95)) or 1.0)
        scaled = np.clip((db + 40.0) / 40.0, 0.0, 1.0)
        values = [int(round(float(v) * 100)) for v in scaled]

        # Smooth only for section detection; the published curve stays raw.
        win = max(1, int(_SMOOTH_S * RATE_HZ))
        kernel = np.ones(win) / win
        smooth = np.convolve(scaled, kernel, mode="same")
        threshold = float(np.percentile(smooth, _HIGH_PERCENTILE))

        sections: list[Section] = []
        start = None
        for i, v in enumerate(smooth):
            if v >= threshold and start is None:
                start = i
            elif v < threshold and start is not None:
                sections.append(_section(start, i))
                start = None
        if start is not None:
            sections.append(_section(start, len(smooth)))
        sections = [
            s for s in sections if (s.end_ms - s.start_ms) >= _MIN_SECTION_S * 1000
        ]

        logger.info(
            "energy: %d samples @%dHz, %d high section(s)",
            len(values),
            RATE_HZ,
            len(sections),
        )
        return Energy(rate_hz=RATE_HZ, values=values), sections
    except Exception as exc:
        logger.warning("energy extraction failed (%s) — document ships without it", exc)
        return None


def _section(start_frame: int, end_frame: int) -> Section:
    ms_per_frame = 1000 // RATE_HZ
    return Section(
        type="high", start_ms=start_frame * ms_per_frame, end_ms=end_frame * ms_per_frame
    )
