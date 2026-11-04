"""Is the lyrics sheet where the singing is? Whole-song anchor checks A1-A3.

The worst failures of the Faz 3 audit came from the lyrics source, not the
aligner: one lrclib record synced 17-19 s late, one verse 3-4 s late, one sheet
for a different mix (``docs/research/perceptual-audit-2026-11.md``). None of
them moves a duration check. This reads a sheet (an lrclib record's stamps, or
the document's own line starts) against the whole-song ear (``audit.ear
--whole`` or ``audit.ext_ear``) and says, per song and per section, how far the
sheet sits from what is heard (``docs/research/yerel-model-denetim-2026-11.md``
section 8):

A1  unique text anchors. Runs of >= MIN_ANCHOR_WORDS consecutive heard words
    (NOISY_MIN_ANCHOR_WORDS on a noisy stem) matching a word n-gram the sheet
    holds exactly ONCE, searched over the whole song: a Smith-Waterman local
    match with no gaps inside an anchor (an anchor is consecutive words), the
    longest run first, each anchor narrowing the search on either side
    (DSAlign's recursion), so anchors stay in order. Lines no anchor reaches
    (repeated refrains above all) are placed afterwards, in order, only
    BETWEEN the placed line before them and the anchored line after them; that
    placement is the narrow letter match of ``audit.textmatch``. Per section the
    median of the line deltas; consecutive sections whose medians agree are
    pooled into a block, and a block's median counts only when enough of its
    lines confirm it (dispersion test) - and a move of more than 5 s only with
    LONG_MOVE_MIN_LINES confirmed lines.
A2  activity correlation: the voice activity the sheet implies against the
    ear's letter mass, over an offset +-30 s x stretch +-5 % grid, accepted at
    NCC >= 0.8. Independent of the text; it confirms A1, and reports a rival
    peak (the refrain period) when one comes close.
A3  coverage / version: per line, the character error against the ear inside
    its corrected window (capped at 1); a song-level 3-gram TF-IDF cosine; the
    share of heard singing that no matched line covers. A different mix leaves
    an unmatched sung block that spells other words AND unmatched sheet lines;
    processed vocals leave unmatched lines but no readable letters.

Delta convention: heard minus sheet, the finder's ``line_offset`` sign. A sheet
stamped late reads negative (GOSSIP -18.9 s). Raw model clock: healthy songs
read +20..+90 ms (the ear's lag against the documents' offset table).

Thresholds are pre-registered: committed before any real run, pinned as
numbers by ``tests/test_audit_anchors.py``.

    python -m audit.anchors <id> --doc D [--lyrics R] [--ear whole.json] [--json out] [--noisy]
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from audit.textmatch import (
    DEFAULT_MS_PER_LETTER,
    MAX_MS_PER_LETTER,
    MIN_LETTERS,
    MIN_MS_PER_LETTER,
    ear_stream,
    letters,
    locate,
    sung_span,
)

Normalize = Callable[[str], str]

# --- pre-registered thresholds (2026-11-04, before any real run) ---------------
# Song flags
SHIFT_MS = 1000                # |block median| above this: the sheet is shifted
LONG_MOVE_MS = 5000            # a block median beyond this ...
LONG_MOVE_MIN_LINES = 5        # ... needs this many confirmed lines [ultrasinger]
COVERAGE_SHARE = 0.25          # Enemy rule: unmatched sung >= 25 % AND sheet lines >= 25 %
# A1 anchors
MIN_ANCHOR_WORDS = 2           # consecutive heard words [hazen06]
NOISY_MIN_ANCHOR_WORDS = 5     # on a noisy stem [demirel21]
MIN_ANCHOR_LETTERS = 8         # "i am" is not evidence
WORD_MATCH_RATIO = 0.75        # two words of >= FUZZY_MIN_LETTERS letters are one heard word
FUZZY_MIN_LETTERS = 4          # shorter words must be spelled exactly
# Sections and the dispersion test
SECTION_GAP_MS = 3000          # no paragraph marks: a silence this long starts a section
AGREE_MS = 1000                # a line confirms a median within this; sections pool within it
MIN_SECTION_LINES = 2          # confirmed lines a block needs to be decided at all
MIN_CONFIRMED_SHARE = 0.6      # ... and the share of its placed lines that confirm
# A2 activity correlation [dali]
A2_RES_MS = 100
A2_MAX_OFFSET_MS = 30_000
A2_STRETCHES = (0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05)
A2_SMOOTH_MS = 500             # the ear's letter mass, box-smoothed
A2_MIN_NCC = 0.8
A2_PERIOD_MIN_MS = 4000        # a rival peak this far from the best ...
A2_AMBIGUITY_NCC = 0.05        # ... and within this of it: the refrain period
# A3 coverage
A3_MIN_LETTERS = 8             # shorter lines match anywhere; not counted
A3_PAD_MS = 2000               # the corrected window around a line
LINE_MATCH_MAX_CER = 0.5       # a line is heard when its capped CER is at most this
SUNG_DILATE_MS = 200           # sung activity: every heard letter +- this
COVER_PAD_MS = 500             # a matched line covers its heard span +- this
BLOCK_JOIN_MS = 1000           # unmatched sung pieces closer than this are one block
BLOCK_MIN_MS = 3000            # ... and a block is at least this long
READABLE_LETTERS_PER_S = 4.0   # a block spells words: this many letters per second ...
READABLE_WORD_SHARE = 0.6      # ... and this share of its heard words look like words
READABLE_BLOCK_SHARE = 0.5     # wrong-version: readable blocks are at least half the block time

VOWELS = frozenset("aeiouy")
# What a song is flagged with; ok and undecidable are not flags.
FLAGS = ("shifted", "wrong-version candidate")


# --- the sheet -----------------------------------------------------------------

@dataclass(frozen=True)
class SheetLine:
    index: int
    text: str
    start_ms: int
    section: int
    ms_per_letter: float       # the rate a mid-line anchor steps back by
    end_ms: int | None = None  # documents only


def _clamp_rate(ms: float) -> float:
    return min(MAX_MS_PER_LETTER, max(MIN_MS_PER_LETTER, ms))


def parse_lrc(synced: str) -> list[tuple[int | None, str, int]]:
    """(start_ms, text, paragraph) per non-empty line: the production parse
    (``lrclib._parse_synced``: every leading stamp stripped, the first wins)
    that also keeps paragraph breaks - empty lines, stamped or not."""
    from kashi_server.pipeline.lrclib import _TIMESTAMP

    out: list[tuple[int | None, str, int]] = []
    paragraph, gap = 0, False
    for raw in synced.splitlines():
        rest, start = raw, None
        while (match := _TIMESTAMP.match(rest)) is not None:
            if start is None:
                mm, ss, frac = match.groups()
                start = int(mm) * 60_000 + int(ss) * 1_000 + (
                    int(frac) if len(frac) == 3 else int(frac) * 10)
            rest = rest[match.end():]
        text = rest.strip()
        if not text:
            gap = gap or bool(out)
            continue
        if gap:
            paragraph, gap = paragraph + 1, False
        out.append((start, text, paragraph))
    return out


def _gap_sections(starts: Sequence[int], ends: Sequence[int]) -> list[int]:
    section, out = 0, []
    for i, start in enumerate(starts):
        if i and start - ends[i - 1] >= SECTION_GAP_MS:
            section += 1
        out.append(section)
    return out


def sheet_from_record(record: dict, normalize: Normalize = letters) -> list[SheetLine]:
    """The record's stamped lines. Paragraphs are its sections; a record without
    any paragraph break is cut at SECTION_GAP_MS silences (the end of a line is
    its start plus its letters at the default rate). Unstamped lines carry no
    time to check and are left out."""
    entries = [(s, t, p) for s, t, p in parse_lrc(record.get("syncedLyrics") or "")
               if s is not None]
    starts = [s for s, _, _ in entries]
    if len({p for _, _, p in entries}) > 1:
        sections = [p for _, _, p in entries]
    else:
        ends = [s + len(normalize(t)) * DEFAULT_MS_PER_LETTER for s, t, _ in entries]
        sections = _gap_sections(starts, ends)
    renumber = {p: i for i, p in enumerate(dict.fromkeys(sections))}
    return [SheetLine(i, t, s, renumber[sec], float(DEFAULT_MS_PER_LETTER))
            for i, ((s, t, _), sec) in enumerate(zip(entries, sections, strict=True))]


def sheet_from_document(doc: dict, normalize: Normalize = letters) -> list[SheetLine]:
    """The document's lines at their first word's start; sections at
    SECTION_GAP_MS silences between a line's end and the next start; the rate
    from the line's own sung span (clamped like ``audit.textmatch``)."""
    lines = [ln for ln in doc.get("lines", []) if (ln.get("text") or "").strip()]
    starts = [(ln.get("words") or [ln])[0]["start_ms"] for ln in lines]
    ends = [ln["end_ms"] for ln in lines]
    sections = _gap_sections(starts, ends)
    out = []
    for i, (ln, start, sec) in enumerate(zip(lines, starts, sections, strict=True)):
        n = len(normalize(ln["text"]))
        span = sung_span(ln)
        rate = _clamp_rate(span / n) if n and span > 0 else float(DEFAULT_MS_PER_LETTER)
        out.append(SheetLine(i, ln["text"], start, sec, rate, ln["end_ms"]))
    return out


