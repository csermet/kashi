"""2.29.1: a shifted lrclib section moves before alignment (pipeline/sheet_shift.py)."""

import pytest

from kashi_server.pipeline.sheet_shift import (
    EAR_LAG_MS,
    SECTION_TOL_MS,
    SHIFT_MS,
    Anchor,
    sections,
    shift_sheet,
)

TEXTS = [
    "walking through the silver morning light",
    "every window holds a different story",
    "carry me across the frozen river",
    "nobody remembers how the garden grew",
    "paper lanterns drifting over harbour",
    "whisper softly underneath the thunder",
]


def _ear(heard_ms: list[int]) -> list[dict]:
    """One token per letter, 60 ms apart, each line from its heard time."""
    tokens = []
    for text, t0 in zip(TEXTS, heard_ms, strict=True):
        for k, ch in enumerate(text.replace(" ", "")):
            tokens.append({"text": ch, "start_ms": t0 + 60 * k})
    return tokens


SUNG = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000]


def test_a_healthy_sheet_stays():
    starts = [t - EAR_LAG_MS for t in SUNG]
    out, applied = shift_sheet(TEXTS, starts, _ear(SUNG))
    assert (out, applied) == (starts, [])


@pytest.mark.parametrize(("late", "moves"), [(SHIFT_MS, False), (SHIFT_MS + 1, True)])
def test_a_whole_sheet_moves_only_past_the_threshold(late, moves):
    starts = [t - EAR_LAG_MS + late for t in SUNG]
    out, applied = shift_sheet(TEXTS, starts, _ear(SUNG))
    assert bool(applied) is moves
    assert out == ([t - EAR_LAG_MS for t in SUNG] if moves else starts)


def test_only_the_shifted_section_moves_and_none_stamps_stay_none():
    # Lines 0-2 sit 19 s late on the sheet, 3-5 are right: only 0-2 move.
    starts: list[int | None] = [t - EAR_LAG_MS + (19_000 if i < 3 else 0)
                                for i, t in enumerate(SUNG)]
    starts[1] = None
    out, applied = shift_sheet(TEXTS, starts, _ear(SUNG))
    assert [(s.first_line, s.last_line, s.median_ms) for s in applied] == [(0, 2, -19_000)]
    assert out == [SUNG[0] - EAR_LAG_MS, None, SUNG[2] - EAR_LAG_MS, *starts[3:]]


def test_unplaceable_lines_give_no_anchor():
    # An ear that heard nothing moves nothing, however late the sheet says.
    starts = [t + 30_000 for t in SUNG]
    assert shift_sheet(TEXTS, starts, []) == (starts, [])


@pytest.mark.parametrize(("gap", "joined"), [(SECTION_TOL_MS, True), (SECTION_TOL_MS + 1, False)])
def test_two_anchors_open_a_section_within_the_tolerance(gap, joined):
    secs = sections([Anchor(0, -5000), Anchor(1, -5000 + gap)])
    assert [(s.first_line, s.last_line) for s in secs] == ([(0, 1)] if joined else [])


@pytest.mark.parametrize(("gap", "joined"), [(SECTION_TOL_MS, True), (SECTION_TOL_MS + 1, False)])
def test_a_section_takes_the_next_anchor_within_the_tolerance(gap, joined):
    secs = sections([Anchor(0, -5000), Anchor(1, -5000), Anchor(2, -5000 + gap)])
    assert [(s.first_line, s.last_line) for s in secs] == [(0, 2 if joined else 1)]


def test_a_lone_anchor_is_never_a_section():
    assert sections([Anchor(0, -9000), Anchor(1, 0), Anchor(2, 9000)]) == []
