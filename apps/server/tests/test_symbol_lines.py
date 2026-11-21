"""Letterless sheet lines and the sheet's own word texts (2026-11-19, candidate for 2.36.0).

`letterless_lines_wordless`: a sheet line with no letter and no digit ('♪') is kept as a line
without words and never given to the aligner; line QA places it on its stamp. Before it, the
romanize=False path aligned the filler "a" (`_fit_to_vocab`) and stored it as the line's one word,
timed wherever the model heard an a-like sound; the uroman path stored one '♪' over the line.

`word_text_from_sheet`: a word's stored text is the sheet's token wherever the aligner was given
another form of it (the romanize=False path drops what its vocabulary lacks); every timing step
still reads the aligner's form.
"""

import json
import sys
import types
from dataclasses import replace

import pytest

from kashi_server.pipeline import alignment
from kashi_server.pipeline.alignment import (
    AlignedWord,
    AlignResult,
    LineTiming,
    _fit_to_vocab,
    regroup_words_into_lines,
    shift_result,
    sung_text,
    with_sheet_texts,
)
from kashi_server.pipeline.line_qa import apply_line_qa, restore_held_ends
from kashi_server.pipeline.windows import Window
from kashi_server.worker import preview_rules as rules

TURKISH = set("abcçdefgğhıijklmnoöprsştuüvyzqwx'")


@pytest.mark.parametrize(
    ("text", "sung"),
    [
        ("♪", False),
        ("♪ ♪", False),
        ("...", False),
        ("- -", False),
        ("", False),
        ("1, 2, 3", True),  # a digit is sung (the overlay's isSungText agrees)
        ("½", True),  # Unicode N, as \p{N}
        ("a", True),
        ("♪ la ♪", True),
        ("İ", True),
        ("Нет", True),
    ],
)
def test_sung_text_is_a_letter_or_a_digit(text, sung):
    assert sung_text(text) is sung


def _rows(*spans):
    return [{"text": t, "start": s, "end": e, "score": -0.1} for t, s, e in spans]


# --- regroup ----------------------------------------------------------------------------------


def test_a_line_not_given_counts_no_word_and_comes_back_wordless_over_its_gap():
    lines = ["one two", "♪", "three"]
    rows = _rows(("one", 1.0, 1.2), ("two", 1.3, 1.6), ("three", 5.0, 5.4))
    got = regroup_words_into_lines(lines, rows, aligned_lines=[True, False, True])
    assert got is not None
    timings, words = got
    assert [line.text for line in timings] == lines
    assert words[1] == []
    assert timings[1] == LineTiming(1600, 5000, "♪", 0.0, unaligned=True)
    assert not timings[0].unaligned and not timings[2].unaligned
    assert [w.text for w in words[2]] == ["three"]


def test_a_first_or_last_line_not_given_sits_at_0_or_on_the_last_word_end():
    rows = _rows(("one", 1.0, 1.2))
    timings, _ = regroup_words_into_lines(
        ["♪", "one", "..."], rows, aligned_lines=[False, True, False]
    )
    assert (timings[0].start_ms, timings[0].end_ms) == (0, 1000)
    assert (timings[2].start_ms, timings[2].end_ms) == (1200, 1200)


def test_a_row_for_a_line_not_given_is_a_token_mismatch():
    rows = _rows(("one", 1.0, 1.2), ("a", 2.0, 2.2), ("three", 5.0, 5.4))
    assert (
        regroup_words_into_lines(["one", "♪", "three"], rows, aligned_lines=[True, False, True])
        is None
    )


def test_without_the_mask_a_letterless_line_is_a_line_of_one_word_as_before():
    rows = _rows(("one", 1.0, 1.2), ("a", 2.0, 2.2), ("three", 5.0, 5.4))
    timings, words = regroup_words_into_lines(["one", "♪", "three"], rows)
    assert [w.text for w in words[1]] == ["a"] and not timings[1].unaligned


