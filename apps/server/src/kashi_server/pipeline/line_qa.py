"""Per-line QA of alignment output against lrclib's synced timestamps.

Motivation (TiK ToK, 2026-07-11): the CTC aligner can lose lock on a heavily
processed section (layered chorus vocals) and dump a block of lines far ahead
of where they are actually sung — up to -15 s in the observed case — while the
DOCUMENT-level quality score still clears the client's 0.5 word-mode gate. The
lrclib synced timestamps were correct in exactly that window, so they serve as
the reference: lines whose aligner start strays far from the (offset-corrected)
lrclib start get snapped to the reference time and lose their word timings
(the overlay renders such lines as plain text inside a word-mode document).

The per-line CTC score is deliberately NOT a flagging signal: measured docs
contain lines with perfect timing and a 0.00 score, so a score gate would
strip words from good lines. Scores are only logged.

Pure module in the `regroup_words_into_lines` tradition: no torch, no I/O,
unit-tested with synthetic data.
"""

import logging
from dataclasses import dataclass, replace
from statistics import median

from kashi_server.pipeline.alignment import AlignResult, LineTiming, quality_from_probs

logger = logging.getLogger(__name__)

# A flagged line is one whose deviation from the median offset exceeds this.
# The median absorbs any systematic shift between the lrclib source and the
# audio we downloaded (duration matching already bounds it to a few seconds).
DRIFT_THRESHOLD_MS = 2500
# Below this many stamped reference lines the median is meaningless — QA is
# skipped and only the monotonicity clamp runs.
MIN_REFERENCE_LINES = 3
# If more than this fraction of referenced lines is flagged, the alignment is
# wholesale garbage: fall back to lrclib line timings for the whole document.
MAX_FLAGGED_FRACTION = 0.5


@dataclass(frozen=True)
class LineQAOutcome:
    result: AlignResult
    flagged: list[int]  # indexes into result.lines that were snapped
    offset_ms: int  # median (aligner - lrclib) offset that was compensated
    degraded_to_line: bool


def _match_references(
    lines: list[LineTiming],
    line_texts: list[str],
    synced_starts_ms: list[int | None],
) -> list[int | None] | None:
    """Reference start per result line, or None if the texts cannot be walked.

    regroup may drop a line (pure-punctuation defence), so result lines are
    matched to lyric lines by TEXT with a forward cursor — order is preserved
    and repeated chorus lines resolve correctly.
    """
    refs: list[int | None] = []
    cursor = 0
    for line in lines:
        while cursor < len(line_texts) and line_texts[cursor] != line.text:
            cursor += 1
        if cursor >= len(line_texts):
            return None
        refs.append(synced_starts_ms[cursor])
        cursor += 1
    return refs


def _clamp_monotonic(lines: list[LineTiming]) -> list[LineTiming]:
    """Line starts never go backwards; ends never precede their start."""
    out: list[LineTiming] = []
    prev_start = 0
    for line in lines:
        start = max(line.start_ms, prev_start)
        end = max(line.end_ms, start)
        out.append(replace(line, start_ms=start, end_ms=end))
        prev_start = start
    return out


def _recompute_ends(
    lines: list[LineTiming], flagged: set[int], originals: list[LineTiming]
) -> list[LineTiming]:
    """A snapped line's old end is as untrustworthy as its old start: it runs
    until the NEXT line's (final) start; the last line keeps its ORIGINAL
    duration (the snapped start makes the local end/start delta meaningless)."""
    out = list(lines)
    for i in sorted(flagged):
        if i + 1 < len(out):
            end = out[i + 1].start_ms
        else:
            end = out[i].start_ms + max(0, originals[i].end_ms - originals[i].start_ms)
        out[i] = replace(out[i], end_ms=max(end, out[i].start_ms))
    return out


