"""The cross-model disagreement measurement, and its pre-registered verdict.

Tested harder than the numbers it prints, for the reason the signals hunt
learned the hard way: a wrong statistic here does not fail loudly. It returns
a plausible table and a confident verdict, and the phase ships an adapter on
a signal that knows nothing — or, worse, kills a good one.

The two cases the whole exercise has to tell apart are constructed
synthetically below, because on synthetic data the right answer is known:
errors that track each other must FAIL P1, errors that do not must pass it.
"""

import json
import re
from pathlib import Path

import pytest

from benchmarks.word_disagreement import (
    P1_MAX_MEDIAN_RHO,
    P2_BAD_MS,
    P2_FLAG_MS,
    _edge_words,
    _jitter_of,
    _rows,
    main,
)


def _report(songs: list[dict], *, jitter: int | None = None, config: dict | None = None) -> dict:
    meta: dict = {"label": "test"}
    if jitter is not None:
        meta["anchor_jitter_ms"] = jitter
    report: dict = {"meta": meta, "jamendo": {"songs": songs}}
    if config is not None:
        report["config"] = config
    return report


def _song(stem: str, rows: list[dict]) -> dict:
    return {"stem": stem, "word_detail": rows}


def _word(i: int, truth_ms: int, hyp_ms: int, **extra) -> dict:
    return {"i": i, "token": f"w{i}", "truth_ms": truth_ms, "hyp_ms": hyp_ms, **extra}


