"""Does a drifted line deserve to lose its words, or only a warning?

The defect this repairs (Faz 8 B4, measured 2026-08-06). A line whose start
strays past `DRIFT_THRESHOLD_MS` from its lrclib anchor has its word timings
**deleted** — the line survives as plain text. That rule has no second
opinion in it: the anchor says "this line is misplaced" and the words die for
it, even when they were internally perfect and merely sat on a shifted clock.

The archive says the rule is too eager. At document scale, the cheapest
threshold catching both genuinely bad songs destroys eleven good ones. At
line scale — 3383 lines with ground truth — flagging the worst 5 % by signal
catches 35 % of the truly bad lines: **seven times chance**, and nowhere near
a separator. A signal that good deserves to be acted on softly and is nowhere
near good enough to justify deleting anything.

So the anchor proposes and the evidence disposes:

- **Vocal onsets** (`onsets.py`) — the only signal independent of the aligner,
  since it comes from the audio rather than the model that produced the
  timings. Measured Spearman +0.399 against per-line truth.
- **Silence coverage** — how much of the line's own span carries no word.
  +0.345. A line smeared across an instrumental gap looks nothing like a sung
  one.

When both corroborate the anchor, the words go, exactly as before. When they
contradict it, the line is **block-shifted onto the anchor and marked
uncertain** — the shift keeps line and words on one clock (the ad-lib path's
precedent), and the mark lets the client de-emphasise rather than the server
destroy.

Pure module: it is handed onsets, never audio. Detection lives at the I/O
boundary in `onsets.py` so this stays testable without librosa.
"""

import logging
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

# A word start this far from the nearest detected vocal onset is unsupported
# by the audio. Deliberately generous: in singing a note onset lands on the
# syllable's VOWEL, so a word start sits early of it by the leading
# consonant's length, and the tolerance has to absorb that bias rather than
# punish every consonant-initial word (measured design note, Faz 8).
# CALIBRATED ON THE RAW ALIGNER CLOCK — which is why `onset_support` undoes
# the Faz 9 lateness shift (word.shift_ms) before comparing: the shift moves
# every English word 60-110 ms further from the onsets it was measured
# against, and judging the shifted clock would spend half this tolerance on
# a display correction (2026-08-12 audit, two independent reviews).
ONSET_TOLERANCE_MS = 200
# Below this fraction of a line's words landing on an onset, the audio does
# not back the timings up. Chosen at the measured operating point: flagging
# the worst ~5% of lines by this signal catches ~35% of genuinely bad ones.
MIN_ONSET_SUPPORT = 0.34
# A line whose words cover less than this fraction of its own span is mostly
# hole — the aligner smeared a few words across a gap it could not explain.
MIN_SPAN_COVERAGE = 0.30
# Corroboration needs evidence: below this many words neither signal means
# anything and the anchor is left to rule alone, as it does today.
MIN_WORDS_FOR_EVIDENCE = 3
# …with one exception, measured after the first field run (2026-08-09).
# NOT ONE word landing near an onset is not weak evidence, it is a positional
# verdict: on 3206 ground-truth lines the 21 with zero support were **67%
# genuinely bad** (median PCO 0.25, median error 578 ms) against a 4% base
# rate — sixteen times the background. Requiring coverage to agree let that
# class through, because coverage measures a line's SHAPE while onsets
# measure its PLACE, and a line dragged somewhere wrong keeps its shape
# perfectly. The field run proved it: "To fight, to fight, to fight" was
# rescued at onset support 0.00 and coverage 1.00.
ZERO_SUPPORT_IS_DAMNING = True


@dataclass(frozen=True)
class LineVerdict:
    """What to do with one flagged line, and why."""

    drop_words: bool
    onset_support: float | None  # fraction landing on an onset; None = unmeasured
    span_coverage: float

    @property
    def reason(self) -> str:
        if self.drop_words:
            return "audio agrees the line is misplaced"
        return "audio backs the words up; shifted onto the anchor and marked"


def _span_coverage(words: list, line_span_ms: int) -> float:
    """Fraction of the line's own span that actually carries a word."""
    if line_span_ms <= 0 or not words:
        return 0.0
    sung = sum(max(0, w.end_ms - w.start_ms) for w in words)
    return min(1.0, sung / line_span_ms)


