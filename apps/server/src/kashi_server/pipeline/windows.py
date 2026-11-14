"""lrclib-anchored window planning for the aligner (hizalama-v2 P3).

The failure mode this kills: on a full song the CTC aligner can lose lock in
one section (layered chorus, heavy processing) and drag whole blocks of lines
seconds away — while most of the song is fine (measured: 27/79 JamendoLyrics
songs show MAE > 1 s with per-song MedAE < 150 ms). lrclib's synced [mm:ss.xx]
line stamps bound that error by construction: each window is aligned
independently, so a lock loss cannot propagate past a window edge. Published
precedent: hierarchical line-then-word alignment (DSE/HCLAS-X lineage) and
anchor segmentation (Demirel ICASSP 2021).

Pure module in the regroup tradition: no torch, no I/O. The aligner slices
one loaded 16 kHz tensor per window; this module only plans the slices.

Design notes (research round 2026-07-11):
- Audio slices OVERLAP on purpose (pad on both sides): melisma bleeds across
  line boundaries and lrclib stamps are crowd-sourced (small offsets are
  normal). Ownership of LINES never overlaps — each line's words come from
  exactly one window.
- Short windows make CTC unstable -> consecutive lines are merged until the
  window is long enough and carries enough words.
- A stampless line cannot anchor a window edge; it rides with its neighbour.
- Too few stamps -> None: the caller falls back to whole-audio alignment
  (also keeps the stamp-free warmup fixture on the old path).
"""

from dataclasses import dataclass

PAD_MS = 350
MIN_WINDOW_MS = 4000
MIN_WORDS = 3
# Below either bound the stamps cannot carve meaningful windows.
MIN_STAMPED_LINES = 4
MIN_STAMPED_FRACTION = 0.8
# A piled run (unpile_starts): this many consecutive words starting within
# this span of the run's first. The benchmark's bunched-run rule.
PILE_MIN_WORDS = 3
PILE_SPAN_MS = 60
# 2.32.1 (whole_track_vote's spacing guard): the vote may not bring two words
# closer than this when their windowed starts are at least this far apart —
# the overlay's short dwell (a word lit under 120 ms reads as a flash).
VOTE_MIN_GAP_MS = 120
# Timing v3, V1 (line_regions): how far a line inside a merged window may
# start from its own stamp, either way. 1200 and 800 were measured on the 20
# pilot songs (2026-11-09): the same moments fixed, 800 also lands BxMxC 0:13
# and SexyBack's third "You ready?" on their voices; noticeable lines against
# 2.30.1 101 -> 95 (1200: 101).
LINE_REGION_MS = 800


@dataclass(frozen=True)
class Window:
    slice_start_ms: int  # padded audio slice bounds (may overlap neighbours)
    slice_end_ms: int
    line_indices: list[int]  # indices into line_texts owned by this window
    # The UNPADDED span this window owns: its first stamp up to the next
    # window's first stamp (or the audio end). Audio past `owned_end_ms` is the
    # next window's; the pad only lets this window hear it. None for windows
    # built by hand without a plan.
    owned_start_ms: int | None = None
    owned_end_ms: int | None = None

    def __post_init__(self):
        if self.slice_end_ms <= self.slice_start_ms:
            raise ValueError("empty window slice")
        if not self.line_indices:
            raise ValueError("window owns no lines")


def _stamps_usable(
    line_texts: list[str], synced_starts_ms: list[int | None], total_ms: int
) -> bool:
    if len(line_texts) != len(synced_starts_ms) or not line_texts or total_ms <= 0:
        return False
    stamped = [start for start in synced_starts_ms if start is not None]
    if len(stamped) < MIN_STAMPED_LINES:
        return False
    if len(stamped) / len(line_texts) < MIN_STAMPED_FRACTION:
        return False
    # non-monotonic stamps: don't trust them as anchors
    return not any(b < a for a, b in zip(stamped, stamped[1:], strict=False))


