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
# from "this is a legacy dense list". See docs in document.py. The client keys
# on "any non-empty value", so a plan bump needs no client change; the version
# is provenance, and 1.1 means repeat-class consistency and gesture accounting.
SELECT_PLAN = "density/1.1"

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

#: What the line's opening word gives up in the per-line contest. Enough to
#: lose a tie, not enough to beat a genuinely stronger category — and it never
#: applies when there is nothing to lose to.
FIRST_WORD_PENALTY = 0.2

#: A gesture never emits more than this many words, so a pathological line of
#: the same word repeated cannot outrun the client's per-line belt.
MAX_RUN_WORDS = 6

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
    #: One normalized token per word, when known. Two lines that sing the same
    #: words carry the same tuple, which is how a chorus is recognised as a
    #: chorus — by its LYRIC, not by an audio segment that may or may not have
    #: survived structure detection.
    norm_tokens: tuple[str, ...] = ()


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
    #: What the EYE counts: a repeated word on one line is one effect, so this
    #: is the number the cadence cap is actually measured against.
    kept_gestures: int = 0
    #: Repeat classes found, and pattern words a repeat could not honour
    #: because it had no such candidate of its own.
    repeat_classes: int = 0
    pattern_missing: int = 0
    #: Types whose rank was zeroed for covering too much of the song.
    disabled_types: tuple[str, ...] = ()


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

    classes = _repeat_classes(valid, lines)
    after_line, pattern_missing = _thin_within_lines(valid, lines, scored, classes)
    class_lines = {li for members in classes.values() for li in members}
    after_density = _thin_by_category(after_line, class_lines)

    song_cap = _song_cap(lines)
    after_cap = _apply_cap(after_density, song_cap, classes)
    after_guard, reinserted = _guarantee_sections(
        after_cap, valid, lines, sections, ranks, scored, disabled
    )
    protected = reinserted | {(t.line, t.word) for t in after_guard if t.line in class_lines}
    kept, dropped_gap = _sweep_gaps(after_guard, lines, scored, protected)

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
        kept_gestures=_gestures(kept),
        repeat_classes=len(classes),
        pattern_missing=pattern_missing,
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
            norm_tokens=tuple(normalize_token(w.text) for w in words),
        )
        for line, words in zip(result.lines, result.words_per_line, strict=False)
    ]
    selection = select_fx_words(tags.words, facts, sections or ())
    return replace(tags, words=selection.words, select=selection.plan), selection.stats


def normalize_token(text: str) -> str:
    """A word reduced to what makes two lines "the same line".

    Case and punctuation are noise here: a chorus written once with a comma and
    once without is the same chorus, and the transcript is not consistent about
    either. Uses the pipeline's own Turkish-safe normalizer (İ/I before lower)
    and then keeps only letters and digits.
    """
    from kashi_server.pipeline.semantics import normalize

    return "".join(ch for ch in normalize(text) if ch.isalnum())


# --- steps ---------------------------------------------------------------


def _empty_stats(candidates: int) -> SelectionStats:
    return SelectionStats(candidates, 0, 0, 0, 0, 0, 0, 0)


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


def _repeat_classes(
    candidates: Sequence[WordTag],
    lines: Sequence[LineFacts],
) -> dict[tuple[str, ...], list[int]]:
    """Lines that sing the same words, grouped.

    The field complaint this exists for: a chorus fired on different words each
    time it came round, and on only some of its repeats. Both followed from
    treating every repeat as an unrelated line — the thinning steps stride over
    the whole song in document order and know nothing about a line being the
    same line again.

    Identity is the LYRIC, deliberately, not the audio section: structure
    detection drops a repeat whose span is too short or too long, so two
    identical choruses can land in different ranks for reasons that have
    nothing to do with the words.

    A class needs a real lyric (at least one sung token — otherwise every
    glyph-only line joins one meaningless class) and at least one candidate
    (a class with nothing to fire decides nothing).
    """
    with_candidates = {t.line for t in candidates}
    by_text: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for li, facts in enumerate(lines):
        if li not in with_candidates:
            continue
        if not any(facts.norm_tokens):
            continue
        by_text[facts.norm_tokens].append(li)
    return {key: members for key, members in by_text.items() if len(members) > 1}


