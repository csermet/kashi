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


@pytest.mark.parametrize("off", [19_000, -19_000])
@pytest.mark.parametrize(("past", "carried"), [(1, True), (0, False), (-1000, False)])
def test_a_neighbour_the_move_overtakes_moves_with_it(off, past, carried):
    # 2.30.1 (GOSSIP's line 0): the sheet sits `off` ms late (early) and the
    # ear hears every line but the first (last), whose stamp lies `past` ms
    # beyond where its neighbour moves to. A stamp the move overtakes moves
    # along, so the windows get stamps in order; one equal to the moved stamp
    # is in order and stays.
    lone, edge = (0, 1) if off > 0 else (5, 4)
    starts: list[int | None] = [t - EAR_LAG_MS + off for t in SUNG]
    starts[lone] = SUNG[edge] - EAR_LAG_MS + (past if off > 0 else -past)
    heard = [tok for tok in _ear(SUNG) if not SUNG[lone] <= tok["start_ms"] < SUNG[lone] + 9000]
    out, applied = shift_sheet(TEXTS, starts, heard)
    assert [(s.median_ms, s.carried) for s in applied] == [(-off, int(carried))]
    assert out[lone] == (starts[lone] - off if carried else starts[lone])
    assert [s for i, s in enumerate(out) if i != lone] == \
        [t - EAR_LAG_MS for i, t in enumerate(SUNG) if i != lone]


def test_a_carry_never_moves_a_line_with_its_own_anchors():
    # Lines 0-2 are heard on their stamps (a section that stays); lines 3-5
    # are heard 20 s before theirs, line 3 even before line 2's stamp. The
    # ears disagree; carrying would move line 2 against its own anchor and
    # then 1 and 0 with it. The stamps stay out of order (whole-audio path).
    heard = [10_000, 20_000, 30_000, 25_000, 35_000, 45_000]
    starts: list[int | None] = [t - EAR_LAG_MS + (20_000 if i >= 3 else 0)
                                for i, t in enumerate(heard)]
    out, applied = shift_sheet(TEXTS, starts, _ear(heard))
    assert [(s.first_line, s.median_ms, s.carried) for s in applied] == [(3, -20_000, 0)]
    assert out[:3] == starts[:3]


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