def test_shift_keeps_a_line_not_given_inside_its_gap_on_the_shifted_words():
    lines = ["one", "♪", "eye"]
    rows = _rows(("one", 1.0, 1.5), ("eye", 1.5, 2.0))
    timings, words = regroup_words_into_lines(lines, rows, aligned_lines=[True, False, True])
    result = AlignResult("word", timings, words, 0.5)
    # a vowel-initial word moves 80 ms, every other 30: the base offset alone would put the
    # '♪' 50 ms past the start of the line after it
    shifted = shift_result(result, -30, {"vowel": -80, "plosive": -30})
    eye = shifted.words_per_line[2][0]
    gap = shifted.lines[1]
    assert gap.unaligned
    assert gap.start_ms <= eye.start_ms and gap.end_ms == eye.start_ms


# --- line QA ------------------------------------------------------------------------------------


def _doc(entries):
    """entries: (text, word starts or None for a line not given); each word 300 ms long."""
    lines, words = [], []
    for text, starts in entries:
        if starts is None:
            lines.append(LineTiming(0, 0, text, 0.0, unaligned=True))
            words.append([])
            continue
        chunk = [AlignedWord(s, s + 300, t, 0.2) for t, s in zip(text.split(), starts, strict=True)]
        lines.append(LineTiming(chunk[0].start_ms, chunk[-1].end_ms, text, 0.5))
        words.append(chunk)
    return AlignResult("word", lines, words, 0.5, windowed=True)


FOUR = [
    ("one two", [1_100, 1_500]),
    ("three four", [5_100, 5_500]),
    ("♪", None),
    ("five six", [12_100, 12_500]),
    ("seven eight", [16_100, 16_500]),
]
FOUR_STAMPS = [1_000, 5_000, 8_000, 12_000, 16_000]


def test_line_qa_places_a_line_not_given_on_its_stamp_as_a_0_ms_marker():
    outcome = apply_line_qa(_doc(FOUR), [t for t, _ in FOUR], FOUR_STAMPS)
    assert outcome.offset_ms == 100  # the four sung lines; the '♪' has no aligner time
    music = outcome.result.lines[2]
    # a marker: the gap after it stays a gap for the overlay (hold, interlude, lead-in)
    assert (music.start_ms, music.end_ms) == (8_100, 8_100)
    assert outcome.flagged == [] and outcome.result.words_per_line[2] == []
    assert outcome.result.quality_score == 1.0  # agreement over the sung lines only


def _stretched(stamp_ms, held_ms=0):
    """The archive's shape (c9xGRpozwYU line 62): the sung line's last word runs over the whole
    interlude to the next sung line, as a segment-starred window pass gives its last word every
    frame to the slice end once the letterless line's filler is no longer after it."""
    entries = [
        ("one two", [1_100, 1_500]),
        ("♪", None),
        ("five six", [12_100, 12_500]),
        ("seven eight", [16_100, 16_500]),
    ]
    doc = _doc(entries)
    words = [list(chunk) for chunk in doc.words_per_line]
    words[0][1] = AlignedWord(1_500, 12_060, "two", 0.2, held_ms=held_ms)
    lines = [replace(doc.lines[0], end_ms=12_060), *doc.lines[1:]]
    return (
        AlignResult("word", lines, words, 0.5, windowed=True),
        [t for t, _ in entries],
        [1_000, stamp_ms, 12_000, 16_000],
    )


