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
    winning pass; the score stays the `edges` one, because word probs feed line
    QA and the quality gate, and those were calibrated on `edges`. Rows are
    tagged `"star"`: "segment" or "edges".

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
                "star": "segment",
            })
        else:
            out.append(tagged)
    return out, True


def whole_track_vote(
    results: list[dict], whole: list[dict], t_ms: int, edges_lead_ms: int = 0
) -> tuple[list[dict], int, bool]:
    """A third vote from the whole track (Faz 9 round 5, "Third vote").

    `results` is the windowed word stream on the EDGES clock (hybrid_star_words);
    `whole` is the same lyric aligned over the whole track, which sits
    `edges_lead_ms` later on the same sounds. A word takes the whole-track
    time when it lies within `t_ms` (inclusive, whole ms) of its windowed time
    plus that lead; the taken time is moved back by the lead, onto the edges
    clock like every other row, and tagged `"star": "whole"`. The score stays
    the windowed one.

    Words are matched by position among WORD rows only (blank segments differ:
    the whole track joins every line, windows join only their own). A
    different word count or text means the two streams cannot be paired, and
    nothing changes. Returns (rows, words taken, paired).
    """
    mine = [i for i, row in enumerate(results) if _is_word(row)]
    theirs = [row for row in whole if _is_word(row)]
    if len(mine) != len(theirs) or any(
        str(results[i].get("text")) != str(w.get("text")) for i, w in zip(mine, theirs, strict=True)
    ):
        return results, 0, False
    back_s = edges_lead_ms / 1000
    out = list(results)
    taken = 0
    for i, w in zip(mine, theirs, strict=True):
        row = out[i]
        if abs(_start_ms(w) - (_start_ms(row) + edges_lead_ms)) <= t_ms:
            out[i] = {
                **row,
                "start": float(w["start"]) - back_s,
                "end": float(w["end"]) - back_s,
                "star": "whole",
            }
            taken += 1
    return out, taken, True