def _line_spans(synced_starts_ms: list[int | None], total_ms: int) -> list[tuple[int, int]]:
    """Nominal (unpadded) span per STAMPED line: [own stamp, next stamped
    line's stamp). A stampless line inherits its predecessor's span end and
    stretches it."""
    spans: list[tuple[int, int]] = []
    for i, start in enumerate(synced_starts_ms):
        if start is None:
            continue  # attached to a neighbour in _line_groups
        end = next((s for s in synced_starts_ms[i + 1 :] if s is not None), total_ms)
        spans.append((start, min(end, total_ms)))
    return spans


def _line_groups(synced_starts_ms: list[int | None]) -> list[list[int]]:
    """A group = one stamped line plus any stampless followers; stampless
    LEADERS (before the first stamp) ride with the first group."""
    groups: list[list[int]] = []
    leaders: list[int] = []
    for i, start in enumerate(synced_starts_ms):
        if start is None:
            (groups[-1] if groups else leaders).append(i)
        else:
            groups.append([i])
    if leaders:
        groups[0] = leaders + groups[0]
    return groups


def _merge_groups(
    groups: list[list[int]],
    spans: list[tuple[int, int]],
    line_texts: list[str],
    min_window_ms: int,
    min_words: int,
) -> list[tuple[int, int, list[int]]]:
    """Merge consecutive groups until each window is long enough AND wordy
    enough. Merging is greedy left-to-right; a trailing short window merges
    backwards into the previous one. Returns (start, end, lines) triples."""
    windows: list[tuple[int, int, list[int]]] = []
    current_lines: list[int] = []
    current_start: int | None = None
    current_end = 0
    # one span per stamped line = per group
    for group, (start, end) in zip(groups, spans, strict=True):
        if current_start is None:
            current_start = start
        current_end = end
        current_lines.extend(group)
        words = sum(len(line_texts[i].split()) for i in current_lines)
        if current_end - current_start >= min_window_ms and words >= min_words:
            windows.append((current_start, current_end, current_lines))
            current_lines = []
            current_start = None
    if current_lines:
        if windows:
            prev_start, _, prev_lines = windows[-1]
            windows[-1] = (prev_start, current_end, prev_lines + current_lines)
        else:
            windows.append((current_start or 0, current_end, current_lines))
    return windows


def _padded_windows(
    windows: list[tuple[int, int, list[int]]], n_lines: int, total_ms: int, pad_ms: int
) -> list[Window] | None:
    try:
        out = [
            Window(
                slice_start_ms=max(0, start - pad_ms),
                slice_end_ms=min(total_ms, end + pad_ms),
                line_indices=lines,
                owned_start_ms=start,
                owned_end_ms=min(end, total_ms),
            )
            for start, end, lines in windows
        ]
    except ValueError:
        # e.g. a stamp beyond the audio end (bad lrclib record) makes an empty
        # slice — anchors are untrustworthy, use the whole-audio path.
        return None
    # Ownership must partition the lines exactly (defensive: regroup depends
    # on it downstream).
    owned = [i for w in out for i in w.line_indices]
    if owned != list(range(n_lines)):
        return None
    return out


def plan_windows(
    line_texts: list[str],
    synced_starts_ms: list[int | None],
    total_ms: int,
    *,
    pad_ms: int = PAD_MS,
    min_window_ms: int = MIN_WINDOW_MS,
    min_words: int = MIN_WORDS,
) -> list[Window] | None:
    """Carve the song into line-anchored windows, or None for the whole-audio
    path (too few stamps / degenerate input)."""
    if not _stamps_usable(line_texts, synced_starts_ms, total_ms):
        return None
    spans = _line_spans(synced_starts_ms, total_ms)
    groups = _line_groups(synced_starts_ms)
    windows = _merge_groups(groups, spans, line_texts, min_window_ms, min_words)
    return _padded_windows(windows, len(line_texts), total_ms, pad_ms)


