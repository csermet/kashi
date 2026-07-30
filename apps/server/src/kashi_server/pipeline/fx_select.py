"""Which tagged words actually get an effect.

`semantics.py` answers "does this word mean fire?". This answers "should we
fire on it, here, in this song?" — a different question with different inputs
(line lengths, section spans, how often the category already fired) and no
lexicon dependency at all, which is why it lives in its own module and can be
tested without loading a 470 MB embedder.

The field verdict that produced it: with an effect on every tagged word, a song
whose lyric leans on one word — 42 occurrences of `love` is a real number from
the library — becomes exhausting. Measured across the archive: mean 18.2 tagged
words per document, max 60, and the old cap was being hit.

The rules, in the order they apply:

  1. rank each line by the section it sits in (chorus > high energy > nothing)
  2. score each candidate (category strength + that rank + rarity)
  3. thin WITHIN a line: a quota from the line's length, never adjacent
  4. thin PER CATEGORY: keep all of a rare one, half of a dominant one
  5. cap the song by CADENCE, not by a flat count
  6. guarantee the chorus is never left empty
  7. sweep globally so two effects never land within 700 ms of each other

Everything here is pure: no clock, no randomness, no I/O. Every sort ends on
`(line, word)` so the output cannot depend on input order.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from kashi_server.pipeline.energy import Section
from kashi_server.pipeline.semantics import WordTag

# Stamped onto the document so the client can tell "the server already chose"
# from "this is a legacy dense list". See docs in document.py.
SELECT_PLAN = "density/1.0"

#: chorus outranks loud, loud outranks nothing.
_RANK_CHORUS = 2
_RANK_HIGH = 1

#: What a rank is worth against a category's own strength (0.4 … 0.9). Half a
#: strength-span, so a chorus alone cannot overrule meaning: a chorus `phone`
#: (0.4 + 0.30) still loses to a verse `explosion` (0.9). Combined with the
#: rarity bonus it CAN — a once-sung chorus `phone` reaches 0.95 — which is
#: intended: heard once, in the hook, is worth hearing.
PRIORITY_BONUS = {_RANK_CHORUS: 0.30, _RANK_HIGH: 0.15, 0: 0.0}

#: A word heard once in the whole song beats the sixth `love` at equal
#: strength. Capped low so rarity cannot outrank two strength classes.
RARITY_BONUS = 0.25
RARE_MAX_COUNT = 6

#: Caner's rule, verbatim: at least two plain words between two effect words.
MIN_PLAIN_WORDS_BETWEEN = 2

#: How many effects a line may carry, by how long the line is. Most sung lines
#: are 5-9 words, so the majority stay at one — "normalde 1, bazen 2".
def _line_quota(words: int) -> int:
    if words <= 5:
        return 1
    if words <= 10:
        return 2
    return 3


#: Below this, a category is a motif rather than wallpaper — keep every one.
KEEP_ALL_BELOW = 4
#: Above it, keep half. Caner's "çok geçiyorsa yarısında".
KEEP_RATIO = 0.5

#: The song cap is a CADENCE, not a number: one effect per ~9 s, floored and
#: ceilinged. A flat count would punish long songs and let a two-minute
#: nightcore edit fire every five seconds — which is the complaint, restated.
SECONDS_PER_EFFECT = 9
#: Only a floor for degenerate snippets. It used to be 12, which OVERRODE the
#: cadence below ~108 s: a one-minute nightcore edit got an effect every five
#: seconds — the exact density this cap exists to prevent.
MIN_SONG_CAP = 4
MAX_SONG_CAP = 24

#: Two effects closer than this read as one to the eye and cost the particle
#: budget twice. Enforced ACROSS lines, not just within one: back-to-back short
#: lines were the gap the per-line rule never covered.
MIN_GAP_MS = 700

#: A section type covering most of the song carries no information. Measured in
#: TIME, exactly as `structure._MAX_COVERAGE` does — NOT as a share of tagged
#: lines. Hook words live in the chorus, so a line-share denominator would put
#: a normal chorus over any threshold and switch the rule off on precisely the
#: songs it exists for. Judged PER TYPE: a sprawling `high` must not discredit
#: an honest `chorus`.
MAX_PRIORITY_COVERAGE = 0.6


@dataclass(frozen=True)
class LineFacts:
    """What the selector needs to know about one rendered line."""

    words: int
    start_ms: int
    end_ms: int
    #: Start time per word, when known — enables the global gap sweep.
    word_starts: tuple[int, ...] = ()


@dataclass(frozen=True)
class SelectionStats:
    candidates: int
    kept: int
    song_cap: int
    dropped_line_rule: int
    dropped_density: int
    dropped_cap: int
    dropped_gap: int
    guard_reinserted: int
    #: Types whose rank was zeroed for covering too much of the song.
    disabled_types: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    plan: str
    words: list[WordTag]
    stats: SelectionStats


def select_fx_words(
    candidates: Sequence[WordTag],
    lines: Sequence[LineFacts],
    sections: Sequence[Section] = (),
) -> Selection:
    """Choose which tagged words actually fire. Pure and deterministic."""
    valid = _sanitize(candidates, lines)
    if not valid:
        return Selection(SELECT_PLAN, [], _empty_stats(len(candidates)))

    ranks, disabled = _rank_lines(valid, lines, sections)
    counts = _category_counts(valid)
    scored = {
        (t.line, t.word): _score(t, ranks.get(t.line, 0), counts[t.tag]) for t in valid
    }

    after_line = _thin_within_lines(valid, lines, scored)
    after_density = _thin_by_category(after_line)

    song_cap = _song_cap(lines)
    after_cap = _apply_cap(after_density, song_cap)
    after_guard, reinserted = _guarantee_sections(
        after_cap, valid, lines, sections, ranks, scored, disabled
    )
    kept, dropped_gap = _sweep_gaps(after_guard, lines, scored, reinserted)

    kept.sort(key=lambda t: (t.line, t.word))
    stats = SelectionStats(
        candidates=len(candidates),
        kept=len(kept),
        song_cap=song_cap,
        dropped_line_rule=len(valid) - len(after_line),
        dropped_density=len(after_line) - len(after_density),
        dropped_cap=len(after_density) - len(after_cap),
        dropped_gap=dropped_gap,
        guard_reinserted=len(reinserted),
        disabled_types=disabled,
    )
    return Selection(SELECT_PLAN, kept, stats)


def thin_fx(tags, result, sections):
    """Adapter: applies the selection to a FxTags built over an AlignResult.

    Kept deliberately thin — everything worth testing lives in the pure
    function above.
    """
    from dataclasses import replace

    facts = [
        LineFacts(
            words=len(words),
            start_ms=line.start_ms,
            end_ms=line.end_ms,
            word_starts=tuple(w.start_ms for w in words),
        )
        for line, words in zip(result.lines, result.words_per_line, strict=False)
    ]
    selection = select_fx_words(tags.words, facts, sections or ())
    return replace(tags, words=selection.words, select=selection.plan), selection.stats


# --- steps ---------------------------------------------------------------


def _empty_stats(candidates: int) -> SelectionStats:
    return SelectionStats(candidates, 0, 0, 0, 0, 0, 0, 0, ())


def _sanitize(candidates: Sequence[WordTag], lines: Sequence[LineFacts]) -> list[WordTag]:
    """Drop what cannot be rendered; a bad index is never worth a crash."""
    seen: set[tuple[int, int]] = set()
    out: list[WordTag] = []
    for tag in candidates:
        if not (0 <= tag.line < len(lines)):
            continue
        facts = lines[tag.line]
        if facts.words <= 0 or not (0 <= tag.word < facts.words):
            continue
        key = (tag.line, tag.word)
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    out.sort(key=lambda t: (t.line, t.word, t.tag))
    return out


def _rank_lines(
    candidates: Sequence[WordTag],
    lines: Sequence[LineFacts],
    sections: Sequence[Section],
) -> tuple[dict[int, int], tuple[str, ...]]:
    """Rank each candidate-carrying line by the section holding its MIDPOINT.

    Midpoint rather than overlap: a line whose tail leaks 50 ms into a chorus
    is not a chorus line, and one scalar keeps the rule decidable.
    """
    by_type: dict[str, list[Section]] = defaultdict(list)
    for section in sections:
        if section.end_ms > section.start_ms:
            by_type[section.type].append(section)

    fx_lines = sorted({t.line for t in candidates})
    if not fx_lines:
        return {}, ()

    membership: dict[str, set[int]] = {}
    for kind, spans in by_type.items():
        hit = {
            li
            for li in fx_lines
            if any(
                s.start_ms <= (lines[li].start_ms + lines[li].end_ms) // 2 < s.end_ms
                for s in spans
            )
        }
        if hit:
            membership[kind] = hit

    # Per type, not combined: a `high` that covers most of the track says
    # nothing, but that is no reason to throw away an honest `chorus`.
    track_ms = max((line.end_ms for line in lines), default=0)
    disabled = tuple(
        sorted(
            kind
            for kind, spans in by_type.items()
            if track_ms > 0
            and sum(s.end_ms - s.start_ms for s in spans) / track_ms > MAX_PRIORITY_COVERAGE
        )
    )
    ranks: dict[int, int] = {}
    for li in fx_lines:
        if "chorus" not in disabled and li in membership.get("chorus", ()):
            ranks[li] = _RANK_CHORUS
        elif "high" not in disabled and li in membership.get("high", ()):
            ranks[li] = _RANK_HIGH
        else:
            ranks[li] = 0
    return ranks, disabled


def _category_counts(candidates: Sequence[WordTag]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for tag in candidates:
        counts[tag.tag] += 1
    return counts


def _score(tag: WordTag, rank: int, occurrences: int) -> float:
    rarity = max(0.0, 1.0 - (occurrences - 1) / (RARE_MAX_COUNT - 1))
    return tag.intensity + PRIORITY_BONUS.get(rank, 0.0) + RARITY_BONUS * rarity


def _thin_within_lines(
    candidates: Sequence[WordTag],
    lines: Sequence[LineFacts],
    scored: dict[tuple[int, int], float],
) -> list[WordTag]:
    """A quota per line, and never two effects side by side."""
    by_line: dict[int, list[WordTag]] = defaultdict(list)
    for tag in candidates:
        by_line[tag.line].append(tag)

    kept: list[WordTag] = []
    for li in sorted(by_line):
        quota = _line_quota(lines[li].words)
        chosen: list[WordTag] = []
        for tag in sorted(by_line[li], key=lambda t: (-scored[(t.line, t.word)], t.word)):
            if len(chosen) >= quota:
                break
            if all(
                abs(tag.word - other.word) > MIN_PLAIN_WORDS_BETWEEN for other in chosen
            ):
                chosen.append(tag)
        kept.extend(chosen)
    kept.sort(key=lambda t: (t.line, t.word))
    return kept


def _stride(items: list[WordTag], keep: int) -> list[WordTag]:
    """Evenly spaced picks over document order — the deterministic "skip some".

    NOT top-k. Within a category `intensity` is a constant, so top-k would
    degenerate into "whatever the sort reached first", which is the
    front-loading bug wearing a new hat. Index 0 is always included, so the
    first time a word is sung it is always seen.
    """
    if keep >= len(items):
        return list(items)
    if keep <= 0:
        return []
    return [items[math.floor(i * len(items) / keep)] for i in range(keep)]


def _thin_by_category(candidates: Sequence[WordTag]) -> list[WordTag]:
    """Keep all of a rare category, half of a dominant one — spread evenly.

    The stride runs over the WHOLE category in document order rather than
    giving priority lines first claim: a category that lives mostly in the
    chorus would otherwise vanish from the verses entirely. The chorus keeps
    its own protection in the ranking, the cap and the guarantee.
    """
    by_tag: dict[str, list[WordTag]] = defaultdict(list)
    for tag in candidates:
        by_tag[tag.tag].append(tag)

    kept: list[WordTag] = []
    for name in sorted(by_tag):
        occurrences = by_tag[name]
        total = len(occurrences)
        if total <= KEEP_ALL_BELOW:
            kept.extend(occurrences)
            continue
        quota = max(KEEP_ALL_BELOW, math.ceil(total * KEEP_RATIO))
        kept.extend(_stride(occurrences, quota))
    kept.sort(key=lambda t: (t.line, t.word))
    return kept


def _song_cap(lines: Sequence[LineFacts]) -> int:
    duration_ms = max((line.end_ms for line in lines), default=0)
    if duration_ms <= 0:
        return MIN_SONG_CAP
    cadence = round(duration_ms / 1000 / SECONDS_PER_EFFECT)
    return max(MIN_SONG_CAP, min(MAX_SONG_CAP, int(cadence)))


def _apply_cap(candidates: Sequence[WordTag], cap: int) -> list[WordTag]:
    """Trim to the song's cadence, keeping the spread rather than the front."""
    if len(candidates) <= cap:
        return list(candidates)
    ordered = sorted(candidates, key=lambda t: (t.line, t.word))
    kept = _stride(ordered, cap)
    kept.sort(key=lambda t: (t.line, t.word))
    return kept


