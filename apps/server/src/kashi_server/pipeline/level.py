"""The stem's level envelope: how loud the aligner's 16 kHz track is, every 20 ms.

Per emission frame (MODEL_HOP_SAMPLES = 320 samples = 20 ms at 16 kHz), two bytes, and one
fraction for the track:

- `levels[i]`: the frame's mean square in half-dB steps under the track's loudest frame, rounded
  half up (0 = the loudest frame; 255 = 127.25 dB under it or quieter, digital silence included):
  step s when 10^(-(s + 0.5)/20) < r <= 10^(-(s - 0.5)/20), r = its mean square / the loudest's,
  each edge as the fixed integer table STEP_EDGES gives it.
- `crossings[i]`: how often the frame's samples change sign, over the pairs of neighbouring samples
  INSIDE the frame (n samples, n - 1 pairs; a sample of 0 counts as non-negative), capped at 255
  (a full frame has at most 319).
- `loudest`: the loudest frame's mean square as an exact fraction (sum of its squared samples,
  its sample count; the first such frame), which puts the levels back on a dBFS scale:
  10 log10(sum / count / 32768^2) - levels[i] / 2 dB.

Generic on purpose: no threshold lives here, the rules that read it choose theirs, and a stored
envelope (pipeline/evidence.py) serves any later rule without the audio. Not audio, and nothing
audio can be rebuilt from: 16 bits per 320 samples (against 5 120), no phase, no spectrum; a
crossing count mixes the fundamental, the formants and the noise into one coarse brightness cue
and is no pitch track. Rebuilt, the two give noise shaped by loudness and brightness - no words.

Exact, so identical on every worker: the samples are the aligner wav's 16-bit integers read with
the standard library (`wave`; on the 39 pilot / hold-out songs they equal what the aligner's own
`load_audio` hands the model, sample for sample, 2026-11-16); sums of squares and crossings are
integer counts; a frame's step comes from comparing its mean square, as an exact fraction of the
loudest, with the integer edges in STEP_EDGES (computed once with the decimal module, whose ln and
exp are correctly rounded in software, not by the platform's libm). No floating point decides a
byte.

The wav is read CHUNK_FRAMES frames at a time (a 20-minute track no longer sits in memory as 64-bit
samples). This module is the I/O boundary for it, as onsets.py is for the onsets: `level_envelope`
reads a file; `frame_stats`, `quantize` and `Envelope` are pure.
"""

from __future__ import annotations

import bisect
import decimal
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
#: The highest stored crossing count.
CROSSINGS_CAP = 255
#: Frames read at a time (20.48 s of audio).
CHUNK_FRAMES = 1024
#: The fixed-point scale of STEP_EDGES.
EDGE_BITS = 96


def _step_edges() -> tuple[int, ...]:
    """STEP_EDGES[j] = floor(10^(-(j + 0.5)/20) * 2^EDGE_BITS), j = 0 .. FLOOR_STEP - 1: the share
    of the loudest frame's mean square at or under which a frame is at least step j + 1. 60
    decimal digits; tests pin the table."""
    ctx = decimal.Context(prec=60)
    ln10 = ctx.ln(decimal.Decimal(10))
    scale = decimal.Decimal(1 << EDGE_BITS)
    edges = []
    for j in range(FLOOR_STEP):
        half_db = ctx.divide(decimal.Decimal(-(2 * j + 1)), decimal.Decimal(40))  # -(j + 0.5)/20
        edge = ctx.multiply(ctx.exp(ctx.multiply(half_db, ln10)), scale)
        edges.append(int(edge.to_integral_value(rounding=decimal.ROUND_FLOOR)))
    return tuple(edges)


STEP_EDGES = _step_edges()


