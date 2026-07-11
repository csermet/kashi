"""pipeline.windows is pure — unit tests only (plan P3 case list)."""

import pytest

from kashi_server.pipeline.windows import (
    MIN_STAMPED_LINES,
    Window,
    plan_windows,
    reconcile_seams,
)

# Four stamped lines, 10s apart, wordy enough to stand alone.
TEXTS = [
    "one two three four",
    "five six seven eight",
    "nine ten eleven",
    "twelve thirteen fourteen",
]
STARTS: list[int | None] = [10_000, 20_000, 30_000, 40_000]
TOTAL = 50_000


def _owned(windows):
    return [i for w in windows for i in w.line_indices]


def test_each_long_line_gets_its_own_padded_window():
    windows = plan_windows(TEXTS, STARTS, TOTAL)
    assert windows is not None and len(windows) == 4
    first = windows[0]
    assert first.line_indices == [0]
    assert first.slice_start_ms == 10_000 - 350  # pad
    assert first.slice_end_ms == 20_000 + 350  # next stamp + pad
    assert windows[-1].slice_end_ms == TOTAL  # clamped, last spans to total
    assert _owned(windows) == [0, 1, 2, 3]


def test_short_lines_merge_until_window_is_viable():
    texts = ["a b c", "d e f", "g h i", "j k l"]
    starts: list[int | None] = [1_000, 2_000, 3_000, 10_000]
    # 1s spans are under MIN_WINDOW_MS -> first three merge; trailing group
    # [10s, 20s] is long enough alone.
    windows = plan_windows(texts, starts, 20_000)
    assert windows is not None
    assert windows[0].line_indices == [0, 1, 2]
    assert windows[1].line_indices == [3]
    assert _owned(windows) == [0, 1, 2, 3]


def test_trailing_short_window_merges_backwards():
    texts = ["one two three four", "five six seven eight", "nine ten eleven", "la"]
    starts: list[int | None] = [10_000, 20_000, 30_000, 39_500]
    # last span 39.5-40s = 500ms, one word -> merges into the previous window
    windows = plan_windows(texts, starts, 40_000)
    assert windows is not None
    assert windows[-1].line_indices[-2:] == [2, 3]
    assert _owned(windows) == [0, 1, 2, 3]


def test_stampless_line_rides_with_its_predecessor():
    texts = TEXTS + ["fifteen sixteen seventeen"]
    starts: list[int | None] = [10_000, None, 30_000, 40_000, 45_000]  # 4 stamped
    windows = plan_windows(texts, starts, TOTAL)
    assert windows is not None
    holder = next(w for w in windows if 1 in w.line_indices)
    assert 0 in holder.line_indices  # attached to the stamped line before it
    assert _owned(windows) == [0, 1, 2, 3, 4]


def test_stampless_leader_rides_with_first_group():
    starts: list[int | None] = [None, 20_000, 30_000, 40_000]
    texts = TEXTS
    # 3 stamped < MIN_STAMPED_LINES -> None; use 5 lines to stay above it
    texts = texts + ["fourteen fifteen"]
    starts = starts + [45_000]
    windows = plan_windows(texts, starts, TOTAL)
    assert windows is not None
    assert windows[0].line_indices[0] == 0
    assert _owned(windows) == [0, 1, 2, 3, 4]


def test_too_few_stamps_returns_none():
    starts: list[int | None] = [10_000, None, None, 40_000]
    assert plan_windows(TEXTS, starts, TOTAL) is None  # 2 < MIN_STAMPED_LINES
    assert MIN_STAMPED_LINES > 2


def test_low_stamp_fraction_returns_none():
    texts = ["w"] * 10
    starts: list[int | None] = [1_000 * i if i < 5 else None for i in range(10)]
    # 5/10 stamped < 0.8 fraction
    assert plan_windows(texts, starts, 60_000) is None


def test_non_monotonic_stamps_return_none():
    starts: list[int | None] = [10_000, 30_000, 20_000, 40_000]
    assert plan_windows(TEXTS, starts, TOTAL) is None


def test_length_mismatch_and_degenerate_inputs_return_none():
    assert plan_windows(TEXTS, [10_000, 20_000], TOTAL) is None
    assert plan_windows([], [], TOTAL) is None
    assert plan_windows(TEXTS, STARTS, 0) is None


def test_window_validation_rejects_empty():
    with pytest.raises(ValueError):
        Window(slice_start_ms=100, slice_end_ms=100, line_indices=[0])
    with pytest.raises(ValueError):
        Window(slice_start_ms=0, slice_end_ms=100, line_indices=[])


def test_reconcile_seams_clamps_backwards_starts():
    results = [
        {"start": 1.0, "end": 2.0, "text": "a"},
        {"start": 1.8, "end": 2.5, "text": "b"},  # pad bleed: fine, monotone
        {"start": 1.2, "end": 1.4, "text": "c"},  # seam violation: before prev
    ]
    fixed = reconcile_seams(results)
    assert [r["start"] for r in fixed] == [1.0, 1.8, 1.8]
    assert fixed[2]["end"] >= fixed[2]["start"]
    # untouched entries keep their identity (no needless copies)
    assert fixed[0] is results[0]
