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


# --- QA v2: border-case gate (density + neighbour score) ---------------------


def _wide_words(start_ms: int, texts: list[str], *, prob: float = 0.2) -> list[AlignedWord]:
    """Realistic words: sung across most of a ~4 s line (density well over the
    gate), unlike the deliberately compact `_words` helper."""
    words = []
    t = start_ms
    for text in texts:
        words.append(AlignedWord(start_ms=t, end_ms=t + 1400, text=text, prob=prob))
        t += 1500
    return words


def _custom_result(entries):
    """entries: (start_ms, text, score, words|None). words=None -> wide words."""
    lines = []
    words_per_line = []
    for start_ms, text, score, words in entries:
        tokens = text.split()
        chunk = words if words is not None else _wide_words(start_ms, tokens)
        end = max((w.end_ms for w in chunk), default=start_ms + 400 * len(tokens))
        lines.append(LineTiming(start_ms=start_ms, end_ms=end, text=text, score=score))
        words_per_line.append(chunk)
    return AlignResult(sync="word", lines=lines, words_per_line=words_per_line, quality_score=0.8)


def test_zero_score_neighbour_of_flagged_line_loses_words():
    # Field case (TiK ToK line 10): drift just UNDER the threshold, score 0.00,
    # right before a snapped block — its words are garbage and must drop.
    entries = [
        (1000, "one a", 0.9, None),
        (5000, "two b", 0.9, None),
        (7000, "ten x", 0.0, None),  # border case: -2s off its 9000 ref, score 0
        (20_000, "flag y", 0.0, None),  # 7s off its 13_000 ref -> flagged+snapped
        (17_500, "five z", 0.9, None),
    ]
    refs = [1000, 5000, 9000, 13_000, 17_500]
    outcome = apply_line_qa(_custom_result(entries), [e[1] for e in entries], refs)
    assert outcome.flagged == [3]
    assert outcome.density_dropped == [2]
    assert outcome.result.words_per_line[2] == []  # border case dropped
    assert outcome.result.words_per_line[0] and outcome.result.words_per_line[1]
    assert outcome.result.lines[2].start_ms == 7000  # timing kept (sub-threshold)


def test_compressed_words_next_to_flagged_line_lose_words():
    # 3 words cover 800ms of a 4000ms reference window (density 0.2 < 0.30).
    squeezed = [
        AlignedWord(9000, 9300, "three", 0.5),
        AlignedWord(9300, 9550, "c", 0.5),
        AlignedWord(9550, 9800, "d", 0.5),
    ]
    entries = [
        (1000, "one a", 0.9, None),
        (5000, "two b", 0.9, None),
        (9000, "three c d", 0.5, squeezed),
        (25_000, "flag y", 0.0, None),  # flagged (12s off 13_000)
        (17_500, "five z", 0.9, None),
    ]
    refs = [1000, 5000, 9000, 13_000, 17_500]
    outcome = apply_line_qa(_custom_result(entries), [e[1] for e in entries], refs)
    assert outcome.flagged == [3]
    assert 2 in outcome.density_dropped
    assert outcome.result.words_per_line[2] == []


def test_short_line_is_never_density_dropped():
    # A one-word exclamation legitimately covers a sliver of its window —
    # density needs enough words to mean anything (reviewer catch).
    entries = [
        (1000, "one a", 0.9, None),
        (5000, "hey", 0.5, [AlignedWord(5000, 5400, "hey", 0.5)]),
        (9000, "three c", 0.9, None),
        (25_000, "flag y", 0.0, None),  # flagged
        (17_500, "five z", 0.9, None),
    ]
    refs = [1000, 5000, 9000, 13_000, 17_500]
    outcome = apply_line_qa(_custom_result(entries), [e[1] for e in entries], refs)
    assert outcome.flagged == [3]
    assert 1 not in outcome.density_dropped
    assert outcome.result.words_per_line[1]


def test_gate_never_runs_without_flags():
    # Low density + zero score, but NO flagged line -> untouched (instrumental
    # tails would otherwise false-positive).
    squeezed = [AlignedWord(9000, 9300, "three", 0.5), AlignedWord(9300, 9600, "c", 0.5)]
    entries = [
        (1000, "one a", 0.0, None),
        (5000, "two b", 0.9, None),
        (9000, "three c", 0.0, squeezed),
    ]
    refs = [1000, 5000, 9000]
    outcome = apply_line_qa(_custom_result(entries), [e[1] for e in entries], refs)
    assert outcome.flagged == [] and outcome.density_dropped == []
    assert outcome.result.words_per_line[2]


def test_zero_score_far_from_flagged_line_is_untouched():
    entries = [
        (1000, "one a", 0.0, None),  # score 0 but 3+ lines away from the flag
        (5000, "two b", 0.9, None),
        (9000, "three c", 0.9, None),
        (13_000, "four d", 0.9, None),
        (30_000, "flag y", 0.0, None),  # flagged (12.5s off 17_500)
    ]
    refs = [1000, 5000, 9000, 13_000, 17_500]
    outcome = apply_line_qa(_custom_result(entries), [e[1] for e in entries], refs)
    assert outcome.flagged == [4]
    assert outcome.density_dropped == []
    assert outcome.result.words_per_line[0]


def test_border_gate_guards_last_line_and_missing_refs():
    # Neighbour is the LAST line (no next ref) with fine score -> density
    # signal cannot compute, line is left alone.
    entries = [
        (1000, "one a", 0.9, None),
        (5000, "two b", 0.9, None),
        (21_000, "flag y", 0.0, None),  # flagged (12s off 9000)
        (13_200, "last z", 0.5, None),
    ]
    refs = [1000, 5000, 9000, None]
    outcome = apply_line_qa(_custom_result(entries), [e[1] for e in entries], refs)
    assert outcome.flagged == [2]
    assert outcome.density_dropped == []
    assert outcome.result.words_per_line[3]


def test_quality_recompute_excludes_border_dropped_words():
    from kashi_server.pipeline.alignment import quality_from_probs

    high = [AlignedWord(7000, 7300, "ten", 1.0), AlignedWord(7400, 7700, "x", 1.0)]
    entries = [
        (1000, "one a", 0.9, _wide_words(1000, ["one", "a"], prob=0.05)),
        (5000, "two b", 0.9, _wide_words(5000, ["two", "b"], prob=0.05)),
        (7000, "ten x", 0.0, high),  # border-dropped; its 1.0 probs must not leak
        (20_000, "flag y", 0.0, None),
        (17_500, "five z", 0.9, _wide_words(17_500, ["five", "z"], prob=0.05)),
    ]
    refs = [1000, 5000, 9000, 13_000, 17_500]
    outcome = apply_line_qa(_custom_result(entries), [e[1] for e in entries], refs)
    assert outcome.density_dropped == [2]
    assert abs(outcome.result.quality_score - quality_from_probs([0.05] * 6)) < 1e-9


def test_density_skips_implausibly_long_reference_windows():
    # An instrumental gap before the flagged line inflates the neighbour's
    # reference "duration" — density says nothing there and must not fire.
    entries = [
        (1000, "one a", 0.9, None),
        (5000, "two b", 0.9, None),
        (9000, "three c", 0.9, None),  # next stamp 37s away (gap) — skip A
        (60_000, "flag y", 0.0, None),  # flagged (14s off 46_000)
    ]
    refs = [1000, 5000, 9000, 46_000]
    outcome = apply_line_qa(_custom_result(entries), [e[1] for e in entries], refs)
    assert outcome.flagged == [3]
    assert outcome.density_dropped == []
    assert outcome.result.words_per_line[2]
