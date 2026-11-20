"""Where a letterless line ('♪', never given to the aligner under `letterless_lines_wordless`)
sits, and what it does to the sung word before it (line_qa._place_unaligned, then after the
held-end restore line_qa._settle_after_restore).

Line QA only places the markers and cuts nothing, so every sung word and line leaves it as the
sheet without letterless lines leaves it, and the held-end restore (which passes over every
marker) reads the same median and bounds. Only then does a marker on its stamp cut: the last word
of the line before, at the stamp, never below the word's floor (its natural length: the
restore's median ms per character times its characters, at least TRIM_MIN_HOLD_MS; or its own
length when shorter). Any other marker (stampless, bounded by a line, after the line before)
shortens nothing. The boundary tests below sit exactly on each bound; the seeded test compares
every document, after the restore, with the same document WITHOUT its letterless lines (the
sheet as if they were not there), exactly. Its one carve-out, ad-lib lines, is stated beside
it."""

import random

from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.line_qa import (
    TRIM_MIN_HOLD_MS,
    _median_char_ms,
    _natural_ms,
    _place_unaligned,
    _settle_after_restore,
    apply_line_qa,
    is_adlib,
    restore_held_ends,
)

MARK = "♪"
CUT_KINDS = ("stamp", "floor")


def _result(entries, *, sync="word"):
    """entries: (text, [(start, end) or (start, end, held_ms), ...]) for a sung line, (MARK, None)
    for a letterless one."""
    lines, words = [], []
    for text, spans in entries:
        if spans is None:
            lines.append(LineTiming(0, 0, text, 0.0, unaligned=True))
            words.append([])
            continue
        chunk = [
            AlignedWord(span[0], span[1], t, 0.2, held_ms=span[2] if len(span) > 2 else 0)
            for t, span in zip(text.split(), spans, strict=True)
        ]
        lines.append(LineTiming(chunk[0].start_ms, chunk[-1].end_ms, text, 0.5))
        words.append(chunk)
    return AlignResult(sync, lines, words if sync == "word" else [], 0.5, windowed=True)


def _final(result, texts, stamps):
    """Line QA, then the held-end restore: the timing a document is built from."""
    outcome = apply_line_qa(result, texts, stamps)
    return outcome, restore_held_ends(outcome.result)[0]


# Three sung lines 100 ms after their stamps, so line QA's offset is 100 exactly; the line
# before the '♪' ends with "two" (3 characters): too few words for a median, so its natural
# length is TRIM_MIN_HOLD_MS.
def _case(last_span, stamp_ms):
    entries = [
        ("one two", [(1_100, 1_400), last_span]),
        (MARK, None),
        ("five six", [(12_100, 12_400), (12_500, 12_800)]),
        ("seven eight", [(16_100, 16_400), (16_500, 16_800)]),
    ]
    return _result(entries), [t for t, _ in entries], [1_000, stamp_ms, 12_000, 16_000]


def test_a_stamp_inside_an_ordinary_last_word_cuts_it_no_shorter_than_its_floor():
    # the reviewer's case: last word 1 500-2 100, stamp + offset 1 501 (and 1 530)
    for stamp in (1_401, 1_430):
        result, texts, stamps = _case((1_500, 2_100), stamp)
        outcome, final = _final(result, texts, stamps)
        # line QA cuts nothing: the word and its line leave it as they came
        assert outcome.result.words_per_line[0] == result.words_per_line[0]
        assert outcome.result.lines[0].end_ms == 2_100
        floor = 1_500 + TRIM_MIN_HOLD_MS
        assert (final.words_per_line[0][1].start_ms, final.words_per_line[0][1].end_ms) == (
            1_500,
            floor,
        )
        assert final.lines[1].start_ms == final.lines[1].end_ms == floor
        assert final.lines[1].placed == "floor" and final.lines[0].end_ms == floor


def test_a_word_shorter_than_its_natural_length_is_never_cut():
    result, texts, stamps = _case((1_500, 1_700), 1_401)  # 200 ms, under the 350 ms floor
    _, final = _final(result, texts, stamps)
    assert final.words_per_line[0][1].end_ms == 1_700
    assert final.lines[1].start_ms == 1_700  # the marker yields to the word's end


def test_a_stamp_exactly_on_the_last_word_s_start_cuts_nothing():
    # 1 400 + 100 == 1 500: not after the word's start, so the marker follows the line's end
    result, texts, stamps = _case((1_500, 12_060), 1_400)
    _, final = _final(result, texts, stamps)
    assert final.words_per_line[0][1].end_ms == 12_060
    assert (final.lines[1].start_ms, final.lines[1].placed) == (12_060, "previous")


def test_a_stamp_on_the_last_word_s_start_is_placed_after_the_line():
    # a line that ends after its last word (12 060 against 1 800): a stamp exactly on that
    # word's start is no cutting stamp; the marker goes after the line's end
    lines = [
        LineTiming(1_000, 12_060, "one two", 0.5),
        LineTiming(0, 0, MARK, 0.0, unaligned=True),
        LineTiming(20_000, 20_300, "go", 0.5),
    ]
    words = [
        [AlignedWord(1_000, 1_300, "one", 0.2), AlignedWord(1_500, 1_800, "two", 0.2)],
        [],
        [AlignedWord(20_000, 20_300, "go", 0.2)],
    ]
    placed = _place_unaligned(lines, [900, 1_400, 19_900], 100, frozenset(), words)
    assert (placed[1].start_ms, placed[1].placed) == (12_060, "previous")
    placed = _place_unaligned(lines, [900, 1_401, 19_900], 100, frozenset(), words)
    assert (placed[1].start_ms, placed[1].placed) == (1_501, "stamp")


