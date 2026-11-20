"""Where a letterless line ('♪', never given to the aligner under `letterless_lines_wordless`)
sits, and what it does to the sung word before it (line_qa._place_unaligned, _cut_at_unaligned).

A marker never lengthens a sung word and never leaves one shorter than its floor: its natural
length (the document's median ms per character times its characters, at least TRIM_MIN_HOLD_MS)
or its own length when shorter. The boundary tests below sit exactly on each bound; the seeded
test at the end compares every document with the same document WITHOUT its letterless lines
(the sheet as if they were not there), values on every threshold and either side of it."""

import random

from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.line_qa import (
    TRIM_MIN_HOLD_MS,
    _cut_at_unaligned,
    _median_char_ms,
    _natural_ms,
    _place_unaligned,
    apply_line_qa,
    is_adlib,
    restore_held_ends,
)

MARK = "♪"


def _result(entries, *, sync="word"):
    """entries: (text, [(start, end), ...]) for a sung line, (MARK, None) for a letterless one."""
    lines, words = [], []
    for text, spans in entries:
        if spans is None:
            lines.append(LineTiming(0, 0, text, 0.0, unaligned=True))
            words.append([])
            continue
        chunk = [AlignedWord(s, e, t, 0.2) for t, (s, e) in zip(text.split(), spans, strict=True)]
        lines.append(LineTiming(chunk[0].start_ms, chunk[-1].end_ms, text, 0.5))
        words.append(chunk)
    return AlignResult(sync, lines, words if sync == "word" else [], 0.5, windowed=True)


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
        outcome = apply_line_qa(result, texts, stamps)
        two = outcome.result.words_per_line[0][1]
        floor = 1_500 + TRIM_MIN_HOLD_MS
        assert (two.start_ms, two.end_ms) == (1_500, floor)
        assert outcome.result.lines[1].start_ms == outcome.result.lines[1].end_ms == floor
        assert outcome.result.lines[0].end_ms == floor


def test_a_word_shorter_than_its_natural_length_is_never_cut():
    result, texts, stamps = _case((1_500, 1_700), 1_401)  # 200 ms, under the 350 ms floor
    outcome = apply_line_qa(result, texts, stamps)
    assert outcome.result.words_per_line[0][1].end_ms == 1_700
    assert outcome.result.lines[1].start_ms == 1_700  # the marker yields to the word's end


def test_a_stamp_exactly_on_the_last_word_s_start_cuts_nothing():
    # 1 400 + 100 == 1 500: not after the word's start, so the marker follows the line's end
    result, texts, stamps = _case((1_500, 12_060), 1_400)
    outcome = apply_line_qa(result, texts, stamps)
    assert outcome.result.words_per_line[0][1].end_ms == 12_060
    assert outcome.result.lines[1].start_ms == 12_060


def test_one_millisecond_after_the_last_word_s_start_the_stamp_wins_down_to_the_floor():
    result, texts, stamps = _case((1_500, 12_060), 1_401)
    outcome = apply_line_qa(result, texts, stamps)
    assert outcome.result.words_per_line[0][1].end_ms == 1_500 + TRIM_MIN_HOLD_MS
    result, texts, stamps = _case((1_500, 12_060), 2_900)  # stamp + offset 3 000
    outcome = apply_line_qa(result, texts, stamps)
    assert outcome.result.words_per_line[0][1].end_ms == 3_000
    assert outcome.result.lines[1].start_ms == 3_000


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
    placed = _place_unaligned(lines, [0, 9_000, 7_700], 100, frozenset({2}), words, None)
    assert placed[0] == lines[0]  # the sung line keeps its end
    assert placed[1].start_ms == placed[1].end_ms == 7_779  # yields to the next line's start
    assert _cut_at_unaligned(placed, words) == words