# --- A1 unique text anchors --------------------------------------------------------

@dataclass(frozen=True)
class HeardWord:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class SheetWord:
    text: str
    line: int
    letters_before: int  # letters of the line before this word


@dataclass(frozen=True)
class Anchor:
    ear_start: int    # index into the heard words
    sheet_start: int  # index into the sheet words
    words: int
    heard_ms: int


@dataclass(frozen=True)
class Placement:
    line: int
    heard_ms: int
    delta_ms: int     # heard minus sheet
    method: str       # "anchor" | "between"


def heard_words(tokens: Sequence[dict], normalize: Normalize = letters) -> list[HeardWord]:
    """The ear's own words: letter runs between word delimiters."""
    out: list[HeardWord] = []
    run: list[dict] = []
    for tok in [*tokens, {"text": "|", "start_ms": 0, "end_ms": 0}]:
        if tok["text"] == "|" or not tok["text"].strip():
            text = normalize("".join(t["text"] for t in run))
            if text:
                out.append(HeardWord(text, run[0]["start_ms"], run[-1]["end_ms"]))
            run = []
        else:
            run.append(tok)
    return out


def sheet_words(sheet: Sequence[SheetLine], normalize: Normalize = letters) -> list[SheetWord]:
    out: list[SheetWord] = []
    for ln in sheet:
        before = 0
        for raw in ln.text.split():
            text = normalize(raw)
            if text:
                out.append(SheetWord(text, ln.index, before))
                before += len(text)
    return out


