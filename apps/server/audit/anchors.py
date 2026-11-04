"""Is the lyrics sheet where the singing is? Whole-song anchor check (Faz 3.5, A1-A3).

The worst Faz 3 failures came from the sheet, not the aligner: one lrclib record
17-19 s late, a verse 3-4 s late, the lyrics of another mix. These checks read a
sheet (line texts with their stamps) against the whole-song ear and need no new
model: the ear is the aligner checkpoint's own greedy letters and letter curve.

  A1  unique text anchors: a sheet line whose letters occur once in the sheet,
      placed unambiguously over the WHOLE song by the ear-text matcher
      (``textmatch.locate``; the +-8 s window stays for word-level placement).
      Consecutive anchors that agree form a section; a section whose median
      offset exceeds SHIFT_FLAG_MS flags a shift.
  A2  activity correlation: the sheet's expected voice activity against the
      ear's P(some letter), over offsets +-30 s and stretches +-5 %. Independent
      of text errors; it confirms A1 and stands in when A1 finds no section.
  A3  coverage: lines heard near their corrected time, singing no line
      explains. The wrong-sheet signature needs both: readable unmatched
      singing AND unmatched sheet lines.

Offsets are heard minus sheet (negative: the sheet is late), on the document
clock (the ear's raw-clock lag, ``score.EAR_LAG_MS``, taken off). Pure: sheets
and ear dicts in, a JSON-ready dict out. Method parameters are pinned by
``tests/test_audit_anchors.py``; the pass/fail gates are in ``audit.gates``.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from audit.gates import PILOT_SHIFT_MS, WRONG_SHEET_UNMATCHED_LINES, WRONG_SHEET_UNMATCHED_SUNG
from audit.score import EAR_LAG_MS
from audit.textmatch import (
    DEFAULT_MS_PER_LETTER,
    MIN_LETTERS,
    MIN_RATIO,
    ear_stream,
    letters,
    locate,
    partial_similarity,
    scan,
)

# A1: a line is an anchor candidate when no other sheet line is this alike
# (partial similarity of the letters; a refrain or a line contained in another
# scores 1.0).
UNIQUE_MAX_SIMILARITY = 0.6
# Consecutive anchors belong to one section while each stays this close to the
# median of the section's last SECTION_RECENT anchors (a slow drift stays one
# section).
SECTION_TOL_MS = 1000
SECTION_RECENT = 3
# A section needs this many agreeing anchors; a lone anchor is never evidence.
MIN_SECTION_ANCHORS = 2
# |section median| above this flags a shift (the Faz 3.5 Step 1 gate: > 1 s).
SHIFT_FLAG_MS = PILOT_SHIFT_MS

# A2: activity on a 100 ms grid; a line is expected to sound for its letters at
# the matcher's default letter rate, never past the next line's stamp.
ACTIVITY_BIN_MS = 100
ACTIVITY_MAX_OFFSET_MS = 30_000
ACTIVITY_STRETCHES = tuple(round(0.95 + 0.01 * k, 2) for k in range(11))
# Accept an activity fit at this normalised cross-correlation (DALI's bar).
ACTIVITY_MIN_NCC = 0.8

# A3: a frame is sung when P(some letter) reaches SUNG_PROB; a matched line
# explains [heard - slack, heard + its expected length + slack].
SUNG_PROB = 0.5
SPAN_SLACK_MS = 500
# Unmatched singing: gaps up to BLOCK_GAP_MS merge, blocks shorter than
# BLOCK_MIN_MS are breaths and ad-libs; a block is readable (it spells words)
# at READABLE_LETTERS_PER_S of the ear's letters.
BLOCK_GAP_MS = 1000
BLOCK_MIN_MS = 3000
READABLE_LETTERS_PER_S = 4.0
# The wrong-sheet signature needs both shares (audit.gates, pre-registered):
# WRONG_SHEET_UNMATCHED_SUNG of the singing and WRONG_SHEET_UNMATCHED_LINES of
# the checked lines unexplained.
# This many consecutive checked lines heard nowhere near their time: a block
# the audio does not sing (an extra paragraph, another edit). Reported only.
UNSUNG_RUN = 3


@dataclass(frozen=True)
class SheetLine:
    text: str
    start_ms: int


@dataclass(frozen=True)
class Anchor:
    line: int
    sheet_ms: int
    heard_ms: int        # on the document clock (ear lag taken off)
    delta_ms: int        # heard - sheet
    ratio: float
    margin: float


@dataclass(frozen=True)
class Section:
    first_line: int
    last_line: int
    anchors: int
    median_ms: int
    mad_ms: int

    @property
    def shifted(self) -> bool:
        return abs(self.median_ms) > SHIFT_FLAG_MS


# --- sheets -------------------------------------------------------------------

def sheet_from_document(doc: dict) -> list[SheetLine]:
    """A document's lines as a sheet: each line's first word start (its line
    start when wordless). Windowed documents follow their stamps, so a shifted
    stamp shows as a shifted line."""
    out = []
    for ln in doc.get("lines", []):
        words = ln.get("words") or []
        out.append(SheetLine(ln.get("text", ""), int(words[0]["start_ms"] if words
                                                     else ln["start_ms"])))
    return out


def sheet_from_record(record: dict) -> list[SheetLine]:
    """An lrclib record's synced lines (lines without a stamp are left out)."""
    from kashi_server.pipeline.lrclib import lyrics_from_record

    lyrics = lyrics_from_record(record)
    starts = lyrics.synced_starts_ms or []
    return [SheetLine(text, start) for text, start in zip(lyrics.line_texts, starts, strict=True)
            if start is not None]


