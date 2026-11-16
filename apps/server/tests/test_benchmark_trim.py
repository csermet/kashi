"""The bench's copy of the word-end sustain trim (benchmarks/trim.py).

Production ran it inside line QA until pipeline 2.31.0; these are its tests,
moved with it (tests/test_line_qa.py, Faz 5 P1).
"""

from benchmarks.trim import trim_word_ends
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.line_qa import TRIM_MAX_HOLD_MS


def _crisp_line(start_ms: int, text: str) -> tuple[LineTiming, list[AlignedWord]]:
    """Words at a steady 100 ms/char with 100 ms gaps — a crisp reference tempo."""
    words = []
    t = start_ms
    for token in text.split():
        words.append(AlignedWord(start_ms=t, end_ms=t + len(token) * 100, text=token, prob=0.5))
        t = words[-1].end_ms + 100
    return LineTiming(start_ms, words[-1].end_ms, text, 0.5), words


def _trim_fixture(extra: list[tuple[LineTiming, list[AlignedWord]]]) -> AlignResult:
    base = [
        _crisp_line(0, "alpha beta gamma"),
        _crisp_line(3000, "delta epsilon zeta"),
        _crisp_line(6000, "eta theta iota"),
    ]
    lines = [line for line, _ in base + extra]
    words = [chunk for _, chunk in base + extra]
    return AlignResult(sync="word", lines=lines, words_per_line=words, quality_score=0.8)


def test_trim_caps_a_hanging_end_and_leaves_crisp_words_alone():
    # "so" sung at ~100 ms/char but CTC extends its end 14 s into the gap.
    hang = (LineTiming(10_000, 24_000, "so", 0.5), [AlignedWord(10_000, 24_000, "so", 0.5)])
    trimmed_result, trimmed = trim_word_ends(_trim_fixture([hang]))
    assert trimmed == 1
    # med 100 ms/char -> allowed = max(350, 2*100*3) = 600.
    assert trimmed_result.words_per_line[3][0].end_ms == 10_600
    # Crisp words are all under the 350 ms floor — byte-for-byte untouched.
    assert trimmed_result.words_per_line[:3] == _trim_fixture([hang]).words_per_line[:3]
    # Line timings are DISPLAY-hold semantics — never touched by the trim.
    assert trimmed_result.lines[3].end_ms == 24_000


def test_trim_never_extends_and_respects_the_max_hold_cap():
    long_word = "abcdefghijklmnopqrstuvwxyz"  # 26 chars -> raw cap 7800 > MAX
    hang = (
        LineTiming(10_000, 40_000, long_word, 0.5),
        [AlignedWord(10_000, 40_000, long_word, 0.5)],
    )
    trimmed_result, trimmed = trim_word_ends(_trim_fixture([hang]))
    assert trimmed == 1
    assert trimmed_result.words_per_line[3][0].end_ms == 10_000 + TRIM_MAX_HOLD_MS


def test_trim_exempts_adlib_lines_entirely():
    # Sustained hook spans are rederive's business (user-approved aesthetics):
    # neither trimmed nor counted into the median char speed.
    hook = (
        LineTiming(10_000, 18_000, "Ooh ooh", 0.5),
        [AlignedWord(10_000, 14_000, "Ooh", 0.5), AlignedWord(14_000, 18_000, "ooh", 0.5)],
    )
    trimmed_result, trimmed = trim_word_ends(_trim_fixture([hook]))
    assert trimmed == 0
    assert trimmed_result.words_per_line[3][0].end_ms == 14_000


def test_trim_skips_documents_with_too_small_a_sample():
    line, chunk = _crisp_line(0, "hi yo")  # 2 measurable words < the sample floor
    hang = (LineTiming(5000, 30_000, "so", 0.5), [AlignedWord(5000, 30_000, "so", 0.5)])
    result = AlignResult(
        sync="word",
        lines=[line, hang[0]],
        words_per_line=[chunk, hang[1]],
        quality_score=0.8,
    )
    trimmed_result, trimmed = trim_word_ends(result)
    assert trimmed == 0
    assert trimmed_result.words_per_line[1][0].end_ms == 30_000


def test_trim_is_a_noop_on_line_sync():
    result = AlignResult(
        sync="line", lines=[LineTiming(0, 1000, "a", 0.5)], words_per_line=[], quality_score=0.8
    )
    assert trim_word_ends(result) == (result, 0)
