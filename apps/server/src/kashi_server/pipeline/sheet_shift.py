"""Is the lyrics sheet where the singing is? Move a shifted section before alignment (2.29.1).

The worst failures of the 2026-11 perceptual audit came from lrclib, not from
the aligner: one record 17-19 s late, one 4-5 s late. Windows are cut around
the stamps and line QA trusts them, so a shifted sheet is aligned against the
wrong audio and then snapped back to the wrong time.

This is the anchor check's A1 (``apps/server/audit/anchors.py``, Faz 3.5 Step 1),
copied, not imported: the audit package does not ship in the image. A sheet line
whose letters no other line shares is placed over the WHOLE song by the
aligner checkpoint's own greedy letters; consecutive placements that agree
form a section; a section whose median offset exceeds SHIFT_MS is moved by that
median (its lines only; see `shift_sheet`). Measured on the audit (2026-11-05):
global shifts detected
0.94, offset error median 64 ms, no shift raised on 79 unmodified songs; the
pilot set's two shifted records read -19.3 s and -4.0 s. Sections only (A2's
activity fit and A3's wrong-sheet rule failed their gates and are not here).
Pure: line texts, stamps and the ear's timed tokens in, stamps out.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

# The audit's parameters, unchanged (audit.anchors / audit.textmatch / audit.score).
UNIQUE_MAX_SIMILARITY = 0.6
SECTION_TOL_MS = 1000
SECTION_RECENT = 3
SHIFT_MS = 1000
MIN_LETTERS = 12
MIN_RATIO = 0.55
MIN_MARGIN = 0.10
AMBIGUITY_MS = 1500
ANCHOR_BLOCK = 3
MS_PER_LETTER = 90
# The ear's raw clock lags the document clock by this much (healthy songs read
# +20..+90 ms, 2026-10-31).
EAR_LAG_MS = 50


def letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def partial_similarity(a: str, b: str) -> float:
    """Best ratio of the shorter string against every same-length window of the longer."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if not short:
        return 0.0
    windows = [long_[i : i + len(short)] for i in range(max(1, len(long_) - len(short) + 1))]
    return max(SequenceMatcher(None, short, w, autojunk=False).ratio() for w in windows)


def ear_stream(tokens: Sequence[dict]) -> tuple[str, list[int]]:
    """The ear's letters as one string, with each letter's time."""
    chars: list[str] = []
    times: list[int] = []
    for tok in tokens:
        for ch in letters(str(tok["text"])):
            chars.append(ch)
            times.append(int(tok["start_ms"]))
    return "".join(chars), times


def place(text: str, stream: str, times: Sequence[int]) -> int | None:
    """Where the ear hears the line over the whole song (raw clock), or None
    unless the best placement is similar enough and clearly better than any
    placement more than AMBIGUITY_MS away."""
    target = letters(text)
    if len(target) < MIN_LETTERS or not stream:
        return None
    span = len(target)
    scored: list[tuple[float, int]] = []
    for i in range(len(stream)):
        piece = stream[i : i + span]
        if len(piece) < span // 2:
            break
        matcher = SequenceMatcher(None, target, piece, autojunk=False)
        first = next((b for b in matcher.get_matching_blocks() if b.size >= ANCHOR_BLOCK), None)
        if first is not None:
            scored.append((matcher.ratio(), times[i + first.b] - round(first.a * MS_PER_LETTER)))
    if not scored:
        return None
    best_ratio, best_t = max(scored)
    rival = max((r for r, t in scored if abs(t - best_t) > AMBIGUITY_MS), default=0.0)
    if best_ratio < MIN_RATIO or best_ratio - rival < MIN_MARGIN:
        return None
    return best_t


def unique_lines(texts: Sequence[str]) -> list[int]:
    """Lines long enough to place whose letters no other line shares."""
    norm = [letters(t) for t in texts]
    return [
        i for i, mine in enumerate(norm)
        if len(mine) >= MIN_LETTERS
        and all(partial_similarity(mine, other) < UNIQUE_MAX_SIMILARITY
                for j, other in enumerate(norm) if j != i and other)
    ]


@dataclass(frozen=True)
class Anchor:
    line: int
    delta_ms: int  # heard (document clock) minus stamp; negative: the sheet is late


@dataclass(frozen=True)
class Section:
    first_line: int
    last_line: int
    anchors: int
    median_ms: int


def _agrees(run: list[Anchor], anchor: Anchor) -> bool:
    recent = [a.delta_ms for a in run[-SECTION_RECENT:]]
    return abs(anchor.delta_ms - statistics.median(recent)) <= SECTION_TOL_MS


def sections(anchors: Sequence[Anchor]) -> list[Section]:
    """A section opens on two consecutive anchors that agree; one that
    disagrees is held back and dropped when the next agrees with the section
    again; two disagreeing anchors that agree with each other open the next."""
    runs: list[list[Anchor]] = []
    run: list[Anchor] = []
    pending: Anchor | None = None
    for a in anchors:
        if run and _agrees(run, a):
            pending = None
            run.append(a)
        elif pending is None:
            pending = a
        elif abs(a.delta_ms - pending.delta_ms) <= SECTION_TOL_MS:
            if run:
                runs.append(run)
            run, pending = [pending, a], None
        else:
            pending = a
    if run:
        runs.append(run)
    # Every run opens with two agreeing anchors, so a lone anchor is never a
    # section (the audit's MIN_SECTION_ANCHORS = 2 filter holds by construction).
    return [
        Section(r[0].line, r[-1].line, len(r), round(statistics.median(a.delta_ms for a in r)))
        for r in runs
    ]


def shift_sheet(texts: Sequence[str], starts: Sequence[int | None], tokens: Sequence[dict]
                ) -> tuple[list[int | None], list[Section]]:
    """(stamps, the shifted sections applied). Unchanged unless a section of
    at least two agreeing anchors sits more than SHIFT_MS away;
    then exactly its first..last lines move. Unanchored lines around it stay:
    a repeated chorus has no unique line to anchor, and on the pilot set
    GOSSIP's second chorus is 3-4 s late where its first verses are 19 s late
    (moving it with them would leave it 15 s early)."""
    stream, times = ear_stream(tokens)
    anchors = []
    for i in unique_lines(texts):
        stamp = starts[i]
        heard = place(texts[i], stream, times) if stamp is not None else None
        if stamp is not None and heard is not None:
            anchors.append(Anchor(i, heard - EAR_LAG_MS - stamp))
    out = list(starts)
    applied: list[Section] = []
    for sec in sections(anchors):
        if abs(sec.median_ms) <= SHIFT_MS:
            continue
        for i in range(sec.first_line, sec.last_line + 1):
            if out[i] is not None:
                out[i] = max(0, int(out[i]) + sec.median_ms)
        applied.append(sec)
    return out, applied