def line_regions(
    window: Window, synced_starts_ms: list[int | None], region_ms: int | None = LINE_REGION_MS
) -> dict[int, tuple[int, int]]:
    """Timing v3, V1 (2.31.0): where each line of a merged window may start.

    `_merge_groups` merges lines until a window is >= 4 s and >= 3 words, so
    two-word lines share one, and nothing inside the window held a line near
    its own stamp: short repeats landed one slot off or crammed onto one
    repeat (SexyBack 2:26, the first "You ready?" 1.2 s late behind a
    stretched "Get your sexy on"). In a window that owns two or more stamped
    lines, each stamped line's first letter must start, on the document
    clock, within `region_ms` of its own stamp, both ends inclusive. The
    aligner applies it as cuts in the trellis (`trellis.letter_bounds_cuts`),
    never as a shift afterwards (the soft offset is dead: a rigid shift moved
    words off their evidence); inside the region the words still land where
    the audio puts them. The next stamp + `region_ms` bound on the line's other
    words follows from the next line's own region, and the last line's from
    the slice, which ends `PAD_MS` past the next window's first stamp.
    Stampless lines and windows with one stamped line get none; `region_ms`
    None switches V1 off. Returns {line index: (earliest, latest) start, ms}."""
    if region_ms is None:
        return {}
    stamped = [(i, s) for i in window.line_indices if (s := synced_starts_ms[i]) is not None]
    if len(stamped) < 2:
        return {}
    return {i: (stamp - region_ms, stamp + region_ms) for i, stamp in stamped}


#: One CTC frame (wav2vec2-family hop: 320 samples at 16 kHz), in seconds — the
#: smallest span a word can honestly be given when it is moved.
FRAME_S = 0.02


def yield_pad_words(results: list[dict], windows: list[Window]) -> tuple[list[dict], int]:
    """At each seam, let the window that OWNS the audio keep it (Faz 9, 2026-10).

    A window's slice reaches PAD_MS into its neighbour so a line can bleed over
    a seam. When CTC finds no home for a line's last word inside the line, it
    can park it in that pad — on audio the next window owns and has already
    placed its own first words on. `reconcile_seams` then resolves the overlap
    the wrong way round: it pushes the next line's correctly placed opening
    words forward onto the stray one, and they come out zero-length ("seam
    piles": 126 lines in 63 of 318 documents, none on the whole-audio path).

    Here the stray words yield instead. A window's trailing words that START
    both at or past its `owned_end_ms` (the next window's first stamp) and
    after the next window's first word — in the neighbour's audio AND in the
    neighbour's way — are pulled back to end where that first word starts, one
    frame each, never before the start of the window's last remaining word (or
    its slice start). With no room left they collapse at the end of their OWN
    line, which is where an unplaceable word belongs rather than at the start of
    the next. Start, not centre, decides: a word that merely runs past the seam
    is the bleed the pad exists for, and the clamp still handles it. Pad words
    that sit BEFORE the next window's first word are left alone: they push
    nothing.

    Each dict must carry the `"window"` index `_align_windows` tags it with.
    Returns the new list and how many words were moved; an untouched input
    comes back element-for-element identical.
    """
    if not results:
        return results, 0
    try:
        tags = [int(r["window"]) for r in results]
    except KeyError as exc:
        raise ValueError("yield_pad_words needs every result tagged with its window") from exc
    out = list(results)
    moved = 0
    for k in range(1, len(out)):
        j = tags[k - 1]
        if tags[k] == j:
            continue  # not a seam
        owned_end_ms = windows[j].owned_end_ms
        if owned_end_ms is None:
            continue
        owned_end = owned_end_ms / 1000
        next_start = float(out[k]["start"])
        # The trailing run of window j that sits in its pad AND past next_start.
        first = k
        while first - 1 >= 0 and tags[first - 1] == j:
            start = float(out[first - 1]["start"])
            if start < owned_end or start <= next_start:
                break
            first -= 1
        if first == k:
            continue  # nothing in the pad is in the next window's way
        if first - 1 >= 0 and tags[first - 1] == j:
            floor = float(out[first - 1]["start"])
            if floor > next_start:
                continue  # the window's own span overlaps the next: the clamp's case
        else:
            floor = windows[j].slice_start_ms / 1000
        end = next_start
        for i in range(k - 1, first - 1, -1):
            start = max(end - FRAME_S, floor)
            end = max(end, floor)
            out[i] = {**out[i], "start": start, "end": end}
            end = start
            moved += 1
    return out, moved


