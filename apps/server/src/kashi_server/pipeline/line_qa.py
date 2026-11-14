"""Per-line QA of alignment output against lrclib's synced timestamps.

Motivation (TiK ToK, 2026-07-11): the CTC aligner can lose lock on a heavily
processed section (layered chorus vocals) and dump a block of lines far ahead
of where they are actually sung — up to -15 s in the observed case — while the
DOCUMENT-level quality score still clears the client's word-mode gate (0.2
since Faz 9 P4). The
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
import re
from dataclasses import dataclass, field, replace
from statistics import median

from kashi_server.pipeline.alignment import (
    AlignedWord,
    AlignResult,
    LineTiming,
    quality_from_probs,
)
from kashi_server.pipeline.arbiter import judge_line

logger = logging.getLogger(__name__)

# A flagged line is one whose deviation from the median offset exceeds this.
# The median absorbs any systematic shift between the lrclib source and the
# audio we downloaded (duration matching already bounds it to a few seconds).
#
# 2.32.0 removed four post-passes the whole-project review (2026-11-13) measured
# as doing nothing on 19 fresh songs: the sub-threshold nudge (it also caused
# two of the owner's heard errors), the call-and-response shift, the V2 voice
# return and the word-end sustain trim. Their document counters
# (`alignment.qa.nudged`, `response_shifted`, `trimmed_ends`) are still written,
# as 0, so the schema does not change (document._qa_counts).
DRIFT_THRESHOLD_MS = 2500
# Below this many stamped reference lines the median is meaningless — QA is
# skipped and only the monotonicity clamp runs.
MIN_REFERENCE_LINES = 3
# If more than this fraction of referenced lines is flagged, the alignment is
# wholesale garbage: fall back to lrclib line timings for the whole document.
MAX_FLAGGED_FRACTION = 0.5
# On that fallback, is the measured offset a CLOCK DIFFERENCE or just noise?
# A real one (a video edit's intro that the song release lacks) shifts every
# line by the same amount, so the deviations sit tightly around their median.
# An aligner that simply lost the song scatters them, and there the median is
# meaningless. Median absolute deviation is the test; the bound is comfortably
# under DRIFT_THRESHOLD_MS, because a spread that wide would have flagged the
# lines individually rather than looked like one shift.
OFFSET_TRUST_MAD_MS = 1500
# QA v2 border-case gate (field case: TiK ToK line 10 "fast flow then stall").
# A lock loss rarely ends EXACTLY at the flagged lines: their neighbours often
# carry damaged word timings that stayed just under the drift threshold. Both
# signals below therefore apply ONLY within this radius of a flagged line —
# a document with no flags never enters the gate (no instrumental-tail false
# positives).
NEIGHBOR_RADIUS = 2
# Signal A: words covering less than this fraction of the line's lrclib
# reference duration are compressed garbage (CTC dumped them at the window
# start). Denominator is the REFERENCE duration — the aligner's own line
# duration is derived from the same words and would always ratio to ~1.
MIN_WORD_DENSITY = 0.30
# When the NEXT stamp is far away, the reference "duration" is mostly an
# instrumental gap, not sung time — the density ratio says nothing there, so
# signal A only fires on plausibly line-sized reference windows.
MAX_PLAUSIBLE_LINE_MS = 10_000
# ...and only on lines with enough words to measure: a one-word exclamation
# legitimately covers a sliver of its window (reviewer catch).
MIN_DENSITY_WORDS = 3
# Signal B: a zero score next to a lock loss is corroborating evidence. Score
# alone is deliberately NOT a signal (good lines can score 0.00); adjacency is
# what makes it meaningful.
NEIGHBOR_SCORE_FLOOR = 0.01
# Ad-lib/nonlexical lines ("Oh-ooh, whoa-oh"): CTC is systematically late on
# them (field: +1-2.5 s; NonLexical songs are the worst benchmark class) while
# the lrclib stamp is human-timed on exactly that hook. Above this deviation
# the ANCHOR wins: the line and its words shift as a block (karaoke animation
# survives, placement snaps to the stamp). Ear-test finding, 2026-07-12.
ADLIB_SNAP_THRESHOLD_MS = 600
# Within-line rederivation (Faz 4 aesthetics): CTC word durations inside a
# sustained hook are unreliable even after the block shift — the karaoke sweep
# jerks. The (anchor-corrected) LINE span is trustworthy and sustained hooks
# stretch quasi-uniformly, so word boundaries are redistributed across the
# span proportionally to character length. Only for lines big enough to
# matter; single words keep the whole span implicitly.
ADLIB_REDERIVE_MIN_SPAN_MS = 500
ADLIB_REDERIVE_MIN_WORDS = 2
# A sustained hook is HELD until the next line; the aligner does not hear it
# that way. Measured across the archive (435 lines, 2026-08-13): an ad-lib
# line leaves a median 747 ms hole before the next line — 44% of its own span
# — where a lexical line leaves 30 ms. 82% of ad-lib lines leave more than
# 300 ms, against 31% of lexical ones. So the karaoke sweep finishes at ~70%
# of the phrase and then sits there while the singer is still on the note
# (field report 2026-08-13: "it goes by in 4 seconds when it should take 5").
# The hold closes that hole before the redistribution below spreads words
# across the span. Bounded, because a long silence after a hook is an
# instrumental break rather than a held note, and stretching words over it is
# the "hanging word" the held-end cap below exists to prevent.
ADLIB_HOLD_MAX_GAP_MS = 1500
# ...and it stops just short of the next line, matching the breath a lexical
# line leaves (measured median 30 ms).
ADLIB_HOLD_BREATH_MS = 60
# 2.32.0: a span this long is not a held hook but a runaway line. The
# re-derivation had no upper bound and spread "Ah-ah, ah-ah" over 23 s on a
# fresh Turkish song (whole-project review, 2026-11-13; the owner heard it
# "very slow, but lit far too early"): its first letter was held at a stamp
# that is ~19 s early (line regions, V1), its last letters sat against the
# next line where the voice is. Re-derived spans in the 2.31.0 archive (279
# lines, 303 documents): median 2 170 ms, p99 9 210, largest healthy 10 110;
# the only two above it are runaways (23 075 ms, that line; 27 990 ms, a last
# line run to the end of the audio). The 39 replayed songs: 22 lines, largest
# healthy 8 460 ms. Any bound between 10 110 and 23 075 sorts them the same.
ADLIB_REDERIVE_MAX_SPAN_MS = 12_000
# A runaway line's words are spread over this long instead (the archive's
# median re-derived span, 2 170 ms, rounded): just before the next line, whose
# start is the one real edge such a span has, and from its start for the
# song's last line, whose end is the audio's and not a voice's.
ADLIB_REDERIVE_RUNAWAY_SPAN_MS = 2_200
# The cap on a word end that `restore_held_ends` grows: start + clamp(chars x
# the document's median ms per character x factor, min, max). It is the
# formula of the word-end sustain trim (Faz 5 P1, words "hanging" past their
# sung duration), which ran inside line QA until 2.31.0 and was removed in
# 2.32.0 (it moved no word start and no dwell on 39 songs); the held-end
# restore kept its cap. The cap scales with the document's own singing speed:
# an absolute one would cut real holds in slow ballads.
TRIM_SUSTAIN_FACTOR = 3.0
TRIM_MIN_HOLD_MS = 350
TRIM_MAX_HOLD_MS = 4000
# Below this many measurable words the median char speed is noise — skip.
TRIM_MIN_SAMPLE_WORDS = 8


_NONLEXICAL_TOKEN = re.compile(
    r"^(?:o+h*|o*o+h+|wh?o+a+h*|a+h+|a+w+|ha+h*|la+|na+h*|y+e+a+h*|ye+|hey+|u+h+|hu+h*|m+h*m+|"
    r"e+h+|a+y+|do+|du+|ba+|pa+|sha+)$",
    re.IGNORECASE,
)
# A one-letter word that IS a vowel. "I" and "a" are lexical to a reader and
# pure vocalization to a singer: no consonant, no landmark, nothing for CTC to
# lock onto — acoustically they behave exactly like the tokens above. They
# cannot make a line an ad-lib on their own (a bare "I" line is not a hook),
# so at least one true nonlexical token is still required.
_VOWEL_WORD = re.compile(r"^[ia]$", re.IGNORECASE)


def is_adlib(text: str) -> bool:
    """Every alphabetic token is a nonlexical vocalization (or a bare vowel
    word beside one). Public: document assembly writes the per-line `adlib`
    schema flag with the same predicate (single source of truth).

    The vowel-word clause is a 2026-08-13 field fix. "Oh I, oh I, oh I, oh I"
    failed the old all-nonlexical test on its four "I"s and so never reached
    the ad-lib snap — measured at +0.6..+0.9 s from its anchor across all six
    occurrences in the archive, and reported by ear as arriving late. "aw" was
    simply missing from the table, which cost more: on JamendoLyrics the line
    "aw ah aw ah aw aw ah" sits 40 SECONDS from its anchor, the single worst
    placement in the set.

    Measured on that benchmark, for lines drifting into the ad-lib snap band:
    the anchor beats the aligner on 67% of ad-lib lines (median 378 -> 182 ms)
    and on 80% of nonlexical lines overall (785 -> 216 ms), while on LEXICAL
    lines the aligner wins by the same margin (103 vs 287 ms). Ad-libs are
    where the human stamp is right and the model is lost — which is exactly
    why this path existed already, and why missing two token shapes mattered.
    """
    tokens = re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ']+", text)
    if not tokens or not any(_NONLEXICAL_TOKEN.match(t) for t in tokens):
        return False
    return all(_NONLEXICAL_TOKEN.match(t) or _VOWEL_WORD.match(t) for t in tokens)


@dataclass(frozen=True)
class LineQAOutcome:
    result: AlignResult
    flagged: list[int]  # indexes into result.lines that were snapped
    offset_ms: int  # median (aligner - lrclib) offset that was compensated
    degraded_to_line: bool
    density_dropped: list[int] = field(default_factory=list)  # neighbours that lost words
    adlib_shifted: list[int] = field(default_factory=list)  # block-shifted onto their anchors
    adlib_rederived: list[int] = field(default_factory=list)  # word spans redistributed
    # Lines the drift threshold flagged but the audio vouched for: their words
    # survive, shifted onto the anchor, carrying a warning instead of a hole.
    uncertain: list[int] = field(default_factory=list)


def _line_words(chunks: list[list[AlignedWord]], i: int) -> list[AlignedWord]:
    """Line ``i``'s words; a word list shorter than the lines reads as empty."""
    return chunks[i] if i < len(chunks) else []