@lru_cache(maxsize=200_000)
def same_word(a: str, b: str) -> bool:
    """One heard word: spelled the same, or (both >= FUZZY_MIN_LETTERS letters)
    alike by at least WORD_MATCH_RATIO."""
    if a == b:
        return True
    if min(len(a), len(b)) < FUZZY_MIN_LETTERS:
        return False
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= WORD_MATCH_RATIO


class _Matcher:
    """Which sheet words each heard word matches, both ways."""

    def __init__(self, heard: Sequence[HeardWord], words: Sequence[SheetWord]):
        self.heard, self.words = heard, words
        by_text: dict[str, list[int]] = {}
        for j, w in enumerate(words):
            by_text.setdefault(w.text, []).append(j)
        self.ear_to_sheet: list[list[int]] = []
        self.sheet_to_ear: list[list[int]] = [[] for _ in words]
        for i, hw in enumerate(heard):
            js = sorted(j for text, idx in by_text.items() if same_word(hw.text, text)
                        for j in idx)
            self.ear_to_sheet.append(js)
            for j in js:
                self.sheet_to_ear[j].append(i)
        self._sets = [set(js) for js in self.ear_to_sheet]

    def match(self, i: int, j: int) -> bool:
        return j in self._sets[i]

    def runs(self, ea: int, eb: int, sa: int, sb: int) -> list[tuple[int, int, int]]:
        """Maximal gapless runs (ear start, sheet start, length) in the range:
        the Smith-Waterman local match with indels forbidden."""
        out: list[tuple[int, int, int]] = []
        prev: dict[int, int] = {}
        for i in range(ea, eb + 1):
            cur: dict[int, int] = {}
            if i < eb:
                for j in self.ear_to_sheet[i]:
                    if sa <= j < sb:
                        cur[j] = prev.get(j - 1, 0) + 1
            for j, n in prev.items():
                if j + 1 not in cur:
                    out.append((i - n, j - n + 1, n))
            prev = cur
        return out

    def window_matches(self, i: int, j: int, n: int) -> bool:
        return all(self.match(i + k, j + k) for k in range(n))


def _valid(m: _Matcher, i0: int, j0: int, n: int, ea: int, eb: int, min_words: int) -> bool:
    if n < min_words:
        return False
    if sum(len(m.words[j0 + k].text) for k in range(n)) < MIN_ANCHOR_LETTERS:
        return False
    # The sheet holds the phrase once: no other sheet place fits the heard run
    # (an exact repeat fits it, so do near-spellings of it); and the phrase fits
    # no other heard place in this range. A unique anchor is unique on both sides.
    if any(j != j0 and j + n <= len(m.words) and m.window_matches(i0, j, n)
           for j in m.ear_to_sheet[i0]):
        return False
    return not any(i != i0 and ea <= i and i + n <= eb and m.window_matches(i, j0, n)
                   for i in m.sheet_to_ear[j0])