def _degrade_to_line(
    result: AlignResult,
    refs: list[int | None],
    flagged: list[int],
    offset_ms: int,
) -> LineQAOutcome:
    """Whole-document fallback: raw lrclib starts (the entire doc moves onto the
    lrclib clock, so no offset mixing), ends chained to the next start. A rare
    stampless line is shifted by -offset so it lands on the same clock."""
    starts = [
        ref if ref is not None else max(0, line.start_ms - offset_ms)
        for line, ref in zip(result.lines, refs, strict=True)
    ]
    lines: list[LineTiming] = []
    for i, line in enumerate(result.lines):
        if i + 1 < len(result.lines):
            end = starts[i + 1]
        else:
            end = starts[i] + max(0, line.end_ms - line.start_ms)
        lines.append(replace(line, start_ms=starts[i], end_ms=max(end, starts[i])))
    return LineQAOutcome(
        result=AlignResult(
            sync="line",
            lines=_clamp_monotonic(lines),
            words_per_line=[],
            quality_score=result.quality_score,
        ),
        flagged=flagged,
        offset_ms=offset_ms,
        degraded_to_line=True,
    )


def apply_line_qa(
    result: AlignResult,
    line_texts: list[str],
    synced_starts_ms: list[int | None] | None,
) -> LineQAOutcome:
    refs: list[int | None] | None = None
    if synced_starts_ms is not None:
        if len(synced_starts_ms) != len(line_texts):
            logger.warning(
                "line QA skipped: %d synced starts vs %d lyric lines",
                len(synced_starts_ms),
                len(line_texts),
            )
        else:
            refs = _match_references(result.lines, line_texts, synced_starts_ms)
            if refs is None:
                logger.warning("line QA skipped: aligned lines do not walk the lyric text")

    if refs is None or sum(ref is not None for ref in refs) < MIN_REFERENCE_LINES:
        return LineQAOutcome(
            result=replace(result, lines=_clamp_monotonic(result.lines)),
            flagged=[],
            offset_ms=0,
            degraded_to_line=False,
        )

    deviations = [
        line.start_ms - ref for line, ref in zip(result.lines, refs, strict=True) if ref is not None
    ]
    offset_ms = round(median(deviations))
    flagged = [
        i
        for i, (line, ref) in enumerate(zip(result.lines, refs, strict=True))
        if ref is not None and abs(line.start_ms - ref - offset_ms) > DRIFT_THRESHOLD_MS
    ]

    referenced = sum(ref is not None for ref in refs)
    if result.sync == "line" or len(flagged) > MAX_FLAGGED_FRACTION * referenced:
        return _degrade_to_line(result, refs, flagged, offset_ms)

    if not flagged:
        return LineQAOutcome(
            result=replace(result, lines=_clamp_monotonic(result.lines)),
            flagged=[],
            offset_ms=offset_ms,
            degraded_to_line=False,
        )

    flagged_set = set(flagged)
    # Snapped starts stay on the aligner's clock (ref + offset) so unflagged
    # neighbours — which keep aligner times — remain on the same timeline.
    lines: list[LineTiming] = []
    for i, line in enumerate(result.lines):
        ref = refs[i]
        if i in flagged_set and ref is not None:
            line = replace(line, start_ms=max(0, ref + offset_ms))
        lines.append(line)
    lines = _clamp_monotonic(_recompute_ends(lines, flagged_set, result.lines))
    words_per_line = [
        [] if i in flagged_set else words for i, words in enumerate(result.words_per_line)
    ]

    for i in flagged:
        logger.info(
            "line QA snap: line %d %r start %dms -> %dms (score %.3f)",
            i,
            result.lines[i].text[:40],
            result.lines[i].start_ms,
            lines[i].start_ms,
            result.lines[i].score,
        )

    surviving_probs = [w.prob for chunk in words_per_line for w in chunk]
    if not surviving_probs:  # every word-bearing line was flagged
        return _degrade_to_line(result, refs, flagged, offset_ms)

    return LineQAOutcome(
        result=AlignResult(
            sync="word",
            lines=lines,
            words_per_line=words_per_line,
            quality_score=quality_from_probs(surviving_probs),
        ),
        flagged=flagged,
        offset_ms=offset_ms,
        degraded_to_line=False,
    )