def _next_start(lines: list[LineTiming], i: int) -> int | None:
    """Start of the line after ``i``, or None for the last line."""
    return lines[i + 1].start_ms if i + 1 < len(lines) else None


def restore_held_ends(result: AlignResult) -> tuple[AlignResult, int]:
    """Give back what the hybrid stars and the vote cut off word ends (2.29.1).

    Runs AFTER line QA: QA reads ends as bounds (the density drop's coverage,
    the arbiter's span coverage, the ad-lib hold's gap) and was tuned on the
    cut ends. A word grows by at most its `held_ms`, never past the next word's
    start (the next line's first word for a line's last word) nor past the
    TRIM_* cap at the document's median ms per character. Only ever LENGTHENS
    an end; ad-lib lines are exempt (their spans belong to
    `rederive_adlib_words`). Returns (result, words lengthened)."""
    if result.sync != "word":
        return result, 0
    med_char_ms = _median_char_ms(result)
    if med_char_ms is None:
        return result, 0
    words_out = [list(chunk) for chunk in result.words_per_line]
    lines_out = list(result.lines)
    restored = 0
    for i, line in enumerate(result.lines):
        if is_adlib(line.text) or i >= len(words_out) or not words_out[i]:
            continue
        later = next((chunk[0].start_ms for chunk in words_out[i + 1 :] if chunk), None)
        chunk = words_out[i]
        for k, w in enumerate(chunk):
            if w.held_ms <= 0:
                continue
            allowed = round(len(w.text) * med_char_ms * TRIM_SUSTAIN_FACTOR)
            end = min(w.end_ms + w.held_ms,
                      w.start_ms + min(TRIM_MAX_HOLD_MS, max(TRIM_MIN_HOLD_MS, allowed)))
            bound = chunk[k + 1].start_ms if k + 1 < len(chunk) else later
            if bound is not None:
                end = min(end, bound)
            if end > w.end_ms:
                chunk[k] = replace(w, end_ms=end)
                restored += 1
        if chunk[-1].end_ms > line.end_ms:
            lines_out[i] = replace(line, end_ms=chunk[-1].end_ms)
    if restored:
        logger.info("held ends restored: %d word(s) lengthened (med %.0fms/char)",
                    restored, med_char_ms)
    return replace(result, lines=lines_out, words_per_line=words_out), restored


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