# --- A1 -----------------------------------------------------------------------

def unique_lines(sheet: Sequence[SheetLine]) -> list[int]:
    """Indices of lines long enough to place whose letters no other line shares."""
    texts = [letters(ln.text) for ln in sheet]
    out = []
    for i, mine in enumerate(texts):
        if len(mine) < MIN_LETTERS:
            continue
        if all(partial_similarity(mine, other) < UNIQUE_MAX_SIMILARITY
               for j, other in enumerate(texts) if j != i and other):
            out.append(i)
    return out


def placements(sheet: Sequence[SheetLine], ear: dict,
               candidates: Sequence[int]) -> dict[int, tuple[int, float, float]]:
    """Whole-song placement of each candidate line: index -> (heard ms on the
    ear's raw clock, ratio, margin). Unambiguous ones only (``locate``)."""
    stream, times = ear_stream(ear["tokens"])
    out = {}
    for i in candidates:
        match = locate(sheet[i].text, 0, stream, times, search_ms=10**9)
        if match is not None:
            out[i] = (match.heard_ms, match.ratio, match.margin)
    return out


def anchors_from(sheet: Sequence[SheetLine], placed: dict[int, tuple[int, float, float]]
                 ) -> list[Anchor]:
    out = []
    for i in sorted(placed):
        heard_raw, ratio, margin = placed[i]
        heard = heard_raw - EAR_LAG_MS
        out.append(Anchor(i, sheet[i].start_ms, heard, heard - sheet[i].start_ms, ratio, margin))
    return out


def _agrees(run: list[Anchor], anchor: Anchor) -> bool:
    recent = [a.delta_ms for a in run[-SECTION_RECENT:]]
    return abs(anchor.delta_ms - statistics.median(recent)) <= SECTION_TOL_MS


def sections(anchors: Sequence[Anchor]) -> tuple[list[Section], list[Anchor]]:
    """(sections, anchors left out). A section opens on two consecutive anchors
    that agree; an anchor that disagrees with its section is held back and
    dropped when the next one agrees with the section again (a misplaced line
    does not split a section); two disagreeing anchors that agree with each
    other close the section and open the next."""
    runs: list[list[Anchor]] = []
    run: list[Anchor] = []
    pending: Anchor | None = None
    dropped: list[Anchor] = []
    for a in anchors:
        if run and _agrees(run, a):
            if pending is not None:
                dropped.append(pending)
                pending = None
            run.append(a)
        elif pending is None:
            pending = a
        elif abs(a.delta_ms - pending.delta_ms) <= SECTION_TOL_MS:
            if run:
                runs.append(run)
            run, pending = [pending, a], None
        else:
            dropped.append(pending)
            pending = a
    if run:
        runs.append(run)
    if pending is not None:
        dropped.append(pending)
    out, short = [], []
    for r in runs:
        if len(r) < MIN_SECTION_ANCHORS:
            short += r
            continue
        deltas = [a.delta_ms for a in r]
        med = statistics.median(deltas)
        out.append(Section(r[0].line, r[-1].line, len(r), round(med),
                           round(statistics.median(abs(d - med) for d in deltas))))
    return out, sorted(dropped + short, key=lambda a: a.line)


