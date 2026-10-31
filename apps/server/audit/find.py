"""Rank the suspicious moments of one song.

    python -m audit.find <id> --doc 2.28=<dump>,2.29=<dump> [--top 15]

Uses the stem's analysis when the stem exists (voice onsets, loudness) and the
whole-song ear when ``ear/<id>/whole.json`` exists; without them only the
document-side checks run. Writes ``runs/<id>/moments.json`` (controls included,
marked) and prints the ranking with mm:ss.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from audit.docs import load_document
from audit.ear import whole_path
from audit.finder import cluster, controls, find
from audit.paths import RUNS_DIR, stem_path


def mmss(t_ms: int) -> str:
    return f"{t_ms // 60000}:{(t_ms // 1000) % 60:02d}.{(t_ms % 1000) // 100}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank one song's suspicious moments.")
    parser.add_argument("id")
    parser.add_argument("--doc", required=True, help="label=path[,label=path...], oldest first")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args(argv)
    versions = []
    for item in args.doc.split(","):
        label, path = item.split("=", 1)
        versions.append((label, load_document(Path(path), args.id)))
    voice, level_at, peak_after = None, None, None
    if stem_path(args.id).exists():
        from audit.analysis import analyse

        song = analyse(args.id)
        voice, level_at, peak_after = song.voice_onsets, song.level_at, song.peak_after
    ear_file = whole_path(args.id)
    ear = json.loads(ear_file.read_text()) if ear_file.exists() else None
    events = find(versions, voice, ear, level_at, peak_after)
    moments = cluster(events)
    ctrl = controls(versions[-1][1], moments, seed=args.id)
    out_dir = RUNS_DIR / args.id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": args.id,
        "inputs": {"versions": [v for v, _ in versions], "stem": voice is not None,
                   "ear": ear is not None},
        "moments": [asdict(m) for m in moments],
        "controls": [m.t_ms for m in ctrl],
    }
    (out_dir / "moments.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    print(f"{args.id}: {len(events)} events, {len(moments)} moments "
          f"(stem {'yes' if voice is not None else 'no'}, ear {'yes' if ear else 'no'})")
    for rank, m in enumerate(moments[: args.top], 1):
        tags = " ".join(f"{k}×{v}" if v > 1 else k for k, v in sorted(m.tags.items()))
        print(f"{rank:>3}. {mmss(m.t_ms):>8}  {m.t_ms:>7}  {m.score:5.1f}  {tags}")
    print("controls:", ", ".join(f"{mmss(t)} ({t})" for t in payload["controls"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