def rederive_adlib_words(result: AlignResult) -> tuple[AlignResult, list[int]]:
    """Hold ad-lib lines to the next line, then redistribute their words
    across that span, weighted by character length (Faz 4 aesthetics).

    Runs AFTER anchor corrections so the span it trusts is the final one.
    Guarantees monotonic, gap-free, span-covering word timings; probs are
    preserved (quality math is unaffected). Pure, unit-tested.

    The HOLD is the 2026-08-13 half: a sustained hook rings until the next
    line and the aligner measures only the part it can segment, so the sweep
    used to finish at ~70% of the phrase and wait. Bounded by
    ADLIB_HOLD_MAX_GAP_MS so a real instrumental break is never filled.

    The span itself is bounded too (2.32.0, `_cap_runaway`): past
    ADLIB_REDERIVE_MAX_SPAN_MS the words go over ADLIB_REDERIVE_RUNAWAY_SPAN_MS
    instead of crawling across the whole of it. And it ends before the next
    line starts (2.32.1, `_end_before_next`).
    """
    if result.sync != "word":
        return result, []
    words_out = [list(chunk) for chunk in result.words_per_line]
    lines_out = list(result.lines)
    changed: list[int] = []
    for i, line in enumerate(result.lines):
        words = _line_words(words_out, i)
        if len(words) < ADLIB_REDERIVE_MIN_WORDS or not is_adlib(line.text):
            continue
        next_start = _next_start(result.lines, i)
        line = _end_before_next(_cap_runaway(_hold_to_next(line, next_start), next_start),
                                next_start)
        if line is not result.lines[i]:
            lines_out[i] = line
        rederived = _respread(words, line)
        if rederived is None:
            continue
        if rederived != words:
            words_out[i] = rederived
        elif lines_out[i] is result.lines[i]:
            continue  # neither the words nor the held line end moved
        changed.append(i)  # the hold can move the line even when the words tie
        logger.info(
            "line QA adlib rederive: line %d %r words respread over %dms",
            i,
            line.text[:40],
            line.end_ms - line.start_ms,
        )
    if not changed:
        return result, []
    # Lines travel with their words: the hold moved line ends too.
    return replace(result, lines=lines_out, words_per_line=words_out), changed