def find_anchors(heard: Sequence[HeardWord], words: Sequence[SheetWord],
                 min_words: int = MIN_ANCHOR_WORDS) -> list[Anchor]:
    """Unique anchors over the whole song, longest first, each one splitting the
    search into the ranges on its two sides (so they stay in order)."""
    m = _Matcher(heard, words)
    out: list[Anchor] = []
    stack = [(0, len(heard), 0, len(words))]
    while stack:
        ea, eb, sa, sb = stack.pop()
        if min(eb - ea, sb - sa) < min_words:
            continue
        runs = sorted(m.runs(ea, eb, sa, sb), key=lambda r: (-r[2], r[0], r[1]))
        best = next((r for r in runs if _valid(m, r[0], r[1], r[2], ea, eb, min_words)), None)
        if best is None:
            continue
        i0, j0, n = best
        out.append(Anchor(i0, j0, n, heard[i0].start_ms))
        stack.append((ea, i0, sa, j0))
        stack.append((i0 + n, eb, j0 + n, sb))
    return sorted(out, key=lambda a: a.sheet_start)


def anchor_placements(sheet: Sequence[SheetLine], heard: Sequence[HeardWord],
                      words: Sequence[SheetWord],
                      anchors: Sequence[Anchor]) -> dict[int, Placement]:
    """Per line its first anchored word, stepped back to the line's start by the
    letters before it: at the singer's own rate when the anchor holds two or
    more of the line's words, else at the line's rate."""
    pairs: dict[int, list[tuple[SheetWord, HeardWord]]] = {}
    for a in anchors:
        by_line: dict[int, list[tuple[SheetWord, HeardWord]]] = {}
        for k in range(a.words):
            sw = words[a.sheet_start + k]
            by_line.setdefault(sw.line, []).append((sw, heard[a.ear_start + k]))
        for line, got in by_line.items():
            if line not in pairs or got[0][0].letters_before < pairs[line][0][0].letters_before:
                pairs[line] = got
    out: dict[int, Placement] = {}
    for line, got in pairs.items():
        (first_sw, first_hw), (last_sw, last_hw) = got[0], got[-1]
        rate = sheet[line].ms_per_letter
        if len(got) >= 2 and last_sw.letters_before > first_sw.letters_before:
            rate = _clamp_rate((last_hw.start_ms - first_hw.start_ms)
                               / (last_sw.letters_before - first_sw.letters_before))
        heard_ms = first_hw.start_ms - round(first_sw.letters_before * rate)
        out[line] = Placement(line, heard_ms, heard_ms - sheet[line].start_ms, "anchor")
    return out


def between_placements(sheet: Sequence[SheetLine], anchored: dict[int, Placement],
                       stream: str, times: Sequence[int], ear_end_ms: int,
                       normalize: Normalize = letters) -> dict[int, Placement]:
    """Lines no anchor reached, in sheet order, each searched only between the
    line placed before it and the next anchored line (the narrow letter match
    of ``audit.textmatch``). A refrain sung four times finds its own one."""
    out: dict[int, Placement] = {}
    anchored_lines = sorted(anchored)
    prev_heard: int | None = None
    for ln in sheet:
        if ln.index in anchored:
            prev_heard = anchored[ln.index].heard_ms
            continue
        k = bisect.bisect_right(anchored_lines, ln.index)
        lo = prev_heard + 1 if prev_heard is not None else 0
        hi = anchored[anchored_lines[k]].heard_ms - 1 if k < len(anchored_lines) else ear_end_ms
        n = len(normalize(ln.text))
        if hi <= lo or n < MIN_LETTERS:
            continue
        half = (hi - lo) // 2
        match = locate(ln.text, lo + half, stream, times, search_ms=half,
                       line_ms=round(ln.ms_per_letter * n), normalize=normalize)
        if match is None:
            continue
        out[ln.index] = Placement(ln.index, match.heard_ms, match.heard_ms - ln.start_ms,
                                  "between")
        prev_heard = match.heard_ms
    return out


# --- sections, blocks, verdicts ----------------------------------------------------

@dataclass
class Block:
    sections: list[int]
    deltas: list[int] = field(default_factory=list)

    @property
    def median_ms(self) -> int:
        return round(statistics.median(self.deltas))

    @property
    def confirmed(self) -> int:
        m = statistics.median(self.deltas)
        return sum(1 for d in self.deltas if abs(d - m) <= AGREE_MS)

    @property
    def verdict(self) -> str:
        return block_verdict(self.deltas)


def agrees(deltas: Sequence[int]) -> bool:
    """The dispersion test: at least MIN_SECTION_LINES lines, and at least
    MIN_CONFIRMED_SHARE of them, within AGREE_MS of the median."""
    if not deltas:
        return False
    median = statistics.median(deltas)
    confirmed = sum(1 for d in deltas if abs(d - median) <= AGREE_MS)
    return confirmed >= MIN_SECTION_LINES and confirmed >= MIN_CONFIRMED_SHARE * len(deltas)