def onset_support(words: list, onset_ms: list[int] | None) -> float | None:
    """Fraction of word starts within `ONSET_TOLERANCE_MS` of a vocal onset.

    None when there is nothing to measure against — no onsets detected, or no
    words. An unmeasurable signal must not be read as a bad one.
    """
    if not onset_ms or not words:
        return None
    hits = 0
    cursor = 0
    # Evidence is judged on the clock it was calibrated on: onsets are
    # acoustic, so the lateness correction is undone per word before
    # comparing. Words that never went through shift_result carry 0.
    starts = sorted(w.start_ms - getattr(w, "shift_ms", 0) for w in words)
    for start in starts:
        # Both sequences are sorted, so the nearest onset is found by walking
        # forward once rather than scanning per word.
        while cursor + 1 < len(onset_ms) and abs(onset_ms[cursor + 1] - start) <= abs(
            onset_ms[cursor] - start
        ):
            cursor += 1
        if abs(onset_ms[cursor] - start) <= ONSET_TOLERANCE_MS:
            hits += 1
    return hits / len(starts)


def better_supported_position(
    words: list, shift_ms: int, onset_ms: list[int] | None, *, margin: float = 0.15
) -> bool:
    """Would moving this line by `shift_ms` put its words on MORE vocal onsets?

    The sub-threshold drift decision (Faz 9, 2026-08-13). A line sitting
    0.3-2.5 s from its lrclib anchor is invisible to `judge_line`: too close
    to be flagged, far enough to be heard. Two positions are on the table —
    where the aligner put the line, and where the anchor says it belongs —
    and the argument between an aligner and a crowd-sourced stamp cannot be
    settled by either of them. The audio can: onsets come from neither.

    So this asks the only question evidence can answer — which of the two
    candidate positions has more word starts landing on a sung onset — and
    answers False on a tie. `margin` makes "more" mean meaningfully more
    (15 percentage points): a one-word difference on a five-word line is
    noise, and moving a line the listener can see is not free.

    Pure: it is handed onsets, never audio.
    """
    if not onset_ms or not words:
        return False
    here = onset_support(words, onset_ms)
    if here is None:
        return False
    moved = onset_support(
        [replace(w, start_ms=max(0, w.start_ms + shift_ms)) for w in words], onset_ms
    )
    if moved is None:
        return False
    return moved >= here + margin


def judge_line(words: list, line_span_ms: int, onset_ms: list[int] | None) -> LineVerdict:
    """Second opinion on a line the drift threshold already flagged. Pure.

    Returns drop_words=True only when the evidence AGREES with the anchor. The
    burden of proof sits on deletion, not on survival: that is the whole
    correction, since the measured cost of the old rule was good documents
    losing word timings they had earned.
    """
    coverage = _span_coverage(words, line_span_ms)
    support = onset_support(words, onset_ms)

    if len(words) < MIN_WORDS_FOR_EVIDENCE:
        # Too few words for either signal to mean anything. The anchor rules
        # alone, exactly as it does today — no new behaviour where there is no
        # new evidence.
        return LineVerdict(drop_words=True, onset_support=support, span_coverage=coverage)

    if support is None:
        # Onset detection unavailable (no librosa, unreadable audio). Coverage
        # alone is the weaker signal, so it only rescues an unambiguous case.
        return LineVerdict(
            drop_words=coverage < MIN_SPAN_COVERAGE,
            onset_support=None,
            span_coverage=coverage,
        )

    if ZERO_SUPPORT_IS_DAMNING and support == 0.0:
        # The one asymmetry. Coverage cannot vouch for a line the audio places
        # nowhere near singing, so it does not get a vote here.
        return LineVerdict(drop_words=True, onset_support=support, span_coverage=coverage)

    unsupported = support < MIN_ONSET_SUPPORT
    hollow = coverage < MIN_SPAN_COVERAGE
    # Otherwise BOTH must corroborate. Partial support is a seven-times-chance
    # signal, which is worth a warning and not worth a deletion on its own.
    return LineVerdict(
        drop_words=unsupported and hollow,
        onset_support=support,
        span_coverage=coverage,
    )