def _hold_to_next(line: LineTiming, next_start: int | None) -> LineTiming:
    """Hold to the next line when the hole is hook-sized, not break-sized.
    Returns ``line`` itself when nothing moves."""
    if next_start is not None:
        gap = next_start - line.end_ms
        if 0 < gap <= ADLIB_HOLD_MAX_GAP_MS:
            held_end = next_start - ADLIB_HOLD_BREATH_MS
            if held_end > line.end_ms:  # a gap under the breath changes nothing
                return replace(line, end_ms=held_end)
    return line


def _cap_runaway(line: LineTiming, next_start: int | None) -> LineTiming:
    """A span over ADLIB_REDERIVE_MAX_SPAN_MS shrinks to
    ADLIB_REDERIVE_RUNAWAY_SPAN_MS: ending a breath before the next line (and
    no later than the line's own end), or from its start for the last line.
    Returns ``line`` itself when nothing moves, and when the next line leaves
    no room for a span the rederive would spread over."""
    if line.end_ms - line.start_ms <= ADLIB_REDERIVE_MAX_SPAN_MS:
        return line
    if next_start is None:
        return replace(line, end_ms=line.start_ms + ADLIB_REDERIVE_RUNAWAY_SPAN_MS)
    end = min(line.end_ms, next_start - ADLIB_HOLD_BREATH_MS)
    start = max(line.start_ms, end - ADLIB_REDERIVE_RUNAWAY_SPAN_MS)
    if end - start < ADLIB_REDERIVE_MIN_SPAN_MS:
        return line
    return replace(line, start_ms=start, end_ms=end)