def _runs_on_line(line_tags: Sequence[WordTag], facts: LineFacts) -> list[list[WordTag]]:
    """Group a line's candidates into gestures.

    A repeated word ("music, music, music") is ONE gesture, not three: the ear
    hears a single insistence. Words with no token of their own — a glyph like
    a note symbol — do not break it, because they are not sung; a different
    sung word does.

    Members are emitted together, but the group counts once against the quota
    and the spacing rule, so a run cannot eat a line's whole budget.
    """
    tokens = facts.norm_tokens
    ordered = sorted(line_tags, key=lambda t: t.word)
    runs: list[list[WordTag]] = []
    for tag in ordered:
        token = tokens[tag.word] if tag.word < len(tokens) else ""
        previous = runs[-1] if runs else None
        if previous and token and _same_run(previous[-1], tag, tokens):
            previous.append(tag)
        else:
            runs.append([tag])
    return runs


def _same_run(previous: WordTag, tag: WordTag, tokens: tuple[str, ...]) -> bool:
    """Same sung word, with nothing sung in between."""
    if previous.word >= len(tokens) or tag.word >= len(tokens):
        return False
    if tokens[previous.word] != tokens[tag.word] or not tokens[tag.word]:
        return False
    return all(not tokens[i] for i in range(previous.word + 1, tag.word))


def _thin_within_lines(
    candidates: Sequence[WordTag],
    lines: Sequence[LineFacts],
    scored: dict[tuple[int, int], float],
    classes: dict[tuple[str, ...], list[int]] | None = None,
) -> tuple[list[WordTag], int]:
    """A quota per line, and never two effects side by side.

    A repeat class decides ONCE, on its first occurrence, and every later
    occurrence wears the same pattern — that is what makes a chorus recognisable
    instead of a different guess each time round.
    """
    by_line: dict[int, list[WordTag]] = defaultdict(list)
    for tag in candidates:
        by_line[tag.line].append(tag)

    patterned: dict[int, list[WordTag]] = {}
    missing = 0
    for members in (classes or {}).values():
        first = members[0]
        pattern = [tag.word for tag in _pick_on_line(by_line[first], lines[first], scored)]
        for li in members:
            # Intersect with THIS line's own candidates. Transcript variance
            # ("lo-ve") or the candidate ceiling can leave a repeat without the
            # word the pattern names; emitting it anyway would invent a tag
            # that was never a candidate. It stays silent here instead.
            available = {tag.word: tag for tag in by_line[li]}
            patterned[li] = [available[w] for w in pattern if w in available]
            missing += sum(1 for w in pattern if w not in available)

    kept: list[WordTag] = []
    for li in sorted(by_line):
        if li in patterned:
            kept.extend(patterned[li])
        else:
            kept.extend(_pick_on_line(by_line[li], lines[li], scored))
    kept.sort(key=lambda t: (t.line, t.word))
    return kept, missing


def _pick_on_line(
    line_tags: Sequence[WordTag],
    facts: LineFacts,
    scored: dict[tuple[int, int], float],
) -> list[WordTag]:
    """The per-line decision, isolated so a repeat class can reuse it once."""
    quota = _line_quota(facts.words)
    runs = _runs_on_line(line_tags, facts)
    chosen: list[list[WordTag]] = []
    for run in sorted(runs, key=lambda r: _run_sort_key(r, facts, scored)):
        if len(chosen) >= quota:
            break
        # Spacing is measured between gestures, from the run's own extent.
        if all(_runs_far_enough(run, other) for other in chosen):
            chosen.append(run)
    # Per gesture, not just in total: a line of nine "yeah"s is one run, and a
    # total-only limit would let it emit all nine — past the client's per-line
    # belt, which would then trim the tail itself and show a run the server
    # never planned.
    return [tag for run in chosen for tag in run[:MAX_RUN_WORDS]]


