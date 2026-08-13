"""Line QA: snap drifted lines to lrclib synced times, drop their words.

The synthetic fixtures model the real TiK ToK failure (2026-07-11): a chorus
block dumped ~15 s ahead of the audio while the surrounding lines were fine.
"""

from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.line_qa import (
    DRIFT_THRESHOLD_MS,
    TRIM_MAX_HOLD_MS,
    apply_line_qa,
    is_adlib,
    trim_word_ends,
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
    # Faz 8 P-B2: the degrade path used to carry the pre-QA number through
    # untouched, so a document with NO word timings kept the aligner's claim
    # (field: BABYMETAL "BxMxC", 32 of 42 lines flagged, shipped 1.00). It is
    # now line-anchor agreement, and the basis says there is no word evidence.
    assert outcome.flagged == [0, 3]
    assert outcome.result.quality_score == 0.5  # 1 - 2/4 lines off their anchor
    assert outcome.result.quality_basis == "line-anchors"
    assert outcome.result.words_per_line == []


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
    # The ramp still sees survivors only — dropped words must not leak in.
    ramp = quality_from_probs([0.05] * 6)
    # …but Faz 8 P-B2: confidence in what SURVIVED is not confidence in the
    # document. Damaging a line used to raise the score by removing its weak
    # words from the pool (field: Tarkan "Op", 1.00 with 19 of 40 lines
    # flagged). The ramp is now scaled by the fraction of referenced lines
    # that came through intact — here 3 of 4.
    expected = round(ramp * 0.75, 4)
    assert outcome.result.quality_score == expected
    assert outcome.result.quality_score < ramp  # damage costs, never pays
    assert outcome.result.quality_basis == "probs+anchors"


def test_unflagged_document_scores_the_full_ramp_at_perfect_agreement():
    from kashi_server.pipeline.alignment import quality_from_probs

    specs = [(1000, "one a"), (5000, "two b"), (9000, "three c")]
    base = _result(specs)
    outcome = apply_line_qa(base, [s[1] for s in specs], [1000, 5000, 9000])
    assert outcome.flagged == []
    # Faz 8 P-B2: an undamaged document is recomputed too, so the basis names
    # the formula honestly. Agreement is 1.0, so the score IS the ramp over
    # every word — nothing is lost by recomputing, only the label gained.
    all_probs = [w.prob for chunk in base.words_per_line for w in chunk]
    assert outcome.result.quality_score == round(quality_from_probs(all_probs), 4)
    assert outcome.result.quality_basis == "probs+anchors"


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
    # Border-dropped words stay out of the ramp (their 1.0 probs must not
    # leak) AND the drop itself costs: 2 of 5 referenced lines are damaged
    # (border drop + the flagged one), so agreement is 3/5.
    expected = round(quality_from_probs([0.05] * 6) * 0.6, 4)
    assert outcome.result.quality_score == expected


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


def _windowed_result(n_lines=4, quality=0.01):
    lines = [LineTiming(i * 10_000, i * 10_000 + 3_000, f"line {i}", 0.5) for i in range(n_lines)]
    words = [
        [AlignedWord(i * 10_000, i * 10_000 + 3_000, f"w{i}", 0.01)] for i in range(n_lines)
    ]
    return AlignResult(
        sync="word", lines=lines, words_per_line=words, quality_score=quality, windowed=True
    )


def test_windowed_quality_is_anchor_agreement_not_probs():
    """Measured: per-window CTC probs don't track accuracy (r=0.36) — a clean
    windowed doc must clear the client's 0.5 gate regardless of prob mass."""
    result = _windowed_result(quality=0.01)  # prob ramp would say ~0
    texts = [line.text for line in result.lines]
    outcome = apply_line_qa(result, texts, [0, 10_000, 20_000, 30_000])
    assert outcome.flagged == []
    assert outcome.result.quality_score == 1.0


def test_windowed_quality_counts_damaged_lines():
    result = _windowed_result(n_lines=8, quality=0.01)
    # push one line far off its anchor -> flagged -> quality = 1 - 1/8
    lines = list(result.lines)
    from dataclasses import replace as dc_replace

    lines[3] = dc_replace(lines[3], start_ms=lines[3].start_ms + 9_000)
    result = dc_replace(result, lines=lines)
    texts = [line.text for line in result.lines]
    outcome = apply_line_qa(result, texts, [i * 10_000 for i in range(8)])
    assert outcome.flagged == [3]
    assert abs(outcome.result.quality_score - (1 - 1 / 8)) < 1e-3


def test_whole_audio_quality_still_prob_based():
    result = _windowed_result(quality=0.01)
    from dataclasses import replace as dc_replace

    result = dc_replace(result, windowed=False)
    texts = [line.text for line in result.lines]
    outcome = apply_line_qa(result, texts, [0, 10_000, 20_000, 30_000])
    assert outcome.result.quality_score < 0.5  # prob ramp, tiny probs


def test_adlib_line_block_shifts_onto_its_anchor():
    """Ear-test fix: 'Oh-ooh whoa-oh' lines come systematically late from CTC;
    past the threshold the lrclib anchor wins and the words ride along."""
    lines = [
        LineTiming(0, 3_000, "real lyric line here", 0.5),
        LineTiming(12_400, 14_000, "Oh-ooh, oh-ooh, whoa-oh", 0.5),  # anchor 10s -> +2.4s late
        LineTiming(20_000, 23_000, "another real lyric line", 0.5),
        LineTiming(30_000, 33_000, "closing real lyric line", 0.5),
    ]
    words = [
        [AlignedWord(0, 3_000, "w", 0.5)],
        [AlignedWord(12_400, 13_000, "oh", 0.5), AlignedWord(13_100, 14_000, "whoa", 0.5)],
        [AlignedWord(20_000, 23_000, "w", 0.5)],
        [AlignedWord(30_000, 33_000, "w", 0.5)],
    ]
    result = AlignResult(
        sync="word", lines=lines, words_per_line=words, quality_score=0.8, windowed=True
    )
    outcome = apply_line_qa(
        result, [line.text for line in lines], [0, 10_000, 20_000, 30_000]
    )
    assert outcome.adlib_shifted == [1]
    assert outcome.flagged == []  # shifted BEFORE flagging -> no snap/word-drop
    shifted = outcome.result.lines[1]
    assert shifted.start_ms == 10_000  # offset 0 -> lands on the anchor
    # After the block shift the word spans are REDISTRIBUTED across the line
    # (Faz 4 rederive): "oh" (2 chars) then "whoa" (4 chars) over 1600 ms.
    assert outcome.adlib_rederived == [1]
    ws = outcome.result.words_per_line[1]
    assert ws[0].start_ms == 10_000 and ws[0].end_ms == 10_533
    assert ws[1].start_ms == 10_533 and ws[1].end_ms == 11_600  # covers the span
    assert outcome.result.quality_score == 1.0  # corrected, not damaged


def test_adlib_within_threshold_is_untouched():
    lines = [
        LineTiming(0, 3_000, "real lyric line here", 0.5),
        LineTiming(10_400, 11_500, "Oh-ooh, oh-ooh, whoa-oh", 0.5),  # +400ms — fine
        LineTiming(20_000, 23_000, "another real lyric line", 0.5),
        LineTiming(30_000, 33_000, "closing real lyric line", 0.5),
    ]
    words = [[AlignedWord(line.start_ms, line.end_ms, "w", 0.5)] for line in lines]
    result = AlignResult(
        sync="word", lines=lines, words_per_line=words, quality_score=0.8, windowed=True
    )
    outcome = apply_line_qa(result, [line.text for line in lines], [0, 10_000, 20_000, 30_000])
    assert outcome.adlib_shifted == []
    assert outcome.result.lines[1].start_ms == 10_400


def test_lexical_line_never_adlib_shifts():
    lines = [
        LineTiming(0, 3_000, "real lyric line here", 0.5),
        LineTiming(12_400, 14_000, "wake up in the morning", 0.5),  # late but LEXICAL
        LineTiming(20_000, 23_000, "another real lyric line", 0.5),
        LineTiming(30_000, 33_000, "closing real lyric line", 0.5),
    ]
    words = [[AlignedWord(line.start_ms, line.end_ms, "w", 0.5)] for line in lines]
    result = AlignResult(
        sync="word", lines=lines, words_per_line=words, quality_score=0.8, windowed=True
    )
    outcome = apply_line_qa(result, [line.text for line in lines], [0, 10_000, 20_000, 30_000])
    assert outcome.adlib_shifted == []


def test_adlib_rederive_spreads_words_by_char_length_on_the_clean_path():
    """Faz 4: even a well-anchored ad-lib line gets its INNER word spans
    redistributed — CTC packs sustained hooks unreliably (NonLexical is the
    worst measured class), the anchored line span is what we trust."""
    lines = [
        LineTiming(0, 3_000, "real lyric line here", 0.5),
        # CTC packed both words into the first 400 ms of a 2 s hook.
        LineTiming(10_000, 12_000, "Oh-ooh, whoa-oh", 0.5),
        LineTiming(20_000, 23_000, "another real lyric line", 0.5),
        LineTiming(30_000, 33_000, "closing real lyric line", 0.5),
    ]
    words = [
        [AlignedWord(0, 3_000, "w", 0.5)],
        [AlignedWord(10_000, 10_200, "Oh-ooh,", 0.9), AlignedWord(10_250, 10_400, "whoa-oh", 0.9)],
        [AlignedWord(20_000, 23_000, "w", 0.5)],
        [AlignedWord(30_000, 33_000, "w", 0.5)],
    ]
    result = AlignResult(
        sync="word", lines=lines, words_per_line=words, quality_score=0.8, windowed=True
    )
    outcome = apply_line_qa(result, [line.text for line in lines], [0, 10_000, 20_000, 30_000])
    assert outcome.adlib_shifted == []  # already on its anchor
    assert outcome.adlib_rederived == [1]
    ws = outcome.result.words_per_line[1]
    # "Oh-ooh," = 7 chars, "whoa-oh" = 7 chars -> even split of the 2 s span.
    assert ws[0].start_ms == 10_000 and ws[0].end_ms == 11_000
    assert ws[1].start_ms == 11_000 and ws[1].end_ms == 12_000
    assert ws[0].prob == 0.9  # probs preserved — quality math untouched
    # Lexical neighbours keep their CTC word timings.
    assert outcome.result.words_per_line[0][0].end_ms == 3_000


def test_adlib_rederive_skips_single_word_and_short_spans():
    lines = [
        LineTiming(0, 3_000, "real lyric line here", 0.5),
        LineTiming(10_000, 10_400, "Oh-ooh, whoa-oh", 0.5),  # span 400 < 500 ms
        LineTiming(20_000, 23_000, "Ooh", 0.5),  # single word
        LineTiming(30_000, 33_000, "closing real lyric line", 0.5),
    ]
    words = [
        [AlignedWord(0, 3_000, "w", 0.5)],
        [AlignedWord(10_000, 10_100, "Oh-ooh,", 0.5), AlignedWord(10_150, 10_250, "whoa-oh", 0.5)],
        [AlignedWord(20_000, 20_500, "Ooh", 0.5)],
        [AlignedWord(30_000, 33_000, "w", 0.5)],
    ]
    result = AlignResult(
        sync="word", lines=lines, words_per_line=words, quality_score=0.8, windowed=True
    )
    outcome = apply_line_qa(result, [line.text for line in lines], [0, 10_000, 20_000, 30_000])
    assert outcome.adlib_rederived == []
    assert outcome.result.words_per_line[1][1].start_ms == 10_150  # untouched
    assert outcome.result.words_per_line[2][0].end_ms == 20_500  # untouched


def test_no_reference_path_still_rederives_adlib_words():
    # Document assembly writes `adlib` regardless of QA references and the
    # overlay sweeps those lines — the rederive must run on QA-less docs too
    # (retro finding: it only ran on the referenced paths).
    specs = [(1000, "Ooh ooh"), (5000, "two b"), (9000, "three c")]
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], None)
    assert outcome.adlib_rederived == [0]
    chunk = outcome.result.words_per_line[0]
    # Gap-free and span-covering: [1000..1400][1400..1800] over the 800ms line.
    assert (chunk[0].start_ms, chunk[0].end_ms) == (1000, 1400)
    assert (chunk[1].start_ms, chunk[1].end_ms) == (1400, 1800)
    assert outcome.result.words_per_line[1]  # lexical lines untouched


