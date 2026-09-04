"""Vocal loudness over time — the evidence a call-and-response needs.

Onsets answer "was something struck here?" and on a pop mix that is almost
always yes: 786 of them in one 4.5-minute song, one every 344 ms, so any
position sits within ~86 ms of one and the signal carries no information
about WHICH position is right (measured 2026-09-04, the whole reason this
module exists). Loudness answers a different question — "is anyone singing
here?" — and its silences are rare and load-bearing.

This module is the I/O boundary, exactly like `onsets.py`: `line_qa.py` stays
pure and testable without librosa by being handed a contour, never a path.

Two facts a future reader will otherwise get wrong:

  * it runs on the SEPARATED VOCAL stem (the caller only passes it when
    separation actually ran) — on a full mix the drums fill every breath and
    the silences this rule looks for do not exist;
  * every dB is RELATIVE TO THE WHOLE SONG'S LOUDEST FRAME (`ref=np.max`), so
    the threshold downstream is scale-free and a slice of the contour keeps
    the same meaning as the whole of it.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SR = 22050
#: 256 samples at 22050 Hz = 11.61 ms per frame. The rule was validated at this
#: resolution; the FX energy curve in `energy.py` is 2 Hz — two frames per
#: second cannot see a 250 ms breath at all, which is why that curve is not
#: reused here despite the similar name.
HOP = 256


@dataclass(frozen=True)
class VocalEnergy:
    hop_ms: float
    #: (frame time in ms, dB relative to the song's loudest frame), ascending.
    frames: tuple[tuple[int, float], ...]


def measure_vocal_energy(wav_path: Path) -> VocalEnergy | None:
    """Loudness contour of `wav_path`, or None when the audio cannot be read.

    Failure returns None rather than raising, like `detect_onsets`: a song
    without a contour loses one repair step, which the caller handles. It must
    never lose the document.
    """
    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(str(wav_path), sr=SR, mono=True)
        rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
        db = librosa.amplitude_to_db(rms, ref=np.max)
    except Exception:
        logger.exception("vocal energy failed for %s", wav_path.name)
        return None

    hop_ms = 1000.0 * HOP / SR
    frames = tuple((round(i * hop_ms), round(float(v), 1)) for i, v in enumerate(db))
    if not frames:
        return None
    logger.info("vocal energy: %d frames in %s", len(frames), wav_path.name)
    return VocalEnergy(hop_ms=hop_ms, frames=frames)