def test_a_last_word_stretched_over_the_interlude_is_cut_at_the_stamp():
    result, texts, stamps = _stretched(3_000, held_ms=5_000)
    outcome = apply_line_qa(result, texts, stamps)
    lines, words = outcome.result.lines, outcome.result.words_per_line
    assert outcome.offset_ms == 100
    assert (lines[1].start_ms, lines[1].end_ms) == (3_100, 3_100)  # on its stamp, not 12 060
    # line QA cuts nothing (the restore must read the marker-free median and bounds) ...
    assert (words[0][1].start_ms, words[0][1].end_ms) == (1_500, 12_060)
    assert lines[0].end_ms == 12_060
    # ... the marker cuts after the restore, held end included
    restored, _ = restore_held_ends(outcome.result)
    assert restored.words_per_line[0][1].end_ms == 3_100 == restored.lines[0].end_ms
    assert restored.lines[1].start_ms == 3_100
    # without the letterless line (the sheet without it) the same word keeps the stretch: the
    # line only ever shortens it
    plain = AlignResult(
        "word",
        [result.lines[0], *result.lines[2:]],
        [result.words_per_line[0], *result.words_per_line[2:]],
        0.5,
        windowed=True,
    )
    ordinary = apply_line_qa(plain, [texts[0], *texts[2:]], [stamps[0], *stamps[2:]])
    assert ordinary.result.words_per_line[0][1].end_ms == 12_060 >= words[0][1].end_ms


def test_a_stamp_before_the_last_word_starts_cuts_nothing():
    result, texts, stamps = _stretched(1_200)  # stamp + offset 1 300: before the last word starts
    outcome = apply_line_qa(result, texts, stamps)
    lines, words = outcome.result.lines, outcome.result.words_per_line
    assert words[0][1].end_ms == 12_060  # the ordinary end, untouched
    assert lines[1].start_ms == lines[1].end_ms == 12_060  # after the line before it


def test_a_marker_before_the_last_word_s_start_follows_the_line_and_cuts_nothing():
    from kashi_server.pipeline.line_qa import _settle_after_restore

    lines = [
        LineTiming(1_000, 3_900, "one two three four", 0.5),
        LineTiming(3_000, 3_000, "♪", 0.0, unaligned=True, placed="stamp"),
        LineTiming(6_000, 6_300, "five", 0.5),
    ]
    words = [
        [
            AlignedWord(1_000, 2_000, "one", 0.2),
            AlignedWord(2_500, 4_000, "two", 0.2),
            AlignedWord(3_000, 3_900, "three", 0.2),  # starts on the marker, longer than 350 ms
            AlignedWord(3_500, 3_900, "four", 0.2),
        ],
        [],
        [AlignedWord(6_000, 6_300, "five", 0.2)],
    ]
    # a snapped line's rescued words can lie past the marker: the line's last word starts after
    # it, so the stamp is no end of that line; the marker follows it and nothing is cut
    settled, cut = _settle_after_restore(lines, words, None)
    assert cut == words
    assert (settled[1].start_ms, settled[1].placed) == (3_900, "previous")
    assert settled[0] == lines[0]


def test_a_line_not_given_counts_in_no_median_and_no_flag():
    # Three sung lines 0, 200 and 400 ms late (median 200); the '♪' sits at 0 against its 8 s
    # stamp, which as a counted line would pull the median to 100 and be flagged.
    entries = [
        ("one two", [1_000, 1_400]),
        ("three four", [5_200, 5_600]),
        ("♪", None),
        ("five six", [12_400, 12_800]),
    ]
    stamps = [1_000, 5_000, 8_000, 12_000]
    outcome = apply_line_qa(_doc(entries), [t for t, _ in entries], stamps)
    assert outcome.offset_ms == 200
    assert outcome.flagged == []
    assert (outcome.result.lines[2].start_ms, outcome.result.lines[2].end_ms) == (8_200, 8_200)


def test_a_line_not_given_never_starts_before_the_line_before_it_ends():
    stamps = [1_000, 5_000, 5_100, 12_000, 16_000]  # a '♪' stamp inside the line before it
    outcome = apply_line_qa(_doc(FOUR), [t for t, _ in FOUR], stamps)
    assert outcome.result.lines[2].start_ms == outcome.result.lines[1].end_ms == 5_800