# --- word-END sustain trim (Faz 5 P1) ---


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


def test_apply_line_qa_trims_before_rederive_and_reports_the_count():
    # No-reference path: the trim must still run, and an ad-lib line keeps its
    # rederived full-span words (trim runs BEFORE rederive, exempts ad-libs).
    hang = (LineTiming(10_000, 24_000, "so", 0.5), [AlignedWord(10_000, 24_000, "so", 0.5)])
    hook = (
        LineTiming(30_000, 31_000, "Ooh ooh", 0.5),
        [AlignedWord(30_000, 30_100, "Ooh", 0.5), AlignedWord(30_150, 30_300, "ooh", 0.5)],
    )
    outcome = apply_line_qa(
        _trim_fixture([hang, hook]),
        [
            "alpha beta gamma",
            "delta epsilon zeta",
            "eta theta iota",
            "so",
            "Ooh ooh",
        ],
        None,
    )
    assert outcome.trimmed_ends == 1
    assert outcome.result.words_per_line[3][0].end_ms == 10_600
    assert outcome.adlib_rederived == [4]
    hook_words = outcome.result.words_per_line[4]
    assert hook_words[0].start_ms == 30_000
    assert hook_words[-1].end_ms == 31_000  # rederived span survived the trim pass


def test_damage_never_raises_the_score():
    """Faz 8 P-B2, the survivor-bias property. The score is drawn from the
    words QA did NOT delete, so damaging a line used to shrink the pool toward
    its most confident members — the worse the document, the better it looked.
    Field: Tarkan "Op" reported 1.00 with 19 of 40 lines flagged and an 11 s
    global offset. Same document, progressively more drift: the score must be
    monotonically non-increasing."""
    from kashi_server.pipeline.alignment import AlignResult

    # Eight lines, and never more than three dragged: the median offset must
    # stay pinned to the undrifted majority. (Drift most of a short document
    # and the median follows THEM — the minority becomes the outlier and
    # damage goes down again. Correct behaviour, wrong experiment.)
    specs = [(1000 + 4000 * i, f"line{i} w") for i in range(8)]
    texts = [s[1] for s in specs]
    refs = [s[0] for s in specs]

    scores = []
    for drifted in range(4):  # 0..3 lines dragged far past the drift threshold
        starts = [s[0] + (60_000 if i < drifted else 0) for i, s in enumerate(specs)]
        base = _result([(start, text) for start, text in zip(starts, texts, strict=True)])
        # The drifted lines carry CONFIDENT words: under the old formula their
        # removal pulled the mean UP. Survivors stay mid-ramp.
        words = [
            _words(start, text.split(), prob=1.0 if i < drifted else 0.05)
            for i, (start, text) in enumerate(zip(starts, texts, strict=True))
        ]
        outcome = apply_line_qa(AlignResult("word", base.lines, words, 0.8), texts, refs)
        scores.append(outcome.result.quality_score)

    assert scores == sorted(scores, reverse=True), scores
    assert scores[0] > scores[-1]  # not merely flat — damage has to cost


