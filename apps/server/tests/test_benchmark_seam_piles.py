"""benchmarks.seam_piles reads benchmark dumps as well as stored documents."""

import pytest

from benchmarks.seam_piles import lines_from_word_detail, seam_piles


def _row(i, token, start, end, line):
    return {
        "i": i,
        "token": token,
        "truth_ms": start,
        "hyp_ms": start,
        "hyp_end_ms": end,
        "line": line,
    }


def test_a_dumped_pile_is_counted_like_a_document_pile():
    detail = [
        _row(0, "stressed", 50_590, 51_140, 0),
        _row(1, "(oh)", 52_381, 52_381, 0),
        _row(2, "Wish", 52_431, 52_431, 1),
        _row(3, "we", 52_431, 52_431, 1),
        _row(4, "could", 52_431, 52_671, 1),
    ]
    lines = lines_from_word_detail(detail)
    assert [len(line["words"]) for line in lines] == [2, 3]
    assert seam_piles(lines) == [1]


def test_rows_are_regrouped_in_annotation_order():
    detail = [_row(1, "b", 200, 300, 0), _row(0, "a", 100, 200, 0)]
    assert [w["text"] for w in lines_from_word_detail(detail)[0]["words"]] == ["a", "b"]


def test_an_old_dump_without_word_ends_is_refused():
    with pytest.raises(ValueError):
        lines_from_word_detail([{"i": 0, "token": "a", "hyp_ms": 1, "line": 0}])