def test_a_line_not_given_never_passes_the_next_sung_line():
    stamps = [1_000, 5_000, 30_000, 12_000, 16_000]  # a '♪' stamp past its neighbour
    outcome = apply_line_qa(_doc(FOUR), [t for t, _ in FOUR], stamps)
    lines = outcome.result.lines
    assert lines[2].start_ms == lines[3].start_ms == 12_100  # it yields; the sung line stays
    assert [w.start_ms for w in outcome.result.words_per_line[3]] == [12_100, 12_500]


def test_agreement_counts_sung_lines_only():
    # line 4 is flagged (sung 9 s from its stamp): 1 of 4 sung referenced lines damaged
    entries = [*FOUR[:4], ("seven eight", [25_100, 25_500])]
    outcome = apply_line_qa(_doc(entries), [t for t, _ in entries], FOUR_STAMPS)
    assert outcome.flagged == [4]
    assert outcome.result.quality_score == 0.75


def test_a_degraded_document_places_it_like_every_line_and_counts_it_in_no_agreement():
    # a line-sync result (the aligner's fallback): line mode; one of four sung lines flagged
    entries = [*FOUR[:4], ("seven eight", [25_100, 25_500])]
    doc = _doc(entries)
    doc = AlignResult("line", doc.lines, [], 0.5, windowed=True)
    outcome = apply_line_qa(doc, [t for t, _ in entries], FOUR_STAMPS)
    assert outcome.degraded_to_line and outcome.flagged == [4]
    assert outcome.result.quality_score == 0.75
    assert outcome.result.lines[2].start_ms == 8_100  # its stamp + the clock offset, as every line


def test_the_line_fallback_gives_a_line_not_given_no_share():
    rows = _rows(("one", 1.0, 1.2), ("two", 2.0, 2.2), ("three", 3.0, 3.2), ("four", 4.0, 4.2))
    result = alignment._line_only_fallback(
        ["one two", "♪", "three four"], rows, True, aligned_lines=[True, False, True]
    )
    assert [line.unaligned for line in result.lines] == [False, True, False]
    assert (result.lines[1].start_ms, result.lines[1].end_ms) == (2_200, 2_200)
    assert (result.lines[2].start_ms, result.lines[2].end_ms) == (3_000, 4_200)


def test_too_few_sung_references_leave_it_where_regroup_put_it():
    entries = [("one two", [1_100, 1_500]), ("♪", None), ("three four", [5_100, 5_500])]
    doc = _doc(entries)
    doc = AlignResult(
        "word",
        [doc.lines[0], LineTiming(1_800, 5_100, "♪", 0.0, True), doc.lines[2]],
        doc.words_per_line,
        0.5,
        windowed=True,
    )
    outcome = apply_line_qa(doc, [t for t, _ in entries], [1_000, 3_000, 5_000])
    assert (outcome.result.lines[1].start_ms, outcome.result.lines[1].end_ms) == (1_800, 1_800)


def _held_before(placed):
    entries = [("one two", [1_100, 1_500]), ("♪", None), ("three four", [5_100, 5_500])]
    doc = _doc(entries)
    words = [list(chunk) for chunk in doc.words_per_line]
    words[0][1] = AlignedWord(1_500, 1_800, "two", 0.2, held_ms=3_000)
    marker = LineTiming(2_000, 2_000, "♪", 0.0, True, placed)
    lines = [doc.lines[0], marker, doc.lines[2]]
    # enough words for the document's median speed: 300 ms per character, so "two" may be
    # restored to 2 700 ms (3 x) and its natural length (its floor) is 900 ms
    filler = [("w", [k * 1_000 + 10_000]) for k in range(8)]
    extra = _doc(filler)
    result = AlignResult(
        "word", lines + extra.lines, words + extra.words_per_line, 0.5, windowed=True
    )
    restored, _ = restore_held_ends(result)
    return restored.words_per_line[0][1], restored.lines[1], restored.lines[0]


