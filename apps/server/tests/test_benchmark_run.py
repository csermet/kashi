"""benchmarks.run's scoring on hand-made songs (no torch, no audio)."""

import argparse
from pathlib import Path

from benchmarks import datasets, run
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming


def _song(lines: list[list[tuple[int, int]]], verified=None) -> datasets.JamendoSong:
    """lines: per line, (truth start, truth end) per word; tokens are w<line>_<k>."""
    texts, starts, words, ends = [], [], [], []
    for i, line in enumerate(lines):
        tokens = [f"w{i}_{k}" for k in range(len(line))]
        texts.append(" ".join(tokens))
        starts.append(line[0][0])
        words += [(s, t) for (s, _), t in zip(line, tokens, strict=True)]
        ends += [e for _, e in line]
    return datasets.JamendoSong(
        stem="hand", artist="a", title="t", language="eng", license_type="CC",
        audio_path=Path("/nonexistent.mp3"), line_texts=texts, line_starts_ms=starts,
        words=words, word_ends_ms=ends, duration_hint_s=ends[-1] / 1000, verified=verified,
    )


def _result(song: datasets.JamendoSong, hyp: list[list[tuple[int, int]]]) -> AlignResult:
    chunks = [
        [AlignedWord(s, e, tok, 0.5) for (s, e), tok in zip(line, text.split(), strict=True)]
        for line, text in zip(hyp, song.line_texts, strict=True)
    ]
    lines = [
        LineTiming(c[0].start_ms if c else 0, c[-1].end_ms if c else 0, text, 0.5)
        for c, text in zip(chunks, song.line_texts, strict=True)
    ]
    return AlignResult(sync="word", lines=lines, words_per_line=chunks, quality_score=0.5)


def _args(**over) -> argparse.Namespace:
    base = {"dump_words": False, "signals": False}
    return argparse.Namespace(**(base | over))


def test_a_scored_song_carries_line_starts_dwell_and_gross_errors():
    song = _song([[(1000, 1300), (1400, 1700)], [(3000, 3200), (3300, 3600)]])
    result = _result(song, [[(1300, 1350), (1360, 1700)], [(2600, 3200), (3800, 3900)]])
    entry: dict = {}
    assert run._word_metrics(_args(), song, result, None, (100, 300, 500), entry)
    assert entry["fluency"] == {
        "lines": 2, "line_late": 1, "line_early": 1,  # +300 late, -400 early
        "pairs": 2, "hyp_short": 1, "truth_short": 0, "both_short": 0,  # 1300 -> 1360
        "words": 4, "gross": 0, "untimed": 0,
    }
    agg = run._jamendo_aggregate([{**entry, "language": "eng", "duration_s": 4.0,
                                   "align_s": 1.0}])
    assert agg["fluency"]["line_starts"]["noticeable_per_song"] == 1.0
    assert agg["fluency"]["dwell"]["pipeline_only_pooled"] == 0.5
    assert agg["per_language"]["eng"]["fluency"] == agg["fluency"]
