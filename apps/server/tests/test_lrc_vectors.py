"""The shared LRC vectors (packages/schemas/vectors/lrc-lines.json), server side.

The overlay runs the same file in apps/overlay/src/main/lrc-vectors.test.ts, so
the two lrclib parsers cannot drift apart on what a line says (pipeline 2.32.1:
enhanced-LRC word tags reached the screen as text on both twins).
"""

import json
from pathlib import Path

import pytest

from kashi_server.pipeline.lrclib import _extract, parse_lrc_line, strip_inline_tags

VECTORS = json.loads(
    (Path(__file__).parents[3] / "packages" / "schemas" / "vectors" / "lrc-lines.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", VECTORS["lines"], ids=lambda c: c["case"])
def test_line_vector(case: dict) -> None:
    parsed = parse_lrc_line(case["raw"])
    if case["text"] is None:
        assert parsed is None
    else:
        assert parsed == (case["stamps_ms"], case["text"])


@pytest.mark.parametrize("case", VECTORS["records"], ids=lambda c: c["case"])
@pytest.mark.usefixtures("shipped_record_gate")
def test_record_vector(case: dict) -> None:
    """The record gate's real floors (conftest opens it for mechanics tests)."""
    extracted = _extract({"syncedLyrics": case["synced"], "duration": case["duration_s"]})
    if case["degenerate"]:
        assert extracted is None
    else:
        assert extracted == (case["texts"], case["starts_ms"], True)


def test_the_vectors_cover_every_variant_found_in_lrclib_data() -> None:
    """The sizing (2026-11-14) found word tags before each word and at the line
    end, tag-only lines, ID tags and a one-digit minute; the decision covers
    syllable tags and square stamps inside a line too. Losing one of these
    cases from the shared file would let the twins drift on it unseen."""
    raws = [case["raw"] for case in VECTORS["lines"]]
    assert any(raw.count("<") >= 3 for raw in raws)  # a tag before each word
    assert any(raw.rstrip().endswith(">") for raw in raws)  # a tag at the line end
    assert any("Ve<" in raw for raw in raws)  # syllable tags
    assert any("] <" in raw or "]<" in raw for raw in raws)
    assert any(raw.startswith("[00:32.00]Velo [") for raw in raws)  # square stamps inside
    assert any(raw.startswith("[offset:") for raw in raws)
    assert any(raw.startswith("[1:") for raw in raws)


def test_untagged_text_is_only_trimmed() -> None:
    """Records without tags must parse exactly as before: inner whitespace and
    every character survive."""
    assert strip_inline_tags("  Velo   tarin\tmox  ") == "Velo   tarin\tmox"


def test_a_long_blank_run_is_linear() -> None:
    """The (?<!\\s) lookbehind keeps a long whitespace run that never reaches a
    tag from being rescanned at every position (ReDoS shape)."""
    import time

    text = "Velo" + " " * 50_000 + "x"
    started = time.perf_counter()
    assert strip_inline_tags(text) == text
    assert time.perf_counter() - started < 0.5
