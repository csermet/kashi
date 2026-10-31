"""Perceived accuracy of one document: how many lines arrive noticeably off.

The blind ear tests (2026-10-31) showed what a listener notices: lines and
words that land a second or more away (New Rules 3:20, Instruction 'seh',
Got 2 Luv U's verses), not piles whose lighting stays within ~0.35 s of the
singing. So the score is line-level timing against the audio, with the
roadmap's asymmetric thresholds for a NOTICEABLE miss: later than +220 ms or
earlier than -330 ms (ISMIR 2021), measured on the document clock (the
overlay's user offset is the listener's own preference, not an error).

Where a line is sung comes from two independent witnesses:
  ear    the ear-text match (finder.line_offsets), minus the model's raw-clock
         lag EAR_LAG_MS (healthy songs read +20..+90 ms, 2026-10-31);
  voice  for a line after >= 500 ms of document silence, the voice's return
         within 1.5 s of its start (finder.LINE_GAP_MS / LINE_VOICE_MAX_MS).
A line with both uses the ear (the voice can belong to an ad-lib).

    python -m audit.score <id> --doc <dump>   (needs the stem and whole ear)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from audit.finder import LINE_GAP_MS, LINE_VOICE_MAX_MS, line_offsets

EAR_LAG_MS = 50
NOTICE_LATE_MS = 220
NOTICE_EARLY_MS = -330


@dataclass(frozen=True)
class LineTiming:
    line: int
    doc_ms: int
    error_ms: int          # document start minus where it is sung (+ = late)
    witness: str           # "ear" or "voice"

    @property
    def noticeable(self) -> bool:
        return self.error_ms > NOTICE_LATE_MS or self.error_ms < NOTICE_EARLY_MS


def line_timings(doc: dict, ear: dict | None, voice_onsets: list[int] | None) -> list[LineTiming]:
    lines = doc.get("lines", [])
    out: dict[int, LineTiming] = {}
    if ear:
        offsets, borrowed = line_offsets(doc, ear)
        for li, off in offsets.items():
            if li in borrowed:
                continue
            start = (lines[li].get("words") or [lines[li]])[0]["start_ms"]
            # heard = start + off; sung = heard - lag; error = start - sung
            out[li] = LineTiming(li, start, EAR_LAG_MS - off, "ear")
    if voice_onsets:
        prev_end: int | None = None
        for li, ln in enumerate(lines):
            words = ln.get("words") or []
            if words and prev_end is not None and li not in out \
                    and words[0]["start_ms"] - prev_end >= LINE_GAP_MS:
                start = words[0]["start_ms"]
                nearest = min(voice_onsets, key=lambda v: abs(v - start))
                if abs(start - nearest) <= LINE_VOICE_MAX_MS:
                    out[li] = LineTiming(li, start, start - nearest, "voice")
            prev_end = ln["end_ms"] if words or prev_end is None else max(prev_end, ln["end_ms"])
    return [out[k] for k in sorted(out)]


def song_score(timings: list[LineTiming], n_lines: int) -> dict:
    """Noticeable misses among the lines a witness could place."""
    if not timings:
        return {"lines": n_lines, "placed": 0}
    misses = [t for t in timings if t.noticeable]
    return {
        "lines": n_lines,
        "placed": len(timings),
        "median_error_ms": round(statistics.median(t.error_ms for t in timings)),
        "noticeable": len(misses),
        "noticeable_share": round(len(misses) / len(timings), 3),
        "late": sum(1 for t in misses if t.error_ms > 0),
        "early": sum(1 for t in misses if t.error_ms < 0),
    }


def main(argv: list[str] | None = None) -> int:
    from audit.analysis import analyse
    from audit.docs import load_document
    from audit.ear import whole_path
    from audit.paths import stem_path

    parser = argparse.ArgumentParser(description="Perceived accuracy of one document.")
    parser.add_argument("id")
    parser.add_argument("--doc", required=True)
    parser.add_argument("--list", action="store_true", help="print every noticeable line")
    args = parser.parse_args(argv)
    doc = load_document(Path(args.doc), args.id)
    ear_file = whole_path(args.id)
    ear = json.loads(ear_file.read_text()) if ear_file.exists() else None
    voice = analyse(args.id).voice_onsets if stem_path(args.id).exists() else None
    timings = line_timings(doc, ear, voice)
    score = song_score(timings, len(doc.get("lines", [])))
    print(json.dumps({"id": args.id, "pipeline_version": doc.get("pipeline_version"), **score}))
    if args.list:
        for t in timings:
            if t.noticeable:
                text = doc["lines"][t.line]["text"][:50]
                print(f"  L{t.line:<3} {t.doc_ms / 1000:7.2f}s {t.error_ms:+6d} ms "
                      f"{t.witness:5} {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