def _end_before_next(line: LineTiming, next_start: int | None) -> LineTiming:
    """2.32.1: a line about to be re-derived may not end past the next line's
    start; it ends a breath before it, as a held one does. The ad-lib snap
    moves a line and its end as a block, and the re-derivation then spread the
    words over audio the next line already owns: the overlay hands the screen
    to the next line at its start, so the sweep never finished (Toz Pembe's
    line 1 ended 900 ms into line 2; 32 re-derived ad-lib lines of the 2.31.0
    archive end past the next start). Runs after `_cap_runaway`, whose span
    test it does not change. Returns ``line`` itself when the end does not
    pass the next start, and when the next line leaves no room for a span the
    re-derivation would spread over."""
    if next_start is None or line.end_ms <= next_start:
        return line
    end = next_start - ADLIB_HOLD_BREATH_MS
    if end - line.start_ms < ADLIB_REDERIVE_MIN_SPAN_MS:
        return line
    return replace(line, end_ms=end)


def _respread(words: list[AlignedWord], line: LineTiming) -> list[AlignedWord] | None:
    """The words spread across the line's span by character weight, or None
    when the span is too short or the words carry no characters."""
    span = line.end_ms - line.start_ms
    if span < ADLIB_REDERIVE_MIN_SPAN_MS:
        return None
    total_chars = sum(len(w.text) for w in words)
    if total_chars <= 0:
        return None
    bounds = [line.start_ms]
    cum = 0
    for w in words:
        cum += len(w.text)
        bounds.append(line.start_ms + round(span * cum / total_chars))
    return [replace(w, start_ms=bounds[k], end_ms=bounds[k + 1]) for k, w in enumerate(words)]


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


def _offset_is_a_clock_difference(deviations: list[int] | None, offset_ms: int) -> bool:
    """Did every line move by the same amount, or did the aligner just scatter?

    Pure. Uses median absolute deviation rather than the mean so a couple of
    genuinely lost lines cannot veto a shift the rest of the document agrees
    on — the same reason the offset itself is a median.
    """
    if not deviations:
        return False
    mad = median([abs(d - offset_ms) for d in deviations])
    return mad <= OFFSET_TRUST_MAD_MS


def _degrade_to_line(
    result: AlignResult,
    refs: list[int | None],
    flagged: list[int],
    offset_ms: int,
    deviations: list[int] | None = None,
) -> LineQAOutcome:
    """Whole-document fallback: lrclib's line spacing, ends chained to the next
    start. A rare stampless line is shifted by -offset so it lands on the same
    clock.

    lrclib's clock is NOT assumed to be the audio's clock (Faz 8 P-B0). The
    field case: a YouTube *video* edit opens with an intro the song release
    does not have, so lrclib's stamps describe an audio that starts minutes
    of nothing earlier. The intro also pushes the durations apart, which drops
    the anchors (ANCHOR_CLOCK_TOLERANCE_S) and leaves whole-audio alignment —
    whose measured mean offset is 2000 ms against 282 ms on the anchored path.
    This function then used raw `ref`, discarding the very shift the aligner
    had just measured. Three of the ten line-mode documents in the archive
    carry an |offset| above 3 s, the largest 16.9 s, all of it thrown away.

    So the offset is applied — but only when it is a CLOCK DIFFERENCE rather
    than noise. A consistent shift shows up as deviations clustered tightly
    around their median; a document the aligner simply got wrong shows up as
    scatter, and there the median means nothing and lrclib's raw clock really
    is the better guess. `OFFSET_TRUST_MAD_MS` is that test.

    The score is RECOMPUTED as line-anchor agreement (Faz 8 P-B2). It used to
    be inherited untouched from the pre-QA result, so a document that lost
    every word timing kept whatever the aligner had claimed — BABYMETAL
    "BxMxC" flagged 32 of its 42 lines and still shipped a 1.00. There is no
    word evidence left here by construction, and `line-anchors` says exactly
    that."""
    shift = offset_ms if _offset_is_a_clock_difference(deviations, offset_ms) else 0
    starts = [
        max(0, ref + shift) if ref is not None else max(0, line.start_ms - offset_ms + shift)
        for line, ref in zip(result.lines, refs, strict=True)
    ]
    lines: list[LineTiming] = []
    for i, line in enumerate(result.lines):
        if i + 1 < len(result.lines):
            end = starts[i + 1]
        else:
            end = starts[i] + max(0, line.end_ms - line.start_ms)
        lines.append(replace(line, start_ms=starts[i], end_ms=max(end, starts[i])))
    referenced = sum(ref is not None for ref in refs)
    agreement = round(1 - len(set(flagged)) / referenced, 4) if referenced else result.quality_score
    return LineQAOutcome(
        result=AlignResult(
            sync="line",
            lines=_clamp_monotonic(lines),
            words_per_line=[],
            quality_score=agreement,
            windowed=result.windowed,
            model_name=result.model_name,
            quality_basis="line-anchors" if referenced else result.quality_basis,
        ),
        flagged=flagged,
        offset_ms=offset_ms,
        degraded_to_line=True,
    )