def _guarantee_sections(
    kept: Sequence[WordTag],
    candidates: Sequence[WordTag],
    lines: Sequence[LineFacts],
    sections: Sequence[Section],
    ranks: dict[int, int],
    scored: dict[tuple[int, int], float],
    disabled: tuple[str, ...],
) -> tuple[list[WordTag], set[tuple[int, int]]]:
    """No chorus that HAS something to say is left silent. Cap-neutral."""
    guard_type = None
    for kind in ("chorus", "high"):
        if kind in disabled:
            continue
        if any(s.type == kind for s in sections):
            guard_type = kind
            break
    if guard_type is None:
        return list(kept), set()

    current = list(kept)
    reinserted: set[tuple[int, int]] = set()
    in_kept = {(t.line, t.word) for t in current}

    for section in sorted(
        (s for s in sections if s.type == guard_type), key=lambda s: (s.start_ms, s.end_ms)
    ):
        member_lines = {
            li
            for li in range(len(lines))
            if section.start_ms
            <= (lines[li].start_ms + lines[li].end_ms) // 2
            < section.end_ms
        }
        if not member_lines:
            continue
        if any(t.line in member_lines for t in current):
            continue  # already speaks
        pool = [t for t in candidates if t.line in member_lines]
        if not pool:
            continue  # nothing to say here
        best = min(pool, key=lambda t: (-scored[(t.line, t.word)], t.line, t.word))
        if (best.line, best.word) in in_kept:
            continue

        # Pay for it: drop the weakest word that is not itself a section's only
        # voice. Rank 0 first; a rank-1 outside this section only if we must.
        evictable = [
            t
            for t in current
            if t.line not in member_lines and (t.line, t.word) not in reinserted
        ]
        rank0 = [t for t in evictable if ranks.get(t.line, 0) == 0]
        pick_from = rank0 or evictable
        if not pick_from:
            continue  # never exceed the cap
        victim = min(pick_from, key=lambda t: (scored[(t.line, t.word)], -t.line, -t.word))
        current.remove(victim)
        in_kept.discard((victim.line, victim.word))
        current.append(best)
        in_kept.add((best.line, best.word))
        reinserted.add((best.line, best.word))

    current.sort(key=lambda t: (t.line, t.word))
    return current, reinserted