def test_line_mode_document_can_never_claim_word_evidence():
    """A document with no words must not present itself as anchor-verified
    word timing. Nine of the ten line-mode documents in the archive shipped at
    >= 0.94 under an "anchors" basis; the client's 0.5 gate waved them all
    through as word-sync material."""
    specs = [(0, "one a"), (2000, "two b"), (4000, "three c"), (6000, "four d")]
    outcome = apply_line_qa(
        _result(specs, sync="line"), [s[1] for s in specs], [1000, 5000, 9000, 13_000]
    )
    assert outcome.result.sync == "line"
    assert outcome.result.words_per_line == []
    assert outcome.result.quality_basis == "line-anchors"
    assert outcome.result.quality_basis != "anchors"  # the label that misled


def test_video_intro_shift_survives_the_degrade():
    """Faz 8 P-B0, the field case: a YouTube VIDEO edit opens with an intro the
    song release does not have, so lrclib's stamps describe audio that begins
    seconds earlier. The intro also pushes the durations apart, which drops the
    anchors and leaves whole-audio alignment. The degrade path used to write
    RAW lrclib starts, throwing away the very shift the aligner had measured —
    three of the ten line-mode documents in the archive carry an |offset| above
    3 s, the largest 16.9 s. Every line moved by the same amount, so it is a
    clock difference and it has to survive."""
    intro_ms = 20_000
    refs = [1000, 5000, 9000, 13_000]
    # sync="line" forces the degrade path; every line sits one intro late.
    specs = [(ref + intro_ms, f"line{i} w") for i, ref in enumerate(refs)]
    outcome = apply_line_qa(_result(specs, sync="line"), [s[1] for s in specs], refs)

    assert outcome.degraded_to_line
    assert outcome.offset_ms == intro_ms
    assert [line.start_ms for line in outcome.result.lines] == [r + intro_ms for r in refs]
    # …and nothing is flagged: a uniform shift is not drift.
    assert outcome.flagged == []