def reconcile_seams(results: list[dict]) -> list[dict]:
    """Global monotonicity across window seams, in the aligner's result-dict
    domain (seconds). Overlapping pads mean a window's first words can land
    slightly before the previous window's last ones; clamp starts forward and
    keep ends >= starts. regroup's own neighbour-clipping handles the rest."""
    out: list[dict] = []
    cursor = 0.0
    for r in results:
        start = max(float(r["start"]), cursor)
        end = max(float(r["end"]), start)
        if start != r["start"] or end != r["end"]:
            r = {**r, "start": start, "end": end}
        out.append(r)
        cursor = start
    return out


def _start_ms(row: dict) -> int:
    return round(float(row["start"]) * 1000)


def _is_word(row: dict) -> bool:
    text = str(row.get("text", ""))
    return text != "<star>" and bool(text.strip())


def _held(row: dict, voted_end_s: float) -> float:
    """Seconds a vote cuts off a word's end (2.29.1): ``row``'s own end, plus
    what an earlier vote already cut, minus the voted end; never negative.

    `segment` stars (and the whole track, starred the same way) put a star
    before every word, and the star swallows the silence between words: a word
    they place keeps little more than its CTC peak (2.29 shipped 7.6 % of the
    pilot set's words at 0 ms). The voted end stays the row's end, because line
    QA reads ends as bounds and was tuned on these; the cut travels as
    ``"held"`` and comes back after line QA (`line_qa.restore_held_ends`).
    Giving it back here instead (measured 2026-11-07) cost line QA 13 of its 17
    nudges on the pilot set."""
    return max(0.0, float(row["end"]) + float(row.get("held", 0.0)) - voted_end_s)


def hybrid_star_words(
    edges: list[dict], segment: list[dict], t_ms: int, edges_lead_ms: int = 0
) -> tuple[list[dict], bool]:
    """Per word, the `segment`-starred time where it agrees with `edges` (Faz 9 round 5).

    One window slice, aligned twice over the same emissions: `edges` (a star at
    each slice edge) and `segment` (a star before every word). `segment` places
    a word it finds more precisely but nearly doubles the gross errors, so it
    only wins where the two agree: within `t_ms` of each other, inclusive, in
    whole milliseconds. An `edges` word lands `edges_lead_ms` EARLIER than the
    same model places it with `segment` stars or over the whole track (round 5
    mechanism check), so the two are compared after that lead.

    Every row comes back on the EDGES clock — a chosen `segment` row moved back
    by the lead — so seams and regrouping downstream compare like with like and
    the caller's path-wide lead nets the move out. Timings come from the
    winning pass, with what it cut off the `edges` end kept as `"held"`
    (2.29.1, `_held`) and the `edges` start as `"edges_start"` (2.30,
    `unpile_starts`); the score stays the `edges` one, because word probs feed
    line QA and the quality gate, and those were calibrated on `edges`. Rows
    are tagged `"star"`: "segment" or "edges".

    When the two passes disagree on the word sequence (they cannot, given one
    text: this is the guard for a library change) the whole window keeps
    `edges` and the second value is False.
    """
    tagged_edges = [{**row, "star": "edges"} for row in edges]
    if len(edges) != len(segment) or any(
        str(e.get("text")) != str(s.get("text")) for e, s in zip(edges, segment, strict=True)
    ):
        return tagged_edges, False
    back_s = edges_lead_ms / 1000
    out: list[dict] = []
    for edge, seg, tagged in zip(edges, segment, tagged_edges, strict=True):
        if _is_word(edge) and abs(_start_ms(seg) - (_start_ms(edge) + edges_lead_ms)) <= t_ms:
            out.append({
                **tagged,
                "start": float(seg["start"]) - back_s,
                "end": float(seg["end"]) - back_s,
                "held": _held(edge, float(seg["end"]) - back_s),
                "edges_start": float(edge["start"]),
                "star": "segment",
            })
        else:
            out.append(tagged)
    return out, True