def block_verdict(deltas: Sequence[int]) -> str:
    """ok / shifted when the block's lines agree on its median; else
    undecidable (too few, too dispersed, or a long move without
    LONG_MOVE_MIN_LINES confirmed lines)."""
    if not agrees(deltas):
        return "undecidable"
    median = statistics.median(deltas)
    confirmed = sum(1 for d in deltas if abs(d - median) <= AGREE_MS)
    if abs(median) > LONG_MOVE_MS and confirmed < LONG_MOVE_MIN_LINES:
        return "undecidable"
    return "shifted" if abs(median) > SHIFT_MS else "ok"


def pool_sections(sheet: Sequence[SheetLine], placed: dict[int, Placement]) -> list[Block]:
    """Consecutive sections whose medians lie within AGREE_MS of the block's
    running median form one block (a verse and a chorus shifted alike are one
    shift); sections without a placed line join nothing and break nothing."""
    per_section: dict[int, list[int]] = {}
    for ln in sheet:
        if ln.index in placed:
            per_section.setdefault(ln.section, []).append(placed[ln.index].delta_ms)
    blocks: list[Block] = []
    for section in sorted(per_section):
        deltas = per_section[section]
        if blocks and abs(statistics.median(deltas) - statistics.median(blocks[-1].deltas)) \
                <= AGREE_MS:
            blocks[-1].sections.append(section)
            blocks[-1].deltas.extend(deltas)
        else:
            blocks.append(Block([section], list(deltas)))
    return blocks


# --- A2 activity correlation -------------------------------------------------------

def _line_spans(sheet: Sequence[SheetLine], normalize: Normalize) -> list[tuple[int, int]]:
    spans = []
    for i, ln in enumerate(sheet):
        length = len(normalize(ln.text)) * ln.ms_per_letter
        if ln.end_ms is not None:
            length = ln.end_ms - ln.start_ms
        if i + 1 < len(sheet):
            length = min(length, sheet[i + 1].start_ms - ln.start_ms)
        spans.append((ln.start_ms, ln.start_ms + max(0, round(length))))
    return spans