def test_scattered_alignment_still_falls_back_to_the_raw_lrclib_clock():
    """The other half of the same decision. When the aligner simply lost the
    song the deviations scatter, the median means nothing, and lrclib's raw
    clock really is the better guess — that behaviour must NOT change."""
    refs = [1000, 5000, 9000, 13_000]
    specs = [(0, "one a"), (2000, "two b"), (4000, "three c"), (6000, "four d")]
    outcome = apply_line_qa(_result(specs, sync="line"), [s[1] for s in specs], refs)
    assert outcome.degraded_to_line
    assert [line.start_ms for line in outcome.result.lines] == refs  # raw, unshifted


def test_offset_trust_is_a_pure_median_absolute_deviation():
    from kashi_server.pipeline.line_qa import (
        OFFSET_TRUST_MAD_MS,
        _offset_is_a_clock_difference,
    )

    assert _offset_is_a_clock_difference([20_000] * 5, 20_000)  # perfect shift
    assert _offset_is_a_clock_difference([19_000, 20_000, 21_000], 20_000)  # tight
    assert not _offset_is_a_clock_difference([0, 20_000, 40_000], 20_000)  # scatter
    assert not _offset_is_a_clock_difference([], 20_000)
    assert not _offset_is_a_clock_difference(None, 20_000)
    # A minority of genuinely lost lines cannot veto a shift the rest agree on
    # — the whole reason this is a MEDIAN absolute deviation and not a mean.
    assert _offset_is_a_clock_difference([20_000, 20_000, 20_000, 90_000], 20_000)
    edge = OFFSET_TRUST_MAD_MS
    assert _offset_is_a_clock_difference([-edge, 0, edge], 0)
    assert not _offset_is_a_clock_difference([-edge - 1, -edge - 1, edge + 1, edge + 1], 0)


