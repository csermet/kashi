"""benchmarks.hygiene reads word dumps back: truth ends from the dump, verified only."""

from pathlib import Path

from benchmarks.hygiene import song_numbers


def test_song_numbers_use_dumped_truth_ends_and_skip_unverified_words():
    detail = [
        {"hyp_ms": 0, "hyp_end_ms": 0, "truth_end_ms": 400, "verified": True},      # 400 early
        {"hyp_ms": 500, "hyp_end_ms": 900, "truth_end_ms": 600, "verified": True},  # 300 late
        {"hyp_ms": 1000, "hyp_end_ms": 1000, "truth_end_ms": 1500, "verified": False},
        {"hyp_ms": 2000, "hyp_end_ms": 2200, "truth_end_ms": 2200, "verified": True},
    ]
    n = song_numbers({"stem": "x", "word_detail": detail}, Path("/nonexistent"), {})
    assert n is not None
    assert n["zero_len_frac"] == 0.5             # hygiene counts every word
    assert n["end_collapse"] == round(1 / 3, 4)  # 3 verified words, one early
    assert n["over_extension"] == round(1 / 3, 4)


def test_song_numbers_without_truth_report_hygiene_only():
    detail = [{"hyp_ms": 0, "hyp_end_ms": 100}, {"hyp_ms": 200, "hyp_end_ms": 230}]
    n = song_numbers({"stem": "missing", "word_detail": detail}, Path("/nonexistent"), {})
    assert n is not None and "end_collapse" not in n and n["short_frac"] == 0.5
    assert song_numbers({"stem": "x"}, Path("/nonexistent"), {}) is None