def theil_sen_drift(anchors: Sequence[Anchor]) -> int | None:
    """Change of the offset over the anchors' span, from the median pairwise
    slope (robust to misplaced lines). None below three anchors."""
    if len(anchors) < 3:
        return None
    slopes = [(b.delta_ms - a.delta_ms) / (b.sheet_ms - a.sheet_ms)
              for k, a in enumerate(anchors) for b in anchors[k + 1:] if b.sheet_ms != a.sheet_ms]
    if not slopes:
        return None
    span = max(a.sheet_ms for a in anchors) - min(a.sheet_ms for a in anchors)
    return round(statistics.median(slopes) * span)


# --- A2 -----------------------------------------------------------------------

def expected_spans(sheet: Sequence[SheetLine], scale: float = 1.0) -> list[tuple[int, int]]:
    """Where each line should sound: its stamp (scaled) for its letters at the
    default letter rate, cut at the next line's stamp."""
    order = sorted(range(len(sheet)), key=lambda i: sheet[i].start_ms)
    spans: list[tuple[int, int]] = [(0, 0)] * len(sheet)
    for k, i in enumerate(order):
        start = round(sheet[i].start_ms * scale)
        length = len(letters(sheet[i].text)) * DEFAULT_MS_PER_LETTER
        if k + 1 < len(order):
            length = min(length, round(sheet[order[k + 1]].start_ms * scale) - start)
        spans[i] = (start, start + max(0, length))
    return spans