def _run_sort_key(
    run: Sequence[WordTag],
    facts: LineFacts,
    scored: dict[tuple[int, int], float],
) -> tuple[float, int]:
    """Best first — and, all else equal, LATER in the line.

    Field feedback: an effect on the line's first word reads as jumping the
    gun. Scores tie constantly (intensity is a per-category constant), so the
    tie-break used to hand every contested line to its opening word. A penalty
    rather than a ban: a line whose only candidate is its first word still
    fires, because a missing effect is worse than an early one.
    """
    best = max(scored[(t.line, t.word)] for t in run)
    if run[0].word == 0:
        best -= FIRST_WORD_PENALTY
    return (-best, -run[-1].word)


def _runs_far_enough(run: Sequence[WordTag], other: Sequence[WordTag]) -> bool:
    first, second = (run, other) if run[0].word < other[0].word else (other, run)
    return second[0].word - first[-1].word > MIN_PLAIN_WORDS_BETWEEN


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


def _thin_by_category(
    candidates: Sequence[WordTag],
    class_lines: set[int] | None = None,
) -> list[WordTag]:
    """Keep all of a rare category, half of a dominant one — spread evenly.

    The stride runs over the WHOLE category in document order rather than
    giving priority lines first claim: a category that lives mostly in the
    chorus would otherwise vanish from the verses entirely. The chorus keeps
    its own protection in the ranking, the cap and the guarantee.
    """
    # Repeat-class words are exempt: thinning them is what made a chorus fire
    # on some repeats and not others. Their overall weight is held down by the
    # song cadence instead, which the class cannot escape.
    exempt = [t for t in candidates if class_lines and t.line in class_lines]
    thinnable = [t for t in candidates if not (class_lines and t.line in class_lines)]

    by_tag: dict[str, list[WordTag]] = defaultdict(list)
    for tag in thinnable:
        by_tag[tag.tag].append(tag)

    kept: list[WordTag] = list(exempt)
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


#: Share of the cadence a repeat class may never take from everything else.
#: Without it a chorus that repeats a dozen times eats the whole budget and the
#: verses go silent — the same wallpaper problem the module exists to prevent,
#: wearing the class as a disguise.
NON_CLASS_RESERVE = 0.25


