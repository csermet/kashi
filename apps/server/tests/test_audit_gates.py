"""audit.gates: the Faz 3.5 pre-registration, pinned as numbers. A test written
against the constants would follow a changed gate silently; these do not."""

from audit import gates


def test_step0_the_frozen_evaluation():
    assert gates.REAL_ERRORS == {"pile": 18, "late": 12, "early": 6, "missing": 6,
                                 "wrong_text": 4}
    assert sum(gates.REAL_ERRORS.values()) == gates.REAL_ERRORS_N == 46
    assert sum(gates.REAL_ERRORS[c] for c in gates.VISIBLE_CLASSES) == gates.VISIBLE_N == 28
    assert (gates.LISTENING_SET_MIN, gates.INPUT_MAX_TOKENS, gates.LOCAL_CONTEXT_TOKENS) == \
        (20, 12_000, 16_000)


def test_step1_the_anchor_check():
    assert (gates.PILOT_SHIFT_MS, gates.PILOT_OFFSET_TOL_MS) == (1000, 1000)
    assert (gates.WRONG_SHEET_UNMATCHED_SUNG, gates.WRONG_SHEET_UNMATCHED_LINES) == (0.25, 0.25)
    assert gates.INJECT_GLOBAL_MS == (2000, 5000, 10_000, 20_000)
    assert gates.INJECT_VERSE_MS == (3000, 20_000)
    assert gates.INJECT_DRIFT_END_MS == (5000, 10_000)
    assert (gates.INJECT_GATED_MIN_MS, gates.INJECT_REPORTED_MS) == (5000, 2000)
    assert (gates.INJECT_DETECTION_MIN, gates.INJECT_OFFSET_ERR_MS,
            gates.INJECT_FALSE_ALARM_MAX) == (0.90, 500, 0.05)


def test_step2_the_second_ear():
    assert (gates.SECOND_EAR_HEALTHY_MEDIAN_MS, gates.SECOND_EAR_SHIFTED_MEDIAN_MS) == (500, 1000)
    assert (gates.SECOND_EAR_SILENT_REGION_MS, gates.SECOND_EAR_SILENT_MAX) == (2000, 0.05)


def test_step3_the_summarizer():
    assert gates.SHUFFLED_RUNS == 3
    assert (gates.M1_RECALL_MIN, gates.M1_PRECISION_MIN) == (0.90, 0.60)
    assert (gates.M2_SPEARMAN_MIN, gates.M2_PRECISION_AT_3_MIN, gates.M2_SONG_MIN_ERRORS) == \
        (0.5, 0.7, 3)
    assert gates.M3_VISIBLE_RECALL_MIN == 0.7
    assert (gates.M4_PROBE_MIN_CORRECT, gates.M4_PROBE_SONGS) == (19, 20)
    assert (gates.M5_SCHEMA_VALID_MIN, gates.M5_AGREEMENT_MIN) == (0.99, 0.90)


def test_step4_the_kill_test():
    assert (gates.KILL_CLIPS, gates.KILL_CLIP_S, gates.KILL_REPEATS) == (20, (4, 8), (2, 4))
    assert (gates.KILL_REPEATED_MIN, gates.KILL_ABSENT_MIN) == (5, 5)
    assert (gates.KILL_COUNT_EXACT_MIN, gates.KILL_ONSET_TOL_MS, gates.KILL_ONSET_WITHIN_MIN,
            gates.KILL_MEDIAN_ONSET_STOP_MS) == (0.80, 250, 0.80, 500)
    assert gates.verdict(True) == "passed" and gates.verdict(False) == "failed"