def _write(tmp_path: Path, name: str, report: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _run(tmp_path, mms_songs, qwen_songs, *, mms_jitter=400, qwen_jitter=400, extra=()):
    mms = _write(tmp_path, "mms.json", _report(mms_songs, jitter=mms_jitter))
    qwen = _write(tmp_path, "qwen.json", _report(qwen_songs, jitter=qwen_jitter))
    argv = ["--mms", str(mms), "--qwen", str(qwen), *extra]
    return main_with_argv(argv)


def _reported(out: str, label: str) -> float:
    """First signed number on the line naming `label` — the report is written
    for a human, so the test reads it the way a human would rather than
    counting whitespace-separated columns."""
    line = next(row for row in out.splitlines() if label in row)
    match = re.search(r"[+-]\d+\.\d+", line)
    assert match, line
    return float(match.group())


def main_with_argv(argv: list[str]) -> int:
    import sys

    saved = sys.argv
    sys.argv = ["word_disagreement", *argv]
    try:
        return main()
    finally:
        sys.argv = saved


# --- the join ---------------------------------------------------------------


def test_join_is_by_annotation_index_not_position():
    """Qwen drops punctuation-only tokens, so its Nth item is NOT the Nth
    annotation word. Pairing positionally would silently compare word 5's MMS
    time with word 7's Qwen time — a fabricated disagreement that grows along
    the song. The annotation index is carried in both files for this reason.
    """
    rows = _rows(
        _report(
            [
                _song(
                    "s",
                    [
                        _word(0, 1000, 1010),
                        # index 1 was a comma: Qwen never emits it
                        _word(2, 3000, 3010),
                    ],
                )
            ]
        )
    )
    assert sorted(rows["s"]) == [0, 2]
    assert rows["s"][2]["truth_ms"] == 3000


def test_a_word_only_one_model_scored_is_dropped_not_guessed(tmp_path, capsys):
    mms = [_song("s", [_word(i, i * 1000, i * 1000 + 20) for i in range(6)])]
    qwen = [_song("s", [_word(i, i * 1000, i * 1000 + 30) for i in (0, 1, 2, 4, 5)])]
    _run(tmp_path, mms, qwen)
    out = capsys.readouterr().out
    assert "5 words" in out  # index 3 vanished with its Qwen row, nothing invented


def test_the_same_index_with_a_different_truth_is_refused(tmp_path, capsys):
    """Identical index but a different ground-truth time means the two files
    describe different data — a stale dump, a changed dataset. Comparing them
    would be meaningless, so those words drop instead."""
    mms = [_song("s", [_word(i, i * 1000, i * 1000 + 20) for i in range(6)])]
    qwen = [_song("s", [_word(i, i * 1000 + (500 if i == 2 else 0), i * 1000) for i in range(6)])]
    _run(tmp_path, mms, qwen)
    assert "5 words" in capsys.readouterr().out


# --- the anchor guard -------------------------------------------------------


def test_mismatched_anchors_are_refused(tmp_path, capsys):
    """THE trap this tool was built around: the two existing result files were
    produced at different jitters (MMS 400 ms, Qwen 0), so a disagreement
    between them is partly one model having been handed the ground truth as
    its anchors. Comparing them must not be possible by accident."""
    songs = [_song("s", [_word(i, i * 1000, i * 1000) for i in range(5)])]
    assert _run(tmp_path, songs, songs, mms_jitter=400, qwen_jitter=0) == 1
    assert "anchor mismatch" in capsys.readouterr().err


def test_the_mismatch_guard_can_be_overridden_deliberately(tmp_path):
    songs = [_song("s", [_word(i, i * 1000, i * 1000) for i in range(5)])]
    code = _run(
        tmp_path, songs, songs, mms_jitter=400, qwen_jitter=0, extra=["--allow-anchor-mismatch"]
    )
    assert code != 1


def test_a_missing_jitter_field_reads_as_zero_not_as_a_match():
    # Both producers defaulted to ground-truth anchors before the flag existed.
    assert _jitter_of({"meta": {}}) == 0
    assert _jitter_of({"meta": {"anchor_jitter_ms": None}}) == 0
    assert _jitter_of({"meta": {"anchor_jitter_ms": 400}}) == 400
    # run.py writes its arguments under `config`, qwen_probe under `meta`.
    assert _jitter_of({"meta": {}, "config": {"anchor_jitter_ms": 400}}) == 400


# --- P1, the question the phase actually owes --------------------------------


def _paired_songs(n_songs: int, n_words: int, coupled: bool):
    """Two models over the same words. `coupled` makes them wrong TOGETHER
    (same word, different amount) — the case that kills the signal."""
    mms_songs, qwen_songs = [], []
    for s in range(n_songs):
        mms_rows, qwen_rows = [], []
        for i in range(n_words):
            truth = i * 1000
            # A deterministic, non-monotonic error pattern: no accidental order.
            hard = (i * 7 + s * 3) % 11
            e_m = hard * 90
            e_q = hard * 60 if coupled else ((i * 5 + s) % 11) * 60
            mms_rows.append(_word(i, truth, truth + e_m, line=i // 4))
            qwen_rows.append(_word(i, truth, truth + e_q, window=i // 5))
        mms_songs.append(_song(f"song{s}", mms_rows))
        qwen_songs.append(_song(f"song{s}", qwen_rows))
    return mms_songs, qwen_songs


def test_p1_fails_when_the_models_are_wrong_on_the_same_words(tmp_path, capsys):
    mms, qwen = _paired_songs(6, 40, coupled=True)
    code = _run(tmp_path, mms, qwen)
    out = capsys.readouterr().out
    assert "P1 — independence" in out
    # Perfectly coupled magnitudes: rho = 1, far above the committed ceiling.
    assert "median rho            +1.000" in out
    assert code == 2
    assert "P1 FAILED" in out
    assert "Do not write the adapter" in out


def test_p1_passes_when_the_models_miss_in_different_places(tmp_path, capsys):
    mms, qwen = _paired_songs(6, 40, coupled=False)
    _run(tmp_path, mms, qwen)
    assert _reported(capsys.readouterr().out, "median rho  ") <= P1_MAX_MEDIAN_RHO


def test_p1_is_per_song_so_hard_songs_cannot_masquerade_as_word_agreement(tmp_path, capsys):
    """The distinction the +0.483 song-level number could not make. Here every
    song has a large constant offset (song A is hard for both models), while
    WITHIN each song the errors are unrelated. Pooling the words would report
    a strong correlation; the per-song median must not."""
    mms_songs, qwen_songs = [], []
    for s in range(6):
        difficulty = s * 4000  # song-level covariance, deliberately huge
        mms_rows, qwen_rows = [], []
        for i in range(30):
            truth = i * 1000
            mms_rows.append(_word(i, truth, truth + difficulty + ((i * 7) % 11) * 40))
            qwen_rows.append(
                _word(i, truth, truth + difficulty + ((i * 5) % 13) * 40, window=i // 5)
            )
        mms_songs.append(_song(f"song{s}", mms_rows))
        qwen_songs.append(_song(f"song{s}", qwen_rows))
    _run(tmp_path, mms_songs, qwen_songs)
    out = capsys.readouterr().out
    per_song = _reported(out, "median rho  ")
    pooled = _reported(out, "pooled Pearson")
    assert per_song <= P1_MAX_MEDIAN_RHO
    assert pooled > per_song  # the confound is real, and P1 is immune to it


# --- P2 / P3 arithmetic ------------------------------------------------------


def test_p2_counts_lift_and_recall_against_the_committed_definitions(tmp_path, capsys):
    """Ten words, hand-built: 2 bad (base rate 20%), and the flag fires on
    exactly one of them plus nothing else. Precision 100% / base 20% = 5x lift,
    recall 50%."""
    mms_rows, qwen_rows = [], []
    for i in range(10):
        truth = i * 1000
        e_m = (P2_BAD_MS + 100) if i < 2 else 10  # words 0,1 are bad
        d = (P2_FLAG_MS + 100) if i == 0 else 0  # flag fires on word 0 only
        mms_rows.append(_word(i, truth, truth + e_m))
        qwen_rows.append(_word(i, truth, truth + e_m - d, window=0))
    _run(tmp_path, [_song("s", mms_rows)], [_song("s", qwen_rows)])
    out = capsys.readouterr().out
    assert "base rate of bad words  20.0% (2/10)" in out
    assert "precision               100.0%" in out
    assert "lift                    5.00x" in out
    assert "recall                  50.0%" in out


def test_p3_catches_a_signal_that_is_really_just_qwen_breaking(tmp_path, capsys):
    """The failure P1 and P2 are both blind to. MMS is perfect on every word;
    Qwen is wildly wrong on a third of them. Disagreement is then loud and
    entirely uninformative — and since there are no bad words to find, only P3
    can say so."""
    mms_rows, qwen_rows = [], []
    for i in range(30):
        truth = i * 1000
        mms_rows.append(_word(i, truth, truth + 5))
        qwen_rows.append(_word(i, truth, truth + (5 if i % 3 else 4000), window=i // 5))
    code = _run(tmp_path, [_song("s", mms_rows)], [_song("s", qwen_rows)])
    out = capsys.readouterr().out
    assert "P3 — false alarms" in out
    assert "-> FAIL" in out
    assert code == 2
    # And it must NOT be reported as the P1 failure — the remedy differs.
    assert "P1 FAILED" not in out
    assert "ONE pre-declared" in out


# --- window edges ------------------------------------------------------------


def test_edge_words_are_the_first_and_last_of_each_window():
    rows = {i: {"i": i, "window": i // 3} for i in range(9)}
    assert _edge_words(rows) == {0, 2, 3, 5, 6, 8}


def test_whole_song_alignment_declares_no_edges():
    # The unwindowed path writes -1; nothing there is a boundary word.
    assert _edge_words({i: {"i": i, "window": -1} for i in range(5)}) == set()


# --- refusals ----------------------------------------------------------------


def test_a_dump_free_file_is_refused_with_the_flag_to_use(tmp_path):
    plain = _write(tmp_path, "plain.json", _report([{"stem": "s", "words": {"count": 3}}]))
    good = _write(tmp_path, "good.json", _report([_song("s", [_word(0, 0, 0)])]))
    with pytest.raises(SystemExit) as excinfo:
        main_with_argv(["--mms", str(plain), "--qwen", str(good)])
    assert "--dump-words" in str(excinfo.value)


def test_no_shared_song_is_an_error_not_an_empty_pass(tmp_path, capsys):
    a = [_song("only-mine", [_word(i, i * 1000, i * 1000) for i in range(5)])]
    b = [_song("only-yours", [_word(i, i * 1000, i * 1000) for i in range(5)])]
    assert _run(tmp_path, a, b) == 1
    assert "no song appears in both" in capsys.readouterr().err