def test_when_the_next_line_leaves_less_than_the_floor_the_word_keeps_its_floor():
    # the next aligned line (snapped) starts 200 ms after the last word: the marker sits on it,
    # the word is cut no shorter than its floor (it then reaches past the marker)
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
    placed = _place_unaligned(lines, [900, 2_900, 1_600], 100, frozenset({2}), words, None)
    assert placed[1].start_ms == 1_700
    assert placed[0].end_ms == 1_500 + TRIM_MIN_HOLD_MS
    cut = _cut_at_unaligned(placed, words)
    assert (cut[0][1].start_ms, cut[0][1].end_ms) == (1_500, 1_500 + TRIM_MIN_HOLD_MS)


def test_a_stamp_past_the_next_line_puts_the_marker_on_its_start():
    result, texts, stamps = _case((1_500, 1_700), 30_000)
    outcome = apply_line_qa(result, texts, stamps)
    assert outcome.result.lines[1].start_ms == outcome.result.lines[2].start_ms == 12_100


def test_a_word_starting_exactly_at_the_marker_is_not_cut():
    lines = [LineTiming(1_000, 4_000, "one two", 0.5), LineTiming(3_000, 3_000, MARK, 0.0, True)]
    words = [[AlignedWord(1_000, 2_000, "one", 0.2), AlignedWord(3_000, 4_000, "two", 0.2)], []]
    assert _cut_at_unaligned(lines, words) == words


def test_the_snapped_path_cuts_too():
    # a far line elsewhere is snapped, so QA takes its snapped path
    entries = [
        ("one two", [(1_100, 1_400), (1_500, 12_060)]),
        (MARK, None),
        ("five six", [(12_100, 12_400), (12_500, 12_800)]),
        ("seven eight", [(16_100, 16_400), (16_500, 16_800)]),
        ("nine ten", [(40_100, 40_400), (40_500, 40_800)]),
    ]
    result = _result(entries)
    outcome = apply_line_qa(result, [t for t, _ in entries], [1_000, 2_900, 12_000, 16_000, 20_000])
    assert outcome.flagged == [4]
    assert outcome.result.words_per_line[0][1].end_ms == 3_000
    assert outcome.result.lines[1].start_ms == 3_000


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
    result = _result(entries)
    outcome = apply_line_qa(result, [t for t, _ in entries], [0, 1_263, 14_293, 17_000, 20_000])
    assert outcome.offset_ms == 130 and outcome.adlib_shifted == [0]
    oh = outcome.result.words_per_line[0][0]
    assert oh.start_ms == 130 and oh.end_ms == 1_393
    assert outcome.result.lines[1].start_ms == 1_393


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


# --- the seeded comparison with the sheet without its letterless lines ------------------------


def _random_case(rng: random.Random):
    """Sung lines 100 ms after their stamps (QA's offset is then 100), one far line now and
    then (snapped), ad-libs, letterless lines after a line whose last word is often stretched
    to the next sung line; each letterless stamp + 100 lands on or next to a bound."""
    entries, stamps = [], []
    t = 1_000
    n = rng.randint(4, 9)
    for k in range(n):
        if k > 0 and entries[-1][1] is not None and rng.random() < 0.35:
            entries.append((MARK, None))
            stamps.append(None)  # set once the next line is known
            continue
        adlib = rng.random() < 0.15
        n_words = rng.randint(1, 4)
        spans, w = [], t
        for _ in range(n_words):
            dur = rng.choice([0, 60, 200, 349, 350, 351, 600, 1_200])
            spans.append((w, w + dur))
            w += dur + rng.choice([0, 40, 300])
        text = (
            " ".join(["oh"] * n_words)
            if adlib
            else " ".join(
                rng.choice(["one", "two", "three", "we", "go", "again"]) for _ in range(n_words)
            )
        )
        entries.append((text, spans))
        far = rng.random() < 0.08 and k > 2
        stamps.append(None if rng.random() < 0.05 else t - 100 - (30_000 if far else 0))
        t = w + rng.choice([300, 2_000, 8_000])
    for i, (text, _spans) in enumerate(entries):
        if text != MARK:
            continue
        prev_spans = entries[i - 1][1]
        nxt = next((e[1][0][0] for e in entries[i + 1 :] if e[1] is not None), None)
        if nxt is not None and rng.random() < 0.6:  # the stretched last word
            s0, _ = prev_spans[-1]
            prev_spans[-1] = (s0, max(s0, nxt - 40))
        last_start, last_end = prev_spans[-1]
        bounds = [
            last_start - 1,
            last_start,
            last_start + 1,
            last_start + TRIM_MIN_HOLD_MS - 1,
            last_start + TRIM_MIN_HOLD_MS,
            last_start + TRIM_MIN_HOLD_MS + 1,
            last_end - 1,
            last_end,
            last_end + 1,
        ]
        if nxt is not None:
            bounds += [nxt - 1, nxt, nxt + 1, nxt + 5_000]
        target = rng.choice(bounds)
        stamps[i] = None if rng.random() < 0.1 else target - 100
    return entries, stamps