def test_a_held_end_is_cut_at_a_marker_on_its_stamp_but_not_below_its_floor():
    two, marker, line = _held_before("stamp")
    # restored as if no '♪' were there (to 4 200), then cut at the stamp (2 000), but not
    # below the floor (900 ms): 2 400, and the marker moves there
    assert (two.start_ms, two.end_ms) == (1_500, 2_400)
    assert (marker.start_ms, marker.end_ms, marker.placed) == (2_400, 2_400, "floor")
    assert line.end_ms == 2_400


def test_a_marker_off_its_stamp_shortens_nothing_and_follows_the_line_before():
    for placed in ("previous", "stampless"):
        two, marker, line = _held_before(placed)
        assert (two.start_ms, two.end_ms) == (1_500, 4_200)  # what the sheet without it gives
        assert marker.start_ms == marker.end_ms == 4_200 == line.end_ms


# --- sheet texts --------------------------------------------------------------------------------


def _fit(token):
    return _fit_to_vocab(token, TURKISH)


def test_sheet_texts_map_one_to_one_and_count():
    line = "İyi, hâl (evet) ♪"
    rows = _rows(*[(_fit(t), 1.0 + k, 1.5 + k) for k, t in enumerate(line.split())])
    assert [r["text"] for r in rows] == ["Iyi", "hal", "evet", "a"]
    tally: dict = {}
    timings, words = regroup_words_into_lines([line], rows, sheet_form=_fit, tally=tally)
    assert [w.text for w in words[0]] == ["Iyi", "hal", "evet", "a"]  # timing reads these
    assert [w.sheet_text for w in words[0]] == ["İyi,", "hâl", "(evet)", "♪"]
    assert tally == {"sheet_words": 4}
    shown = with_sheet_texts(AlignResult("word", timings, words, 0.5))
    assert [w.text for w in shown.words_per_line[0]] == line.split()
    assert all(w.sheet_text == "" for w in shown.words_per_line[0])
    assert [(w.start_ms, w.end_ms) for w in shown.words_per_line[0]] == [
        (w.start_ms, w.end_ms) for w in words[0]
    ]


def test_a_line_whose_forms_do_not_map_keeps_the_aligners_texts():
    rows = _rows(("bir", 1.0, 1.2), ("iki", 1.3, 1.5))
    tally: dict = {}
    _, words = regroup_words_into_lines(["bir üç"], rows, sheet_form=_fit, tally=tally)
    assert [w.sheet_text for w in words[0]] == ["", ""]
    assert tally == {"sheet_kept": 1}


def test_an_equal_form_sets_no_sheet_text_and_the_result_is_unchanged():
    rows = _rows(("one", 1.0, 1.2))
    timings, words = regroup_words_into_lines(["one"], rows, sheet_form=lambda t: t)
    result = AlignResult("word", timings, words, 0.5)
    assert words[0][0].sheet_text == "" and with_sheet_texts(result) is result


def test_without_sheet_form_no_word_carries_a_sheet_text():
    rows = _rows(("Iyi", 1.0, 1.2))
    _, words = regroup_words_into_lines(["İyi,"], rows)
    assert words[0][0].sheet_text == ""


class Vocabulary:
    def get_vocab(self):
        return {ch: k for k, ch in enumerate(sorted(TURKISH))}


