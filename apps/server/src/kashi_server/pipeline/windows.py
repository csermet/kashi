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

    def __post_init__(self):
        if self.slice_end_ms <= self.slice_start_ms:
            raise ValueError("empty window slice")
        if not self.line_indices:
            raise ValueError("window owns no lines")


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
    if len(line_texts) != len(synced_starts_ms) or not line_texts or total_ms <= 0:
        return None
    stamped = [start for start in synced_starts_ms if start is not None]
    if len(stamped) < MIN_STAMPED_LINES:
        return None
    if len(stamped) / len(line_texts) < MIN_STAMPED_FRACTION:
        return None
    if any(b < a for a, b in zip(stamped, stamped[1:], strict=False)):
        return None  # non-monotonic stamps: don't trust them as anchors

    # Nominal (unpadded) span per line: [own stamp, next stamped line's stamp).
    # A stampless line inherits its predecessor's span end and stretches it.
    spans: list[tuple[int, int]] = []
    for i in range(len(line_texts)):
        start = synced_starts_ms[i]
        if start is None:
            continue  # attached to a neighbour below
        end = total_ms
        for j in range(i + 1, len(line_texts)):
            nxt = synced_starts_ms[j]
            if nxt is not None:
                end = nxt
                break
        spans.append((start, min(end, total_ms)))

    # Group lines: a group = one stamped line plus any stampless followers;
    # stampless LEADERS (before the first stamp) ride with the first group.
    groups: list[list[int]] = []
    leaders: list[int] = []
    for i in range(len(line_texts)):
        if synced_starts_ms[i] is None:
            (groups[-1] if groups else leaders).append(i)
        else:
            groups.append([i])
    if leaders:
        groups[0] = leaders + groups[0]

    # Merge consecutive groups until each window is long enough AND wordy
    # enough. Merging is greedy left-to-right; a trailing short window merges
    # backwards into the previous one.
    windows: list[tuple[int, int, list[int]]] = []  # (start, end, lines)
    current_lines: list[int] = []
    current_start: int | None = None
    current_end = 0
    span_index = 0
    for group in groups:
        start, end = spans[span_index]  # one span per stamped line = per group
        span_index += 1
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

    try:
        out = [
            Window(
                slice_start_ms=max(0, start - pad_ms),
                slice_end_ms=min(total_ms, end + pad_ms),
                line_indices=lines,
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
    if owned != list(range(len(line_texts))):
        return None
    return out


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
