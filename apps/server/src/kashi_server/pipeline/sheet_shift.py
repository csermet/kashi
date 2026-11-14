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

2.30.1: a neighbour the move overtakes moves with it (`_carry_overtaken`).
Moving only the anchored lines left GOSSIP's line 0 at 31.7 s behind line 1's
new 16.0 s, and Got 2 Luv U's last two intro repeats behind line 16's new
20.8 s: stamps out of order, so `windows.plan_windows` refused them and both
songs fell back to whole-audio alignment (no hybrid, vote or V3), and line QA
snapped GOSSIP's line 0 onto its stale stamp, 17 s late. The ear hears those
lines at the section's offset (line 0 at -17.5 s; the 16 intro repeats one
for one at -3.7 s).

2.32.1: the lines a move leaves behind take their side's offset
(`_adopt_left_behind`). A section moved later never overtakes the lines
before it, and one moved earlier never overtakes the lines after it, so
those stayed at the old clock: Toz Pembe's two opening "Ah-ah" lines (too
short to anchor) lit 19 s before the voice while lines 2-29 moved +19.3 s,
and GOSSIP's middle and end (no unique line from line 25 on) stayed 19 s
late behind lines 1-24. Lines before the first section and after the last
now go with it, lines between two sections with one of them: the order of
the stamps decides first, then where the ear hears each line (near its stamp
moved by one side's offset or the other's), then position (the nearer edge
stamp). JamendoLyrics, injected errors (gates pre-registered, 2026-11-14):
of the left-behind lines > 1 s off, 6 094 of 6 124 come within 1 s; 12 of
6 106 changed lines end farther from the human stamp; healthy sheets 0 of 79
change.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace
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
    carried: int = 0  # stamped neighbours the move overtook, moved along (2.30.1)
    adopted: int = 0  # unanchored lines left behind, taken over by position (2.32.1)


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


def _carry_overtaken(out: list[int | None], taken: set[int], sec: Section) -> int:
    """Move along the stamped lines just outside a moved section that its move
    overtakes: before an earlier-moved section, a stamp now later than the
    next stamp; after a later-moved one, earlier than the previous. Such a
    stamp cannot be right (the line is sung before / after the section), and
    the section's offset is the one estimate there is. Walks outward until a
    stamp keeps its order; stampless lines are passed over, lines of any
    anchored section (``taken``) stop the walk. Returns the lines moved."""
    step = -1 if sec.median_ms < 0 else 1
    edge = sec.first_line if step < 0 else sec.last_line
    bound = out[edge]
    if bound is None:  # an anchor's line is always stamped
        return 0
    carried = 0
    j = edge + step
    while 0 <= j < len(out) and j not in taken:
        stamp = out[j]
        if stamp is not None:
            if (stamp <= bound) if step < 0 else (stamp >= bound):  # in order: done
                break
            bound = max(0, stamp + sec.median_ms)
            out[j] = bound
            taken.add(j)
            carried += 1
        j += step
    return carried


def shift_sheet(texts: Sequence[str], starts: Sequence[int | None], tokens: Sequence[dict]
                ) -> tuple[list[int | None], list[Section]]:
    """(stamps, the shifted sections applied). Unchanged unless a section of
    at least two agreeing anchors sits more than SHIFT_MS away;
    then its first..last lines move, and with them any neighbour whose stamp
    the move overtakes (`_carry_overtaken`, 2.30.1), and then the unanchored
    lines it left behind on either side, by position (`_adopt_left_behind`,
    2.32.1). The 2.29.1 reading that kept them ("GOSSIP's second chorus is
    3-4 s late where its first verses are 19 s late") came from the ±8 s
    window; the whole-song ear hears every stamped line after GOSSIP's moved
    section at -19 s (2026-11-14)."""
    stream, times = ear_stream(tokens)
    anchors = []
    for i in unique_lines(texts):
        stamp = starts[i]
        heard = place(texts[i], stream, times) if stamp is not None else None
        if stamp is not None and heard is not None:
            anchors.append(Anchor(i, heard - EAR_LAG_MS - stamp))
    found = sections(anchors)
    # Lines with anchors of their own (moved or not) are never carried.
    taken = {i for sec in found for i in range(sec.first_line, sec.last_line + 1)}
    moving = [sec for sec in found if abs(sec.median_ms) > SHIFT_MS]
    out = list(starts)
    for sec in moving:
        for i in range(sec.first_line, sec.last_line + 1):
            stamp = out[i]
            if stamp is not None:
                out[i] = max(0, stamp + sec.median_ms)
    carried = {id(sec): _carry_overtaken(out, taken, sec) for sec in moving}
    adopted = _adopt_left_behind(texts, starts, out, anchors, found, stream, times)
    applied = [replace(sec, carried=carried[id(sec)], adopted=adopted[k])
               for k, sec in enumerate(found) if id(sec) in carried]
    return out, applied


def _offset(sec: Section | None) -> int:
    """What a section does to its lines: its median when it moves, else 0."""
    return sec.median_ms if sec is not None and abs(sec.median_ms) > SHIFT_MS else 0


def _moved(stamp: int, offset: int) -> int:
    return stamp if offset == 0 else max(0, stamp + offset)


def _inversions(stamps: Sequence[int]) -> int:
    return sum(b < a for a, b in zip(stamps, stamps[1:], strict=False))


