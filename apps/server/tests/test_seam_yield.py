"""Seam piles in windowed alignment (Faz 9, 2026-10): the window that owns the
audio keeps it, and a stray word in a neighbour's pad yields.

Field case: Stressed Out, first chorus. The next line's lrclib stamp is
52030 ms; the previous window's slice runs to 52380. CTC parked the line-final
"(oh)" in that pad, and the forward clamp then pushed "Wish" and "we" — placed
correctly at 52185 and 52353 — onto it as zero-length words.
"""

import pytest

from kashi_server.pipeline.windows import (
    FRAME_S,
    Window,
    plan_windows,
    reconcile_seams,
    yield_pad_words,
)

# Window 6 owns line 9 up to the next stamp (52030); window 7 owns line 10.
W6 = Window(46_170, 52_380, [9], owned_start_ms=46_520, owned_end_ms=52_030)
W7 = Window(51_680, 58_240, [10], owned_start_ms=52_030, owned_end_ms=57_890)
WINDOWS = {6: W6, 7: W7}


def word(text, start, end, window):
    return {"text": text, "start": start, "end": end, "score": -0.1, "window": window}


def stressed_out():
    return [
        word("stressed", 50.590, 51.140, 6),
        word("out", 51.140, 51.420, 6),
        word("(oh)", 52.381, 52.400, 6),  # parked in the pad, past "Wish"
        word("Wish", 52.185, 52.353, 7),
        word("we", 52.353, 52.438, 7),
        word("could", 52.438, 52.732, 7),
    ]


def windows_list():
    # yield_pad_words indexes windows by the tag; tags here are 6 and 7.
    return [W6 if i == 6 else W7 if i == 7 else Window(0, 1, [0]) for i in range(8)]


def starts(results):
    return [round(r["start"], 3) for r in results]


def test_the_field_case_keeps_the_next_line_where_it_was():
    results = stressed_out()
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 1
    oh = yielded[2]
    assert (round(oh["start"], 3), round(oh["end"], 3)) == (52.165, 52.185)
    # The next window's words are the very same objects: nothing touched them.
    assert yielded[3] is results[3] and yielded[4] is results[4] and yielded[5] is results[5]
    # ...and the clamp that runs afterwards has nothing left to push.
    assert starts(reconcile_seams(yielded)) == [50.59, 51.14, 52.165, 52.185, 52.353, 52.438]


def test_without_the_yield_the_clamp_piles_the_next_line_up():
    # What production does today: the documented zero-length pile.
    clamped = reconcile_seams(stressed_out())
    assert starts(clamped)[3:5] == [52.381, 52.381]


def test_start_exactly_on_the_owned_end_is_in_the_pad():
    # The next line's first word sits in ITS leading pad (52.0 < stamp 52.03).
    results = [word("a", 51.0, 51.5, 6), word("b", 52.030, 52.3, 6), word("c", 52.0, 52.4, 7)]
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 1
    assert round(yielded[1]["end"], 3) == 52.0


def test_a_millisecond_before_the_owned_end_is_the_windows_own_audio():
    results = [word("a", 51.0, 51.5, 6), word("b", 52.029, 52.3, 6), word("c", 52.0, 52.4, 7)]
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 0
    assert yielded == results  # left to the clamp


def test_a_pad_word_that_is_not_in_the_way_stays_put():
    # In the pad, but before the next window's first word: it pushes nothing.
    results = [word("a", 51.0, 51.5, 6), word("b", 52.1, 52.2, 6), word("c", 52.1, 52.4, 7)]
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 0
    assert yielded[1] is results[1]


def test_several_pad_words_stack_one_frame_each():
    results = [
        word("a", 51.0, 51.5, 6),
        word("b", 52.30, 52.32, 6),
        word("c", 52.33, 52.36, 6),
        word("d", 52.2, 52.5, 7),
    ]
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 2
    assert [(round(r["start"], 3), round(r["end"], 3)) for r in yielded[1:3]] == [
        (round(52.2 - 2 * FRAME_S, 3), round(52.2 - FRAME_S, 3)),
        (round(52.2 - FRAME_S, 3), 52.2),
    ]