def apply_line_qa(
    result: AlignResult,
    line_texts: list[str],
    synced_starts_ms: list[int | None] | None,
    onset_ms: list[int] | None = None,
) -> LineQAOutcome:
    refs = _resolve_references(result, line_texts, synced_starts_ms)

    if refs is None or sum(ref is not None for ref in refs) < MIN_REFERENCE_LINES:
        return _finish_unreferenced(result)

    deviations = [
        line.start_ms - ref for line, ref in zip(result.lines, refs, strict=True) if ref is not None
    ]
    offset_ms = round(median(deviations))
    result, adlib_shifted = _shift_adlibs(result, refs, offset_ms)
    flagged = [
        i
        for i, (line, ref) in enumerate(zip(result.lines, refs, strict=True))
        if ref is not None and abs(line.start_ms - ref - offset_ms) > DRIFT_THRESHOLD_MS
    ]

    referenced = sum(ref is not None for ref in refs)
    if result.sync == "line" or len(flagged) > MAX_FLAGGED_FRACTION * referenced:
        return replace(
            _degrade_to_line(result, refs, flagged, offset_ms, deviations),
            adlib_shifted=adlib_shifted,
        )

    if not flagged:
        return _finish_clean(result, refs, offset_ms, adlib_shifted)

    flagged_set = set(flagged)
    lines = _snap_flagged_lines(result, refs, flagged_set, offset_ms)
    density_dropped = _border_case_drops(result, refs, flagged_set)
    words_per_line, uncertain = _rescue_or_drop_words(
        result, lines, flagged_set, density_dropped, onset_ms
    )

    _log_snaps_and_drops(result, lines, flagged, density_dropped)

    surviving_probs = [w.prob for chunk in words_per_line for w in chunk]
    if not surviving_probs:  # flag + border drops emptied every word-bearing line
        return _degrade_to_line(result, refs, flagged, offset_ms, deviations)

    score, basis = _quality(result, refs, flagged_set | density_dropped, surviving_probs)
    snapped = AlignResult(
        sync="word",
        lines=lines,
        words_per_line=words_per_line,
        quality_score=score,
        windowed=result.windowed,
        model_name=result.model_name,
        quality_basis=basis,
    )
    snapped, rederived = rederive_adlib_words(snapped)
    return LineQAOutcome(
        result=snapped,
        flagged=flagged,
        offset_ms=offset_ms,
        degraded_to_line=False,
        density_dropped=sorted(density_dropped),
        uncertain=uncertain,
        adlib_shifted=adlib_shifted,
        adlib_rederived=rederived,
    )


def _resolve_references(
    result: AlignResult,
    line_texts: list[str],
    synced_starts_ms: list[int | None] | None,
) -> list[int | None] | None:
    """lrclib's start per aligned line, or None when they cannot be paired."""
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
    return refs


def _finish_unreferenced(result: AlignResult) -> LineQAOutcome:
    # No reference ≠ no ad-libs: document assembly still writes the adlib
    # flag and the overlay still sweeps, so the rederive must run here too
    # (retro finding — QA-less docs swept CTC's scattered spans).
    clamped = replace(result, lines=_clamp_monotonic(result.lines))
    clamped, rederived = rederive_adlib_words(clamped)
    return LineQAOutcome(
        result=clamped,
        flagged=[],
        offset_ms=0,
        degraded_to_line=False,
        adlib_rederived=rederived,
    )