def test_align_maps_the_romanize_false_forms_back_to_the_sheet(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: SliceAudio(8 * 16_000)),
    )
    model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (model, Vocabulary()))

    def fake(_m, tokenizer, piece, texts, language, star="segment", *, romanize, **_k):
        fitted = _fit_to_vocab(" ".join(texts), alignment._vocab_chars(tokenizer))
        return [
            {"text": t, "start": 1.0 + k, "end": 1.5 + k, "score": -0.1}
            for k, t in enumerate(fitted.split())
        ]

    monkeypatch.setattr(alignment, "_align_texts", fake)
    args = (tmp_path / "x.wav", ["İyi, hâl"], "tur", None, "m", False, 0, None)
    on = alignment.align(*args, letterless_wordless=False, sheet_texts=True)
    off = alignment.align(*args, letterless_wordless=False, sheet_texts=False)
    assert [(w.text, w.sheet_text) for w in on.words_per_line[0]] == [
        ("Iyi", "İyi,"),
        ("hal", "hâl"),
    ]
    assert [w.sheet_text for w in off.words_per_line[0]] == ["", ""]
    # the uroman path's texts are the sheet's already: nothing to give back
    uroman = alignment.align(*args[:5], True, 0, None, letterless_wordless=False, sheet_texts=True)
    assert [w.sheet_text for w in uroman.words_per_line[0]] == ["", ""]
    # with no switch passed, align() reads the setting itself, as the worker's job does: on by
    # default since 2.36.0, off where the setting is switched off (2.35.0's form, the rollback)
    from kashi_server.worker import preview

    def sheet_texts():
        return [w.sheet_text for w in alignment.align(*args).words_per_line[0]]

    assert sheet_texts() == ["İyi,", "hâl"]
    with preview.overridden({"word_text_from_sheet": False}):
        assert sheet_texts() == ["", ""]
    assert sheet_texts() == ["İyi,", "hâl"]


def test_align_and_review_hands_on_the_sheets_texts_after_the_timing_chain(monkeypatch, tmp_path):
    from kashi_server.pipeline.lrclib import LyricsText
    from kashi_server.worker import process

    words = [AlignedWord(1_000, 1_300, "Iyi", 0.2, sheet_text="İyi,", held_ms=500)]
    aligned = AlignResult("word", [LineTiming(1_000, 1_300, "İyi,", 0.5)], [words], 0.5)
    lyrics = LyricsText(["İyi,"], "İyi,", 1, True)
    monkeypatch.setattr(process, "_shift_sheet", lambda *a: (lyrics, None, []))
    monkeypatch.setattr(process, "_align_stage", lambda *a, **k: (aligned, False, tmp_path))
    monkeypatch.setattr(process, "detect_onsets", lambda wav: None)
    job = types.SimpleNamespace(id=1)
    reviewed = process.align_and_review(None, job, tmp_path, tmp_path, lyrics)  # type: ignore[arg-type]
    assert reviewed.aligned.words_per_line[0][0].text == "Iyi"
    assert [w.text for w in reviewed.result.words_per_line[0]] == ["İyi,"]


# --- align(): what the passes are given ---------------------------------------------------------

LINES = ["one two three", "♪", "four five six", "seven eight nine"]
STAMPS = [1_000, 5_000, 9_000, 13_000]
TOTAL_S = 18
AT = {
    "one": 1.1,
    "two": 1.5,
    "three": 1.9,
    "♪": 5.5,
    "four": 9.1,
    "five": 9.5,
    "six": 9.9,
    "seven": 13.1,
    "eight": 13.5,
    "nine": 13.9,
}


class SliceAudio:
    """A 16 kHz mono stand-in that remembers where each slice starts."""

    def __init__(self, n_samples: int, start: int = 0):
        self.shape = (n_samples,)
        self.start = start

    def __getitem__(self, key):
        _, window = key
        stop = min(window.stop, self.start + self.shape[0])
        return SliceAudio(stop - window.start, window.start)