def test_after_a_snapped_line_the_marker_is_placed_after_its_rescued_words():
    # the snapped line moved from 1 000 to 5 000; its words will be block-shifted with it (to
    # 5 000-7 000), so a stamp at 5 500 lies inside them: the marker goes after them
    lines = [
        LineTiming(5_000, 9_000, "one two", 0.5),
        LineTiming(0, 0, MARK, 0.0, unaligned=True),
        LineTiming(20_000, 20_300, "go", 0.5),
    ]
    originals = [LineTiming(1_000, 3_000, "one two", 0.5), lines[1], lines[2]]
    words = [
        [AlignedWord(1_000, 2_000, "one", 0.2), AlignedWord(2_000, 3_000, "two", 0.2)],
        [],
        [AlignedWord(20_000, 20_300, "go", 0.2)],
    ]
    placed = _place_unaligned(lines, [4_900, 5_400, 19_900], 100, frozenset({0}), words, originals)
    assert (placed[1].start_ms, placed[1].placed) == (7_000, "previous")


def test_one_millisecond_after_the_last_word_s_start_the_stamp_wins_down_to_the_floor():
    result, texts, stamps = _case((1_500, 12_060), 1_401)
    _, final = _final(result, texts, stamps)
    assert final.words_per_line[0][1].end_ms == 1_500 + TRIM_MIN_HOLD_MS
    result, texts, stamps = _case((1_500, 12_060), 2_900)  # stamp + offset 3 000
    _, final = _final(result, texts, stamps)
    assert final.words_per_line[0][1].end_ms == 3_000
    assert (final.lines[1].start_ms, final.lines[1].placed) == (3_000, "stamp")


def test_the_floor_reads_the_document_s_median_ms_per_character():
    entries = [
        (
            "aaaa bbbb cccc dddd eeee ffff gggg hhhh",
            [(k * 1_000, k * 1_000 + 800) for k in range(8)],
        )
    ]
    result = _result(entries)
    med = _median_char_ms(result)
    assert med == 200.0  # 800 ms over 4 characters
    word = AlignedWord(0, 5_000, "word", 0.2)
    assert _natural_ms(word, med) == 800
    assert _natural_ms(word, None) == TRIM_MIN_HOLD_MS
    assert _natural_ms(AlignedWord(0, 5_000, "a", 0.2), med) == TRIM_MIN_HOLD_MS


def test_the_next_line_bounds_the_stamp_before_it_is_compared_with_the_last_word():
    # the next aligned line (snapped) starts before the line before's last word: no cut at all
    lines = [
        LineTiming(7_779, 9_579, "one two three", 0.5),
        LineTiming(0, 0, MARK, 0.0, unaligned=True),
        LineTiming(7_779, 7_900, "oh", 0.5),
    ]
    words = [
        [
            AlignedWord(7_779, 8_379, "one", 0.2),
            AlignedWord(8_379, 8_379, "two", 0.2),
            AlignedWord(8_379, 9_579, "three", 0.2),
        ],
        [],
        [AlignedWord(7_779, 7_900, "oh", 0.2)],
    ]
    placed = _place_unaligned(lines, [0, 9_000, 7_700], 100, frozenset({2}), words)
    assert placed[0] == lines[0]  # the sung line keeps its end
    assert placed[1].start_ms == placed[1].end_ms == 7_779  # yields to the next line's start
    assert placed[1].placed == "next"
    settled, cut = _settle_after_restore(placed, words, None)
    assert cut == words and settled[0] == lines[0]


def test_when_the_next_line_starts_inside_the_floor_the_word_keeps_its_floor():
    # the next aligned line (snapped) starts 200 ms after the last word's start
    lines = [
        LineTiming(1_000, 12_060, "one two", 0.5),
        LineTiming(0, 0, MARK, 0.0, unaligned=True),
        LineTiming(1_700, 1_900, "oh", 0.5),
    ]
    words = [
        [AlignedWord(1_000, 1_300, "one", 0.2), AlignedWord(1_500, 12_060, "two", 0.2)],
        [],
        [AlignedWord(1_700, 1_900, "oh", 0.2)],
    ]
    # a stamp past the next line: the marker sits on that line and shortens nothing
    placed = _place_unaligned(lines, [900, 2_900, 1_600], 100, frozenset({2}), words)
    assert (placed[1].start_ms, placed[1].placed) == (1_700, "next")
    assert placed[0] == lines[0]
    for med in (None, 200.0):
        settled, cut = _settle_after_restore(placed, words, med)
        assert cut == words and settled[0].end_ms == 12_060
        assert (settled[1].start_ms, settled[1].placed) == (1_700, "next")
    # a stamp inside the word (1 600) with the next line inside its floor: the word keeps its
    # floor (350 ms; 600 ms at 200 ms per character), the marker is bounded by the next line
    placed = _place_unaligned(lines, [900, 1_500, 1_600], 100, frozenset({2}), words)
    assert (placed[1].start_ms, placed[1].placed) == (1_600, "stamp")
    assert placed[0] == lines[0]  # line QA cuts nothing
    for med, floor in ((None, 1_850), (200.0, 2_100)):
        settled, cut = _settle_after_restore(placed, words, med)
        assert (cut[0][1].start_ms, cut[0][1].end_ms) == (1_500, floor)
        assert settled[0].end_ms == floor
        assert (settled[1].start_ms, settled[1].placed) == (1_700, "floor")


