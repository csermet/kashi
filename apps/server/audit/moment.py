"""One audit moment: which overlay frames to take, and its numbers.

Pure functions over documents, the song analysis and the ear output; no I/O.
Clocks: documents and audio share the SONG clock (player position). The
overlay lights words at ``position + offset`` (``timing_offset_ms``, default
+200), so on the song clock the screen lights word w at ``w.start - offset``.
"""

from __future__ import annotations

from collections.abc import Sequence

STRIP_HALF_MS = 1250
STRIP_MAX_FRAMES = 10
STRIP_MIN_FRAMES = 8
STRIP_GRID_MS = 250
# A frame this long after a word's (screen) start shows it settled.
EVENT_LAG_MS = 30
# Frames closer than this are one frame.
FRAME_MERGE_MS = 40
# Lead-in check: how far before a line's screen start to look.
LEAD_IN_PROBE_MS = 250

# Word hygiene (Faz 0 values; calibrated in Faz 2 before tests pin them).
SHORT_WORD_MS = 60
PILE_SPAN_MS = 60


def words_in(doc: dict, t0: int, t1: int) -> list[tuple[int, int, dict]]:
    """(line index, word index, word) for every word starting in [t0, t1]."""
    out = []
    for li, line in enumerate(doc.get("lines", [])):
        for wi, word in enumerate(line.get("words") or []):
            if t0 <= word["start_ms"] <= t1:
                out.append((li, wi, word))
    return out


def lines_in(doc: dict, t0: int, t1: int) -> list[tuple[int, dict]]:
    """Lines overlapping [t0, t1]."""
    return [(i, ln) for i, ln in enumerate(doc.get("lines", []))
            if ln["start_ms"] <= t1 and ln["end_ms"] >= t0]


def strip_times(doc: dict, t_ms: int, offset_ms: int,
                half_ms: int = STRIP_HALF_MS) -> list[int]:
    """Player positions to render: every word's screen activation inside the
    strip window (+EVENT_LAG), each line's lead-in, then a grid to fill up."""
    lo, hi = t_ms - half_ms, t_ms + half_ms
    events: list[int] = []
    for _, line in lines_in(doc, lo + offset_ms, hi + offset_ms):
        if line.get("words"):
            events.append(line["start_ms"] - offset_ms - LEAD_IN_PROBE_MS)
    for _, _, word in words_in(doc, lo + offset_ms, hi + offset_ms):
        events.append(word["start_ms"] - offset_ms + EVENT_LAG_MS)
    events = sorted(e for e in events if lo <= e <= hi)
    merged: list[int] = []
    for e in events:
        if not merged or e - merged[-1] >= FRAME_MERGE_MS:
            merged.append(e)
    if len(merged) > STRIP_MAX_FRAMES:
        step = (len(merged) - 1) / (STRIP_MAX_FRAMES - 1)
        merged = [merged[round(i * step)] for i in range(STRIP_MAX_FRAMES)]
    grid = lo
    while len(merged) < STRIP_MIN_FRAMES and grid <= hi:
        if all(abs(grid - m) >= FRAME_MERGE_MS for m in merged):
            merged.append(grid)
        grid += STRIP_GRID_MS
    return sorted(merged)


def word_flags(words: Sequence[dict]) -> list[list[str]]:
    """Per word of one line: 'zero' (0 ms), 'short' (< SHORT_WORD_MS),
    'pile' (another word of the line starts within PILE_SPAN_MS)."""
    flags: list[list[str]] = []
    for i, w in enumerate(words):
        f: list[str] = []
        dur = w["end_ms"] - w["start_ms"]
        if dur <= 0:
            f.append("zero")
        elif dur < SHORT_WORD_MS:
            f.append("short")
        if any(j != i and abs(o["start_ms"] - w["start_ms"]) <= PILE_SPAN_MS
               for j, o in enumerate(words)):
            f.append("pile")
        flags.append(f)
    return flags


def line_numbers(line: dict, offset_ms: int, voice_onset: int | None,
                 onset: int | None, level_db: float | None) -> dict:
    """How a line's first word sits against the audio evidence."""
    words = line.get("words") or []
    start = words[0]["start_ms"] if words else line["start_ms"]
    out: dict = {
        "text": line["text"],
        "start_ms": start,
        "screen_lights_ms": start - offset_ms,
        "level_db_at_start": level_db,
        "nearest_voice_onset_ms": voice_onset,
        "nearest_onset_ms": onset,
    }
    if voice_onset is not None:
        out["doc_minus_voice_ms"] = start - voice_onset
        out["screen_minus_voice_ms"] = start - offset_ms - voice_onset
    if words:
        flags = word_flags(words)
        out["words"] = [
            {"text": w["text"], "start_ms": w["start_ms"], "dur_ms": w["end_ms"] - w["start_ms"],
             "flags": fl}
            for w, fl in zip(words, flags, strict=True)
        ]
    for key in ("uncertain", "adlib", "words_derived"):
        if line.get(key):
            out[key] = True
    if not words:
        out["no_words"] = True
    return out


def ear_letters_in(ear: dict | None, t0: int, t1: int) -> str | None:
    """The model's greedy letters inside [t0, t1] (raw model clock)."""
    if not ear:
        return None
    return "".join(t["text"] for t in ear["tokens"]
                   if t["start_ms"] < t1 and t["end_ms"] > t0).replace("|", " ").strip()


def main(argv: list[str] | None = None) -> int:
    """Print the strip's player times for a moment, comma-separated."""
    import argparse
    from pathlib import Path

    from audit.docs import load_document

    parser = argparse.ArgumentParser(description="Strip frame times for one moment.")
    parser.add_argument("id")
    parser.add_argument("t_ms", type=int)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--offset", type=int, default=200)
    args = parser.parse_args(argv)
    doc = load_document(Path(args.doc), args.id)
    print(",".join(str(t) for t in strip_times(doc, args.t_ms, args.offset)))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
