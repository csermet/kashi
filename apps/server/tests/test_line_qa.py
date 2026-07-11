"""Line QA: snap drifted lines to lrclib synced times, drop their words.

The synthetic fixtures model the real TiK ToK failure (2026-07-11): a chorus
block dumped ~15 s ahead of the audio while the surrounding lines were fine.
"""

from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.line_qa import (
    DRIFT_THRESHOLD_MS,
    apply_line_qa,
)


def _words(start_ms: int, texts: list[str], *, prob: float = 0.2) -> list[AlignedWord]:
    words = []
    t = start_ms
    for text in texts:
        words.append(AlignedWord(start_ms=t, end_ms=t + 300, text=text, prob=prob))
        t += 400
    return words


def _result(line_specs: list[tuple[int, str]], *, sync: str = "word") -> AlignResult:
    """line_specs: (start_ms, text); words derived from the text."""
    lines = []
    words_per_line = []
    for start_ms, text in line_specs:
        tokens = text.split()
        chunk = _words(start_ms, tokens)
        lines.append(
            LineTiming(start_ms=start_ms, end_ms=start_ms + 400 * len(tokens), text=text, score=0.5)
        )
        words_per_line.append(chunk)
    return AlignResult(
        sync=sync,
        lines=lines,
        words_per_line=words_per_line if sync == "word" else [],
        quality_score=0.8,
    )


def test_tiktok_pattern_snaps_the_drifted_block_and_drops_its_words():
    # Lines 0-2 agree with lrclib; line 3 is sung at 46 s but aligned at 34 s.
    specs = [(1000, "one a"), (5000, "two b"), (9000, "three c"), (34_000, "four d")]
    refs = [1000, 5000, 9000, 46_000]
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], refs)

    assert not outcome.degraded_to_line
    assert outcome.flagged == [3]
    snapped = outcome.result.lines[3]
    assert snapped.start_ms == 46_000  # ref + ~0 offset
    assert outcome.result.words_per_line[3] == []  # words dropped
    assert outcome.result.words_per_line[0]  # neighbours untouched
    assert outcome.result.lines[0].start_ms == 1000
    assert outcome.result.sync == "word"


def test_correct_time_zero_score_line_is_left_alone():
    # Score is NOT a flagging signal (measured: good lines can score 0.00).
    specs = [(1000, "one a"), (5000, "two b"), (9000, "three c"), (13_000, "four d")]
    result = _result(specs)
    zero_scored = [
        LineTiming(line.start_ms, line.end_ms, line.text, 0.0) if i == 1 else line
        for i, line in enumerate(result.lines)
    ]
    result = AlignResult("word", zero_scored, result.words_per_line, result.quality_score)
    outcome = apply_line_qa(result, [s[1] for s in specs], [1000, 5000, 9000, 13_000])
    assert outcome.flagged == []
    assert outcome.result.words_per_line[1]  # words kept


def test_consistent_global_offset_is_not_flagged():
    # Aligner runs 1.2 s late everywhere (different audio edit) — the median
    # offset absorbs it and nothing is flagged.
    specs = [(2200, "one a"), (6200, "two b"), (10_200, "three c"), (14_200, "four d")]
    refs = [1000, 5000, 9000, 13_000]
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], refs)
    assert outcome.flagged == []
    assert outcome.offset_ms == 1200
    assert [line.start_ms for line in outcome.result.lines] == [s[0] for s in specs]


def test_majority_drift_degrades_the_whole_document_to_line_sync():
    specs = [(1000, "one a"), (20_000, "two b"), (30_000, "three c"), (40_000, "four d")]
    refs = [1000, 5000, 9000, 13_000]  # 3 of 4 referenced lines are far off
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], refs)

    assert outcome.degraded_to_line
    assert outcome.result.sync == "line"
    assert outcome.result.words_per_line == []
    # Raw lrclib starts, ends chained to the next start.
    assert [line.start_ms for line in outcome.result.lines] == refs
    assert outcome.result.lines[0].end_ms == refs[1]
    # Last line keeps its old duration (2 tokens * 400ms).
    assert outcome.result.lines[3].end_ms == refs[3] + 800


def test_line_sync_input_with_synced_reference_moves_to_lrclib_times():
    # _line_only_fallback output: proportional spread, sync="line", no words.
    specs = [(0, "one a"), (2000, "two b"), (4000, "three c"), (6000, "four d")]
    refs = [1000, 5000, 9000, 13_000]
    outcome = apply_line_qa(_result(specs, sync="line"), [s[1] for s in specs], refs)
    assert outcome.degraded_to_line
    assert [line.start_ms for line in outcome.result.lines] == refs
    assert outcome.result.quality_score == 0.8  # untouched on the degrade path