def whole_track_vote(
    results: list[dict], whole: list[dict], t_ms: int, edges_lead_ms: int = 0,
    *, keep_order: bool = True,
) -> tuple[list[dict], int, bool]:
    """A third vote from the whole track (Faz 9 round 5, "Third vote").

    `results` is the windowed word stream on the EDGES clock (hybrid_star_words);
    `whole` is the same lyric aligned over the whole track, which sits
    `edges_lead_ms` later on the same sounds. A word takes the whole-track
    time when it lies within `t_ms` (inclusive, whole ms) of its windowed time
    plus that lead; the taken time is moved back by the lead, onto the edges
    clock like every other row, and tagged `"star": "whole"`. The score stays
    the windowed one; the windowed start stays as `"edges_start"` unless the
    hybrid already kept an earlier one.

    Words are matched by position among WORD rows only (blank segments differ:
    the whole track joins every line, windows join only their own). A
    different word count or text means the two streams cannot be paired, and
    nothing changes. Returns (rows, words taken, paired).

    `keep_order` (2.32.1): a word is refused the whole-track time when that
    time would start it at or after the next word, or at or before the
    previous word, where that neighbour keeps its windowed time, or would
    bring it under VOTE_MIN_GAP_MS of a neighbour the windows kept at least
    that far away; refusals repeat until none is left (`_order_refusals`).
    Where the whole-track pass drifts late over a phrase, a 1 200 ms vote
    takes some of its words and not the next ones, and the seam pass after
    the vote clamped the kept words onto the voted one: 229 of the 274 words
    in new 0 ms runs on the 2.32.0 archive (vote-pile-guard, 2026-11-14).
    False reproduces 2.32.0.
    """
    mine = [i for i, row in enumerate(results) if _is_word(row)]
    theirs = [row for row in whole if _is_word(row)]
    if len(mine) != len(theirs) or any(
        str(results[i].get("text")) != str(w.get("text")) for i, w in zip(mine, theirs, strict=True)
    ):
        return results, 0, False
    back_s = edges_lead_ms / 1000
    voted = [
        abs(_start_ms(w) - (_start_ms(results[i]) + edges_lead_ms)) <= t_ms
        for i, w in zip(mine, theirs, strict=True)
    ]
    if keep_order:
        voted = _order_refusals(
            voted,
            [_start_ms(results[i]) for i in mine],
            [round((float(w["start"]) - back_s) * 1000) for w in theirs],
        )
    out = list(results)
    taken = 0
    for i, w, vote in zip(mine, theirs, voted, strict=True):
        row = out[i]
        if vote:
            out[i] = {
                **row,
                "start": float(w["start"]) - back_s,
                "end": float(w["end"]) - back_s,
                "held": _held(row, float(w["end"]) - back_s),
                "edges_start": float(row.get("edges_start", row["start"])),
                "star": "whole",
            }
            taken += 1
    return out, taken, True


def _order_refusals(
    voted: list[bool], kept_ms: list[int], taken_ms: list[int], min_gap_ms: int = VOTE_MIN_GAP_MS
) -> list[bool]:
    """The vote's order and spacing guard (2.32.1): `voted` with refusals, pair
    by pair of adjacent words, each at its taken start (`taken_ms`) if voted
    and its windowed start (`kept_ms`) if not:

    - order: a voted word that would start at or after its kept next word, or
      at or before its kept previous one, is refused;
    - spacing: two words that would start under `min_gap_ms` apart while their
      windowed starts are at least that far apart refuse their voted
      member(s) — the whole-track pass squeezing a pair the windows kept apart.

    A refusal changes the pairs around it, so the check repeats until nothing
    changes. Two voted words are never out of order: the whole-track pass is
    one path, its starts never go backwards."""
    voted = list(voted)
    changed = True
    while changed:
        changed = False
        for n in range(len(voted) - 1):
            a, b = voted[n], voted[n + 1]
            if not (a or b):
                continue
            gap = (taken_ms[n + 1] if b else kept_ms[n + 1]) - (taken_ms[n] if a else kept_ms[n])
            out_of_order = a != b and gap <= 0
            squeezed = gap < min_gap_ms <= kept_ms[n + 1] - kept_ms[n]
            if out_of_order or squeezed:
                voted[n] = voted[n + 1] = False
                changed = True
    return voted


