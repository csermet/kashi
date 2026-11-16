"""The stem's level envelope: how loud the aligner's 16 kHz track is, every 20 ms.

One value per emission frame (MODEL_HOP_SAMPLES = 320 samples = 20 ms at 16 kHz): the mean square
of the frame's samples, in decibels relative to the loudest frame of the track, kept in half-dB
steps as one byte (0 = the loudest frame, 255 = 127.5 dB under it or quieter, digital silence
included). Generic on purpose: no threshold lives here, the rules that read it choose theirs, and a
stored envelope (pipeline/evidence.py) serves any later rule without the audio.

Deterministic across workers: the samples are the decoded wav's 16-bit integers, read with the
standard library (never the aligner's float tensor, which torch builds per device); each frame's
energy is an exact integer sum; the only floating-point steps are scalar CPython operations (two
divisions, one `math.log10`, one multiplication) and the half-dB rounding, so the CPU worker and the
GPU helper, which run the same image, produce the same bytes.

This module is the I/O boundary for it, as onsets.py is for the onsets: `level_envelope` reads a
file; `Envelope` is pure.
"""

from __future__ import annotations

import logging
import math
import wave
from dataclasses import dataclass
from pathlib import Path

from kashi_server.pipeline import evidence

logger = logging.getLogger(__name__)

#: Samples per value: one emission frame (alignment.MODEL_HOP_SAMPLES) at 16 kHz.
FRAME_SAMPLES = 320
SAMPLE_RATE = 16_000
STEP_MS = FRAME_SAMPLES * 1000 // SAMPLE_RATE
#: One stored step, in dB.
UNIT_DB = 0.5
#: The quietest stored value: 255 half-dB steps = 127.5 dB under the loudest frame, or silence.
FLOOR_STEP = 255


@dataclass(frozen=True)
class Envelope:
    """`levels[i]` covers samples [320 i, 320 (i + 1)) of the track, i.e. [20 i, 20 i + 20) ms on
    the aligner's clock: `levels[i] * UNIT_DB` dB under the track's loudest frame."""

    levels: bytes
    step_ms: int = STEP_MS

    def db(self, frame: int) -> float:
        """The frame's level in dB re the loudest frame (<= 0)."""
        return -UNIT_DB * self.levels[frame]

    def quiet_share(self, t0_ms: int, t1_ms: int, threshold_db: float) -> float | None:
        """Share of the frames that START in [t0_ms, t1_ms) whose level is below `threshold_db`
        (dB re the loudest frame), or None when no frame starts there."""
        first = max(0, -(-t0_ms // self.step_ms))
        last = min(len(self.levels), -(-t1_ms // self.step_ms))
        if last <= first:
            return None
        quiet = sum(1 for k in range(first, last) if self.db(k) < threshold_db)
        return quiet / (last - first)


def quantize(mean_squares: list[float]) -> bytes:
    """Half-dB steps under the loudest mean square, rounded half up, clipped to FLOOR_STEP. A
    frame of zero energy, and every frame of an all-zero track, is FLOOR_STEP."""
    top = max(mean_squares, default=0.0)
    out = bytearray()
    for value in mean_squares:
        if value <= 0.0 or top <= 0.0:
            out.append(FLOOR_STEP)
            continue
        steps = math.floor(-20.0 * math.log10(value / top) + 0.5)  # 2 x (10 log10) per dB
        out.append(min(FLOOR_STEP, max(0, steps)))
    return bytes(out)


def mean_squares(samples) -> list[float]:
    """Per FRAME_SAMPLES frame, the mean of the squared integer samples (the last frame over the
    samples it has). Exact integer sums, one division each."""
    import numpy as np

    values = np.asarray(samples, dtype=np.int64)
    n = int(values.shape[0])
    full = n // FRAME_SAMPLES
    sums = (values[: full * FRAME_SAMPLES].reshape(full, FRAME_SAMPLES) ** 2).sum(axis=1)
    out = [int(s) / FRAME_SAMPLES for s in sums.tolist()]
    tail = values[full * FRAME_SAMPLES :]
    if tail.shape[0]:
        out.append(int((tail**2).sum()) / int(tail.shape[0]))
    return out


def level_envelope(wav_path: Path) -> Envelope | None:
    """The envelope of the aligner's wav (16 kHz, mono, 16-bit: process._decode's output), or None
    when it cannot be read as one. A failure says so (`evidence.extractor_failed`): a record made in
    such a job is not stored, as for the onsets. A silent track is a value (all FLOOR_STEP)."""
    try:
        import numpy as np

        with wave.open(str(wav_path), "rb") as f:
            if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, SAMPLE_RATE):
                raise ValueError(
                    f"not 16 kHz mono 16-bit: {f.getnchannels()} ch, {f.getsampwidth()} B, "
                    f"{f.getframerate()} Hz")
            raw = f.readframes(f.getnframes())
        samples = np.frombuffer(raw, dtype="<i2")
        envelope = Envelope(quantize(mean_squares(samples)))
    except Exception:
        logger.exception("level envelope failed for %s", Path(wav_path).name)
        evidence.extractor_failed("level envelope")
        return None
    logger.info("level envelope: %d frames of %d ms", len(envelope.levels), envelope.step_ms)
    return envelope