def _sung(entries):
    return [i for i, (text, _) in enumerate(entries) if text != MARK]


def test_a_marker_never_lengthens_a_word_nor_cuts_one_below_its_floor():
    rng = random.Random(20261120)
    seen = {"cut": 0, "on_stamp": 0, "snapped": 0}
    for _ in range(1_500):
        entries, stamps = _random_case(rng)
        texts = [t for t, _ in entries]
        result = _result(entries)
        with_marks = apply_line_qa(result, texts, stamps)
        a, _ = restore_held_ends(with_marks.result)
        keep = _sung(entries)
        plain_entries = [entries[i] for i in keep]
        plain = apply_line_qa(
            _result(plain_entries), [t for t, _ in plain_entries], [stamps[i] for i in keep]
        )
        b, _ = restore_held_ends(plain.result)
        med = _median_char_ms(result)
        assert with_marks.flagged == [keep[j] for j in plain.flagged]
        seen["snapped"] += bool(with_marks.flagged)
        for j, i in enumerate(keep):
            if is_adlib(texts[i]) or a.sync != "word" or b.sync != "word":
                continue  # an ad-lib is respread over its held span, which a marker can end
            given, wa, wb = result.words_per_line[i], a.words_per_line[i], b.words_per_line[j]
            assert len(wa) == len(wb)
            if len(wa) != len(given):
                continue  # words line QA dropped, in both
            before_mark = i + 1 < len(texts) and texts[i + 1] == MARK
            for g, x, y in zip(given, wa, wb, strict=True):
                assert x.start_ms == y.start_ms  # a marker moves no sung word's start
                assert x.end_ms <= y.end_ms  # ... and lengthens none
                if before_mark and x.start_ms == g.start_ms:
                    floor = min(g.end_ms - g.start_ms, _natural_ms(g, med))
                    assert x.end_ms - x.start_ms >= floor  # ... nor cuts one below its floor
                    seen["cut"] += x.end_ms < y.end_ms
        for i, line in enumerate(a.lines):
            if not line.unaligned:
                continue
            assert line.start_ms == line.end_ms  # a 0 ms marker
            after = next((x.start_ms for x in a.lines[i + 1 :] if not x.unaligned), None)
            assert after is None or line.start_ms <= after
            prev = result.words_per_line[i - 1]
            if (
                stamps[i] is not None
                and not with_marks.flagged
                and prev
                and not is_adlib(texts[i - 1])
                and with_marks.offset_ms == 100
                and not with_marks.degraded_to_line
            ):
                want = stamps[i] + 100 if after is None else min(stamps[i] + 100, after)
                last = prev[-1]
                if want > last.start_ms:
                    floor_end = last.start_ms + min(
                        last.end_ms - last.start_ms, _natural_ms(last, med)
                    )
                    expected = max(want, floor_end)
                    expected = expected if after is None else min(expected, after)
                    assert line.start_ms == expected
                    seen["on_stamp"] += 1
    assert all(v > 30 for v in seen.values()), seen


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
    # report only: the verdict reads none of it
    comparison = rules.compare(old, new, None)
    assert "voiced" not in comparison["letterless"]["cut_voiced"][0]
    assert rules.verdict(comparison) == rules.verdict({**comparison, "letterless": {}})
