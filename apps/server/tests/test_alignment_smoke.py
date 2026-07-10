"""The one test that touches the real model. Marked slow: it needs the `align`
extra and downloads ~1.2 GB of weights on first run."""

import pytest

pytest.importorskip("ctc_forced_aligner", reason="alignment extra not installed")

pytestmark = pytest.mark.slow


def test_warmup_aligns_the_speech_fixture():
    from kashi_server.worker.warmup import ensure_model

    quality = ensure_model()
    assert 0.0 < quality <= 1.0


def test_align_produces_word_timings_for_both_lines():
    from kashi_server.pipeline.alignment import align
    from kashi_server.worker.warmup import FIXTURE_TXT, FIXTURE_WAV

    line_texts = [line.strip() for line in FIXTURE_TXT.read_text().splitlines() if line.strip()]
    result = align(FIXTURE_WAV, line_texts, "eng")

    assert result.sync == "word"
    assert len(result.lines) == len(line_texts)
    assert [line.text for line in result.lines] == line_texts

    words = [word for chunk in result.words_per_line for word in chunk]
    assert len(words) == sum(len(line.split()) for line in line_texts)
    # Monotone, non-negative, inside the clip.
    assert words[0].start_ms >= 0
    for previous, current in zip(words, words[1:], strict=False):
        assert previous.start_ms <= current.start_ms
        assert previous.end_ms <= current.start_ms
    assert words[-1].end_ms <= 7_000  # the fixture is ~6 s
