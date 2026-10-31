"""Duration hygiene and end collapse, read back from existing result files.

The 2026-10 rounds dumped every word (word_detail: start and END) but were
judged on onset PCO, so the collapse of word durations in 2.29 sat unread in
the files. This reads any dump, old or new, and prints per run:

  zero_len  share of words with end <= start          (hypothesis only)
  short     share of words shorter than 60 ms         (hypothesis only)
  bunched   runs of >= 3 words starting within 60 ms  (hypothesis only)
  collapse  share of verified words ending > 250 ms EARLY   (needs truth ends)
  overext   share of verified words ending > 250 ms LATE

Truth ends come from word_detail's truth_end_ms when present (runs after
2026-10-31), else from the dataset on disk, paired by word index.

    python -m benchmarks.hygiene results/A.json [results/B.json ...] \\
        [--jamendo-root jamendolyrics]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

from benchmarks import metrics

DATA_DIR = Path(__file__).resolve().parent / "data"


def _truth_ends(stem: str, root: Path, cache: dict[str, list[int] | None]) -> list[int] | None:
    if stem not in cache:
        from benchmarks.datasets import load_jamendo

        try:
            songs = load_jamendo(root, stems={stem})
        except FileNotFoundError:
            songs = []
        cache[stem] = songs[0].word_ends_ms if songs else None
    return cache[stem]


def song_numbers(song: dict, root: Path, cache: dict) -> dict | None:
    detail = song.get("word_detail")
    if not detail:
        return None
    starts = [w["hyp_ms"] for w in detail]
    ends = [w["hyp_end_ms"] for w in detail]
    out = metrics.duration_hygiene(starts, ends)
    truth = [w.get("truth_end_ms") for w in detail]
    if any(t is None for t in truth):
        loaded = _truth_ends(song["stem"], root, cache)
        truth = loaded if loaded is not None and len(loaded) == len(detail) else None
    if truth is not None:
        signed = [float(e - t) for e, t, w in zip(ends, truth, detail, strict=True)
                  if w.get("verified", True)]
        out["end_collapse"] = round(metrics.end_collapse_rate(signed), 4)
        out["over_extension"] = round(metrics.over_extension_rate(signed), 4)
    return out


def run_numbers(path: Path, root: Path) -> dict:
    data = json.loads(path.read_text())
    cache: dict[str, list[int] | None] = {}
    rows = [n for s in data.get("jamendo", {}).get("songs", [])
            if (n := song_numbers(s, root, cache)) is not None]
    out = {"label": data.get("meta", {}).get("label", path.stem), "songs": len(rows)}
    if not rows:
        return out
    out |= {
        "zero_len": round(mean(r["zero_len_frac"] for r in rows), 4),
        "short": round(mean(r["short_frac"] for r in rows), 4),
        "bunched": sum(r["bunched_runs"] for r in rows),
    }
    with_truth = [r for r in rows if "end_collapse" in r]
    if with_truth:
        out["collapse"] = round(mean(r["end_collapse"] for r in with_truth), 4)
        out["overext"] = round(mean(r["over_extension"] for r in with_truth), 4)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Duration hygiene of result files.")
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--jamendo-root", default="jamendolyrics")
    args = parser.parse_args(argv)
    root = DATA_DIR / args.jamendo_root
    print(f"{'run':28} {'songs':>5} {'zero_len':>9} {'short':>7} {'bunched':>8} "
          f"{'collapse':>9} {'overext':>8}")
    for path in args.results:
        n = run_numbers(path, root)
        if n["songs"] == 0:
            print(f"{n['label'][:28]:28} {'no word dump':>5}")
            continue
        print(f"{n['label'][:28]:28} {n['songs']:5d} {n['zero_len']:9.3f} {n['short']:7.3f} "
              f"{n['bunched']:8d} {n.get('collapse', float('nan')):9.3f} "
              f"{n.get('overext', float('nan')):8.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