def _finish_clean(
    result: AlignResult,
    refs: list[int | None],
    offset_ms: int,
    adlib_shifted: list[int],
) -> LineQAOutcome:
    """No line flagged: clamp, score and the ad-lib rederive."""
    clean = replace(result, lines=_clamp_monotonic(result.lines))
    all_probs = [w.prob for chunk in result.words_per_line for w in chunk]
    # Undamaged, but the basis still has to name the formula: an unflagged
    # non-windowed document is "probs+anchors" with an agreement of 1.0,
    # not the bare ramp it used to be labelled as.
    score, basis = _quality(result, refs, set(), all_probs)
    clean = replace(clean, quality_score=score, quality_basis=basis)
    clean, rederived = rederive_adlib_words(clean)
    return LineQAOutcome(
        result=clean,
        flagged=[],
        offset_ms=offset_ms,
        degraded_to_line=False,
        adlib_shifted=adlib_shifted,
        adlib_rederived=rederived,
    )


def _snap_flagged_lines(
    result: AlignResult, refs: list[int | None], flagged_set: set[int], offset_ms: int
) -> list[LineTiming]:
    # Snapped starts stay on the aligner's clock (ref + offset) so unflagged
    # neighbours — which keep aligner times — remain on the same timeline.
    lines: list[LineTiming] = []
    for i, line in enumerate(result.lines):
        ref = refs[i]
        if i in flagged_set and ref is not None:
            line = replace(line, start_ms=max(0, ref + offset_ms))
        lines.append(line)
    lines = _clamp_monotonic(_recompute_ends(lines, flagged_set, result.lines))
    return lines


def _rescue_or_drop_words(
    result: AlignResult,
    lines: list[LineTiming],
    flagged_set: set[int],
    density_dropped: set[int],
    onset_ms: list[int] | None,
) -> tuple[list[list], list[int]]:
    # The anchor proposes, the audio disposes (Faz 8 B4). A flagged line used
    # to lose its words unconditionally; now the evidence gets a vote, and
    # deletion carries the burden of proof. A rescued line is BLOCK-SHIFTED
    # with its snapped start so line and words stay on one clock — the ad-lib
    # path's precedent — and marked uncertain so the client can de-emphasise
    # what the server no longer destroys.
    words_per_line: list[list] = []
    uncertain: list[int] = []
    for i, words in enumerate(result.words_per_line):
        if i in density_dropped:
            words_per_line.append([])
            continue
        if i not in flagged_set:
            words_per_line.append(words)
            continue
        original = result.lines[i]
        verdict = judge_line(words, original.end_ms - original.start_ms, onset_ms)
        if verdict.drop_words:
            words_per_line.append([])
            continue
        delta = lines[i].start_ms - original.start_ms
        words_per_line.append(
            [replace(w, start_ms=w.start_ms + delta, end_ms=w.end_ms + delta) for w in words]
        )
        uncertain.append(i)
        logger.info(
            "line QA rescue: line %d %r kept (onset support %s, coverage %.2f) — %s",
            i,
            original.text[:40],
            "n/a" if verdict.onset_support is None else f"{verdict.onset_support:.2f}",
            verdict.span_coverage,
            verdict.reason,
        )
    return words_per_line, uncertain


def _log_snaps_and_drops(
    result: AlignResult, lines: list[LineTiming], flagged: list[int], density_dropped: set[int]
) -> None:
    for i in flagged:
        logger.info(
            "line QA snap: line %d %r start %dms -> %dms (score %.3f)",
            i,
            result.lines[i].text[:40],
            result.lines[i].start_ms,
            lines[i].start_ms,
            result.lines[i].score,
        )
    for i in sorted(density_dropped):
        logger.info(
            "line QA border drop: line %d %r (score %.3f)",
            i,
            result.lines[i].text[:40],
            result.lines[i].score,
        )


