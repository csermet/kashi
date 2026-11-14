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
    # along, so the windows get stamps in order (`carried`); one equal to the
    # moved stamp is in order and was left at the old clock until 2.32.1,
    # which adopts it by position with the same offset (`adopted`).
    lone, edge = (0, 1) if off > 0 else (5, 4)
    starts: list[int | None] = [t - EAR_LAG_MS + off for t in SUNG]
    starts[lone] = SUNG[edge] - EAR_LAG_MS + (past if off > 0 else -past)
    heard = [tok for tok in _ear(SUNG) if not SUNG[lone] <= tok["start_ms"] < SUNG[lone] + 9000]
    out, applied = shift_sheet(TEXTS, starts, heard)
    assert [(s.median_ms, s.carried, s.adopted) for s in applied] == \
        [(-off, int(carried), int(not carried))]
    assert out[lone] == max(0, starts[lone] - off)
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


SHORT = ["ah ah ah", "oh oh oh", "na na na", "la la la"]  # under MIN_LETTERS: never anchors


def _sheet(
    lines: list[tuple[str, int, int | None]],
) -> tuple[list[str], list[int | None], list[dict]]:
    """(texts, stamps, ear) from (text, sung ms, stamp): the ear hears every
    long line where it is sung, one token a letter 60 ms apart; short lines
    are not heard (the Toz Pembe "Ah-ah" lines: vocalisations)."""
    texts = [text for text, _, _ in lines]
    starts = [stamp for _, _, stamp in lines]
    tokens = [{"text": ch, "start_ms": sung + 60 * k}
              for text, sung, _ in lines if text not in SHORT
              for k, ch in enumerate(text.replace(" ", ""))]
    return texts, starts, sorted(tokens, key=lambda t: t["start_ms"])


@pytest.mark.parametrize("early", [19_335, -19_335])
def test_lines_left_before_and_after_a_moved_section_take_its_offset(early):
    # 2.32.1. Toz Pembe (sheet 19.3 s early, section moved later): the two
    # opening interjections sit before the section and were never carried.
    # GOSSIP (sheet late, moved earlier): the repeats after it stayed late.
    # Both sides go with the section now, the song start clamping at 0.
    sung = [20_000, 23_000, *(30_000 + 10_000 * k for k in range(6)), 95_000, 98_000]
    texts = [SHORT[0], SHORT[1], *TEXTS, SHORT[2], SHORT[3]]
    lines = [(t, s, s - EAR_LAG_MS - early) for t, s in zip(texts, sung, strict=True)]
    texts, starts, ear = _sheet(lines)
    out, applied = shift_sheet(texts, starts, ear)
    assert [(s.first_line, s.last_line, s.median_ms) for s in applied] == [(2, 7, early)]
    assert out == [max(0, s - EAR_LAG_MS) for s in sung]
    # The move overtakes the two lines on one side (carried since 2.30.1); the
    # two on the other side were left at the old clock until 2.32.1.
    assert (applied[0].carried, applied[0].adopted) == (2, 2)


def test_a_lone_anchor_heard_on_its_stamp_keeps_the_lines_before_it():
    # A unique line the ear places on its own stamp (a lone anchor: no second
    # one agrees with it) says the sheet is right there: it and the lines
    # before it stay; the short line between it and the section still goes.
    sung = [5_000, 10_000, 14_000, 20_000, 30_000, 40_000, 50_000, 60_000]
    texts = [SHORT[0], TEXTS[0], SHORT[1], *TEXTS[1:6]]
    stamps = [sung[0] - EAR_LAG_MS, sung[1] - EAR_LAG_MS,
              *(s - EAR_LAG_MS - 8_000 for s in sung[2:])]
    texts, starts, ear = _sheet(list(zip(texts, sung, stamps, strict=True)))
    out, applied = shift_sheet(texts, starts, ear)
    assert [(s.first_line, s.median_ms, s.adopted) for s in applied] == [(3, 8_000, 1)]
    assert out == [*starts[:2], *(s - EAR_LAG_MS for s in sung[2:])]