def run(monkeypatch, tmp_path, wordless, lines=LINES, **kwargs):
    seen: list = []
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: SliceAudio(TOTAL_S * 16_000)),
    )
    model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (model, object()))

    def fake_align_texts(_m, _t, piece, texts, language, star, *, romanize, exact_stride, **kw):
        seen.append((piece.start, piece.shape[0], star, list(texts), kw.get("starts_ms")))
        offset = piece.start / 16_000
        return [
            {
                "text": word,
                "start": AT[word] - offset,
                "end": AT[word] - offset + 0.3,
                "score": -0.1,
            }
            for word in " ".join(texts).split()
        ]

    monkeypatch.setattr(alignment, "_align_texts", fake_align_texts)
    kwargs.setdefault("first_heard_ms", None)
    # "settings": align() reads both switches itself, as the worker's job does
    switches = (
        {} if wordless == "settings" else {"letterless_wordless": wordless, "sheet_texts": False}
    )
    result = alignment.align(
        tmp_path / "x.wav",
        lines,
        "eng",
        STAMPS,
        "m",
        True,
        0,
        None,
        window_star="edges",
        **switches,
        **kwargs,
    )
    return result, seen


def test_align_reads_the_letterless_setting_itself(monkeypatch, tmp_path):
    from kashi_server.worker import preview

    # on by default since 2.36.0; switched off, the '♪' is aligned as 2.35.0 aligned it
    default, _ = run(monkeypatch, tmp_path, "settings")
    assert default.words_per_line[1] == [] and default.lines[1].unaligned
    with preview.overridden({"letterless_lines_wordless": False}):
        off, _ = run(monkeypatch, tmp_path, "settings")
    assert [w.text for w in off.words_per_line[1]] == ["♪"] and not off.lines[1].unaligned
    after, _ = run(monkeypatch, tmp_path, "settings")
    assert after == default


def test_the_first_heard_rule_counts_a_letterless_line_as_no_words(monkeypatch, tmp_path):
    counts: list = []

    def spy(results, whole, line_words, *a, **k):
        counts.append(list(line_words))
        return results, 0

    monkeypatch.setattr(alignment, "start_at_heard_onset", spy)
    run(monkeypatch, tmp_path, True, first_heard_ms=500)
    run(monkeypatch, tmp_path, False, first_heard_ms=500)
    assert counts == [[3, 0, 3, 3], [3, 1, 3, 3]]


def test_a_sheet_with_no_sung_line_is_aligned_as_before(monkeypatch, tmp_path):
    marks = ["♪", "♪", "♪", "♪"]
    AT_ALL = {"♪": 5.5}
    monkeypatch.setattr(sys.modules[__name__], "AT", {**AT, **AT_ALL})
    result, seen = run(monkeypatch, tmp_path, True, lines=marks, whole_vote=False)
    assert seen and all(texts for *_, texts, _ in seen)
    assert not any(line.unaligned for line in result.lines)


def test_the_passes_are_never_given_a_letterless_line_and_cut_the_same_slices(
    monkeypatch, tmp_path
):
    before, seen_before = run(monkeypatch, tmp_path, False)
    after, seen_after = run(monkeypatch, tmp_path, True)
    # the same windows over the same audio (the evidence's slices), the whole track included
    assert [(s, n, star) for s, n, star, _, _ in seen_before] == [
        (s, n, star) for s, n, star, _, _ in seen_after
    ]
    assert all("♪" not in " ".join(texts) for *_, texts, _ in seen_after)
    assert any("♪" in " ".join(texts) for *_, texts, _ in seen_before)
    # the window of the '♪' and the line after it keeps that line's region
    held = [regions for _, _, star, texts, regions in seen_after if texts == ["four five six"]]
    assert held and held[0] is not None and len(held[0]) == 1 and held[0][0] is not None
    assert [w.text for w in before.words_per_line[1]] == ["♪"]
    assert after.words_per_line[1] == [] and after.lines[1].unaligned
    assert [line.text for line in after.lines] == LINES


def test_a_window_with_no_sung_line_is_not_aligned(monkeypatch, tmp_path):
    seen: list = []

    def fake(_m, _t, piece, texts, *a, **k):
        seen.append(list(texts))
        return []

    monkeypatch.setattr(alignment, "_align_texts", fake)
    window = Window(0, 4_000, [0], 0, 4_000)
    # through the wrapper: it puts the guard's window label back afterwards
    out = alignment._align_windows(
        None,
        None,
        SliceAudio(4_000 * 16),
        [window],
        ["♪"],
        "eng",
        True,
        star="edges",
        aligned_lines=[False],
    )
    assert out == [] and seen == []