def _quality(
    result: AlignResult,
    refs: list[int | None],
    damaged: set[int],
    surviving_probs: list[float],
) -> tuple[float, str]:
    """Returns (score, basis) — the basis names the formula that produced it.

    Windowed path: anchor agreement alone. Measured (79 songs, 2026-07-12) the
    per-window probs do NOT track true accuracy (r=0.36; 10 of 13 sub-gate
    scores had PCO>=0.88), which would wrongly push good documents under the
    client's word-mode gate — the same finding that dropped that gate to 0.2
    in Faz 9 P4. Lines that stayed within the drift threshold
    of their lrclib anchor ARE the windowed path's accuracy evidence.

    Otherwise the prob ramp — but SCALED BY the same agreement whenever
    reference stamps exist (Faz 8 P-B2). `surviving_probs` is drawn from the
    words QA did not delete, so the pool improves every time a line is
    damaged: the worse the document, the more confident its remainder looks.
    Tarkan "Op" shipped a 1.00 with 19 of 40 lines flagged and an 11 s global
    offset. Confidence in what survived is not confidence in the document, so
    the surviving-word ramp is multiplied by the fraction of referenced lines
    that came through intact. With no references there is no damage measure
    and the ramp alone is the honest answer.
    """
    referenced = [i for i, ref in enumerate(refs) if ref is not None]
    if not referenced:
        return quality_from_probs(surviving_probs), "ctc-probs"
    agreement = 1 - len(damaged & set(referenced)) / len(referenced)
    if result.windowed:
        return round(agreement, 4), "anchors"
    return round(quality_from_probs(surviving_probs) * agreement, 4), "probs+anchors"


def _shift_adlibs(
    result: AlignResult, refs: list[int | None], offset_ms: int
) -> tuple[AlignResult, list[int]]:
    """Move nonlexical lines (and their words, as a block) onto their lrclib
    anchor when the aligner strayed past ADLIB_SNAP_THRESHOLD_MS. The anchor
    stays on the aligner's clock (ref + offset) like every other snap."""
    lines = list(result.lines)
    words = [list(chunk) for chunk in result.words_per_line]
    shifted: list[int] = []
    for i, (line, ref) in enumerate(zip(result.lines, refs, strict=True)):
        if ref is None or not is_adlib(line.text):
            continue
        delta = (ref + offset_ms) - line.start_ms
        if abs(delta) <= ADLIB_SNAP_THRESHOLD_MS:
            continue
        start = max(0, line.start_ms + delta)
        lines[i] = replace(line, start_ms=start, end_ms=max(start, line.end_ms + delta))
        if i < len(words) and words[i]:
            words[i] = [
                replace(
                    w,
                    start_ms=max(0, w.start_ms + delta),
                    end_ms=max(0, w.start_ms + delta, w.end_ms + delta),
                )
                for w in words[i]
            ]
        shifted.append(i)
        logger.info(
            "line QA adlib shift: line %d %r %+dms onto its anchor",
            i,
            line.text[:40],
            delta,
        )
    if not shifted:
        return result, []
    return replace(result, lines=lines, words_per_line=words), shifted


def _border_case_drops(
    result: AlignResult,
    refs: list[int | None],
    flagged: set[int],
) -> set[int]:
    """Word drops for damaged NEIGHBOURS of a lock loss (QA v2, field case:
    a line whose drift stayed just under the threshold but whose word timings
    are garbage — "fast flow then stall"). Runs only around flagged lines."""
    neighborhood = {
        j
        for i in flagged
        for j in range(i - NEIGHBOR_RADIUS, i + NEIGHBOR_RADIUS + 1)
        if 0 <= j < len(result.lines) and j not in flagged
    }
    drops: set[int] = set()
    for i in sorted(neighborhood):
        words = _line_words(result.words_per_line, i)
        if not words:
            continue
        # Signal B: zero score right next to a lock loss (no stamp needed —
        # it reads only the aligner's own confidence).
        if result.lines[i].score < NEIGHBOR_SCORE_FLOOR or _compressed_words(words, refs, i):
            drops.add(i)
    return drops


def _compressed_words(words: list[AlignedWord], refs: list[int | None], i: int) -> bool:
    """Signal A: words compressed into a fraction of the lrclib reference
    duration (needs this line's stamp AND the next one's)."""
    ref = refs[i]
    next_ref = refs[i + 1] if i + 1 < len(refs) else None
    if len(words) < MIN_DENSITY_WORDS:
        return False
    if ref is None or next_ref is None or not 0 < next_ref - ref <= MAX_PLAUSIBLE_LINE_MS:
        return False
    coverage = (words[-1].end_ms - words[0].start_ms) / (next_ref - ref)
    return coverage < MIN_WORD_DENSITY
