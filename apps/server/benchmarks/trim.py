"""The word-end sustain trim, kept for the bench only (pipeline 2.32.0 removed it).

Until 2.31.0 line QA capped every word end at start + clamp(chars x the
document's median ms per character x 3, 350 ms, 4 s) (Faz 5 P1, ear test:
words "hanging" past their sung duration). The whole-project review
(2026-11-13) found it moved no word start and no on-screen dwell on 39 songs,
and 2.32.0 removed it from production; `line_qa.restore_held_ends` kept its
cap formula (the TRIM_* constants).

The bench keeps this copy, on by default (`--trim-ends`), so that its end
axis (word-end MAE, 0 ms and < 60 ms shares) stays comparable with every
result file since 2026-07. Neither choice would make that axis describe
production: the bench never runs `restore_held_ends`, which production has
applied since 2.29.1. Word starts, and so every PCO, line-start and dwell
number, do not depend on it. The logic is line_qa's at 2.31.0, unchanged.
"""

import logging
from dataclasses import replace
from statistics import median

from kashi_server.pipeline.alignment import AlignedWord, AlignResult
from kashi_server.pipeline.line_qa import (
    TRIM_MAX_HOLD_MS,
    TRIM_MIN_HOLD_MS,
    TRIM_MIN_SAMPLE_WORDS,
    TRIM_SUSTAIN_FACTOR,
    is_adlib,
)

logger = logging.getLogger("benchmarks")


def trim_word_ends(result: AlignResult) -> tuple[AlignResult, int]:
    """Cap each word's end at start + clamp(chars × med_char_ms × factor).

    med_char_ms is the document-wide median per-character word duration, so
    the cap adapts to the song's tempo. Only ever SHORTENS an end (the
    intra-line next-start clip stays the other bound). Ad-lib lines are
    exempt. Pure, unit-tested.
    """
    if result.sync != "word":
        return result, 0
    med_char_ms = _median_char_ms(result)
    if med_char_ms is None:
        return result, 0
    words_out = [list(chunk) for chunk in result.words_per_line]
    trimmed = 0
    for i, line in enumerate(result.lines):
        if is_adlib(line.text) or i >= len(words_out):
            continue
        trimmed += _cap_word_ends(words_out[i], med_char_ms)
    if not trimmed:
        return result, 0
    logger.info("bench end trim: %d word end(s) capped (med %.0fms/char)", trimmed, med_char_ms)
    return replace(result, words_per_line=words_out), trimmed


def _median_char_ms(result: AlignResult) -> float | None:
    """Document-wide median per-character word duration (ad-libs excluded), or
    None when too few words carry a span to trust it."""
    speeds: list[float] = []
    for i, line in enumerate(result.lines):
        if is_adlib(line.text) or i >= len(result.words_per_line):
            continue
        for w in result.words_per_line[i]:
            if w.text and w.end_ms > w.start_ms:
                speeds.append((w.end_ms - w.start_ms) / len(w.text))
    if len(speeds) < TRIM_MIN_SAMPLE_WORDS:
        return None
    return median(speeds)


def _cap_word_ends(chunk: list[AlignedWord], med_char_ms: float) -> int:
    """Cap the words of one line in place; returns how many ends moved."""
    trimmed = 0
    for k, w in enumerate(chunk):
        allowed = round(len(w.text) * med_char_ms * TRIM_SUSTAIN_FACTOR)
        capped = w.start_ms + min(TRIM_MAX_HOLD_MS, max(TRIM_MIN_HOLD_MS, allowed))
        if w.end_ms > capped:
            chunk[k] = replace(w, end_ms=capped)
            trimmed += 1
    return trimmed
