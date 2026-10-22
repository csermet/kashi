"""The pop set's valid region, applied to its `verified` column."""

import csv

import pytest

from benchmarks import pop_valid


def _rows(lines):
    """Word rows for lines of the given word counts; the last word closes a line."""
    rows = []
    for count in lines:
        for k in range(count):
            rows.append(
                {"word_start": "1", "word_end": "2", "line_end": "2" if k == count - 1 else "nan"}
            )
    return rows


def test_line_indices_follow_line_end_markers():
    assert pop_valid.line_indices(_rows([2, 1, 3])) == [0, 0, 1, 2, 2, 2]


def test_a_lines_region_keeps_whole_lines_and_a_words_region_a_prefix():
    rows = _rows([2, 1, 3])
    assert pop_valid.valid_mask(rows, ("lines", (0, 2))) == [True, True, False, True, True, True]
    assert pop_valid.valid_mask(rows, ("words", 0, 2)) == [True, True, True, False, False, False]


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["word_start", "word_end", "line_end", "verified"])
        writer.writeheader()
        writer.writerows({**r, "verified": r.get("verified", "1")} for r in rows)


def test_apply_rewrites_verified_and_keeps_builder_exclusions(tmp_path, monkeypatch):
    monkeypatch.setattr(pop_valid, "REGIONS", {"song": ("lines", (1,))})
    monkeypatch.setattr(pop_valid, "EXPECTED_WORDS", {"song": 1})
    rows = _rows([1, 2])
    rows[2]["verified"] = "0"  # the builder excluded it (out of order)
    path = tmp_path / "annotations" / "words" / "song.csv"
    _write(path, rows)
    assert pop_valid.apply(tmp_path) == 1
    with path.open() as fh:
        assert [r["verified"] for r in csv.DictReader(fh)] == ["0", "1", "0"]


def test_a_count_that_moved_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pop_valid, "REGIONS", {"song": ("lines", (0,))})
    monkeypatch.setattr(pop_valid, "EXPECTED_WORDS", {"song": 5})
    _write(tmp_path / "annotations" / "words" / "song.csv", _rows([2]))
    with pytest.raises(SystemExit):
        pop_valid.apply(tmp_path, check=True)