def _word_time(tag: WordTag, lines: Sequence[LineFacts]) -> int:
    """When the word's own start is known, use it; otherwise estimate.

    The estimate matters more than it looks. Falling back to the LINE's start
    would give every word on a line the same timestamp, and the gap sweep
    below would then read a legitimately spaced pair as simultaneous and throw
    one away — silently undoing the per-line quota it is supposed to complement.
    """
    facts = lines[tag.line]
    if tag.word < len(facts.word_starts):
        return facts.word_starts[tag.word]
    if facts.words <= 0:
        return facts.start_ms
    span = max(0, facts.end_ms - facts.start_ms)
    return facts.start_ms + span * tag.word // facts.words


def _sweep_gaps(
    kept: Sequence[WordTag],
    lines: Sequence[LineFacts],
    scored: dict[tuple[int, int], float],
    protected: set[tuple[int, int]],
) -> tuple[list[WordTag], int]:
    """Across lines, not just within one.

    The per-line rule cannot see two short lines sung back to back, which is
    exactly where "one every so often" broke down.
    """
    ordered = sorted(kept, key=lambda t: (_word_time(t, lines), t.line, t.word))
    out: list[WordTag] = []
    dropped = 0
    for tag in ordered:
        if not out:
            out.append(tag)
            continue
        previous = out[-1]
        if _word_time(tag, lines) - _word_time(previous, lines) >= MIN_GAP_MS:
            out.append(tag)
            continue
        # Too close: one of them goes. A word re-inserted by the section
        # guarantee is never the one dropped.
        key, prev_key = (tag.line, tag.word), (previous.line, previous.word)
        if prev_key in protected:
            # Both rescued and too close together: keep both rather than
            # silently undoing a guarantee. Two sections each need a voice, and
            # `guard_reinserted` would otherwise report a rescue that did not
            # survive.
            if key in protected:
                out.append(tag)
                continue
            dropped += 1
            continue
        if key in protected or scored[key] > scored[prev_key]:
            out[-1] = tag
        dropped += 1
    return out, dropped