def activity_correlation(sheet: Sequence[SheetLine], ear: dict,
                         normalize: Normalize = letters) -> dict:
    """Best NCC of the sheet's implied activity against the ear's letter mass,
    over the offset x stretch grid. ``offset_ms`` maps sheet time 0;
    ``mid_offset_ms`` is the shift at the sheet's median line (what A1 reads)."""
    import numpy as np

    probs = np.asarray(ear.get("letter_prob") or [], dtype=float)
    stride, t0 = float(ear["stride_ms"]), float(ear.get("start_ms", 0))
    if not len(probs) or not sheet:
        return {"accepted": False, "ncc": None}
    res, pad = A2_RES_MS, A2_MAX_OFFSET_MS // A2_RES_MS
    n = math.ceil((t0 + len(probs) * stride) / res)
    bins = ((t0 + np.arange(len(probs)) * stride) // res).astype(int)
    sums = np.bincount(bins, weights=probs, minlength=n)[:n]
    counts = np.bincount(bins, minlength=n)[:n]
    obs = np.divide(sums, counts, out=np.zeros(n), where=counts > 0)
    k = max(1, round(A2_SMOOTH_MS / res))
    obs = np.convolve(obs, np.ones(k) / k, mode="same")
    obs = obs - obs.mean()
    obs_norm = float(np.sqrt((obs * obs).sum()))
    spans = _line_spans(sheet, normalize)
    mid_sheet = statistics.median(ln.start_ms for ln in sheet)
    best: tuple[float, float, int] | None = None   # (ncc, stretch, offset bins)
    best_row: np.ndarray | None = None
    offsets = np.arange(-pad, pad + 1)
    for stretch in A2_STRETCHES:
        expected = np.zeros(n + 2 * pad)
        for s, e in spans:
            a, b = round(s * stretch / res) + pad, round(e * stretch / res) + pad
            expected[max(0, a):max(0, min(len(expected), b))] = 1.0
        windows = np.lib.stride_tricks.sliding_window_view(expected, n)[pad - offsets]
        centred = windows - windows.mean(axis=1, keepdims=True)
        norms = np.sqrt((centred * centred).sum(axis=1)) * obs_norm
        ncc = np.divide(centred @ obs, norms, out=np.zeros(len(offsets)), where=norms > 0)
        i = int(np.argmax(ncc))
        if best is None or ncc[i] > best[0] + 1e-12:
            best, best_row = (float(ncc[i]), stretch, int(offsets[i])), ncc
    assert best is not None and best_row is not None
    ncc_best, stretch, off = best
    far = np.abs(offsets - off) * res >= A2_PERIOD_MIN_MS
    rival_i = int(np.argmax(np.where(far, best_row, -np.inf)))
    rival = float(best_row[rival_i])
    offset_ms = off * res
    return {
        "accepted": a2_accepts(ncc_best),
        "ncc": round(ncc_best, 3),
        "stretch": stretch,
        "offset_ms": offset_ms,
        "mid_offset_ms": round((stretch - 1) * mid_sheet + offset_ms),
        "rival_ncc": round(rival, 3),
        "rival_offset_ms": int(offsets[rival_i]) * res,
        "ambiguous": bool(rival >= ncc_best - A2_AMBIGUITY_NCC),
    }


def a2_accepts(ncc: float) -> bool:
    return ncc >= A2_MIN_NCC


# --- A3 coverage / version ---------------------------------------------------------

def semiglobal_cer(target: str, window: str) -> tuple[float, int, int]:
    """(capped CER, first, last index into ``window``) of the best placement of
    the whole ``target`` anywhere inside ``window`` (free start and end)."""
    n = len(target)
    if not n:
        return 1.0, -1, -1
    if not window:
        return 1.0, -1, -1
    prev = [0] * (len(window) + 1)
    prev_start = list(range(len(window) + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * len(window)
        cur_start = [0] * (len(window) + 1)
        for j in range(1, len(window) + 1):
            sub = prev[j - 1] + (target[i - 1] != window[j - 1])
            dele = prev[j] + 1
            ins = cur[j - 1] + 1
            if sub <= dele and sub <= ins:
                cur[j], cur_start[j] = sub, prev_start[j - 1] if i > 1 else j - 1
            elif dele <= ins:
                cur[j], cur_start[j] = dele, prev_start[j] if i > 1 else j
            else:
                cur[j], cur_start[j] = ins, cur_start[j - 1]
        prev, prev_start = cur, cur_start
    j_best = min(range(1, len(window) + 1), key=lambda j: (prev[j], j))
    return min(1.0, prev[j_best] / n), prev_start[j_best], j_best - 1


def _merge(spans: list[tuple[int, int]], join_ms: int = 0) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1] + join_ms:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _subtract(spans: list[tuple[int, int]], cover: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = []
    for s, e in spans:
        cur = s
        for cs, ce in cover:
            if ce <= cur or cs >= e:
                continue
            if cs > cur:
                out.append((cur, cs))
            cur = max(cur, ce)
        if cur < e:
            out.append((cur, e))
    return out


def _length(spans: list[tuple[int, int]]) -> int:
    return sum(e - s for s, e in spans)


def tfidf_cosine(sheet: Sequence[SheetLine], ear_text: str,
                 normalize: Normalize = letters) -> float:
    """3-gram TF-IDF cosine of the whole sheet against the ear's letters; the
    IDF counts the sheet's lines as documents (a gram every line has weighs
    least) [vaglio21]."""
    lines = [normalize(ln.text) for ln in sheet]
    grams = [Counter(s[i:i + 3] for i in range(len(s) - 2)) for s in lines]
    df = Counter(g for c in grams for g in c)
    big_n = len(lines)

    def idf(g: str) -> float:
        return math.log((1 + big_n) / (1 + df.get(g, 0))) + 1

    a = Counter()
    for c in grams:
        a.update(c)
    ear = normalize(ear_text)
    b = Counter(ear[i:i + 3] for i in range(len(ear) - 2))
    va = {g: tf * idf(g) for g, tf in a.items()}
    vb = {g: tf * idf(g) for g, tf in b.items()}
    dot = sum(v * vb.get(g, 0.0) for g, v in va.items())
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    return round(dot / (na * nb), 3) if na and nb else 0.0


def block_readable(tokens: Sequence[dict], start: int, end: int,
                   normalize: Normalize = letters) -> dict:
    """Does the ear spell words inside [start, end)? Letters per second and the
    share of its words with two or more letters and a vowel."""
    inside = [t for t in tokens if start <= t["start_ms"] < end]
    hw = heard_words(inside, normalize)
    n_letters = sum(len(w.text) for w in hw)
    rate = n_letters / max(1e-9, (end - start) / 1000)
    wordlike = sum(1 for w in hw if len(w.text) >= 2 and VOWELS & set(w.text))
    share = wordlike / len(hw) if hw else 0.0
    return {"start_ms": start, "end_ms": end, "letters_per_s": round(rate, 2),
            "word_share": round(share, 2),
            "readable": rate >= READABLE_LETTERS_PER_S and share >= READABLE_WORD_SHARE}


def coverage_rule(unmatched_sung_ms: int, sung_ms: int, unmatched_lines: int,
                  counted_lines: int) -> bool:
    """The pre-registered Enemy rule: unmatched sung duration >= COVERAGE_SHARE
    of the sung duration AND >= COVERAGE_SHARE of the counted lines unmatched."""
    if sung_ms <= 0 or counted_lines <= 0:
        return False
    return (unmatched_sung_ms >= COVERAGE_SHARE * sung_ms
            and unmatched_lines >= COVERAGE_SHARE * counted_lines)


def coverage(sheet: Sequence[SheetLine], ear: dict, offset_of: Callable[[int], int],
             normalize: Normalize = letters) -> dict:
    tokens = [t for t in ear["tokens"] if t["text"] != "|" and t["text"].strip()]
    stream, times = ear_stream(tokens, normalize=normalize)
    spans = _line_spans(sheet, normalize)
    rows, cover = [], []
    for ln, (s, e) in zip(sheet, spans, strict=True):
        target = normalize(ln.text)
        if len(target) < A3_MIN_LETTERS:
            continue
        off = offset_of(ln.index)
        lo, hi = s + off - A3_PAD_MS, e + off + A3_PAD_MS
        i0, i1 = bisect.bisect_left(times, lo), bisect.bisect_right(times, hi)
        cer, a, b = semiglobal_cer(target, stream[i0:i1])
        matched = cer <= LINE_MATCH_MAX_CER
        if matched and a >= 0:
            cover.append((times[i0 + a] - COVER_PAD_MS, times[i0 + b] + COVER_PAD_MS))
        rows.append({"line": ln.index, "cer": round(cer, 3), "matched": matched})
    sung = _merge([(t["start_ms"] - SUNG_DILATE_MS, t["end_ms"] + SUNG_DILATE_MS)
                   for t in tokens if normalize(t["text"])])
    sung = [(max(0, s), e) for s, e in sung]
    uncovered = _subtract(sung, _merge(cover))
    blocks = [(s, e) for s, e in _merge(uncovered, BLOCK_JOIN_MS) if e - s >= BLOCK_MIN_MS]
    readable = [block_readable(tokens, s, e, normalize) for s, e in blocks]
    sung_ms, unmatched_ms = _length(sung), _length(uncovered)
    unmatched_lines = sum(1 for r in rows if not r["matched"])
    rule = coverage_rule(unmatched_ms, sung_ms, unmatched_lines, len(rows))
    block_ms = sum(r["end_ms"] - r["start_ms"] for r in readable)
    readable_ms = sum(r["end_ms"] - r["start_ms"] for r in readable if r["readable"])
    return {
        "counted_lines": len(rows),
        "matched_lines": len(rows) - unmatched_lines,
        "sung_ms": sung_ms,
        "unmatched_sung_ms": unmatched_ms,
        "unmatched_sung_share": round(unmatched_ms / sung_ms, 3) if sung_ms else None,
        "unmatched_line_share": round(unmatched_lines / len(rows), 3) if rows else None,
        "tfidf_cosine": tfidf_cosine(sheet, "".join(t["text"] for t in tokens), normalize),
        "coverage_rule": rule,
        "blocks": readable,
        "wrong_version": bool(rule and block_ms and readable_ms >= READABLE_BLOCK_SHARE * block_ms),
        "lines": rows,
    }


# --- the song ------------------------------------------------------------------

def check(sheet: Sequence[SheetLine], ear: dict, min_words: int = MIN_ANCHOR_WORDS,
          normalize: Normalize = letters) -> dict:
    """A1-A3 for one sheet against one whole-song ear."""
    heard = heard_words(ear["tokens"], normalize)
    words = sheet_words(sheet, normalize)
    anchors = find_anchors(heard, words, min_words)
    anchored = anchor_placements(sheet, heard, words, anchors)
    stream, times = ear_stream(ear["tokens"], normalize=normalize)
    ear_end = round(ear.get("start_ms", 0) + len(ear.get("letter_prob") or []) * ear["stride_ms"])
    ear_end = max(ear_end, times[-1] if times else 0)
    placed = {**anchored, **between_placements(sheet, anchored, stream, times, ear_end,
                                                normalize)}
    blocks = pool_sections(sheet, placed)
    all_deltas = [p.delta_ms for p in placed.values()]
    song_median = round(statistics.median(all_deltas)) if all_deltas else None
    section_block = {s: b for b in blocks for s in b.sections}

    def offset_of(line: int) -> int:
        """A3's correction: the block's median when its lines agree (a long
        move without enough lines is not claimed, but it is still where the
        block is heard), else the song's."""
        block = section_block.get(sheet[line].section)
        if block is not None and agrees(block.deltas):
            return block.median_ms
        return song_median or 0

    a2 = activity_correlation(sheet, ear, normalize)
    if a2.get("accepted") and song_median is not None:
        a2["confirms_a1"] = abs(a2["mid_offset_ms"] - song_median) <= AGREE_MS
    a3 = coverage(sheet, ear, offset_of, normalize)
    flags = []
    if any(b.verdict == "shifted" for b in blocks):
        flags.append("shifted")
    if a3["wrong_version"]:
        flags.append("wrong-version candidate")
    if flags:
        flag = flags[0]
    elif any(b.verdict == "ok" for b in blocks) and not a3["coverage_rule"]:
        flag = "ok"
    else:
        flag = "undecidable"
    sections = []
    for sec in sorted({ln.section for ln in sheet}):
        ds = [placed[ln.index].delta_ms for ln in sheet if ln.section == sec and ln.index in placed]
        sections.append({
            "section": sec,
            "lines": sum(1 for ln in sheet if ln.section == sec),
            "placed": len(ds),
            "median_ms": round(statistics.median(ds)) if ds else None,
            "mad_ms": round(statistics.median(abs(d - statistics.median(ds)) for d in ds))
            if ds else None,
        })
    return {
        "flag": flag,
        "flags": flags,
        "a1": {
            "heard_words": len(heard),
            "anchors": [{**asdict(a), "line": words[a.sheet_start].line} for a in anchors],
            "lines": [asdict(placed[k]) | {"section": sheet[k].section,
                                           "sheet_ms": sheet[k].start_ms}
                      for k in sorted(placed)],
            "sections": sections,
            "blocks": [{"sections": b.sections, "placed": len(b.deltas), "confirmed": b.confirmed,
                        "median_ms": b.median_ms, "verdict": b.verdict} for b in blocks],
            "song": {"lines": len(sheet), "placed": len(placed),
                     "anchored": len(anchored), "median_ms": song_median},
        },
        "a2": a2,
        "a3": a3,
    }


def _s(ms: int | None) -> str:
    return "-" if ms is None else f"{ms / 1000:+.1f} s"


def summary_line(video_id: str, result: dict) -> str:
    a1, a2, a3 = result["a1"], result["a2"], result["a3"]
    blocks = ", ".join(f"S{'+'.join(map(str, b['sections']))} {_s(b['median_ms'])} "
                       f"n={b['confirmed']}/{b['placed']} {b['verdict']}" for b in a1["blocks"])
    a2_text = (f"A2 {_s(a2.get('mid_offset_ms'))} NCC {a2['ncc']}"
               f"{'' if a2.get('accepted') else ' (rejected)'}"
               f"{' ambiguous' if a2.get('ambiguous') else ''}") if a2.get("ncc") is not None \
        else "A2 -"
    share = a3["unmatched_sung_share"]
    return (f"{video_id} {result['flag']}: song {_s(a1['song']['median_ms'])} "
            f"({a1['song']['placed']}/{a1['song']['lines']} lines, "
            f"{a1['song']['anchored']} anchored) [{blocks}]; {a2_text}; "
            f"A3 {a3['matched_lines']}/{a3['counted_lines']} lines heard, "
            f"unmatched sung {'-' if share is None else f'{share:.0%}'}")


def main(argv: list[str] | None = None) -> int:
    from audit.docs import load_document
    from audit.ear import whole_path

    parser = argparse.ArgumentParser(description="Is the lyrics sheet where the singing is?")
    parser.add_argument("id")
    parser.add_argument("--doc", required=True, help="document JSON or a [{id, doc}] dump")
    parser.add_argument("--lyrics", help="the lrclib record (/api/get JSON); else the document "
                                         "is the sheet")
    parser.add_argument("--ear", help="whole-song ear file (default ear/<id>/whole.json)")
    parser.add_argument("--json", help="write the full result here")
    parser.add_argument("--noisy", action="store_true",
                        help=f"noisy stem: anchors need {NOISY_MIN_ANCHOR_WORDS} words")
    args = parser.parse_args(argv)
    doc = load_document(Path(args.doc), args.id)
    ear = json.loads(Path(args.ear or whole_path(args.id)).read_text())
    inputs: dict = {"sheet": "document", "ear_model": ear.get("model"),
                    "ear_timing": ear.get("timing", "ctc"),
                    "doc_lyrics_source_id": (doc.get("alignment") or {}).get("lyrics_source_id")}
    if args.lyrics:
        record = json.loads(Path(args.lyrics).read_text())
        sheet = sheet_from_record(record)
        inputs |= {"sheet": "record", "record_id": record.get("id"),
                   "record_matches_doc": record.get("id") == inputs["doc_lyrics_source_id"]}
        if not inputs["record_matches_doc"]:
            print(f"warning: record {record.get('id')} is not the document's lyrics source "
                  f"({inputs['doc_lyrics_source_id']})", file=sys.stderr)
    else:
        sheet = sheet_from_document(doc)
    result = check(sheet, ear, NOISY_MIN_ANCHOR_WORDS if args.noisy else MIN_ANCHOR_WORDS)
    result = {"id": args.id, "inputs": inputs, **result}
    print(summary_line(args.id, result))
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