def test_the_cut_reads_the_restore_s_median_for_the_floor():
    # "tonight" (7 characters) at 100 ms per character: its natural length is 700 ms
    lines = [
        LineTiming(1_000, 3_000, "tonight", 0.5),
        LineTiming(1_400, 1_400, MARK, 0.0, True, "stamp"),
        LineTiming(5_000, 5_300, "go", 0.5),
    ]
    words = [
        [AlignedWord(1_000, 3_000, "tonight", 0.2)],
        [],
        [AlignedWord(5_000, 5_300, "go", 0.2)],
    ]
    settled, cut = _settle_after_restore(lines, words, 100.0)
    assert cut[0][0].end_ms == 1_700 and settled[0].end_ms == 1_700
    assert (settled[1].start_ms, settled[1].placed) == (1_700, "floor")
    settled, cut = _settle_after_restore(lines, words, None)  # 350 ms: the stamp
    assert cut[0][0].end_ms == 1_400 and settled[0].end_ms == 1_400
    assert (settled[1].start_ms, settled[1].placed) == (1_400, "stamp")


def test_without_a_median_a_marker_still_follows_the_words_before_it():
    # under 8 measured words nothing is restored; the marker off its stamp still moves to the
    # end of the word before it
    lines = [
        LineTiming(1_000, 1_500, "one two", 0.5),
        LineTiming(1_200, 1_200, MARK, 0.0, True, "previous"),
        LineTiming(5_000, 5_300, "go", 0.5),
    ]
    words = [
        [AlignedWord(1_000, 1_100, "one", 0.2), AlignedWord(1_100, 1_500, "two", 0.2)],
        [],
        [AlignedWord(5_000, 5_300, "go", 0.2)],
    ]
    out, restored = restore_held_ends(AlignResult("word", lines, words, 0.5, windowed=True))
    assert restored == 0
    assert out.lines[1].start_ms == out.lines[1].end_ms == 1_500
    assert out.words_per_line == words


def test_a_marker_moved_by_the_marker_before_it_is_no_longer_on_its_stamp():
    lines = [
        LineTiming(1_000, 1_500, "one two", 0.5),
        LineTiming(2_000, 2_000, MARK, 0.0, True, "previous"),
        LineTiming(1_800, 1_800, MARK, 0.0, True, "stamp"),
        LineTiming(5_000, 5_300, "go", 0.5),
    ]
    words = [
        [AlignedWord(1_000, 1_100, "one", 0.2), AlignedWord(1_100, 1_500, "two", 0.2)],
        [],
        [],
        [AlignedWord(5_000, 5_300, "go", 0.2)],
    ]
    settled, _ = _settle_after_restore(lines, words, None)
    assert (settled[2].start_ms, settled[2].placed) == (2_000, "previous")


def test_a_degraded_marker_cuts_the_line_before_only_from_its_stamp():
    from kashi_server.pipeline.line_qa import _degrade_to_line

    lines = [
        LineTiming(1_000, 3_000, "one two", 0.5),
        LineTiming(2_000, 2_000, MARK, 0.0, True),
        LineTiming(6_000, 6_500, "three", 0.5),
    ]
    result = AlignResult("word", lines, [[], [], []], 0.5, windowed=True)
    # stampless (regroup put it at 2 000): after the line before, which keeps its end
    out = _degrade_to_line(result, [1_000, None, 6_000], [0], 0).result.lines
    assert [(x.start_ms, x.end_ms) for x in out] == [(1_000, 6_000), (6_000, 6_000), (6_000, 6_500)]
    assert out[1].placed == "stampless"
    # on its stamp (2 400), after the line before starts: that line ends there
    out = _degrade_to_line(result, [1_000, 2_400, 6_000], [0], 0).result.lines
    assert [(x.start_ms, x.end_ms) for x in out] == [(1_000, 2_400), (2_400, 2_400), (6_000, 6_500)]
    assert out[1].placed == "stamp"


def test_a_stamp_past_the_next_line_puts_the_marker_on_its_start():
    result, texts, stamps = _case((1_500, 1_700), 30_000)
    _, final = _final(result, texts, stamps)
    assert final.lines[1].start_ms == final.lines[2].start_ms == 12_100
    assert final.lines[1].placed == "next"