def ear_activity(ear: dict, bin_ms: int = ACTIVITY_BIN_MS) -> list[float]:
    """P(some letter) averaged on a ``bin_ms`` grid from 0 ms of the document
    clock (the ear's lag taken off first)."""
    probs, stride = ear["letter_prob"], float(ear["stride_ms"])
    t0 = int(ear.get("start_ms", 0)) - EAR_LAG_MS
    if not probs:
        return []
    n = max(1, math.ceil((t0 + len(probs) * stride) / bin_ms))
    sums, counts = [0.0] * n, [0] * n
    for k, p in enumerate(probs):
        b = int((t0 + k * stride) // bin_ms)
        if b < 0:
            continue
        sums[b] += p
        counts[b] += 1
    return [s / c if c else 0.0 for s, c in zip(sums, counts, strict=True)]


def activity_fit(sheet: Sequence[SheetLine], ear: dict) -> dict:
    """Best (offset, stretch) of the sheet's expected activity against the ear,
    by normalised cross-correlation; offset is heard minus sheet."""
    import numpy as np

    act = np.asarray(ear_activity(ear))
    n = act.size
    max_lag = ACTIVITY_MAX_OFFSET_MS // ACTIVITY_BIN_MS
    if n == 0 or not sheet:
        return {"offset_ms": None, "stretch": None, "ncc": None, "ncc_at_zero": None,
                "confident": False}
    best = (-2.0, 0, 1.0)
    at_zero = None
    a = act - act.mean()
    a_norm = float(np.sqrt((a * a).sum())) or 1.0
    for s in ACTIVITY_STRETCHES:
        exp = np.zeros(n + 2 * max_lag)
        for lo, hi in expected_spans(sheet, s):
            b0, b1 = lo // ACTIVITY_BIN_MS + max_lag, -(-hi // ACTIVITY_BIN_MS) + max_lag
            exp[max(0, b0):max(0, min(exp.size, b1))] = 1.0
        for lag in range(-max_lag, max_lag + 1):
            # the sheet moved by `lag` bins: ear[t] against exp[t - lag]
            window = exp[max_lag - lag: max_lag - lag + n]
            w = window - window.mean()
            w_norm = float(np.sqrt((w * w).sum()))
            ncc = float((a * w).sum()) / (a_norm * w_norm) if w_norm else 0.0
            if s == 1.0 and lag == 0:
                at_zero = ncc
            if ncc > best[0]:
                best = (ncc, lag, s)
    ncc, lag, s = best
    ncc = round(ncc, 3)  # the value reported is the value compared
    return {"offset_ms": lag * ACTIVITY_BIN_MS, "stretch": s,
            "ncc": ncc, "ncc_at_zero": None if at_zero is None else round(at_zero, 3),
            "confident": ncc >= ACTIVITY_MIN_NCC}


# --- A3 -----------------------------------------------------------------------

def corrected_starts(sheet: Sequence[SheetLine], anchors: Sequence[Anchor],
                     secs: Sequence[Section]) -> list[int]:
    """Each line's start on the heard clock: an anchor's own heard time; other
    lines take their nearest section's median (by line index); with no
    section, the sheet's own stamp."""
    by_line = {a.line: a.heard_ms for a in anchors}
    out = []
    for i, ln in enumerate(sheet):
        if i in by_line:
            out.append(by_line[i])
        elif secs:
            near = min(secs, key=lambda s: 0 if s.first_line <= i <= s.last_line
                       else min(abs(i - s.first_line), abs(i - s.last_line)))
            out.append(ln.start_ms + near.median_ms)
        else:
            out.append(ln.start_ms)
    return out


def trigram_cosine(a: str, b: str, idf: dict[str, float] | None = None) -> float:
    """Cosine of letter 3-gram counts (TF-IDF when ``idf`` is given)."""
    def grams(s: str) -> Counter[str]:
        return Counter(s[i:i + 3] for i in range(len(s) - 2))
    ga, gb = grams(letters(a)), grams(letters(b))
    w = idf or {}
    dot = sum(ga[g] * gb[g] * w.get(g, 1.0) ** 2 for g in ga.keys() & gb.keys())
    na = math.sqrt(sum((c * w.get(g, 1.0)) ** 2 for g, c in ga.items()))
    nb = math.sqrt(sum((c * w.get(g, 1.0)) ** 2 for g, c in gb.items()))
    return round(dot / (na * nb), 3) if na and nb else 0.0


def coverage(sheet: Sequence[SheetLine], ear: dict, starts: Sequence[int]) -> dict:
    """Lines heard near their corrected start, and singing no line explains."""
    stream, times = ear_stream(ear["tokens"])
    checked = matched = 0
    errors: list[float] = []
    spans: list[tuple[int, int]] = []
    unmatched_lines: list[int] = []
    for i, ln in enumerate(sheet):
        n = len(letters(ln.text))
        if n < MIN_LETTERS:
            continue
        checked += 1
        # Heard near its corrected start at all (no margin test: a refrain line
        # sung twice within the window is still heard). The ear's raw clock
        # carries the lag, so it goes back on for the search.
        around = starts[i] + EAR_LAG_MS
        scored = scan(ln.text, around, stream, times)
        # equally good placements (a refrain sung twice): the nearest one
        best_ratio, best_t = max(scored, key=lambda rt: (rt[0], -abs(rt[1] - around))) \
            if scored else (0.0, 0)
        errors.append(1.0 - best_ratio)
        if best_ratio < MIN_RATIO:
            unmatched_lines.append(i)
            continue
        matched += 1
        heard = best_t - EAR_LAG_MS
        spans.append((heard - SPAN_SLACK_MS, heard + n * DEFAULT_MS_PER_LETTER + SPAN_SLACK_MS))
    probs, stride, t0 = ear["letter_prob"], float(ear["stride_ms"]), int(ear.get("start_ms", 0))
    spans.sort()
    sung_frames = unmatched_frames = 0
    unmatched_times: list[int] = []
    for k, p in enumerate(probs):
        if p < SUNG_PROB:
            continue
        t = round(t0 + k * stride) - EAR_LAG_MS
        sung_frames += 1
        if not any(lo <= t <= hi for lo, hi in spans):
            unmatched_frames += 1
            unmatched_times.append(t)
    blocks = unmatched_blocks(unmatched_times, stride, ear)
    unmatched_set, longest, run = set(unmatched_lines), 0, 0
    for i, ln in enumerate(sheet):
        if len(letters(ln.text)) >= MIN_LETTERS:  # the checked lines, in sheet order
            run = run + 1 if i in unmatched_set else 0
            longest = max(longest, run)
    return {
        "lines_checked": checked,
        "longest_unmatched_run": longest,
        "lines_matched": matched,
        "unmatched_lines": unmatched_lines,
        "unmatched_lines_share": round(len(unmatched_lines) / checked, 3) if checked else 0.0,
        "sung_ms": round(sung_frames * stride),
        "unmatched_sung_share": round(unmatched_frames / sung_frames, 3) if sung_frames else 0.0,
        "blocks": blocks,
        "readable_blocks": sum(1 for b in blocks if b["readable"]),
        "line_error_mean": round(statistics.mean(errors), 3) if errors else None,
    }


def unmatched_blocks(times: Sequence[int], stride: float, ear: dict) -> list[dict]:
    """Runs of unmatched sung frames (gaps <= BLOCK_GAP_MS merge), >= BLOCK_MIN_MS
    long, with the ear's letter rate inside (raw-clock tokens shifted by the lag)."""
    groups: list[list[int]] = []
    for t in times:
        if groups and t - groups[-1][-1] <= BLOCK_GAP_MS:
            groups[-1].append(t)
        else:
            groups.append([t])
    out = []
    letter_times = [tok["start_ms"] - EAR_LAG_MS for tok in ear["tokens"]
                    if letters(tok["text"])]
    for g in groups:
        lo, hi = g[0], round(g[-1] + stride)
        if hi - lo < BLOCK_MIN_MS:
            continue
        n = sum(1 for t in letter_times if lo <= t <= hi)
        rate = n / ((hi - lo) / 1000)
        out.append({"start_ms": lo, "end_ms": hi, "letters": n,
                    "letters_per_s": round(rate, 2), "readable": rate >= READABLE_LETTERS_PER_S})
    return out


# --- the song -----------------------------------------------------------------

def check(sheet: Sequence[SheetLine], ear: dict,
          placed: dict[int, tuple[int, float, float]] | None = None) -> dict:
    """A1-A3 for one sheet. ``placed`` reuses whole-song placements by line
    index (they depend on the line texts, not on the stamps)."""
    candidates = unique_lines(sheet)
    if placed is None:
        placed = placements(sheet, ear, candidates)
    anchors = anchors_from(sheet, placed)
    secs, left_out = sections(anchors)
    activity = activity_fit(sheet, ear)
    kept = [a for a in anchors if a not in left_out]
    cov = coverage(sheet, ear, corrected_starts(sheet, kept, secs))
    shifted = [s for s in secs if s.shifted]
    flags: list[str] = []
    shift_kind = None
    if shifted:
        flags.append("shift")
        shift_kind = "global" if len(shifted) == len(secs) else "section"
    elif not secs and activity["confident"] and abs(activity["offset_ms"]) > SHIFT_FLAG_MS:
        flags.append("shift")
        shift_kind = "activity"
    readable = cov["readable_blocks"] > 0
    if (readable and cov["unmatched_sung_share"] >= WRONG_SHEET_UNMATCHED_SUNG
            and cov["unmatched_lines_share"] >= WRONG_SHEET_UNMATCHED_LINES):
        flags.append("wrong_sheet")
    elif readable:
        flags.append("unsheeted_singing")  # reported, never an alarm
    if cov["longest_unmatched_run"] >= UNSUNG_RUN:
        flags.append("unsung_lines")  # reported, never an alarm
    return {
        "lines": len(sheet),
        "anchor_candidates": len(candidates),
        "anchors": [asdict(a) for a in anchors],
        "left_out": [a.line for a in left_out],
        "sections": [asdict(s) | {"shifted": s.shifted} for s in secs],
        "drift_ms": theil_sen_drift(kept),
        "activity": activity,
        "coverage": cov,
        "shift_kind": shift_kind,
        "flags": flags,
    }


ALARMS = frozenset({"shift", "wrong_sheet"})