def test_flagged_line_with_audio_backing_keeps_its_words_shifted(monkeypatch):
    """Faz 8 B4 end to end. A drifted line whose words land on real onsets and
    fill their span is a CLOCK disagreement, not bad word timing. It used to
    lose its words; now it is block-shifted onto the anchor — line and words on
    one clock, the ad-lib path's precedent — and marked uncertain."""
    from kashi_server.pipeline.alignment import AlignResult, LineTiming

    texts = ["one a b", "two c d", "three e f", "four g h"]
    refs = [1000, 5000, 9000, 13_000]
    # Line 3 sits 30 s late: far past the drift threshold, so it is flagged.
    starts = [1000, 5000, 9000, 43_000]
    lines, words = [], []
    for start, text in zip(starts, texts, strict=True):
        tokens = text.split()
        chunk = _words(start, tokens)
        words.append(chunk)
        lines.append(LineTiming(start, chunk[-1].end_ms, text, 0.5))
    result = AlignResult("word", lines, words, 0.8)
    # Onsets exactly under every word, including the drifted line's.
    onsets = [w.start_ms for chunk in words for w in chunk]

    outcome = apply_line_qa(result, texts, refs, onsets)

    assert outcome.flagged == [3]
    assert outcome.uncertain == [3]  # warned, not destroyed
    kept = outcome.result.words_per_line[3]
    assert kept, "the audio backed these words up — they must survive"
    # …and they moved WITH the line: the first word starts where the line does.
    assert kept[0].start_ms == outcome.result.lines[3].start_ms
    # The shift is rigid — internal spacing is the aligner's, untouched.
    assert [w.end_ms - w.start_ms for w in kept] == [
        w.end_ms - w.start_ms for w in words[3]
    ]