# --- the preview's rules ------------------------------------------------------------------------


def _line(text, start, words=None):
    out = {"text": text, "start_ms": start, "end_ms": start + 1_000}
    if words:
        out["words"] = [{"text": t, "start_ms": s, "end_ms": s + 200} for t, s in words]
    return out


def test_a_letterless_line_losing_its_word_is_no_lost_lyric_and_no_unjudged_move():
    old = {
        "lines": [
            _line("one", 1_000, [("one", 1_000)]),
            _line("♪", 2_000, [("a", 2_000)]),
            _line("two", 9_000, [("two", 9_000)]),
        ]
    }
    new = {
        "lines": [
            _line("one", 1_000, [("one", 1_000)]),
            _line("♪", 5_000),
            _line("two", 9_000, [("two", 9_000)]),
        ]
    }
    comparison = rules.compare(old, new, None, letterless_wordless=True)
    assert comparison["w3"] == [] and comparison["w2_unjudged"] == []
    assert [row["line"] for row in comparison["moved_lines"]] == [1]  # still listed
    # a build that gave the '♪' to the aligner (the production setting) is judged as in 0.43.0
    production = rules.compare(old, new, None)
    assert production["w3"] == [1] and production["w2_unjudged"] == [1]
    assert rules.vetoes(production) == ["W3"]
    # a sung line losing its words still vetoes
    new["lines"][2] = _line("two", 9_000)
    assert rules.compare(old, new, None, letterless_wordless=True)["w3"] == [2]


def test_the_rules_report_what_word_ends_and_neighbouring_starts_did():
    old = {
        "lines": [
            _line("one two", 1_000, [("one", 1_000), ("two", 1_500)]),
            _line("♪", 2_000, [("a", 2_000)]),
            _line("three four", 9_000, [("three", 9_000), ("four", 9_400)]),
            _line("five", 12_000, [("five", 12_000)]),
        ]
    }
    new = json.loads(json.dumps(old))
    new["lines"][1].pop("words")
    two = new["lines"][0]["words"][1]
    two["end_ms"] = two["start_ms"] + 2_000  # 200 -> 2 000 ms: grew by more than 1 s
    new["lines"][0]["end_ms"] = two["end_ms"]  # a sung line's end moved by more than 500 ms
    new["lines"][2]["words"][0]["start_ms"] += 150  # a neighbour's start
    new["lines"][3]["words"][0]["start_ms"] += 150  # not a neighbour
    for comparison in (
        rules.compare(old, new, None),
        rules.compare(old, new, None, letterless_wordless=True),
    ):
        assert comparison["ends"] == {
            "words_grew_over_1000": 1,
            "largest_final_hold": {"line": 0, "old_ms": 200, "new_ms": 2_000},
            "sung_ends_moved_over_500": 1,
            "neighbour_starts_moved_over_100": 1,
        }
        assert rules.vetoes(comparison) in ([], ["W3"])  # report only: no veto of its own


def test_the_rules_report_qa_s_offset_and_way_out():
    def doc(offset, flagged=0, basis="anchors", sync="word"):
        qa = {"offset_ms": offset, "flagged": flagged}
        return {"sync": sync, "alignment": {"qa": qa, "quality_basis": basis}, "lines": []}

    assert rules.compare(doc(0), doc(10), None)["qa"] == {
        "offset_ms": [0, 10],
        "exit": ["clean", "clean"],
    }
    assert rules.compare(doc(0, flagged=2), doc(0, basis="line-anchors", sync="line"), None)["qa"][
        "exit"
    ] == ["snapped", "line"]
    assert rules.qa_exit(doc(0, basis="ctc-probs")) == "unreferenced"