def test_a_word_starting_exactly_at_the_marker_is_not_cut():
    lines = [
        LineTiming(1_000, 4_000, "one two", 0.5),
        LineTiming(3_000, 3_000, MARK, 0.0, True, "stamp"),
    ]
    words = [[AlignedWord(1_000, 2_000, "one", 0.2), AlignedWord(3_000, 4_000, "two", 0.2)], []]
    settled, cut = _settle_after_restore(lines, words, None)
    assert cut == words
    assert (settled[1].start_ms, settled[1].placed) == (4_000, "previous")


def _snapped_case(stamp_ms):
    """Ten 4-character words of 800 ms (200 ms per character: the restore's median) and a line
    whose last word, "tonight", runs over the gap; a far line elsewhere is snapped, so line QA
    takes its snapped path. The natural length of "tonight" is 1 400 ms (350 without a median)."""
    entries = [
        ("aaaa bbbb tonight", [(1_100, 1_900), (2_000, 2_800), (3_000, 14_060)]),
        (MARK, None),
        ("cccc dddd", [(14_100, 14_900), (15_000, 15_800)]),
        ("eeee ffff", [(18_100, 18_900), (19_000, 19_800)]),
        ("gggg hhhh", [(22_100, 22_900), (23_000, 23_800)]),
        ("iiii jjjj", [(50_100, 50_900), (51_000, 51_800)]),
    ]
    stamps = [1_000, stamp_ms, 14_000, 18_000, 22_000, 30_000]
    return _result(entries), [t for t, _ in entries], stamps


def test_the_snapped_path_cuts_after_the_restore_at_the_restore_s_median():
    # stamp + offset 3 600, inside the natural length: the floor (3 000 + 1 400) wins
    result, texts, stamps = _snapped_case(3_500)
    outcome, final = _final(result, texts, stamps)
    assert outcome.flagged == [5] and outcome.offset_ms == 100
    assert _median_char_ms(outcome.result) == 200.0
    # line QA cut nothing: the line's end and its last word are the aligner's
    assert outcome.result.lines[0].end_ms == 14_060
    assert outcome.result.words_per_line[0][2].end_ms == 14_060
    tonight = final.words_per_line[0][2]
    assert (tonight.start_ms, tonight.end_ms) == (3_000, 4_400)
    assert (final.lines[1].start_ms, final.lines[1].placed) == (4_400, "floor")
    assert final.lines[0].end_ms == 4_400
    # past the natural length, the stamp itself
    _, final = _final(*_snapped_case(4_900))
    assert final.words_per_line[0][2].end_ms == 5_000 == final.lines[0].end_ms
    assert (final.lines[1].start_ms, final.lines[1].placed) == (5_000, "stamp")


def test_an_ad_lib_shifted_onto_its_anchor_is_cut_where_it_now_stands():
    """The reviewer's case: the ad-lib's one word spans 1 863-14 363 until line QA shifts it
    onto its anchor (130); the marker's stamp + offset is 1 393. Placed with the words as they
    stand after the shift, the 12.5 s hold does not survive."""
    entries = [
        ("oh", [(1_863, 14_363)]),
        (MARK, None),
        ("five six", [(14_423, 14_700), (14_800, 15_000)]),
        ("seven eight", [(17_130, 17_400), (17_500, 17_800)]),
        ("nine ten", [(20_130, 20_400), (20_500, 20_800)]),
    ]
    outcome, final = _final(
        _result(entries), [t for t, _ in entries], [0, 1_263, 14_293, 17_000, 20_000]
    )
    assert outcome.offset_ms == 130 and outcome.adlib_shifted == [0]
    oh = final.words_per_line[0][0]
    assert oh.start_ms == 130 and oh.end_ms == 1_393
    assert final.lines[1].start_ms == 1_393


def test_a_marker_never_bounds_an_ad_lib_snap():
    """`_snap_end` passes over a letterless line: an ad-lib whose anchor lands just before the
    '♪' stamp snaps as if the line were not there (the marker is placed afterwards)."""
    entries = [
        ("one two", [(1_100, 1_400), (1_500, 1_800)]),
        ("oh oh", [(9_000, 9_300), (9_300, 9_600)]),
        (MARK, None),
        ("five six", [(12_100, 12_400), (12_500, 12_800)]),
        ("seven eight", [(16_100, 16_400), (16_500, 16_800)]),
    ]
    stamps = [1_000, 4_000, 4_001, 12_000, 16_000]
    with_mark = apply_line_qa(_result(entries), [t for t, _ in entries], stamps)
    without = [e for e in entries if e[0] != MARK]
    plain = apply_line_qa(_result(without), [t for t, _ in without], [1_000, 4_000, 12_000, 16_000])
    assert with_mark.adlib_shifted == [1] and plain.adlib_shifted == [1]
    assert with_mark.result.lines[1].start_ms == plain.result.lines[1].start_ms == 4_100


def test_a_degraded_document_writes_its_letterless_lines_as_0_ms_markers():
    entries = [
        ("one two", [(1_100, 1_400), (1_500, 1_800)]),
        (MARK, None),
        ("five six", [(12_100, 12_400), (12_500, 12_800)]),
        ("seven eight", [(16_100, 16_400), (16_500, 16_800)]),
    ]
    result = _result(entries, sync="line")
    outcome = apply_line_qa(result, [t for t, _ in entries], [1_000, 8_000, 12_000, 16_000])
    music = outcome.result.lines[1]
    assert outcome.degraded_to_line and music.start_ms == music.end_ms == 8_100