LONG3 = ["silver mountains echo every evening",
         "golden rivers wander through the valley",
         "crimson feathers scatter on the meadow"]


@pytest.mark.parametrize(("middle", "goes"), [(30_000, False), (30_001, True)])
def test_lines_between_two_sections_go_with_the_nearer_edge_stamp(middle, goes):
    # Lines 0-2 are heard on their stamps (a section that stays); lines 6-8
    # are 2 s late on the sheet (a section moved earlier). Lines 3-5 have no
    # anchor: each goes with the section whose edge stamp is nearer on the
    # sheet (line 2 at 20 s, line 6 at 40 s), a tie to the left. Line 4 sits
    # exactly halfway or 1 ms past it; every split keeps the stamps in order.
    texts = [*TEXTS[:3], *SHORT[:3], *LONG3]
    sheet = [5_000, 12_000, 20_000, 25_000, middle, 35_000, 40_000, 50_000, 60_000]
    heard = [*sheet[:6], *(s - 2_000 for s in sheet[6:])]
    lines = [(t, h + EAR_LAG_MS, s) for t, h, s in zip(texts, heard, sheet, strict=True)]
    texts, starts, ear = _sheet(lines)
    out, applied = shift_sheet(texts, starts, ear)
    assert [(s.first_line, s.last_line, s.median_ms, s.adopted) for s in applied] == \
        [(6, 8, -2_000, 2 if goes else 1)]
    assert out == [*sheet[:4], middle - 2_000 if goes else middle, *(s - 2_000 for s in sheet[5:])]


def test_a_split_that_would_put_stamps_out_of_order_is_not_taken():
    # Lines 0-2 move 10 s later, lines 4-6 10 s earlier; line 3 (35 s) sits
    # between their new stamps (30 s, 40 s) and halfway between their old ones.
    # Either side's offset would put it past the other side's edge, so it
    # keeps its stamp rather than break the order 2.32.0 leaves.
    texts = [*TEXTS[:3], SHORT[0], *LONG3]
    sheet = [0, 10_000, 20_000, 35_000, 50_000, 60_000, 70_000]
    heard = [10_000, 20_000, 30_000, 35_000, 40_000, 50_000, 60_000]
    lines = [(t, h + EAR_LAG_MS, s) for t, h, s in zip(texts, heard, sheet, strict=True)]
    texts, starts, ear = _sheet(lines)
    out, applied = shift_sheet(texts, starts, ear)
    assert [(s.first_line, s.median_ms, s.adopted) for s in applied] == \
        [(0, 10_000, 0), (4, -10_000, 0)]
    assert out == heard


REPEAT = "yellow balloons dancing across december"


@pytest.mark.parametrize(("late", "line0"), [(0, 4_950), (10_000, 4_950)])
def test_the_ear_decides_a_repeated_line_left_behind(late, line0):
    # The sheet is 10 s late from line 1 on; line 0, a repeated line (never an
    # anchor), is right on the sheet (`late` 0) or late too. The ear hears it
    # at 5 s either way: near its stamp it stays, near its stamp moved with
    # the section it goes. The short line between goes with the section by
    # position; the closing repeat, heard at its moved stamp, goes too.
    texts = [REPEAT, SHORT[0], *TEXTS, REPEAT]
    sung = [5_000, 9_000, *(20_000 + 10_000 * k for k in range(6)), 80_000]
    stamps = [5_000 - EAR_LAG_MS + late, *(s - EAR_LAG_MS + 10_000 for s in sung[1:])]
    texts, starts, ear = _sheet(list(zip(texts, sung, stamps, strict=True)))
    out, applied = shift_sheet(texts, starts, ear)
    assert [(s.first_line, s.last_line, s.median_ms) for s in applied] == [(2, 7, -10_000)]
    assert out == [line0, *(s - EAR_LAG_MS for s in sung[1:])]
    assert applied[0].adopted == (2 if late == 0 else 3)


