"""Where is a lyric line sung? Its letters matched against the ear's letters.

The whole-song ear is a timed letter stream (greedy CTC). A line's own letters
(lowercased, letters only) are slid along that stream near the document's
start; the best similarity gives where the model hears the line. Rough by
design: a greedy decode of singing is phonetic ("yalona give ye the remidy
fiset your free"), so only strong, unambiguous matches count.

Measured on Got 2 Luv U (2026-10-31): it reads a verse 3.6-5.6 s late in both
document versions, line by line, where the onset-based checks only saw piles.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

SEARCH_MS = 8000          # look this far either side of the document's start
MIN_LETTERS = 12          # shorter lines match anywhere
MIN_RATIO = 0.55          # similarity of the best placement
MIN_MARGIN = 0.10         # ... and this much better than any placement > 1.5 s away (inclusive)
AMBIGUITY_MS = 1500
# The heard time comes from the first matching run of at least this many
# letters, not the window's start (a window opening early still contains the
# whole match), stepped back by that run's place in the line: a misheard line
# start ("saken you must sleep" for "Talkin' in my sleep") must not read late.
ANCHOR_BLOCK = 3
DEFAULT_MS_PER_LETTER = 90
# A line's own rate, clamped: a held last word can stretch a line to 4.6 s
# for 17 letters (New Rules "You gettin' over him"), which would step back 1 s.
MIN_MS_PER_LETTER = 50
MAX_MS_PER_LETTER = 150


def letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def partial_similarity(a: str, b: str) -> float:
    """Best SequenceMatcher ratio of the shorter string against every window of
    the longer one of the same length (a short line inside a long one scores
    as the overlap, not diluted by the long line's other letters)."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if not short:
        return 0.0
    windows = [long_[i : i + len(short)] for i in range(max(1, len(long_) - len(short) + 1))]
    return max(SequenceMatcher(None, short, w, autojunk=False).ratio() for w in windows)


@dataclass(frozen=True)
class LineMatch:
    heard_ms: int
    ratio: float
    margin: float


def sung_span(line: dict) -> int:
    """First word's start to last word's start (a held last word says nothing
    about the rate); the line's own span when it has fewer than two words."""
    words = line.get("words") or []
    if len(words) >= 2:
        return words[-1]["start_ms"] - words[0]["start_ms"]
    return line["end_ms"] - line["start_ms"]


def ear_stream(tokens: Sequence[dict]) -> tuple[str, list[int]]:
    """The ear's letters as one string, with each letter's time."""
    chars: list[str] = []
    times: list[int] = []
    for tok in tokens:
        for ch in letters(tok["text"]):
            chars.append(ch)
            times.append(tok["start_ms"])
    return "".join(chars), times


def locate(line_text: str, doc_start_ms: int, stream: str, times: Sequence[int],
           search_ms: int = SEARCH_MS, line_ms: int | None = None) -> LineMatch | None:
    """Best unambiguous placement of the line's letters near ``doc_start_ms``.
    ``line_ms`` (the document's line duration) sets the letter rate used to step
    back from the first matching run to the line's start."""
    target = letters(line_text)
    if len(target) < MIN_LETTERS or not stream:
        return None
    per_letter = (min(MAX_MS_PER_LETTER, max(MIN_MS_PER_LETTER, line_ms / len(target)))
                  if line_ms and line_ms > 0 else DEFAULT_MS_PER_LETTER)
    lo = next((i for i, t in enumerate(times) if t >= doc_start_ms - search_ms), len(times))
    hi = next((i for i, t in enumerate(times) if t > doc_start_ms + search_ms), len(times))
    span = len(target)
    scored: list[tuple[float, int]] = []
    for i in range(lo, hi):
        piece = stream[i : i + span]
        if len(piece) < span // 2:
            break
        matcher = SequenceMatcher(None, target, piece, autojunk=False)
        first = next((b for b in matcher.get_matching_blocks() if b.size >= ANCHOR_BLOCK), None)
        if first is not None:
            scored.append((matcher.ratio(), times[i + first.b] - round(first.a * per_letter)))
    if not scored:
        return None
    best_ratio, best_t = max(scored)
    rival = max((r for r, t in scored if abs(t - best_t) > AMBIGUITY_MS), default=0.0)
    if best_ratio < MIN_RATIO or best_ratio - rival < MIN_MARGIN:
        return None
    return LineMatch(heard_ms=best_t, ratio=round(best_ratio, 3),
                     margin=round(best_ratio - rival, 3))
