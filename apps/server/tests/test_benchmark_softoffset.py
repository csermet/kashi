"""Faz 6 P2 spike: per-line soft-offset transform (bench-only module)."""

from benchmarks.softoffset import MIN_KNOTS, apply_soft_offset, line_offsets
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming


def _result(starts: list[int], *, line_len: int = 1800) -> AlignResult:
    lines = [
        LineTiming(s, s + line_len, f"line {i}", 0.8) for i, s in enumerate(starts)
    ]
    words = [
        [
            AlignedWord(s, s + 400, f"w{i}a", 0.7),
            AlignedWord(s + 500, s + 900, f"w{i}b", 0.7),
        ]
        for i, s in enumerate(starts)
    ]
    return AlignResult(
        sync="word", lines=lines, words_per_line=words, quality_score=0.9, windowed=True
    )


def test_uniform_drift_is_recovered_exactly_by_both_modes():
    # Whole take sits 600ms late; anchors carry the truth.
    starts = [1600, 5600, 9600, 13600, 17600]
    anchors: list[int | None] = [s - 600 for s in starts]
    for mode in ("soft-median", "soft-pl"):
        fixed = apply_soft_offset(_result(starts), anchors, mode=mode)
        assert [line.start_ms for line in fixed.lines] == [s - 600 for s in starts]
        # Words ride rigidly with their line.
        assert fixed.words_per_line[2][0].start_ms == starts[2] - 600
        assert fixed.words_per_line[2][1].end_ms == starts[2] + 900 - 600


def test_median_filter_kills_a_single_noisy_anchor():
    starts = [1000, 5000, 9000, 13000, 17000]
    anchors: list[int | None] = [1000, 5000, 10800, 13000, 17000]  # one +1800 outlier
    offsets = line_offsets(_result(starts), anchors, mode="soft-median")
    assert offsets is not None
    # The outlier's own line takes its NEIGHBOURHOOD median (0), not +1800.
    assert offsets[2] == 0
    assert all(o == 0 for o in offsets)


def test_anchorless_lines_borrow_and_interpolate():
    starts = [1000, 5000, 9000, 13000, 17000]
    # Growing drift: anchors say lines drift progressively later.
    anchors: list[int | None] = [1200, None, 9600, None, 18000]
    step = line_offsets(_result(starts), anchors, mode="soft-median")
    pl = line_offsets(_result(starts), anchors, mode="soft-pl")
    assert step is not None and pl is not None
    # soft-median: anchor-less line 1 borrows nearest knot's filtered delta.
    assert step[1] in (step[0], step[2])
    # soft-pl: anchor-less line 1 sits BETWEEN its neighbours' deltas.
    lo, hi = sorted((pl[0], pl[2]))
    assert lo <= pl[1] <= hi
    # Flat extrapolation beyond the last knot.
    assert pl[4] == pl[-1]


def test_too_few_anchors_is_a_noop():
    starts = [1000, 5000, 9000]
    anchors: list[int | None] = [1500, None, None]  # 1 < MIN_KNOTS
    assert MIN_KNOTS > 1
    result = _result(starts)
    assert apply_soft_offset(result, anchors, mode="soft-pl") is result


def test_clamp_prevents_line_order_inversion():
    starts = [1000, 1400, 9000, 13000, 17000]
    # Anchors try to drag line 0 past line 1 (huge +5s on an early line
    # after filtering would still clamp) — order must survive.
    anchors: list[int | None] = [6000, 6100, 9100, 13100, 17100]
    fixed = apply_soft_offset(_result(starts, line_len=300), anchors, mode="soft-median")
    line_starts = [line.start_ms for line in fixed.lines]
    assert line_starts == sorted(line_starts)
    for line, words in zip(fixed.lines, fixed.words_per_line, strict=True):
        assert line.end_ms >= line.start_ms
        assert words[0].start_ms <= words[1].start_ms