def test_flagged_line_the_audio_disowns_still_loses_its_words():
    """The other half stays intact: when the evidence agrees with the anchor,
    the old behaviour is the right behaviour."""
    from kashi_server.pipeline.alignment import AlignResult, LineTiming

    texts = ["one a b", "two c d", "three e f", "four g h"]
    refs = [1000, 5000, 9000, 13_000]
    starts = [1000, 5000, 9000, 43_000]
    lines, words = [], []
    for start, text in zip(starts, texts, strict=True):
        tokens = text.split()
        chunk = [
            AlignedWord(start + i * 40, start + i * 40 + 20, tok, 0.5)
            for i, tok in enumerate(tokens)
        ]  # 60 ms of sound in a multi-second line: hollow
        words.append(chunk)
        lines.append(LineTiming(start, start + 9000, text, 0.5))
    result = AlignResult("word", lines, words, 0.8)
    onsets = [w.start_ms for chunk in words[:3] for w in chunk]  # nothing under line 3

    outcome = apply_line_qa(result, texts, refs, onsets)
    assert outcome.flagged == [3]
    assert outcome.uncertain == []
    assert outcome.result.words_per_line[3] == []


def test_without_onsets_the_old_rule_still_governs():
    """No audio evidence, no new behaviour — the pre-arbiter path is the
    default so a librosa failure can never silently change documents."""
    specs = [(1000, "one a"), (5000, "two b"), (9000, "three c"), (34_000, "four d")]
    outcome = apply_line_qa(_result(specs), [s[1] for s in specs], [1000, 5000, 9000, 46_000])
    assert outcome.flagged == [3]
    assert outcome.result.words_per_line[3] == []
    assert outcome.uncertain == []


# --- sub-threshold drift nudge (Faz 9, field report 2026-08-12) ------------


def _nudge_case(line_start_ms: int, onsets: list[int]):
    """One 4-word line whose anchor sits at 10 000 ms, plus enough clean
    reference lines for QA to run at all."""
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming

    words = [
        AlignedWord(start_ms=line_start_ms + k * 400, end_ms=line_start_ms + k * 400 + 300,
                    text=t, prob=0.9)
        for k, t in enumerate(["hold", "me", "closer", "now"])
    ]
    lines = [
        LineTiming(start_ms=1_000, end_ms=2_000, text="first line here", score=0.9),
        LineTiming(start_ms=5_000, end_ms=6_000, text="second line here", score=0.9),
        LineTiming(
            start_ms=line_start_ms,
            end_ms=line_start_ms + 1_500,
            text="hold me closer now",
            score=0.9,
        ),
        LineTiming(start_ms=20_000, end_ms=21_000, text="last line here", score=0.9),
    ]
    per_line = [
        [AlignedWord(start_ms=1_000, end_ms=1_500, text="first", prob=0.9)],
        [AlignedWord(start_ms=5_000, end_ms=5_500, text="second", prob=0.9)],
        words,
        [AlignedWord(start_ms=20_000, end_ms=20_500, text="last", prob=0.9)],
    ]
    result = AlignResult(sync="word", lines=lines, words_per_line=per_line, quality_score=0.9)
    texts = [line.text for line in lines]
    refs = [1_000, 5_000, 10_000, 20_000]
    return apply_line_qa(result, texts, refs, onsets)


