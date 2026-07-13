"""Lyricsfile parser: golden fixtures against the vendored 1.0 spec.

Contract under test: a job must NEVER fail over a lyricsfile — every problem
returns None (fallback to syncedLyrics/CTC); valid word-level files become an
AlignResult on the human clock, quality 1.0.
"""

from pathlib import Path

from kashi_server.pipeline.lyricsfile import (
    MAX_LYRICSFILE_BYTES,
    alignresult_from_lyricsfile,
)

FIXTURES = Path(__file__).parent / "fixtures" / "lyricsfile"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_valid_word_level_file_parses_on_the_human_clock():
    result = alignresult_from_lyricsfile(_load("valid.yaml"), duration_s=200.0)
    assert result is not None
    assert result.sync == "word" and result.quality_score == 1.0 and not result.windowed
    assert [line.text for line in result.lines] == ["Meet me at the hotel", "Second line"]
    words = result.words_per_line[0]
    # Trailing spaces stripped for rendering; times verbatim from the file.
    assert [w.text for w in words] == ["Meet", "me", "at", "the", "hotel"]
    assert (words[0].start_ms, words[0].end_ms) == (12000, 12300)
    # "at" has no end_ms -> runs to the next word's start.
    assert (words[2].start_ms, words[2].end_ms) == (12500, 12800)
    assert all(w.prob == 1.0 for w in words)
    # Line 2 has no end_ms and no next line -> ends at its last word's end.
    assert result.lines[1].end_ms == 17400


def test_offset_ms_shifts_every_stamp():
    result = alignresult_from_lyricsfile(_load("offset.yaml"), duration_s=100.0)
    assert result is not None
    assert result.lines[0].start_ms == 1500
    assert result.words_per_line[0][0].start_ms == 1500
    assert result.words_per_line[0][1].end_ms == 2500


def test_word_text_mismatch_drops_only_that_lines_words():
    result = alignresult_from_lyricsfile(_load("mixed-mismatch.yaml"), duration_s=100.0)
    assert result is not None
    assert [w.text for w in result.words_per_line[0]] == ["Good", "line", "here"]
    assert result.words_per_line[1] == []  # mismatched words never sweep


def test_rejections_fall_back_to_none():
    assert alignresult_from_lyricsfile(_load("line-only.yaml"), 100.0) is None
    assert alignresult_from_lyricsfile(_load("version-2.yaml"), 100.0) is None
    assert alignresult_from_lyricsfile(_load("nonmono.yaml"), 100.0) is None
    assert alignresult_from_lyricsfile(_load("instrumental.yaml"), 100.0) is None
    assert alignresult_from_lyricsfile(None, 100.0) is None
    assert alignresult_from_lyricsfile("", 100.0) is None
    assert alignresult_from_lyricsfile("not: [valid", 100.0) is None
    assert alignresult_from_lyricsfile("just a scalar", 100.0) is None


def test_oversized_file_is_rejected_before_parsing():
    huge = "version: '1.0'\n# " + "x" * MAX_LYRICSFILE_BYTES
    assert alignresult_from_lyricsfile(huge, 100.0) is None


def test_stamps_timed_to_a_different_edit_are_rejected():
    # valid.yaml runs to 17.4s; a 10s download cannot be the same edit.
    assert alignresult_from_lyricsfile(_load("valid.yaml"), duration_s=10.0) is None


def test_declared_duration_mismatch_rejects_shorter_edits_too():
    # valid.yaml declares duration_ms: 200000 — an extended mix download
    # (300s) must reject it even though the last stamp (17.4s) would pass
    # the one-sided gate (reviewer: radio-edit-vs-extended hole).
    assert alignresult_from_lyricsfile(_load("valid.yaml"), duration_s=300.0) is None


def test_explicit_word_end_is_clamped_to_the_next_line_start():
    raw = """
version: "1.0"
metadata: {title: T, artist: A}
lines:
  - text: "First line"
    start_ms: 1000
    end_ms: 3000
    words:
      - {text: "First ", start_ms: 1000, end_ms: 1500}
      - {text: "line", start_ms: 1500, end_ms: 9000}
  - text: "Second"
    start_ms: 4000
    end_ms: 5000
    words:
      - {text: "Second", start_ms: 4000, end_ms: 5000}
"""
    result = alignresult_from_lyricsfile(raw, duration_s=100.0)
    assert result is not None
    assert result.words_per_line[0][1].end_ms == 4000  # garbage 9000 clamped


def test_last_word_without_end_in_a_line_without_end_gets_the_resolved_line_end():
    raw = """
version: "1.0"
metadata: {title: T, artist: A}
lines:
  - text: "Open ending"
    start_ms: 1000
    words:
      - {text: "Open ", start_ms: 1000, end_ms: 1400}
      - {text: "ending", start_ms: 1500}
  - text: "Next"
    start_ms: 6000
    end_ms: 7000
    words:
      - {text: "Next", start_ms: 6000, end_ms: 7000}
"""
    result = alignresult_from_lyricsfile(raw, duration_s=100.0)
    assert result is not None
    assert result.lines[0].end_ms == 6000  # resolved to next line's start
    assert result.words_per_line[0][1].end_ms == 6000  # word rides the resolved end


def test_wordless_line_stamped_past_the_audio_still_rejects():
    raw = """
version: "1.0"
metadata: {title: T, artist: A}
lines:
  - text: "Early words"
    start_ms: 1000
    end_ms: 2000
    words:
      - {text: "Early ", start_ms: 1000, end_ms: 1400}
      - {text: "words", start_ms: 1500, end_ms: 2000}
  - text: "Ghost outro line"
    start_ms: 500000
    end_ms: 510000
"""
    assert alignresult_from_lyricsfile(raw, duration_s=100.0) is None