def test_the_restore_reads_no_chunk_past_the_last_line():
    # more word chunks than lines (alignment never builds one; the arithmetic is 0.44.0's): the
    # last line's held end is bounded by nothing past it
    filler = [[AlignedWord(1_000 + k * 1_000, 1_800 + k * 1_000, "bbbb", 0.2)] for k in range(8)]
    lines = [LineTiming(c[0].start_ms, c[0].end_ms, "bbbb", 0.5) for c in filler]
    lines.append(LineTiming(20_000, 20_400, "aaaa", 0.5))  # the last line, its end held
    words = [*filler, [AlignedWord(20_000, 20_400, "aaaa", 0.2, held_ms=600)]]
    extra = [[AlignedWord(20_500, 20_600, "cccc", 0.2)]]  # a chunk with no line
    result = AlignResult("word", lines, words + extra, 0.5, windowed=True)
    out, _ = restore_held_ends(result)
    # nothing bounds the last line (not the stray chunk at 20 500): its 600 ms come back
    assert out.words_per_line[8][0].end_ms == 21_000


# --- the seeded comparison with the sheet without its letterless lines ------------------------
#
# Every document is put through line QA and the held-end restore twice: as it is, and with its
# letterless lines removed (the sheet as if they were not there, same stamps for the rest).
# Exactly, with M the median ms per character of the marker-free document's measured words after
# line QA (stated here on its own: non-ad-lib words longer than 0 ms, at least 8 of them):
#   - line QA's output is the marker-free one for every sung line outside an ad-lib: the same
#     words, starts and ends (QA cuts nothing), so the restore reads the same median and bounds;
#   - after the restore every such word equals the marker-free one, except the last word of a
#     line right before a marker on its stamp s ("stamp" / "floor") that starts before s and
#     reaches past it: that one ends at max(s, its start + min(its length, max(350, characters x
#     M))) (350 without M); its line ends at that marker, every other line as marker-free;
#   - a line document's sung line starts equal the marker-free document's, its ends too except
#     a line right before a marker on its stamp, which ends there;
#   - a marker is 0 ms, never past the next sung line's start nor before a marker before it; on
#     its stamp it sits there ("stamp") or at the cut word's end ("floor"); one without a stamp
#     never cuts.
# Carve-out: ad-lib lines. `rederive_adlib_words` holds an ad-lib to the next line's start and
# respreads its words over that span; the next line is the marker, as it was the filler line in
# a stored document, so a marker can end an ad-lib's hold where the sheet without it would not.

_WORDS = ["one", "two", "three", "we", "go", "again", "tonight"]
# held ends: none, 1 ms, under / around the floor, and on and around the 4 s cap
_HELD = [0, 0, 0, 1, 290, 800, 3_999, 4_000, 4_001, 10_000]


def _random_case(rng: random.Random):
    """Sung lines near their stamps (monotone, or with a stamp jumbled now and then), far lines
    (snapped, words rescued or dropped), ad-libs, letterless lines first, last, consecutive,
    stampless, after a line whose last word is often stretched to the next sung line, their
    stamps (+ the offset) on or next to every bound, held ends on every word; now and then a
    line document. From 2 lines up, so documents fall on both sides of the median's 8 words."""
    entries, stamps = [], []
    t = rng.choice([500, 1_000])
    for k in range(rng.randint(2, 10)):
        first = k == 0 and rng.random() < 0.1
        after_mark = bool(entries) and entries[-1][1] is None
        if first or (k > 0 and rng.random() < (0.15 if after_mark else 0.3)):
            entries.append((MARK, None))
            stamps.append(None)  # set once the neighbours are known
            continue
        adlib = rng.random() < 0.15
        spans, w = [], t
        for _ in range(rng.randint(1, 4)):
            dur = rng.choice([0, 60, 200, 349, 350, 351, 600, 1_200])
            spans.append((w, w + dur, rng.choice(_HELD)))
            w += dur + rng.choice([0, 40, 300])
        n = len(spans)
        text = " ".join(["oh"] * n) if adlib else " ".join(rng.choice(_WORDS) for _ in range(n))
        entries.append((text, spans))
        far = k > 2 and rng.random() < 0.08
        jumble = rng.random() < 0.08
        stamp = t - 100 - (30_000 if far else 0) + (rng.choice([-4_000, 4_000]) if jumble else 0)
        stamps.append(None if rng.random() < 0.05 else max(0, stamp))
        t = w + rng.choice([300, 2_000, 8_000])
    if rng.random() < 0.15:
        entries.append((MARK, None))
        stamps.append(None)
    for i, (text, _spans) in enumerate(entries):
        if text != MARK:
            continue
        prev = next((e[1] for e in reversed(entries[:i]) if e[1] is not None), None)
        nxt = next((e[1][0][0] for e in entries[i + 1 :] if e[1] is not None), None)
        if prev is not None and nxt is not None and entries[i - 1][1] is not None:
            if rng.random() < 0.6:  # the stretched last word
                s0, _, held = prev[-1]
                prev[-1] = (s0, max(s0, nxt - 40), held)
        bounds = [0, 5_000]
        if prev is not None:
            last_start, last_end, _ = prev[-1]
            bounds += [
                last_start - 1,
                last_start,
                last_start + 1,
                last_start + 349,
                last_start + 350,
                last_start + 351,
                last_start + 1_199,
                last_start + 1_200,
                last_end - 1,
                last_end,
                last_end + 1,
            ]
        if nxt is not None:
            bounds += [nxt - 1, nxt, nxt + 1, nxt + 5_000]
        stamps[i] = None if rng.random() < 0.12 else max(0, rng.choice(bounds) - 100)
    return entries, stamps, rng.random() < 0.08