def unpile_starts(
    results: list[dict], min_words: int = PILE_MIN_WORDS, span_ms: int = PILE_SPAN_MS
) -> tuple[list[dict], int, int]:
    """Timing v3, V3 (2.30.0): a piled run of starts falls back to the `edges` clock.

    `segment` stars (and the whole track, starred the same way) put a star
    before every word; where a phrase's letters smear, the stars swallow the
    gaps and several words land on one CTC peak. The hybrid and the vote take
    those times because they sit within `T` of the `edges` ones, so a pile the
    `edges` pass never made ships (2.29: 47 runs of 3+ on the pilot set, 2.28:
    18; eng20 14 against `edges`' 3). Here, `min_words` or more consecutive
    words starting within `span_ms` of the run's first (inclusive, whole ms:
    `benchmarks.metrics.duration_hygiene`'s rule) take their `"edges_start"`
    when any of them carries one; a run of `edges` words is `edges`' own and
    stays. The words after the run that those starts overtake follow it onto
    the `edges` clock while they carry an `"edges_start"`: the word after a
    pile is often squeezed with it (CEO "I run it like a CEO", 2026-11-09: the
    run's `edges` starts were clamped to a squeezed "a" and piled again).
    Starts stay monotone: each lies between the previous word's start and the
    start of the first word that is not overtaken. A block that would come out,
    with its two neighbours, with as many piled words as it had stays as it
    was: `edges` piles there too, or a clamp would pile it onto a neighbour
    (Crazy In Love 1:14.9). Ends stay where the vote put them (line QA reads them, 2.29.1) unless
    a start passes one: the end follows the start and `"held"` shrinks by what
    that took. Rows moved are tagged `"star": "edges"`. Returns (rows, runs
    moved, words moved)."""
    words = [i for i, r in enumerate(results) if _is_word(r)]
    out = list(results)
    runs = moved = 0
    j = 0
    while j < len(words):
        k = j
        first_ms = _start_ms(out[words[j]])
        while k + 1 < len(words) and _start_ms(out[words[k + 1]]) - first_ms <= span_ms:
            k += 1
        run = words[j : k + 1]
        if len(run) < min_words or not any("edges_start" in out[i] for i in run):
            j = k + 1
            continue
        cursor = float(out[words[j - 1]]["start"]) if j > 0 else 0.0
        starts: list[float] = []
        m = j
        while m < len(words):
            row = out[words[m]]
            if m > k and ("edges_start" not in row or float(row["start"]) >= cursor):
                break  # not overtaken, or `edges` placed it: the ceiling
            cursor = max(float(row.get("edges_start", row["start"])), cursor)
            starts.append(cursor)
            m += 1
        if m < len(words):
            ceiling = float(out[words[m]]["start"])
            starts = [min(s, ceiling) for s in starts]
        block = words[j:m]
        # The neighbours count too: a start clamped onto either one piles with it.
        edge_left = [_start_ms(out[words[j - 1]])] if j > 0 else []
        edge_right = [_start_ms(out[words[m]])] if m < len(words) else []
        before = edge_left + [_start_ms(out[i]) for i in block] + edge_right
        after = edge_left + [round(s * 1000) for s in starts] + edge_right
        if _piled_words(after, min_words, span_ms) >= _piled_words(before, min_words, span_ms):
            j = k + 1
            continue
        runs += 1
        for i, start in zip(block, starts, strict=True):
            row = out[i]
            if start == float(row["start"]):
                continue
            end = max(float(row["end"]), start)
            held = max(0.0, float(row.get("held", 0.0)) - (end - float(row["end"])))
            out[i] = {**row, "start": start, "end": end, "held": held, "star": "edges"}
            moved += 1
        j = m
    return out, runs, moved


def _piled_words(starts_ms: list[int], min_words: int, span_ms: int) -> int:
    """Words in runs of `min_words` or more starting within `span_ms` of the run's first."""
    piled = j = 0
    while j < len(starts_ms):
        k = j
        while k + 1 < len(starts_ms) and starts_ms[k + 1] - starts_ms[j] <= span_ms:
            k += 1
        if k - j + 1 >= min_words:
            piled += k - j + 1
        j = k + 1
    return piled
