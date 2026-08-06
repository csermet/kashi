"""Vocal onset detection — the arbiter's only aligner-independent evidence.

Every other confidence signal in the chain is derived from the model that
produced the timings, which is why the shipped quality score correlates just
+0.36 with real accuracy: it is the model grading its own homework. Onsets
come from the audio. If the aligner drifts, its words stop landing on them,
and nothing inside the forward pass can hide that.

Measured (79 songs, 3383 lines, 2026-08-06): Spearman +0.399 against per-line
ground truth — the strongest of every free candidate, and it survives its own
confounder (removing onset density leaves +0.469 at song scale).

This module is the I/O boundary. It exists separately so `arbiter.py` and
`line_qa.py` stay pure and testable without librosa: they are handed onset
times, never a file path.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def detect_onsets(wav_path: Path) -> list[int] | None:
    """Vocal onset times in ms, or None when the audio cannot be read.

    Runs on the SEPARATED VOCAL stem, unlike `beats.py` which needs the full
    mix — drums carry the beat, but a word start has to land on a sung onset,
    and percussion would drown the evidence.

    Failure returns None rather than raising: a song without onsets loses one
    arbiter signal, which the arbiter handles explicitly. It must never lose
    the document.
    """
    try:
        import librosa

        y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time", backtrack=True)
    except Exception:
        logger.exception("onset detection failed for %s", wav_path.name)
        return None

    times_ms = [round(float(t) * 1000) for t in onsets]
    logger.info("onsets: %d detected in %s", len(times_ms), wav_path.name)
    return times_ms or None