def test_a_line_drifting_under_the_flag_threshold_moves_when_the_audio_agrees():
    """The field case: a line 700 ms late is too close to be flagged and far
    enough to be seen. The anchor says 10 000; the onsets are there, not where
    the aligner put the words."""
    onsets = [1_000, 5_000, 10_000, 10_400, 10_800, 11_200, 20_000]
    out = _nudge_case(10_700, onsets)
    assert out.nudged == [2]
    assert out.result.lines[2].start_ms == 10_000
    assert out.result.words_per_line[2][0].start_ms == 10_000  # words came along
    assert out.flagged == []  # never flagged, never deleted


def test_the_line_stays_when_the_audio_backs_the_ALIGNER(monkeypatch):
    """The anchor is crowd-sourced and can simply be wrong. Onsets sitting
    where the aligner put the words must win — the point is evidence, not
    obedience to lrclib."""
    onsets = [1_000, 5_000, 10_700, 11_100, 11_500, 11_900, 20_000]
    out = _nudge_case(10_700, onsets)
    assert out.nudged == []
    assert out.result.lines[2].start_ms == 10_700


def test_no_onsets_means_no_nudge():
    """Without the independent signal there is no argument to settle, and the
    aligner keeps what it produced."""
    out = _nudge_case(10_700, [])
    assert out.nudged == []
    assert out.result.lines[2].start_ms == 10_700


def test_a_drift_too_small_to_see_is_left_alone():
    """Below the band the line is where it belongs; moving it would be churn.

    The onsets here would REWARD the move (0.25 support becomes 1.0), so only
    the band keeps the line still — otherwise this test would pass for the
    wrong reason."""
    onsets = [1_000, 5_000, 10_000, 10_400, 10_800, 11_200, 20_000]
    out = _nudge_case(10_150, onsets)  # 150 ms: under SUBTHRESHOLD_DRIFT_MS
    assert out.nudged == []
    assert out.result.lines[2].start_ms == 10_150


def test_the_band_sits_above_the_evidence_it_depends_on():
    """The lower bound is a cheap skip, not the protection, and the two must
    not be confused by the next person to tune them: below the arbiter's
    onset tolerance a shift cannot change support at all — every word stays
    inside tolerance before AND after — so a smaller band would only ask
    questions whose answer is already known."""
    from kashi_server.pipeline.alignment import AlignedWord
    from kashi_server.pipeline.arbiter import ONSET_TOLERANCE_MS, better_supported_position
    from kashi_server.pipeline.line_qa import SUBTHRESHOLD_DRIFT_MS

    assert SUBTHRESHOLD_DRIFT_MS > ONSET_TOLERANCE_MS
    # ...and here is the fact behind the rule: onsets exactly on the shifted
    # starts still lose, because the current position is already supported.
    tiny = ONSET_TOLERANCE_MS - 50
    words = [
        AlignedWord(start_ms=10_000 + tiny + k * 400, end_ms=10_100 + tiny + k * 400,
                    text="w", prob=0.9)
        for k in range(4)
    ]
    onsets = [10_000 + k * 400 for k in range(4)]
    assert better_supported_position(words, -tiny, onsets) is False