def test_a_gap_with_nothing_left_behind_keeps_its_stamps_even_out_of_order():
    # Lines 0-2 move 2 s earlier, lines 4-6 15 s earlier; line 3 (38 s) is
    # overtaken by the second move and carried with it (2.30.1) to 23 s,
    # before line 2's new 28 s. Nothing in the gap is left at the old clock,
    # so 2.32.1 does not decide it again (the nearer edge would say line 2's
    # side): 2.32.0's stamps stand.
    texts = [*TEXTS[:3], SHORT[0], *LONG3]
    sheet = [10_000, 20_000, 30_000, 38_000, 50_000, 60_000, 70_000]
    heard = [8_000, 18_000, 28_000, 38_000, 35_000, 45_000, 55_000]
    lines = [(t, h + EAR_LAG_MS, s) for t, h, s in zip(texts, heard, sheet, strict=True)]
    texts, starts, ear = _sheet(lines)
    out, applied = shift_sheet(texts, starts, ear)
    assert [(s.first_line, s.median_ms, s.carried, s.adopted) for s in applied] == \
        [(0, -2_000, 0, 0), (4, -15_000, 1, 0)]
    assert out == [8_000, 18_000, 28_000, 23_000, 35_000, 45_000, 55_000]


def test_a_lone_anchor_halfway_between_two_offsets_does_not_vote():
    # Lines 0-4 are 8 s early on the sheet; line 5, unique and heard 4 s after
    # its stamp, is a lone anchor exactly halfway between the section's offset
    # and the song end's 0. It decides nothing, so the line goes with the
    # section by position.
    texts = [*TEXTS[:5], LONG3[0]]
    sheet = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000]
    heard = [18_000, 28_000, 38_000, 48_000, 58_000, 64_000]
    lines = [(t, h + EAR_LAG_MS, s) for t, h, s in zip(texts, heard, sheet, strict=True)]
    texts, starts, ear = _sheet(lines)
    out, applied = shift_sheet(texts, starts, ear)
    assert [(s.first_line, s.last_line, s.median_ms, s.adopted) for s in applied] == \
        [(0, 4, 8_000, 1)]
    assert out[5] == 68_000


def test_a_section_exactly_on_the_shift_threshold_lends_no_offset():
    # Lines 0-2 are heard exactly SHIFT_MS after their stamps (a section that
    # stays), lines 4-6 10 s before theirs (moved). Line 3, nearer lines 0-2,
    # goes with the section that stays and so keeps its stamp.
    texts = [*TEXTS[:3], SHORT[0], *LONG3]
    sheet = [10_000, 20_000, 30_000, 33_000, 60_000, 70_000, 80_000]
    heard = [11_000, 21_000, 31_000, 33_000, 50_000, 60_000, 70_000]
    lines = [(t, h + EAR_LAG_MS, s) for t, h, s in zip(texts, heard, sheet, strict=True)]
    texts, starts, ear = _sheet(lines)
    out, applied = shift_sheet(texts, starts, ear)
    assert [(s.first_line, s.median_ms, s.adopted) for s in applied] == [(4, -10_000, 0)]
    assert out == [*sheet[:4], 50_000, 60_000, 70_000]


def test_a_gap_no_moved_section_borders_is_left_alone():
    # Two sections that both stay: their unanchored lines never move.
    texts = [*TEXTS[:3], SHORT[0], *TEXTS[3:]]
    sung = [10_000, 20_000, 30_000, 35_000, 40_000, 50_000, 60_000]
    lines = [(t, s, s - EAR_LAG_MS + (900 if t == SHORT[0] else 0))
             for t, s in zip(texts, sung, strict=True)]
    texts, starts, ear = _sheet(lines)
    assert shift_sheet(texts, starts, ear) == (starts, [])


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