def _apply_cap(
    candidates: Sequence[WordTag],
    cap: int,
    classes: dict[tuple[str, ...], list[int]] | None = None,
) -> list[WordTag]:
    """Trim to the song's cadence, keeping the spread rather than the front.

    Gestures, not words: a repeated word on one line is one effect to the eye,
    so counting its members separately would make a run look like it blew the
    budget.
    """
    if _gestures(candidates) <= cap:
        return list(candidates)

    class_lines = {li for members in (classes or {}).values() for li in members}
    klass = [t for t in candidates if t.line in class_lines]
    singles = [t for t in candidates if t.line not in class_lines]
    if not klass:
        return _stride_to_cap(candidates, cap)

    # Singles first, but never below the reserve — the verses keep a voice.
    floor = min(_gestures(singles), math.ceil(cap * NON_CLASS_RESERVE))
    room_for_singles = max(floor, cap - _gestures(klass))
    trimmed_singles = _stride_to_cap(singles, room_for_singles)

    kept = sorted(klass + trimmed_singles, key=lambda t: (t.line, t.word))
    if _gestures(kept) <= cap:
        return kept

    # Still over: thin the PATTERN itself, identically across every repeat, so
    # the chorus stays recognisable while it gets quieter.
    kept = _thin_class_pattern(klass, trimmed_singles, cap, classes or {})
    if _gestures(kept) <= cap:
        return kept

    # Last resort: drop whole repeats. The pattern never changes; some repeats
    # simply stay silent, which reads far better than a different guess each time.
    # The loss is shared BETWEEN classes in proportion to their size. Striding
    # over the combined pool let one chorus keep every repeat while another
    # kept one in eleven — measured in the field — which is the very
    # inconsistency the class was introduced to remove, wearing a new hat.
    klass_now = [t for t in kept if t.line in class_lines]
    singles_now = [t for t in kept if t.line not in class_lines]
    room = max(1, cap - _gestures(singles_now))

    alive_by_class = [
        sorted({t.line for t in klass_now} & set(members))
        for members in (classes or {}).values()
    ]
    alive_by_class = [members for members in alive_by_class if members]
    total = sum(len(members) for members in alive_by_class)
    if total == 0:
        return sorted(singles_now, key=lambda t: (t.line, t.word))

    survivors: set[int] = set()
    for members in alive_by_class:
        share = max(1, round(room * len(members) / total))
        survivors.update(_stride_lines(members, min(share, len(members))))

    by_line: dict[int, list[WordTag]] = defaultdict(list)
    for tag in klass_now:
        by_line[tag.line].append(tag)

    def assemble() -> list[WordTag]:
        out = singles_now + [t for li in sorted(survivors) for t in by_line[li]]
        out.sort(key=lambda t: (t.line, t.word))
        return out

    # Proportional rounding can overshoot; trim from whichever class is
    # currently largest so the shape of the loss stays even.
    result = assemble()
    while _gestures(result) > cap:
        biggest = max(
            alive_by_class,
            key=lambda m: (len([li for li in m if li in survivors]), -m[0]),
        )
        alive = sorted(li for li in biggest if li in survivors)
        if len(alive) <= 1:
            break
        survivors.discard(alive[len(alive) // 2])
        result = assemble()
    return result


def _gestures(candidates: Sequence[WordTag]) -> int:
    """How many effects the eye counts — a run on one line is one."""
    seen: set[tuple[int, str]] = set()
    for tag in candidates:
        seen.add((tag.line, tag.tag))
    return len(seen)


def _stride_to_cap(candidates: Sequence[WordTag], cap: int) -> list[WordTag]:
    """Stride over GESTURES, keeping every member of the ones that survive."""
    groups: dict[tuple[int, str], list[WordTag]] = defaultdict(list)
    for tag in sorted(candidates, key=lambda t: (t.line, t.word)):
        groups[(tag.line, tag.tag)].append(tag)
    keys = list(groups)
    if len(keys) <= cap:
        return list(candidates)
    if cap <= 0:
        return []
    chosen = [keys[math.floor(i * len(keys) / cap)] for i in range(cap)]
    out = [tag for key in chosen for tag in groups[key]]
    out.sort(key=lambda t: (t.line, t.word))
    return out


def _stride_lines(lines_in_order: list[int], keep: int) -> list[int]:
    if keep >= len(lines_in_order):
        return lines_in_order
    if keep <= 0:
        return []
    return [lines_in_order[math.floor(i * len(lines_in_order) / keep)] for i in range(keep)]


def _thin_class_pattern(
    klass: Sequence[WordTag],
    singles: Sequence[WordTag],
    cap: int,
    classes: dict[tuple[str, ...], list[int]],
) -> list[WordTag]:
    """Drop one GESTURE from each class's pattern, in every repeat of it.

    A gesture, not a word. "music, music, music" is one insistence to the ear
    and one entry to `_gestures`, so removing its members one at a time frees
    exactly zero budget while dismantling the repetition the run exists to
    render — the loop below would grind a five-word run down to one word, buy
    nothing for it, and only then move on. Measured on the first refreshed
    document: `kept == kept_gestures == 22`, which can only happen when no run
    anywhere kept a second member.

    Per class, deliberately. Treating the classes' word indices as one shared
    pattern deletes whole classes: two choruses that happen to fire on words 4
    and 3 have a union of {3,4}, and dropping the later one silences every
    repeat of the first — a class wiped out by a step meant to make it quieter.
    """
    line_to_class: dict[int, int] = {}
    for index, members in enumerate(classes.values()):
        for li in members:
            line_to_class[li] = index

    # class -> gesture -> its word indices. Bucketed by tag because that is what
    # `_gestures` counts: one category on one line is one effect to the eye,
    # however many words carry it.
    grouped: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for tag in sorted(klass, key=lambda t: (t.line, t.word)):
        key = line_to_class.get(tag.line, -1)
        words = grouped[key][tag.tag]
        if tag.word not in words:
            words.append(tag.word)

    patterns: dict[int, list[list[int]]] = {
        key: [
            sorted(words)
            for _, words in sorted(gestures.items(), key=lambda kv: min(kv[1]))
        ]
        for key, gestures in grouped.items()
    }

    # Trim the widest patterns first: a class already down to one gesture has
    # nothing left to give without falling silent, which is the next step's job.
    def current() -> list[WordTag]:
        alive = [t for t in klass if _in_pattern(t, patterns, line_to_class)]
        return alive + list(singles)

    while _gestures(current()) > cap:
        widest = max(patterns, key=lambda k: (len(patterns[k]), -k))
        if len(patterns[widest]) <= 1:
            break
        patterns[widest] = patterns[widest][:-1]

    trimmed = [t for t in klass if _in_pattern(t, patterns, line_to_class)]
    return sorted(list(singles) + trimmed, key=lambda t: (t.line, t.word))


def _in_pattern(
    tag: WordTag,
    patterns: dict[int, list[list[int]]],
    line_to_class: dict[int, int],
) -> bool:
    gestures = patterns.get(line_to_class.get(tag.line, -1), [])
    return any(tag.word in words for words in gestures)


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
        # A whole gesture enters, never a fragment of one: reinserting the first
        # "music" of "music, music, music" would leave that repeat firing a
        # different pattern from its siblings — the exact inconsistency the
        # repeat class exists to remove, reintroduced by the rescue.
        entering = sorted(
            (t for t in pool if t.line == best.line and t.tag == best.tag),
            key=lambda t: t.word,
        )[:MAX_RUN_WORDS]

        # Pay for it: drop the weakest GESTURE that is not itself a section's
        # only voice. Rank 0 first; a rank-1 outside this section only if we
        # must. Gestures rather than words, because that is what the cap counts
        # — evicting one word of a five-word run frees no budget at all, so a
        # word-for-word trade would quietly push the song past its cadence.
        evictable = {
            (t.line, t.word)
            for t in current
            if t.line not in member_lines and (t.line, t.word) not in reinserted
        }
        gestures: dict[tuple[int, str], list[WordTag]] = defaultdict(list)
        for t in current:
            gestures[(t.line, t.tag)].append(t)
        whole = [
            members
            for members in gestures.values()
            if all((t.line, t.word) in evictable for t in members)
        ]
        rank0 = [g for g in whole if ranks.get(g[0].line, 0) == 0]
        pick_from = rank0 or whole
        if not pick_from:
            continue  # never exceed the cap
        victims = min(
            pick_from,
            key=lambda g: (
                max(scored[(t.line, t.word)] for t in g),
                -g[0].line,
                -g[0].word,
            ),
        )
        for victim in victims:
            current.remove(victim)
            in_kept.discard((victim.line, victim.word))
        for member in entering:
            if (member.line, member.word) in in_kept:
                continue
            current.append(member)
            in_kept.add((member.line, member.word))
            reinserted.add((member.line, member.word))

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

    Gestures, not words: a repeated word is deliberately sung fast, so its
    members sit far closer together than the minimum gap. Timing them
    individually would have the sweep dismantle the very gesture the line-level
    rule just decided to keep — it is atomic here, timed at its last member.
    """
    groups: dict[tuple[int, str], list[WordTag]] = defaultdict(list)
    for tag in kept:
        groups[(tag.line, tag.tag)].append(tag)
    for members in groups.values():
        members.sort(key=lambda t: t.word)

    def when(key: tuple[int, str]) -> int:
        return _word_time(groups[key][-1], lines)

    def strength(key: tuple[int, str]) -> float:
        return max(scored[(t.line, t.word)] for t in groups[key])

    def guarded(key: tuple[int, str]) -> bool:
        return any((t.line, t.word) in protected for t in groups[key])

    ordered = sorted(groups, key=lambda k: (when(k), k[0], groups[k][0].word))
    survivors: list[tuple[int, str]] = []
    dropped = 0
    for key in ordered:
        if not survivors:
            survivors.append(key)
            continue
        previous = survivors[-1]
        if when(key) - when(previous) >= MIN_GAP_MS:
            survivors.append(key)
            continue
        # Too close: one of them goes. A gesture the section guarantee (or a
        # repeat class) put there is never the one dropped.
        if guarded(previous):
            if guarded(key):
                survivors.append(key)  # both are owed a voice — keep both
                continue
            dropped += 1
            continue
        if guarded(key) or strength(key) > strength(previous):
            survivors[-1] = key
        dropped += 1

    out = [tag for key in survivors for tag in groups[key]]
    out.sort(key=lambda t: (t.line, t.word))
    return out, dropped