def test_a_marginal_win_is_not_a_win():
    """Evidence has to be MEANINGFULLY better, not better by one word: moving
    a line the listener can see is not free, and a single-word difference is
    noise. Eight words, four supported where they are, five if moved — a
    0.125 gain, under the 0.15 margin."""
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming

    start = 10_700  # 700 ms drift: inside the band
    words = [
        AlignedWord(start_ms=start + k * 400, end_ms=start + k * 400 + 300, text="w", prob=0.9)
        for k in range(8)
    ]
    lines = [
        LineTiming(start_ms=1_000, end_ms=2_000, text="first line here", score=0.9),
        LineTiming(start_ms=5_000, end_ms=6_000, text="second line here", score=0.9),
        LineTiming(start_ms=start, end_ms=start + 3_200, text="w w w w w w w w", score=0.9),
        LineTiming(start_ms=20_000, end_ms=21_000, text="last line here", score=0.9),
    ]
    per_line = [
        [AlignedWord(start_ms=1_000, end_ms=1_500, text="first", prob=0.9)],
        [AlignedWord(start_ms=5_000, end_ms=5_500, text="second", prob=0.9)],
        words,
        [AlignedWord(start_ms=20_000, end_ms=20_500, text="last", prob=0.9)],
    ]
    # Four onsets sit on the CURRENT starts, five on the shifted ones.
    onsets = sorted(
        [1_000, 5_000, 20_000]
        + [start + k * 400 for k in (0, 1, 2, 3)]
        + [10_000 + k * 400 for k in (4, 5, 6, 7)]
        + [10_000 + 400 * 3 - 1]
    )
    result = AlignResult(sync="word", lines=lines, words_per_line=per_line, quality_score=0.9)
    out = apply_line_qa(
        result, [line.text for line in lines], [1_000, 5_000, 10_000, 20_000], onsets
    )
    assert out.nudged == []
    assert out.result.lines[2].start_ms == start


# --- ad-lib detection: the two shapes it was missing (field fix 2026-08-13) -


def test_a_hook_of_oh_and_i_is_an_adlib():
    """"Oh I, oh I, oh I, oh I" failed the old all-nonlexical test on its four
    "I"s and never reached the ad-lib snap. Measured at +0.6..+0.9 s from its
    anchor across all six occurrences in the archive, and reported by ear as
    late — the exact line this clause exists for."""
    assert is_adlib("Oh I, oh I, oh I, oh I")
    assert is_adlib("Oh, I")


def test_aw_is_a_vocalization():
    """"aw" was simply missing from the table. On JamendoLyrics the line
    "aw ah aw ah aw aw ah" sits FORTY SECONDS from its anchor — the single
    worst placement in the set, and unreachable by the repair built for it."""
    assert is_adlib("aw ah aw ah aw aw ah")
    assert is_adlib("Aww")


def test_a_bare_vowel_word_is_not_a_hook_on_its_own():
    """"I" and "a" are only ad-lib-compatible BESIDE a real vocalization; a
    line that is nothing but them is ordinary text that happens to be short."""
    assert not is_adlib("I")
    assert not is_adlib("a")
    assert not is_adlib("I I I")


def test_only_the_vowel_letters_get_the_exemption():
    """The clause is about ACOUSTICS: "I" and "a" are pure vowels, which is
    why they behave like "oh". A consonant letter is a different thing — "Oh
    b" is not a hook, and widening this to any single letter would start
    swallowing initialisms and stray letters."""
    assert not is_adlib("Oh b")
    assert not is_adlib("Oh, k")
    assert is_adlib("Oh, i")


def test_lexical_lines_that_merely_start_with_oh_stay_lexical():
    """The clause must not swallow sung sentences: an ad-lib line loses its
    aligner timing to the anchor, and doing that to a real line would be the
    worst trade in the pipeline."""
    assert not is_adlib("Oh I love you")
    assert not is_adlib("Oh, I'm in love with your body")
    assert not is_adlib("I am the one")
    assert not is_adlib("Come on boy, move that body")