def _sung(entries):
    return [i for i, (text, _) in enumerate(entries) if text != MARK]


def _measured(result):
    return [
        (w.end_ms - w.start_ms) / len(w.text)
        for line, chunk in zip(result.lines, result.words_per_line, strict=False)
        if not is_adlib(line.text)
        for w in chunk
        if w.text and w.end_ms > w.start_ms
    ]


def _median(result):
    speeds = sorted(_measured(result))
    if len(speeds) < 8:
        return None
    mid = len(speeds) // 2
    return speeds[mid] if len(speeds) % 2 else (speeds[mid - 1] + speeds[mid]) / 2


def test_a_marker_never_lengthens_a_word_nor_cuts_one_below_its_floor():
    rng = random.Random(20261120)
    seen = dict.fromkeys(
        (
            "documents",
            "line_documents",
            "cut",
            "cut_to_floor",
            "restored",
            "snapped",
            "rescued",
            "consecutive",
            "first",
            "last",
            "measured_7",
            "measured_8",
            "measured_9",
            "no_median",
            *("stamp", "floor", "next", "previous", "stampless"),
        ),
        0,
    )
    for _ in range(4_000):
        entries, stamps, line_doc = _random_case(rng)
        texts = [t for t, _ in entries]
        sync = "line" if line_doc else "word"
        with_marks = apply_line_qa(_result(entries, sync=sync), texts, stamps)
        a, _ = restore_held_ends(with_marks.result)
        keep = _sung(entries)
        plain_entries = [entries[i] for i in keep]
        plain = apply_line_qa(
            _result(plain_entries, sync=sync),
            [t for t, _ in plain_entries],
            [stamps[i] for i in keep],
        )
        b, restored = restore_held_ends(plain.result)
        seen["documents"] += 1
        seen["restored"] += restored > 0
        assert with_marks.flagged == [keep[j] for j in plain.flagged]
        assert with_marks.degraded_to_line == plain.degraded_to_line
        assert with_marks.offset_ms == plain.offset_ms
        seen["snapped"] += bool(with_marks.flagged)
        seen["rescued"] += bool(with_marks.uncertain)
        measured = len(_measured(plain.result)) if plain.result.sync == "word" else 0
        seen[f"measured_{measured}"] = seen.get(f"measured_{measured}", 0) + 1
        med = _median(plain.result) if plain.result.sync == "word" else None
        seen["no_median"] += med is None and plain.result.sync == "word"
        offset = with_marks.offset_ms
        for j, i in enumerate(keep):
            if is_adlib(texts[i]):
                continue
            # line QA, word documents: the marker-free output, line and words (a line document
            # has no restore after it: its markers cut line ends in line QA, checked below)
            qa_a, qa_b = with_marks.result.lines[i], plain.result.lines[j]
            assert qa_a.start_ms == qa_b.start_ms
            if a.sync == "word":
                assert qa_a.end_ms == qa_b.end_ms
                assert with_marks.result.words_per_line[i] == plain.result.words_per_line[j]
            la, lb = a.lines[i], b.lines[j]
            assert la.start_ms == lb.start_ms
            mark = a.lines[i + 1] if i + 1 < len(a.lines) else None
            cuts = mark is not None and mark.unaligned and mark.placed in CUT_KINDS
            s = None
            if cuts:
                assert stamps[i + 1] is not None  # one without a stamp never cuts
                s = max(0, stamps[i + 1] + offset)
            if a.sync != "word":
                seen["line_documents"] += 1
                assert la.end_ms == (min(lb.end_ms, mark.start_ms) if cuts else lb.end_ms)
                continue
            wa, wb = a.words_per_line[i], b.words_per_line[j]
            expected = [(y.start_ms, y.end_ms) for y in wb]
            if cuts and wb and wb[-1].start_ms < s < wb[-1].end_ms:
                y = wb[-1]
                natural = max(350, round(len(y.text) * med)) if med else 350
                end = max(s, y.start_ms + min(y.end_ms - y.start_ms, natural))
                expected[-1] = (y.start_ms, end)
                seen["cut"] += 1
                seen["cut_to_floor"] += end > s
            assert [(x.start_ms, x.end_ms) for x in wa] == expected
            if cuts and wb:
                assert la.end_ms == min(lb.end_ms, max(s, expected[-1][1]))
            else:
                assert la.end_ms == lb.end_ms
        previous_marker = None
        for i, line in enumerate(a.lines):
            if not line.unaligned:
                continue
            seen[line.placed] += 1
            seen["first"] += i == 0
            seen["last"] += i == len(a.lines) - 1
            seen["consecutive"] += i > 0 and a.lines[i - 1].unaligned
            assert line.start_ms == line.end_ms
            after = next((x.start_ms for x in a.lines[i + 1 :] if not x.unaligned), None)
            assert after is None or line.start_ms <= after
            assert previous_marker is None or line.start_ms >= previous_marker
            previous_marker = line.start_ms
            if stamps[i] is None:
                assert line.placed not in CUT_KINDS
                continue
            s = max(0, stamps[i] + offset)
            if line.placed == "stamp" and a.sync == "word":
                assert line.start_ms == s
            elif line.placed == "floor":
                assert line.start_ms > s or line.start_ms == after
            elif line.placed == "next":
                assert after is not None
    for key in ("measured_7", "measured_8", "measured_9"):
        assert seen[key] > 5, seen
    assert all(v > 20 for k, v in seen.items() if not k.startswith("measured_")), seen