def _adopt_left_behind(texts: Sequence[str], starts: Sequence[int | None],
                       out: list[int | None], anchors: Sequence[Anchor],
                       found: Sequence[Section], stream: str, times: Sequence[int]) -> list[int]:
    """2.32.1: the stamped lines between found sections (and before the first,
    after the last) that a moved neighbour left at their old stamps take the
    offset of the side they belong to by position. Per gap (the lines strictly
    between two consecutive sections; the song start and end count as sides
    that stay), a split puts the first lines with the left side and the rest
    with the right: fewest stamps out of order first, then the fewest lines
    whose own evidence (`_votes`) points to the other side, then the split
    nearest the default (before the first section: every line with it; after
    the last: every line with it; between two: each line with the section
    whose edge stamp is nearer on the sheet, a tie to the left), then the
    smaller split. A gap no moved section borders, or where no line was left
    behind, is not touched; a split that would leave more stamps out of order
    than today is never taken. Mutates ``out``; returns the lines adopted per
    found section."""
    inside = {i for sec in found for i in range(sec.first_line, sec.last_line + 1)}
    own = {a.line: a.delta_ms for a in anchors if a.line not in inside}
    adopted = [0] * len(found)
    for k in range(len(found) + 1):
        left = found[k - 1] if k > 0 else None
        right = found[k] if k < len(found) else None
        d_left, d_right = _offset(left), _offset(right)
        if d_left == 0 and d_right == 0:
            continue
        lo = left.last_line + 1 if left is not None else 0
        hi = right.first_line if right is not None else len(starts)
        gap = [(i, s) for i in range(lo, hi) if (s := starts[i]) is not None]
        if all(out[i] != s for i, s in gap):
            continue  # nothing left behind (or no stamped line): today's stamps stand
        before = [_stamp(out[left.last_line])] if left is not None else []
        after = [_stamp(out[right.first_line])] if right is not None else []
        today = _inversions([*before, *(_stamp(out[i]) for i, _ in gap), *after])
        default = _default_split(gap, starts, left, right)
        votes = _votes(gap, texts, own, d_left, d_right, stream, times)
        best: tuple[tuple[int, int, int, int], list[int]] | None = None
        for split in range(len(gap) + 1):
            new = [_moved(s, d_left if j < split else d_right) for j, (_, s) in enumerate(gap)]
            disorder = _inversions([*before, *new, *after])
            if disorder > today:
                continue
            against = sum(votes.get(i) == ("right" if j < split else "left")
                          for j, (i, _) in enumerate(gap))
            cost = (disorder, against, abs(split - default), split)
            if best is None or cost < best[0]:
                best = (cost, new)
        if best is None:
            continue
        split = best[0][3]
        for j, ((i, s), stamp) in enumerate(zip(gap, best[1], strict=True)):
            side = k - 1 if j < split else k
            if out[i] == s and stamp != s and 0 <= side < len(found):
                adopted[side] += 1
            out[i] = stamp
    return adopted


def _votes(gap: Sequence[tuple[int, int]], texts: Sequence[str], own: dict[int, int],
           d_left: int, d_right: int, stream: str, times: Sequence[int]) -> dict[int, str]:
    """Which side a gap line's own evidence takes. A lone anchor (a unique
    line the ear placed over the whole song, in no section) goes with the side
    whose offset is nearer its own delta; a tie decides nothing. Any other
    line of MIN_LETTERS or more goes with the side near whose moved stamp
    (± AMBIGUITY_MS) the ear hears its letters, at MIN_RATIO or better and
    MIN_MARGIN ahead of the other side; a repeat is fine here, the two
    windows are all it is asked about. Without the ear's vote the carry moved
    more healthy lines next to an injected verse than it brought back (61
    against 34; with it 11 against 34, gates.md)."""
    votes: dict[int, str] = {}
    for i, s in gap:
        if i in own:
            to_left, to_right = abs(own[i] - d_left), abs(own[i] - d_right)
            if to_left != to_right:
                votes[i] = "left" if to_left < to_right else "right"
        elif len(letters(texts[i])) >= MIN_LETTERS:
            r_left = _ratio_near(texts[i], s + d_left + EAR_LAG_MS, stream, times)
            r_right = _ratio_near(texts[i], s + d_right + EAR_LAG_MS, stream, times)
            if r_left >= MIN_RATIO and r_left - r_right >= MIN_MARGIN:
                votes[i] = "left"
            elif r_right >= MIN_RATIO and r_right - r_left >= MIN_MARGIN:
                votes[i] = "right"
    return votes


def _ratio_near(text: str, t: int, stream: str, times: Sequence[int]) -> float:
    """`place`'s best similarity among placements within AMBIGUITY_MS of ``t``."""
    target = letters(text)
    span = len(target)
    reach = AMBIGUITY_MS + span * MS_PER_LETTER
    best = 0.0
    for i, at in enumerate(times):
        if at < t - reach:
            continue
        if at > t + reach:
            break
        piece = stream[i : i + span]
        if len(piece) < span // 2:
            break
        matcher = SequenceMatcher(None, target, piece, autojunk=False)
        first = next((b for b in matcher.get_matching_blocks() if b.size >= ANCHOR_BLOCK), None)
        if first is not None and abs(times[i + first.b] - round(first.a * MS_PER_LETTER) - t) \
                <= AMBIGUITY_MS:
            best = max(best, matcher.ratio())
    return best


def _stamp(value: int | None) -> int:
    assert value is not None  # a stamped line keeps a stamp; an anchor's line is stamped
    return value


def _default_split(gap: Sequence[tuple[int, int]], starts: Sequence[int | None],
                   left: Section | None, right: Section | None) -> int:
    """How many of the gap's lines go left when nothing else decides: none
    before the first section, all after the last, and between two sections
    those whose sheet stamp is nearer the left section's last stamp than the
    right section's first (a tie goes left)."""
    if left is None:
        return 0
    if right is None:
        return len(gap)
    a, b = _stamp(starts[left.last_line]), _stamp(starts[right.first_line])
    return sum(s - a <= b - s for _, s in gap)
