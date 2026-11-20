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
# The QA v2 border-case "density drop" (the words of unflagged lines within two
# lines of a flagged one were dropped when their score was under 0.01 or they
# covered under 30 % of their stamp window) was removed in 2.34.0: on the
# 287-song archive rig it left 256 lines wordless where 141 remain without it,
# and 106 of the 116 re-lit lines are lit acceptably (vote-pile-guard notes,
# section 12; review step C.4). `density_dropped` stays in the document's
# `alignment.qa`, always 0, so the schema and its readers do not change.
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

    Runs AFTER line QA: QA reads ends as bounds (the arbiter's span coverage,
    the ad-lib hold's gap) and was tuned on the cut ends. A word grows by at
    most its `held_ms`, never past the next word's start (the next line's
    first word for a line's last word) nor past the
    TRIM_* cap at the document's median ms per character. Only ever LENGTHENS
    an end; ad-lib lines are exempt (their spans belong to
    `rederive_adlib_words`). Returns (result, words lengthened).

    A letterless line (`unaligned`, a 0 ms marker) is passed over, as if the
    sheet had none: line QA cut no word at it, so the median here is the one
    the sheet without it gives too, and the bounds are. `_settle_markers` then
    lets a marker on its stamp cut the restored word before it (never below its
    floor, on this same median) and moves any other marker to the restored end
    of the line before it."""
    if result.sync != "word":
        return result, 0
    med_char_ms = _median_char_ms(result)
    if med_char_ms is None:
        if not any(line.unaligned for line in result.lines):
            return result, 0
        lines, words = _settle_markers(list(result.lines), result.words_per_line, None)
        return replace(result, lines=lines, words_per_line=words), 0
    words_out = [list(chunk) for chunk in result.words_per_line]
    lines_out = list(result.lines)
    restored = 0
    last_line = min(len(result.lines), len(words_out))
    for i, line in enumerate(result.lines):
        if is_adlib(line.text) or i >= len(words_out) or not words_out[i]:
            continue
        # the next line's first word (a line without words, a marker among them, passed over;
        # never a chunk past the last line)
        later = next(
            (words_out[j][0].start_ms for j in range(i + 1, last_line) if words_out[j]), None
        )
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
    lines_out, words_out = _settle_markers(lines_out, words_out, med_char_ms)
    return replace(result, lines=lines_out, words_per_line=words_out), restored


def _settle_markers(
    lines: list[LineTiming], words: list[list[AlignedWord]], med_char_ms: float | None
) -> tuple[list[LineTiming], list[list[AlignedWord]]]:
    """`_settle_after_restore`, and the placement count logged once per document."""
    if not any(line.unaligned for line in lines):
        return lines, words
    lines, words = _settle_after_restore(lines, words, med_char_ms)
    logger.info("letterless lines placed: %s", marker_kinds(lines))
    return lines, words


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
    line starts (2.34.0, `_end_before_next`).
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
    """2.34.0: a line about to be re-derived may not end past the next line's
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


def _natural_ms(word: AlignedWord, med_char_ms: float | None) -> int:
    """A word's natural length: the document's median ms per character (`_median_char_ms`, the
    held-end restore's own measure; restore allows up to 3x it, capped at TRIM_MAX_HOLD_MS)
    times its characters, never under TRIM_MIN_HOLD_MS (the shortest hold the restore gives).
    TRIM_MIN_HOLD_MS alone without a median."""
    spoken = round(len(word.text) * med_char_ms) if med_char_ms else 0
    return max(TRIM_MIN_HOLD_MS, spoken)


def _floor_end(word: AlignedWord, med_char_ms: float | None) -> int:
    """Where a letterless line may at the earliest end ``word``: its natural length from its
    start (`_natural_ms`), or its own end when it is shorter."""
    return word.start_ms + min(word.end_ms - word.start_ms, _natural_ms(word, med_char_ms))


#: How a letterless line was placed (`LineTiming.placed`): on its stamp + QA's offset; past its
#: stamp, at the end of the word before it, which it cut to that word's floor; bounded by the
#: next aligned line's start; after the line before it (its stamp lay earlier, or a marker before
#: it or the clamp moved it); stampless (where regroup put it).
MARKER_KINDS = ("stamp", "floor", "next", "previous", "stampless")
#: The kinds that cut the word before them (at the stamp, never below its floor). The others
#: shorten nothing: the restore passes over them and they follow the line before
#: (`_settle_markers`).
CUT_KINDS = ("stamp", "floor")


def marker_kinds(lines: list[LineTiming]) -> dict[str, int]:
    """How many letterless lines were placed each way (`MARKER_KINDS`); empty without one."""
    placed = [line.placed for line in lines if line.unaligned]
    return {kind: placed.count(kind) for kind in MARKER_KINDS} if placed else {}


def _place_unaligned(
    lines: list[LineTiming],
    refs: list[int | None] | None,
    offset_ms: int,
    snapped: set[int] | frozenset[int] = frozenset(),
    words: list[list[AlignedWord]] | None = None,
    originals: list[LineTiming] | None = None,
) -> list[LineTiming]:
    """The lines the aligner was not given (`unaligned`: no letter, no digit) placed, each a
    marker 0 ms long (its end is its start): the gap after it stays a gap, which the overlay
    holds (<= 5 s), shows as an interlude (longer) and leads the next line into, as it does when
    no such line is there. Never past the next aligned line's start, which bounds the stamp
    first: a line the aligner never heard moves no sung line.

    Line QA only places them and cuts nothing: every word and every sung line leaves QA as the
    sheet without letterless lines leaves it, so the held-end restore reads the same median and
    bounds. A marker shortens a sung word only after the restore (`_settle_markers`), by cutting
    at its own stamp and never below the word's floor (`_floor_end`). A segment-starred window
    pass gives the last word of its text every frame to the slice end (ctc_forced_aligner's
    get_spans pads the last span to the end of the trailing blank), so the aligner's end of the
    word before a letterless line is no evidence either way: it can run over the whole interlude
    (2026-11-20, the archive: a 20 s hold), or be a true short end. With a stamp (ref +
    offset_ms) not past the next line:

    - after the start of the last word of an ordinary sung line before it: on the stamp
      ("stamp", the kind that cuts);
    - otherwise after the line before it ends (starts, when that line is a marker; when it was
      snapped, after its rescued words): on the stamp when it lies later ("stamp"), else at that
      end ("previous");

    a stamp past the next line puts it on that line's start ("next"); without one, where regroup
    put it ("stampless", after the line before). `words` are the result's words per line,
    `originals` the lines before the snaps (where a snapped line's rescued words will lie).
    Pure; lines come back as they were when none is unaligned."""
    if not any(line.unaligned for line in lines):
        return lines
    out = list(lines)
    for i, line in enumerate(out):
        if not line.unaligned:
            continue
        after = next((later.start_ms for later in out[i + 1 :] if not later.unaligned), None)
        before = out[i - 1] if i > 0 else None
        chunk = words[i - 1] if words is not None and 0 < i <= len(words) else []
        held = before is not None and (before.unaligned or (i - 1) in snapped)
        if before is None:
            lo = 0
        elif not held:
            lo = before.end_ms
        else:
            lo = before.start_ms
            if (i - 1) in snapped and chunk and originals is not None:
                # the rescue block-shifts the snapped line's words with its start
                lo = max(lo, chunk[-1].end_ms + before.start_ms - originals[i - 1].start_ms)
        ref = refs[i] if refs is not None else None
        last = chunk[-1] if chunk and not held else None
        if ref is None:
            start, kind = max(line.start_ms, lo), "stampless"
        else:
            want = max(0, ref + offset_ms)
            if after is not None and want > after:
                start, kind = after, "next"
            elif last is not None and want > last.start_ms:
                start, kind = want, "stamp"
            else:
                start = max(want, lo)
                kind = "stamp" if start == want else "previous"
        if after is not None and start > after:
            start, kind = after, "next"
        out[i] = replace(line, start_ms=start, end_ms=start, placed=kind)
    return out


def _settle_after_restore(
    lines: list[LineTiming], words: list[list[AlignedWord]], med_char_ms: float | None
) -> tuple[list[LineTiming], list[list[AlignedWord]]]:
    """After the held-end restore, which passes over every marker (as if no letterless line were
    there), with the restore's own median (`med_char_ms`, the one the sheet without letterless
    lines gives). A marker on its stamp ("stamp") after the start of the last word of the line
    before cuts that word, when it reaches past the stamp, at the stamp or at the word's floor
    when that is later, and moves to the cut end when it is the floor ("floor"); the line ends
    there too. When that last word starts at or after the stamp, the marker follows it and cuts
    nothing ("previous"). Any other marker follows the line before it to its restored words' end
    and shortens nothing. Never past the next aligned line's start (there "next", unless it cut),
    never before a marker before it ("previous" when that moved it). Words and lines come back
    as they were when none is unaligned."""
    if not any(line.unaligned for line in lines):
        return lines, words
    lines, words = list(lines), [list(chunk) for chunk in words]
    for i, line in enumerate(lines):
        if not line.unaligned or i == 0:
            continue
        after = next((later.start_ms for later in lines[i + 1 :] if not later.unaligned), None)
        before = lines[i - 1]
        start, kind = line.start_ms, line.placed
        if before.unaligned:
            if start < before.start_ms:
                start = before.start_ms
                kind = "previous" if kind in CUT_KINDS else kind
        elif i - 1 < len(words) and words[i - 1]:
            last = words[i - 1][-1]
            if kind in CUT_KINDS and last.start_ms < start:
                if start < last.end_ms:
                    end = max(start, _floor_end(last, med_char_ms))
                    words[i - 1] = [*words[i - 1][:-1], replace(last, end_ms=end)]
                    if end > start:
                        start, kind = end, "floor"
                if before.end_ms > start:
                    lines[i - 1] = replace(before, end_ms=max(before.start_ms, start))
            elif kind != "next":
                kind = "previous" if kind in CUT_KINDS else kind
                start = max(start, last.end_ms)
        if after is not None and start > after:
            start, kind = after, kind if kind in CUT_KINDS else "next"
        lines[i] = replace(line, start_ms=start, end_ms=start, placed=kind)
    return lines, words


def _settle(
    lines: list[LineTiming],
    originals: list[LineTiming],
    refs: list[int | None] | None,
    offset_ms: int,
    snapped: set[int] | frozenset[int] = frozenset(),
    words: list[list[AlignedWord]] | None = None,
) -> list[LineTiming]:
    """The line timeline QA hands on: the unaligned lines placed (`_place_unaligned`, 0 ms
    markers, after the ad-lib shifts and the snaps), the snapped lines ending at the next sung
    line's start (`_recompute_ends`), starts monotonic. Without unaligned lines:
    `_clamp_monotonic(_recompute_ends(lines, snapped, ...))`, what QA ran before them."""
    placed = _place_unaligned(lines, refs, offset_ms, snapped, words, originals)
    out = _clamp_monotonic(_recompute_ends(placed, set(snapped), originals))
    if placed is lines:
        return out
    # a marker the clamp lifted (a sung line before it starts later) sits after that line
    return [
        replace(c, placed="previous")
        if c.unaligned and c.placed != "stampless" and c.start_ms != p.start_ms
        else c
        for c, p in zip(out, placed, strict=True)
    ]


def _recompute_ends(
    lines: list[LineTiming], flagged: set[int], originals: list[LineTiming]
) -> list[LineTiming]:
    """A snapped line's old end is as untrustworthy as its old start: it runs
    until the NEXT line's (final) start; the last line keeps its ORIGINAL
    duration (the snapped start makes the local end/start delta meaningless)."""
    out = list(lines)
    for i in sorted(flagged):
        # the next SUNG line: a letterless line (`unaligned`, a 0 ms marker) is passed over
        nxt = next((j for j in range(i + 1, len(out)) if not out[j].unaligned), None)
        if nxt is not None:
            end = out[nxt].start_ms
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


def _degraded_with_markers(
    result: AlignResult, refs: list[int | None], starts: list[int]
) -> list[LineTiming]:
    """`_degrade_to_line`'s timeline when the sheet has letterless lines: the sung lines exactly
    as the sheet without them gives them (starts, ends chained to the next SUNG line, the
    clamp), then each marker, 0 ms, against that timeline. On its stamp when that lies after the
    start of the sung line before it, which then ends there ("stamp"); otherwise, or without a
    stamp, at that line's end ("previous" / "stampless"); after a marker, never before it; never
    past the next sung line's start ("next"). A marker moves no sung start and lengthens no sung
    line."""
    sung = [i for i, line in enumerate(result.lines) if not line.unaligned]
    chained: list[LineTiming] = []
    for k, i in enumerate(sung):
        line = result.lines[i]
        if k + 1 < len(sung):
            end = starts[sung[k + 1]]
        else:
            end = starts[i] + max(0, line.end_ms - line.start_ms)
        chained.append(replace(line, start_ms=starts[i], end_ms=max(end, starts[i])))
    out: list[LineTiming] = list(result.lines)
    for i, line in zip(sung, _clamp_monotonic(chained), strict=True):
        out[i] = line
    for i, line in enumerate(out):
        if not line.unaligned:
            continue
        after = next((later.start_ms for later in out[i + 1 :] if not later.unaligned), None)
        before = out[i - 1] if i > 0 else None
        start, kind = starts[i], "stamp" if refs[i] is not None else "stampless"
        if before is not None and before.unaligned:
            if start < before.start_ms:
                start, kind = before.start_ms, "previous" if kind == "stamp" else kind
        elif before is not None:
            if kind == "stampless" or start <= before.start_ms:
                start, kind = max(start, before.end_ms), "previous" if kind == "stamp" else kind
        if after is not None and start > after:
            start, kind = after, "next"
        if kind == "stamp" and before is not None and not before.unaligned:
            if before.end_ms > start:
                out[i - 1] = replace(before, end_ms=start)
        out[i] = replace(line, start_ms=start, end_ms=start, placed=kind)
    return out


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
    if any(line.unaligned for line in result.lines):
        clamped = _degraded_with_markers(result, refs, starts)
    else:
        lines: list[LineTiming] = []
        for i, line in enumerate(result.lines):
            if i + 1 < len(result.lines):
                end = starts[i + 1]
            else:
                end = starts[i] + max(0, line.end_ms - line.start_ms)
            lines.append(replace(line, start_ms=starts[i], end_ms=max(end, starts[i])))
        clamped = _clamp_monotonic(lines)
    # A line the aligner was not given is placed like every line here, but judged by nothing.
    referenced = sum(
        ref is not None and not line.unaligned for line, ref in zip(result.lines, refs, strict=True)
    )
    agreement = round(1 - len(set(flagged)) / referenced, 4) if referenced else result.quality_score
    return LineQAOutcome(
        result=AlignResult(
            sync="line",
            lines=clamped,
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
    # A line the aligner was not given (`unaligned`) has no aligner time to judge: it counts in
    # no median, flag or agreement, and is placed on its stamp (`_place_unaligned`).
    if refs is None:
        return _finish_unreferenced(result)
    judged = [None if line.unaligned else ref for line, ref in zip(result.lines, refs, strict=True)]
    if sum(ref is not None for ref in judged) < MIN_REFERENCE_LINES:
        return _finish_unreferenced(result)

    deviations = [
        line.start_ms - ref
        for line, ref in zip(result.lines, judged, strict=True)
        if ref is not None
    ]
    offset_ms = round(median(deviations))
    # Lines the aligner was not given are placed last (`_settle`), after the ad-lib shifts and
    # the snaps, and no sung line's decision reads them (`_snap_end` passes over them).
    result, adlib_shifted = _shift_adlibs(result, refs, offset_ms)
    flagged = [
        i
        for i, (line, ref) in enumerate(zip(result.lines, judged, strict=True))
        if ref is not None and abs(line.start_ms - ref - offset_ms) > DRIFT_THRESHOLD_MS
    ]

    referenced = sum(ref is not None for ref in judged)
    if result.sync == "line" or len(flagged) > MAX_FLAGGED_FRACTION * referenced:
        return replace(
            _degrade_to_line(result, refs, flagged, offset_ms, deviations),
            adlib_shifted=adlib_shifted,
        )

    if not flagged:
        return _finish_clean(result, refs, offset_ms, adlib_shifted)

    flagged_set = set(flagged)
    lines = _snap_flagged_lines(result, refs, flagged_set, offset_ms)
    words_per_line, uncertain = _rescue_or_drop_words(result, lines, flagged_set, onset_ms)

    _log_snaps(result, lines, flagged)

    surviving_probs = [w.prob for chunk in words_per_line for w in chunk]
    if not surviving_probs:  # arbiter drops emptied every word-bearing line
        return _degrade_to_line(result, refs, flagged, offset_ms, deviations)

    score, basis = _quality(result, refs, flagged_set, surviving_probs)
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
    lines = _settle(result.lines, result.lines, None, 0, words=result.words_per_line)
    clamped = replace(result, lines=lines)
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
    lines = _settle(result.lines, result.lines, refs, offset_ms, words=result.words_per_line)
    clean = replace(result, lines=lines)
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
    return _settle(lines, result.lines, refs, offset_ms, flagged_set, result.words_per_line)


def _rescue_or_drop_words(
    result: AlignResult,
    lines: list[LineTiming],
    flagged_set: set[int],
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


def _log_snaps(result: AlignResult, lines: list[LineTiming], flagged: list[int]) -> None:
    for i in flagged:
        logger.info(
            "line QA snap: line %d %r start %dms -> %dms (score %.3f)",
            i,
            result.lines[i].text[:40],
            result.lines[i].start_ms,
            lines[i].start_ms,
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
    referenced = [
        i for i, ref in enumerate(refs) if ref is not None and not result.lines[i].unaligned
    ]
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
    stays on the aligner's clock (ref + offset) like every other snap.

    The block never passes a neighbour (2.34.0): its end stops at the next
    line's start, and the snap is refused when the start would not fall
    between the previous line's start and the next line's, or when a hook of
    several words would be left too little span to spread them (`_snap_end`)."""
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
        end = _snap_end(
            lines, i, start, max(start, line.end_ms + delta), len(_line_words(words, i))
        )
        if end is None:
            logger.info(
                "line QA adlib shift refused: line %d %r %+dms would pass a neighbour",
                i,
                line.text[:40],
                delta,
            )
            continue
        lines[i] = replace(line, start_ms=start, end_ms=end)
        if i < len(words) and words[i]:
            # Word ends stop where the line does. A hook of two or more words
            # is re-derived over the line's span at the end of QA anyway
            # (`_snap_end` guarantees it the span); this cut holds a single word.
            words[i] = [
                replace(
                    w,
                    start_ms=max(0, w.start_ms + delta),
                    end_ms=min(end, max(0, w.start_ms + delta, w.end_ms + delta)),
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


def _snap_end(lines: list[LineTiming], i: int, start: int, end: int, n_words: int) -> int | None:
    """Where ad-lib line ``i`` (``n_words`` words), snapped to ``start``, may
    end; None = refuse the snap (the line keeps the aligner's time). Pure.

    The block shift keeps the aligner's span, and a hook the aligner held up
    to the next line ran PAST it once moved later: the 2.31.0 archive has 18
    such pairs in 12 of 307 documents (largest 1.19 s), its words still
    sweeping after the next line began. The anchored START is the evidence
    the snap exists for; the shifted end is not, and the next line's start is
    the bound the ad-lib hold already holds a hook against, so the end stops
    there (an end that only touches it is left as it was). Refused:

    - a start at or before the previous line's, or at or past the next line's:
      the lines would go out of order (and line QA's monotonic clamp would
      then lift the next line off its own words);
    - a hook of ADLIB_REDERIVE_MIN_WORDS or more that the cut would leave less
      than ADLIB_REDERIVE_MIN_SPAN_MS: too short to re-derive, its words would
      pile up at the cut. A single word just becomes short (the one archive
      case, 270 ms of room: the voice witness put the anchored start 151 ms
      from the voice, so refusing it would undo a right snap).

    ``lines`` holds the snaps made so far, so the previous line is where it
    now stands; the next one is still the aligner's."""
    # A line the aligner was not given (`unaligned`, a '♪') is no neighbour here: it is placed
    # after the snaps (`_place_unaligned`), and a sung line is never moved by one.
    before = next((lines[j] for j in range(i - 1, -1, -1) if not lines[j].unaligned), None)
    after = next((lines[j] for j in range(i + 1, len(lines)) if not lines[j].unaligned), None)
    if before is not None and start <= before.start_ms:
        return None
    if after is not None:
        next_start = after.start_ms
        if start >= next_start:
            return None
        if end > next_start:
            if n_words >= ADLIB_REDERIVE_MIN_WORDS and (
                next_start - start < ADLIB_REDERIVE_MIN_SPAN_MS
            ):
                return None
            return next_start
    return end