def test_no_reference_only_clamps_monotonicity():
    specs = [(5000, "one a"), (1000, "two b"), (9000, "three c")]
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], None)
    assert outcome.flagged == [] and outcome.offset_ms == 0
    starts = [line.start_ms for line in outcome.result.lines]
    assert starts == sorted(starts)  # backwards start clamped forward
    assert outcome.result.words_per_line[1]  # nothing dropped


def test_too_few_stamped_references_skips_qa():
    specs = [(1000, "one a"), (5000, "two b"), (9000, "three c"), (34_000, "four d")]
    refs = [1000, None, None, 46_000]  # only 2 usable stamps < MIN_REFERENCE_LINES
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], refs)
    assert outcome.flagged == []
    assert outcome.result.lines[3].start_ms == 34_000  # untouched


def test_quality_score_recomputed_from_surviving_words():
    from kashi_server.pipeline.alignment import quality_from_probs

    specs = [(1000, "one a"), (5000, "two b"), (9000, "three c"), (34_000, "four d")]
    refs = [1000, 5000, 9000, 46_000]
    base = _result(specs)
    # Survivors get a mid-ramp prob (0.05 -> ~0.46); the drifted line's words a
    # huge one. If the dropped words leaked into the recompute, the mean would
    # jump above the ramp's high anchor and the score would hit 1.0.
    words = [_words(s, t.split(), prob=0.05) for s, t in specs[:3]]
    words.append(_words(34_000, "four d".split(), prob=1.0))
    result = AlignResult("word", base.lines, words, 0.8)
    outcome = apply_line_qa(result, [s[1] for s in specs], refs)

    assert outcome.flagged == [3]
    expected = quality_from_probs([0.05] * 6)  # survivors only
    assert abs(outcome.result.quality_score - expected) < 1e-9
    assert outcome.result.quality_score < 1.0


def test_unflagged_document_keeps_its_original_quality_score():
    specs = [(1000, "one a"), (5000, "two b"), (9000, "three c")]
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], [1000, 5000, 9000])
    assert outcome.flagged == []
    assert outcome.result.quality_score == 0.8  # no drop -> no recompute


def test_snapped_line_end_chains_to_next_start_and_last_keeps_duration():
    specs = [(1000, "one a"), (24_000, "two b"), (9000, "three c"), (40_000, "four d")]
    refs = [1000, 5000, 9000, 52_000]  # lines 1 and 3 drift
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], refs)
    assert outcome.flagged == [1, 3]
    lines = outcome.result.lines
    assert lines[1].end_ms == lines[2].start_ms  # chained to the next final start
    assert lines[3].end_ms == lines[3].start_ms + 800  # last: old duration kept
    starts = [line.start_ms for line in lines]
    assert starts == sorted(starts)


def test_length_mismatch_between_refs_and_lines_skips_qa():
    specs = [(1000, "one a"), (5000, "two b"), (9000, "three c"), (34_000, "four d")]
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], [1000, 5000])  # too short
    assert outcome.flagged == []
    assert outcome.result.lines[3].start_ms == 34_000


def test_regroup_dropped_line_still_matches_by_text_cursor():
    # regroup may skip a lyric line: result has 3 lines, lyrics have 4 (chorus
    # repeats the same text — cursor matching must not mismatch the repeat).
    line_texts = ["hook x", "verse a", "hook x", "outro z"]
    refs = [1000, 5000, 9000, 13_000]
    specs = [(1000, "hook x"), (9000, "hook x"), (25_000, "outro z")]  # "verse a" dropped
    outcome = apply_line_qa(_result(specs), line_texts, refs)
    # Second "hook x" matches the SECOND ref (9000), so it is not flagged;
    # "outro z" is 12 s off its ref and gets snapped.
    assert outcome.flagged == [2]
    assert outcome.result.lines[2].start_ms == 13_000


def test_drift_just_inside_threshold_is_kept():
    specs = [
        (1000, "one a"),
        (5000 + DRIFT_THRESHOLD_MS, "two b"),
        (9000, "three c"),
        (13_000, "four d"),
    ]
    refs = [1000, 5000, 9000, 13_000]
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], refs)
    # Deviation equals the threshold after the (small) median offset shift —
    # strictly-greater comparison keeps it.
    assert outcome.flagged == []
