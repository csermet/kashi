"""Per-line soft-offset EXPERIMENT (Faz 6 P2) — bench-only, never imported
by production code.

Hypothesis: on the windowed path a large share of word-start error is
line-common drift (the whole line sits early/late relative to its lrclib
anchor while intra-line word spacing is roughly right). Production line_qa
only HARD-snaps lines whose start drifts >2.5s and leaves everything else
untouched; a smoothed per-line offset applied to every line (and its words)
could recover the sub-threshold drift — or, with noisy anchors, inject
crowd-stamp jitter straight into word timings. This module exists to
measure exactly that trade-off against Jamendo word-level ground truth.

Both modes keep lines RIGID (every word in a line shifts by the same
amount — no intra-line stretching) and differ only in how the per-line
offset is derived from the anchor deltas:

- "median": each anchored line takes the median-filtered delta of its own
  neighborhood (window MEDIAN_WINDOW); anchor-less lines borrow the
  nearest anchored knot. A step function over lines.
- "pl": piecewise-linear interpolation of the filtered (time, delta)
  knots evaluated at each line start; flat extrapolation at the edges.
  Smoother between sparse anchors.
"""

from dataclasses import replace

from kashi_server.pipeline.alignment import AlignResult

MEDIAN_WINDOW = 5  # odd; centered on the line, clipped at the edges
MIN_KNOTS = 3  # fewer anchors than this → not enough signal, no-op

MODES = ("soft-median", "soft-pl")


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def _median_filter(deltas: list[int], window: int) -> list[int]:
    half = window // 2
    return [
        _median(deltas[max(0, i - half) : i + half + 1]) for i in range(len(deltas))
    ]


def _interp(knots: list[tuple[int, int]], t: int) -> int:
    """Piecewise-linear evaluation with flat extrapolation."""
    if t <= knots[0][0]:
        return knots[0][1]
    if t >= knots[-1][0]:
        return knots[-1][1]
    for (t0, d0), (t1, d1) in zip(knots, knots[1:], strict=False):
        if t0 <= t <= t1:
            if t1 == t0:
                return d0
            return round(d0 + (d1 - d0) * (t - t0) / (t1 - t0))
    return knots[-1][1]  # unreachable; defensive


def line_offsets(
    result: AlignResult,
    anchors: list[int | None],
    *,
    mode: str,
    median_window: int = MEDIAN_WINDOW,
) -> list[int] | None:
    """Per-line shift (ms) for every line, or None when the experiment
    should no-op (too few anchors)."""
    if len(anchors) != len(result.lines):
        return None
    knot_index: list[int] = [
        i for i, anchor in enumerate(anchors) if anchor is not None
    ]
    if len(knot_index) < MIN_KNOTS:
        return None
    raw = [anchors[i] - result.lines[i].start_ms for i in knot_index]  # type: ignore[operator]
    filtered = _median_filter(raw, median_window)

    if mode == "soft-median":
        # Step function: anchored lines take their own filtered delta,
        # anchor-less lines borrow the nearest anchored neighbour's.
        offsets = []
        for i in range(len(result.lines)):
            nearest = min(range(len(knot_index)), key=lambda k: abs(knot_index[k] - i))
            offsets.append(filtered[nearest])
        return offsets
    if mode == "soft-pl":
        knots = [
            (result.lines[i].start_ms, filtered[k]) for k, i in enumerate(knot_index)
        ]
        return [_interp(knots, line.start_ms) for line in result.lines]
    raise ValueError(f"unknown soft-offset mode {mode!r}")


def apply_soft_offset(
    result: AlignResult,
    anchors: list[int | None],
    *,
    mode: str,
    median_window: int = MEDIAN_WINDOW,
) -> AlignResult:
    """Shift every line (and its words) by its smoothed anchor delta.

    Lines stay rigid; a light monotonic clamp keeps successive line starts
    non-decreasing after the shift (crossed lines would be render nonsense).
    quality_score/windowed are left untouched — this is a measurement
    transform, not provenance.
    """
    offsets = line_offsets(result, anchors, mode=mode, median_window=median_window)
    if offsets is None:
        return result

    new_lines = []
    new_words = []
    prev_start = None
    for line, words, offset in zip(
        result.lines, result.words_per_line, offsets, strict=True
    ):
        start = max(0, line.start_ms + offset)
        if prev_start is not None and start < prev_start:
            start = prev_start  # clamp: no line-order inversions
        shift = start - line.start_ms  # actual shift after clamping
        prev_start = start
        new_lines.append(
            replace(line, start_ms=start, end_ms=max(start, line.end_ms + shift))
        )
        new_words.append(
            [
                replace(
                    w,
                    start_ms=max(0, w.start_ms + shift),
                    end_ms=max(0, w.end_ms + shift),
                )
                for w in words
            ]
        )
    return replace(result, lines=new_lines, words_per_line=new_words)