def test_pad_words_stop_at_the_own_lines_last_word():
    # Room for both: they stack just before the next line. With the next line
    # 5 ms after the own last word there is no room, and they collapse onto
    # the own line's end rather than onto the next line's start.
    results = [
        word("a", 52.02, 52.25, 6),  # its own audio: owned_end is 52.03
        word("b", 52.30, 52.32, 6),
        word("c", 52.33, 52.36, 6),
        word("d", 52.2, 52.5, 7),
    ]
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 2
    assert [round(r["start"], 3) for r in yielded[1:3]] == [52.16, 52.18]
    tight = [
        word("a", 52.02, 52.25, 6),
        word("b", 52.30, 52.32, 6),
        word("c", 52.33, 52.36, 6),
        word("d", 52.025, 52.5, 7),
    ]
    yielded, moved = yield_pad_words(tight, windows_list())
    assert moved == 2
    assert [(round(r["start"], 3), round(r["end"], 3)) for r in yielded[1:3]] == [
        (52.02, 52.02),
        (52.02, 52.025),
    ]


def test_an_overlap_inside_the_owned_span_is_left_to_the_clamp():
    # The window's own last word already starts after the next line: there is
    # nowhere for the pad word to go that would not reorder the window.
    results = [word("a", 52.0, 52.1, 6), word("b", 52.3, 52.4, 6), word("c", 51.9, 52.5, 7)]
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 0
    assert yielded == results


def test_a_window_with_only_pad_words_floors_at_its_slice_start():
    results = [word("x", 52.30, 52.35, 6), word("y", 52.10, 52.4, 7)]
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 1
    assert (round(yielded[0]["start"], 3), round(yielded[0]["end"], 3)) == (52.08, 52.1)


def test_empty_segments_ride_along_with_their_run():
    # Japanese units can be empty-text segments; they move with the run.
    results = [word("a", 51.0, 51.5, 6), word("", 52.30, 52.30, 6), word("c", 52.2, 52.4, 7)]
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 1
    assert yielded[1]["text"] == ""
    assert round(yielded[1]["end"], 3) == 52.2


def test_within_one_window_nothing_is_a_seam():
    results = [word("a", 52.5, 52.6, 6), word("b", 52.4, 52.7, 6)]
    yielded, moved = yield_pad_words(results, windows_list())
    assert (moved, yielded) == (0, results)


def test_a_window_without_ownership_is_skipped():
    bare = [Window(46_170, 52_380, [9]) if i == 6 else W7 for i in range(8)]
    yielded, moved = yield_pad_words(stressed_out(), bare)
    assert moved == 0


def test_untagged_results_are_a_programming_error():
    with pytest.raises(ValueError):
        yield_pad_words([{"text": "a", "start": 1.0, "end": 2.0}], windows_list())


def test_nothing_in_nothing_out():
    assert yield_pad_words([], windows_list()) == ([], 0)


def test_plan_windows_records_what_each_window_owns():
    texts = ["one two three", "four five six", "seven eight nine", "ten eleven twelve"]
    plan = plan_windows(texts, [1_000, 6_000, 11_000, 16_000], 20_000)
    assert plan is not None
    assert [(w.owned_start_ms, w.owned_end_ms) for w in plan] == [
        (1_000, 6_000),
        (6_000, 11_000),
        (11_000, 16_000),
        (16_000, 20_000),
    ]
    # The pad is around the owned span, never inside it.
    for w in plan:
        assert w.owned_start_ms is not None and w.owned_end_ms is not None
        assert w.slice_start_ms <= w.owned_start_ms < w.owned_end_ms <= w.slice_end_ms


def test_an_own_word_starting_exactly_with_the_next_line_still_takes_the_pad_words():
    # floor == next start: they collapse there, at the end of their own line.
    results = [word("a", 52.02, 52.2, 6), word("b", 52.30, 52.32, 6), word("c", 52.02, 52.5, 7)]
    yielded, moved = yield_pad_words(results, windows_list())
    assert moved == 1
    assert (round(yielded[1]["start"], 3), round(yielded[1]["end"], 3)) == (52.02, 52.02)