@dataclass(frozen=True)
class Envelope:
    """`levels[i]` covers samples [320 i, 320 (i + 1)) of the track, i.e. [20 i, 20 i + 20) ms on
    the aligner's clock: `levels[i] * UNIT_DB` dB under the track's loudest frame. `crossings[i]`
    is the same frame's sign-change count (empty when not computed); `loudest` the loudest frame's
    (sum of squared samples, sample count)."""

    levels: bytes
    step_ms: int = STEP_MS
    crossings: bytes = b""
    loudest: tuple[int, int] = (0, 0)

    def db(self, frame: int) -> float:
        """The frame's level in dB re the loudest frame (<= 0)."""
        return -UNIT_DB * self.levels[frame]

    def loudest_dbfs(self) -> float | None:
        """The loudest frame's mean square in dB re a full-scale 16-bit square (None: silence)."""
        total, count = self.loudest
        if total <= 0 or count <= 0:
            return None
        return 10 * math.log10(total / count / 32_768**2)

    def quiet_share(self, t0_ms: int, t1_ms: int, threshold_db: float) -> float | None:
        """Share of the frames that START in [t0_ms, t1_ms) whose level is below `threshold_db`
        (dB re the loudest frame), or None when no frame starts there."""
        first = max(0, -(-t0_ms // self.step_ms))
        last = min(len(self.levels), -(-t1_ms // self.step_ms))
        if last <= first:
            return None
        quiet = sum(1 for k in range(first, last) if self.db(k) < threshold_db)
        return quiet / (last - first)


def frame_stats(samples) -> tuple[list[int], list[int], bytes]:
    """Per FRAME_SAMPLES frame of int16 `samples` (the last frame over the samples it has): the sum
    of the squared samples, the sample count and the sign-change count inside the frame, capped at
    CROSSINGS_CAP. Integers only."""
    import numpy as np

    values = np.asarray(samples, dtype=np.int64)
    full = int(values.shape[0]) // FRAME_SAMPLES
    body = values[: full * FRAME_SAMPLES].reshape(full, FRAME_SAMPLES)
    sums = [int(s) for s in (body**2).sum(axis=1).tolist()]
    signs = body >= 0
    crossings = [int(c) for c in (signs[:, 1:] != signs[:, :-1]).sum(axis=1).tolist()]
    counts = [FRAME_SAMPLES] * full
    tail = values[full * FRAME_SAMPLES :]
    if tail.shape[0]:
        sums.append(int((tail**2).sum()))
        counts.append(int(tail.shape[0]))
        tail_signs = tail >= 0
        crossings.append(int((tail_signs[1:] != tail_signs[:-1]).sum()))
    return sums, counts, bytes(min(CROSSINGS_CAP, c) for c in crossings)


def quantize(sums: list[int], counts: list[int]) -> tuple[bytes, tuple[int, int]]:
    """Each frame's half-dB step under the loudest frame, and the loudest frame as (sum of squared
    samples, sample count). Frame k is step s = the number of edges j with
    sums[k] / counts[k] <= STEP_EDGES[j] / 2^EDGE_BITS * top_sum / top_count, compared as integers:
    rounded half up, a frame of zero energy (and every frame of an all-zero track) FLOOR_STEP."""
    if not sums:
        return b"", (0, 0)
    top_sum, top_count = sums[0], counts[0]
    for total, count in zip(sums, counts, strict=True):
        if total * top_count > top_sum * count:
            top_sum, top_count = total, count
    rising: dict[int, list[int]] = {}  # per frame size: -(edge * top_sum * count), increasing
    out = bytearray()
    for total, count in zip(sums, counts, strict=True):
        if count not in rising:
            rising[count] = [-(edge * top_sum * count) for edge in STEP_EDGES]
        out.append(bisect.bisect_right(rising[count], -((total * top_count) << EDGE_BITS)))
    return bytes(out), (top_sum, top_count)


def level_envelope(wav_path: Path) -> Envelope | None:
    """The envelope of the aligner's wav (16 kHz, mono, 16-bit: process._decode's output), or None
    when it cannot be read as one. A failure says so (`evidence.extractor_failed`): a record made in
    such a job is not stored, as for the onsets. A silent track is a value (all FLOOR_STEP)."""
    try:
        import numpy as np

        sums: list[int] = []
        counts: list[int] = []
        crossings = bytearray()
        with wave.open(str(wav_path), "rb") as f:
            if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, SAMPLE_RATE):
                raise ValueError(
                    f"not 16 kHz mono 16-bit: {f.getnchannels()} ch, {f.getsampwidth()} B, "
                    f"{f.getframerate()} Hz")
            short = False
            while raw := f.readframes(CHUNK_FRAMES * FRAME_SAMPLES):
                if short:  # a frame would straddle two reads
                    raise ValueError("a short read before the end of the wav")
                chunk_sums, chunk_counts, chunk_crossings = frame_stats(
                    np.frombuffer(raw, dtype="<i2"))
                short = len(raw) != CHUNK_FRAMES * FRAME_SAMPLES * 2
                sums += chunk_sums
                counts += chunk_counts
                crossings += chunk_crossings
        levels, loudest = quantize(sums, counts)
        envelope = Envelope(levels, crossings=bytes(crossings), loudest=loudest)
    except Exception:
        logger.exception("level envelope failed for %s", Path(wav_path).name)
        evidence.extractor_failed("level envelope")
        return None
    logger.info("level envelope: %d frames of %d ms", len(envelope.levels), envelope.step_ms)
    return envelope