# --- the preview's report ---------------------------------------------------------------------


def _doc_line(text, start, end, words=None):
    line = {"text": text, "start_ms": start, "end_ms": end}
    if words:
        line["words"] = [{"text": t, "start_ms": s, "end_ms": e} for t, s, e in words]
    return line


def test_the_preview_reports_both_directions_around_letterless_lines():
    from kashi_server.pipeline.level import Envelope
    from kashi_server.worker import preview_rules as rules

    qa_old = {"adlib_shifted": 0, "adlib_rederived": 1, "flagged": 0}
    qa_new = {"adlib_shifted": 1, "adlib_rederived": 1, "flagged": 0}
    old = {
        "alignment": {"qa": qa_old},
        "lines": [
            _doc_line("one two", 1_000, 1_600, [("one", 1_000, 1_100), ("two", 1_100, 1_600)]),
            _doc_line(MARK, 1_600, 1_900, [("a", 1_600, 1_900)]),
            _doc_line("three", 2_400, 7_000, [("three", 2_400, 7_000)]),  # a 4.6 s hold ...
            _doc_line("four", 11_000, 11_300, [("four", 11_000, 11_300)]),  # ... before a break
        ],
    }
    new = {
        "alignment": {"qa": qa_new},
        "lines": [
            _doc_line("one two", 1_000, 1_200, [("one", 1_000, 1_100), ("two", 1_100, 1_200)]),
            _doc_line(MARK, 1_200, 1_200),  # on the previous line's end, 1.2 s from the next
            _doc_line("three", 2_400, 2_400, [("three", 2_400, 2_400)]),  # 0 ms line and word
            _doc_line("four", 11_000, 10_900, [("four", 11_000, 11_300)]),  # ends before it starts
        ],
    }
    # voiced from 1 100 to 1 300, silent after: a quarter of the cut span 1 200-1 600 is voiced
    levels = [255] * 1_000
    for frame in range(1_100 // 20, 1_300 // 20):
        levels[frame] = 0
    report = rules.compare(old, new, Envelope(bytes(levels)))["letterless"]
    assert report["final_words_before_letterless"] == [1, 1]
    assert report["final_smallest_ms"] == [500, 100]
    assert report["final_under_120"] == [0, 1] and report["final_under_200"] == [0, 1]
    assert report["final_holds_over_4000"] == [0, 0]
    assert report["gap_holds_over_4000"] == [1, 0]
    assert report["markers_at_previous_end"] == [1, 1]
    assert report["markers_under_500_to_next"] == [0, 0]
    assert report["markers_at_next_start"] == [0, 0]
    assert report["lines_ending_before_last_word"] == [0, 1]
    assert report["zero_ms_sung_lines"] == [0, 1] and report["zero_ms_words"] == [0, 1]
    assert report["adlib_shifted"] == [0, 1] and report["flagged"] == [0, 0]
    assert report["final_shortened"] == 1
    assert report["cut_voiced"] == [{"line": 0, "old_ms": 500, "new_ms": 100, "voiced": 0.25}]
    # FORMAT 9: the 100 ms "two" ends on the marker, under its natural 350 ms: a stopped hold
    assert report["final_held_stopped"] == [0, 1]
    assert report["markers_before_previous_last_start"] == [0, 0]
    assert report["markers_before_previous_last_end"] == [0, 0]
    assert report["final_unpaired"] == 0 and report["nonlast_words_cut_at_marker"] == 0
    assert report["line_sync_sung_starts_moved"] == 0
    assert report["marker_kinds"] == [None, None] and report["uncertain"] == [None, None]
    # report only: the verdict reads none of it
    comparison = rules.compare(old, new, None)
    assert "voiced" not in comparison["letterless"]["cut_voiced"][0]
    assert rules.verdict(comparison) == rules.verdict({**comparison, "letterless": {}})


def test_a_document_counts_how_its_markers_were_placed_and_nothing_without_one():
    from kashi_server.pipeline.document import _qa_counts

    result, texts, stamps = _case((1_500, 2_100), 1_401)  # lifted to the floor of "two"
    outcome = apply_line_qa(result, texts, stamps)
    final, _ = restore_held_ends(outcome.result)
    counts = _qa_counts(outcome, final)
    assert counts["markers"] == {"stamp": 0, "floor": 1, "next": 0, "previous": 0, "stampless": 0}
    # without a letterless line (the setting off) the block is what it was before
    keep = [0, 2, 3]
    plain = _result(
        [(texts[i], [(w.start_ms, w.end_ms) for w in result.words_per_line[i]]) for i in keep]
    )
    outcome = apply_line_qa(plain, [texts[i] for i in keep], [stamps[i] for i in keep])
    final, _ = restore_held_ends(outcome.result)
    assert "markers" not in _qa_counts(outcome, final)
    assert set(_qa_counts(outcome, final)) == {
        "flagged",
        "density_dropped",
        "adlib_shifted",
        "nudged",
        "adlib_rederived",
        "response_shifted",
        "offset_ms",
        "trimmed_ends",
        "uncertain",
    }


def test_the_preview_counts_markers_before_the_previous_word_and_what_they_cut():
    from kashi_server.worker import preview_rules as rules

    old = {
        "sync": "word",
        "alignment": {"qa": {"uncertain": 0}},
        "lines": [
            _doc_line("one two", 1_000, 1_600, [("one", 1_000, 1_100), ("two", 1_100, 1_600)]),
            _doc_line(MARK, 1_600, 1_900, [("a", 1_600, 1_900)]),
            _doc_line("three", 2_400, 3_000, [("three", 2_400, 3_000)]),
        ],
    }
    kinds = {"stamp": 1, "floor": 0, "next": 0, "previous": 0, "stampless": 0}
    new = {
        "sync": "word",
        "alignment": {"qa": {"uncertain": 1, "markers": kinds}},
        "lines": [
            # a marker before the last word starts: "one" (not the last word) ends on it, and
            # "two" moved its start (so no stored word pairs it) and lost length
            _doc_line("one two", 1_000, 1_100, [("one", 1_000, 1_050), ("two", 1_060, 1_100)]),
            _doc_line(MARK, 1_050, 1_050),
            _doc_line("three", 2_400, 3_000, [("three", 2_400, 3_000)]),
        ],
    }
    report = rules.compare(old, new, None)["letterless"]
    assert report["final_held_stopped"] == [0, 0]  # "two" is short, but the marker is not on it
    assert report["markers_before_previous_last_start"] == [0, 1]
    assert report["markers_before_previous_last_end"] == [0, 1]
    assert report["uncertain"] == [0, 1] and report["marker_kinds"] == [None, kinds]
    assert report["nonlast_words_cut_at_marker"] == 1
    assert report["final_shortened"] == 0 and report["final_unpaired"] == 1
    assert report["line_sync_sung_starts_moved"] == 0
    # a line-sync build with a letterless line: the sung starts against the stored ones
    line_doc = {
        "sync": "line",
        "alignment": {"qa": {}},
        "lines": [
            _doc_line("one two", 1_000, 1_600),
            _doc_line(MARK, 1_600, 1_600),
            _doc_line("three", 2_500, 3_000),
        ],
    }
    assert rules.compare(old, line_doc, None)["letterless"]["line_sync_sung_starts_moved"] == 1

    def built(two_end, mark, three_start):
        return {
            "sync": "word",
            "alignment": {"qa": {}},
            "lines": [
                _doc_line(
                    "one two", 1_000, two_end, [("one", 1_000, 1_100), ("two", 1_100, two_end)]
                ),
                _doc_line(MARK, mark, mark),
                _doc_line("three", three_start, 3_000, [("three", three_start, 3_000)]),
            ],
        }

    # "two" 300 ms, the marker on its end: over 200 ms but under its natural 350 ms; and a word
    # build whose sung start moved is no line-sync move
    report = rules.compare(old, built(1_400, 1_400, 2_500), None)["letterless"]
    assert report["final_held_stopped"] == [0, 1]
    assert report["line_sync_sung_starts_moved"] == 0
    assert report["markers_before_previous_last_end"] == [0, 0]
    # a marker inside the last word: before its end, not before its start
    report = rules.compare(old, built(1_600, 1_300, 2_400), None)["letterless"]
    assert report["markers_before_previous_last_start"] == [0, 0]
    assert report["markers_before_previous_last_end"] == [0, 1]


def test_the_schema_declares_the_counts_a_candidate_document_carries():
    import copy
    import json

    import pytest

    from kashi_server.config import settings
    from kashi_server.pipeline.document import PipelineError, validate_document

    example = json.loads((settings.schema_path.parent / "examples" / "word.json").read_text())
    validate_document(example)  # a document without either count, as before
    doc = copy.deepcopy(example)
    kinds = {"stamp": 2, "floor": 1, "next": 0, "previous": 0, "stampless": 0}
    doc["alignment"]["qa"].update(uncertain=1, markers=kinds)
    validate_document(doc)
    for bad in (
        {"uncertain": -1},
        {"markers": {**kinds, "stamp": -1}},
        {"markers": {**kinds, "lifted": 1}},
        {"markers": {k: v for k, v in kinds.items() if k != "floor"}},
    ):
        broken = copy.deepcopy(doc)
        broken["alignment"]["qa"].update(bad)
        with pytest.raises(PipelineError):
            validate_document(broken)
