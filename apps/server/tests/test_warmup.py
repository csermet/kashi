"""ensure_model failure paths (align itself is monkeypatched — the real model
run lives in the slow marker's smoke test)."""

import pytest

from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.worker import warmup


def _fake_align(words: list[AlignedWord], sync: str = "word") -> AlignResult:
    return AlignResult(
        sync=sync,
        lines=[LineTiming(0, 5000, "fixture", 0.9)],
        words_per_line=[words] if sync == "word" else [],
        quality_score=0.9,
    )


def _fixture_files(tmp_path):
    wav = tmp_path / "speech-5s.wav"
    txt = tmp_path / "speech-5s.txt"
    wav.write_bytes(b"RIFF")
    txt.write_text("the quick brown fox\n")
    return wav, txt


def _words(n: int, start: int = 100) -> list[AlignedWord]:
    return [AlignedWord(start + i * 500, start + i * 500 + 400, f"w{i}", 0.5) for i in range(n)]


def test_missing_fixtures_raise(tmp_path):
    with pytest.raises(RuntimeError, match="fixtures missing"):
        warmup.ensure_model(tmp_path / "nope.wav", tmp_path / "nope.txt")


def test_too_few_words_raise(tmp_path, monkeypatch):
    wav, txt = _fixture_files(tmp_path)
    monkeypatch.setattr(warmup, "align", lambda *a, **k: _fake_align(_words(2)))
    with pytest.raises(RuntimeError, match="produced 2 words"):
        warmup.ensure_model(wav, txt)


def test_line_fallback_raises(tmp_path, monkeypatch):
    wav, txt = _fixture_files(tmp_path)
    monkeypatch.setattr(warmup, "align", lambda *a, **k: _fake_align([], sync="line"))
    with pytest.raises(RuntimeError, match="sync=line"):
        warmup.ensure_model(wav, txt)


def test_implausible_timings_raise(tmp_path, monkeypatch):
    wav, txt = _fixture_files(tmp_path)
    late = _words(6, start=9_000)  # first word after the 5s fixture ends
    monkeypatch.setattr(warmup, "align", lambda *a, **k: _fake_align(late))
    with pytest.raises(RuntimeError, match="implausible"):
        warmup.ensure_model(wav, txt)


def test_healthy_run_returns_quality(tmp_path, monkeypatch):
    wav, txt = _fixture_files(tmp_path)
    monkeypatch.setattr(warmup, "align", lambda *a, **k: _fake_align(_words(6)))
    assert warmup.ensure_model(wav, txt) == 0.9
