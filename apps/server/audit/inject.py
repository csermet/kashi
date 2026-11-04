"""Seeded errors injected into a correct lyrics sheet: the anchor check's bench gate.

The DALI audit found the three classes this check is for: offsets (constant,
linearly drifting or local), wrong lyrics and extra paragraphs at the end
[kick24]. Each input pair is a whole-song ear and the CORRECT record for that
audio (on the bench: a JamendoLyrics song's human line starts, ears made
locally); every case damages a copy of the record and runs ``audit.anchors``
on it.

Cases per song, run in a seeded random order: unmodified; global +2 / +5 / +10
/ +20 s (the sheet late); global -2 / -5 / -10 s (the sheet early, stamps
clamped at 0: reported only, never gated); a linear drift reaching
DRIFT_RANGE_MS at the last line; one verse shifted VERSE_SHIFT_RANGE_MS; one
verse deleted; an extra trailing paragraph (lines of another song in the set,
after the last line). The gate (``docs/research/yerel-model-denetim-2026-11.md``,
Faz 3.5 step 1; the early direction stays out of it):

- shifts of >= 5 s (global and verse) detected (song flagged ``shifted``) in
  >= 90 % of cases; 2 s is reported;
- offset error when detected (the shifted part's median against the injected
  shift) <= 0.5 s, every detected case (the 90th percentile is reported);
- false alarms (a flag on an unmodified sheet) <= 5 %.
Early shifts, drift, deleted verses and extra paragraphs are reported, not gated.

    python -m audit.inject manifest --jamendo ROOT --out DIR     # records from Jamendo
    python -m audit.inject run --pairs DIR/manifest.json --seed 1 [--json out] [--noisy]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from audit.anchors import (
    LONG_MOVE_MIN_LINES,
    MIN_ANCHOR_WORDS,
    NOISY_MIN_ANCHOR_WORDS,
    SheetLine,
    check,
    sheet_from_record,
)

GLOBAL_SHIFTS_MS = (2000, 5000, 10_000, 20_000)
EARLY_SHIFTS_MS = (-2000, -5000, -10_000)   # the sheet early: reported, never gated
DRIFT_RANGE_MS = (2000, 10_000)        # the last line ends up this much later
VERSE_SHIFT_RANGE_MS = (3000, 20_000)
EXTRA_LINES = 4
EXTRA_GAP_MS = 4000
GATED_SHIFT_MS = 5000
# Pre-registered gates
MIN_DETECTION = 0.90
MAX_OFFSET_ERROR_MS = 500     # every detected case
OFFSET_ERROR_PERCENTILE = 0.90  # reported
MAX_FALSE_ALARM = 0.05
FLAGGED = frozenset({"shifted", "wrong-version candidate"})


@dataclass(frozen=True)
class Case:
    song: str
    kind: str              # none | global | early | drift | verse | deleted | extra
    shift_ms: int = 0      # global / early / verse: the injected shift; drift: at the last line
    section: int | None = None


def _stamp(ms: int) -> str:
    cs = max(0, round(ms / 10))
    return f"[{cs // 6000:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}]"


def record_of(sheet: Sequence[SheetLine], record_id: int = 0) -> dict:
    """An lrclib-like record of the sheet; sections become paragraphs."""
    out: list[str] = []
    for i, ln in enumerate(sheet):
        if i and ln.section != sheet[i - 1].section:
            out.append("")
        out.append(f"{_stamp(ln.start_ms)} {ln.text}")
    return {"id": record_id, "syncedLyrics": "\n".join(out), "instrumental": False}


def _renumber(sheet: list[SheetLine]) -> list[SheetLine]:
    return [replace(ln, index=i) for i, ln in enumerate(sheet)]


def inject(sheet: Sequence[SheetLine], case: Case,
           donor: Sequence[SheetLine] = ()) -> list[SheetLine]:
    """The sheet with the case's damage (pure; ``donor`` feeds ``extra``)."""
    lines = list(sheet)
    if case.kind == "none":
        return lines
    if case.kind == "global":
        return [replace(ln, start_ms=ln.start_ms + case.shift_ms) for ln in lines]
    if case.kind == "early":
        # a record holds no negative stamp: lines pushed before 0 sit at 0
        return [replace(ln, start_ms=max(0, ln.start_ms + case.shift_ms)) for ln in lines]
    if case.kind == "drift":
        first, last = lines[0].start_ms, lines[-1].start_ms
        span = max(1, last - first)
        return [replace(ln, start_ms=ln.start_ms + round(case.shift_ms * (ln.start_ms - first)
                                                          / span)) for ln in lines]
    if case.kind == "verse":
        return [replace(ln, start_ms=ln.start_ms + case.shift_ms) if ln.section == case.section
                else ln for ln in lines]
    if case.kind == "deleted":
        return _renumber([ln for ln in lines if ln.section != case.section])
    if case.kind == "extra":
        t = lines[-1].start_ms
        section = lines[-1].section + 1
        extra = []
        for ln in list(donor)[:EXTRA_LINES]:
            t += EXTRA_GAP_MS
            extra.append(replace(ln, start_ms=t, section=section))
        return _renumber(lines + extra)
    raise ValueError(f"unknown case kind {case.kind!r}")


def cases_for(song: str, sheet: Sequence[SheetLine], rng: random.Random) -> list[Case]:
    """A shifted verse is a section of at least LONG_MOVE_MIN_LINES lines: the
    check never moves a shorter block by more than 5 s (so a gated shift of a
    shorter verse would measure the case mix, not the check); a deleted verse
    is any section of two or more lines. A one-section song has neither."""
    sections = sorted({ln.section for ln in sheet})
    size = {s: sum(1 for ln in sheet if ln.section == s) for s in sections}
    several = len(sections) > 1
    shiftable = [s for s in sections if size[s] >= LONG_MOVE_MIN_LINES] if several else []
    deletable = [s for s in sections if size[s] >= 2] if several else []
    out = [Case(song, "none")]
    out += [Case(song, "global", s) for s in GLOBAL_SHIFTS_MS]
    out += [Case(song, "early", s) for s in EARLY_SHIFTS_MS]
    out.append(Case(song, "drift", rng.randint(*DRIFT_RANGE_MS)))
    if shiftable:
        out.append(Case(song, "verse", rng.randint(*VERSE_SHIFT_RANGE_MS), rng.choice(shiftable)))
    if deletable:
        out.append(Case(song, "deleted", section=rng.choice(deletable)))
    out.append(Case(song, "extra"))
    return out


def affected_median(result: dict, case: Case, sheet: Sequence[SheetLine]) -> int | None:
    """The check's reading of the damaged part: the song's median for a global
    shift (late or early), the injected verse's lines for a verse shift."""
    lines = result["a1"]["lines"]
    if case.kind in ("global", "early"):
        return result["a1"]["song"]["median_ms"]
    if case.kind == "verse":
        deltas = [ln["delta_ms"] for ln in lines if sheet[ln["line"]].section == case.section]
        return round(statistics.median(deltas)) if deltas else None
    return None


def evaluate(case: Case, sheet: Sequence[SheetLine], result: dict) -> dict:
    """One case's outcome. A shift moves the sheet by shift_ms (late when
    positive, early when negative), so a correct reading (heard minus sheet) is
    -shift_ms."""
    flagged = result["flag"] in FLAGGED
    out = {"song": case.song, "kind": case.kind, "shift_ms": case.shift_ms,
           "section": case.section, "flag": result["flag"], "flagged": flagged}
    if case.kind in ("global", "early", "verse"):
        if case.kind == "verse":
            # the injected verse's own block must read shifted, not any block
            block = next((b for b in result["a1"]["blocks"] if case.section in b["sections"]),
                         None)
            detected = block is not None and block["verdict"] == "shifted"
        else:
            detected = "shifted" in result["flags"]
        median = affected_median(result, case, sheet)
        out |= {"detected": detected, "read_ms": median,
                "error_ms": None if median is None or not detected
                else abs(median + case.shift_ms)}
    elif case.kind == "drift":
        out["detected"] = "shifted" in result["flags"]
    elif case.kind in ("deleted", "extra"):
        a3 = result["a3"]
        out |= {"unmatched_sung_share": a3["unmatched_sung_share"],
                "unmatched_line_share": a3["unmatched_line_share"]}
    return out


def _percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest rank: the smallest value with at least q of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def gate(outcomes: Sequence[dict]) -> dict:
    """The pre-registered gate over every case outcome."""
    shifts = [o for o in outcomes if o["kind"] in ("global", "verse")]
    gated = [o for o in shifts if o["shift_ms"] >= GATED_SHIFT_MS]
    small = [o for o in shifts if o["shift_ms"] < GATED_SHIFT_MS]
    errors = [o["error_ms"] for o in gated if o.get("detected") and o["error_ms"] is not None]
    none = [o for o in outcomes if o["kind"] == "none"]
    detection = sum(o["detected"] for o in gated) / len(gated) if gated else None
    p_err = _percentile(errors, OFFSET_ERROR_PERCENTILE)
    max_err = max(errors) if errors else None
    false_alarm = sum(o["flagged"] for o in none) / len(none) if none else None

    def rate(kind: str) -> float | None:
        rows = [o for o in outcomes if o["kind"] == kind]
        return round(sum(o.get("detected", o["flagged"]) for o in rows) / len(rows), 3) \
            if rows else None

    def share(rows: Sequence[dict]) -> float | None:
        return round(sum(o["detected"] for o in rows) / len(rows), 3) if rows else None

    early = [o for o in outcomes if o["kind"] == "early"]
    early_big = [o for o in early if -o["shift_ms"] >= GATED_SHIFT_MS]
    early_errors = [o["error_ms"] for o in early_big
                    if o.get("detected") and o["error_ms"] is not None]

    checks = {
        "detection_ge_5s": detection is not None and detection >= MIN_DETECTION,
        "offset_error": max_err is not None and max_err <= MAX_OFFSET_ERROR_MS,
        "false_alarm": false_alarm is not None and false_alarm <= MAX_FALSE_ALARM,
    }
    return {
        "cases": len(outcomes),
        "detection_ge_5s": None if detection is None else round(detection, 3),
        "detection_lt_5s": round(sum(o["detected"] for o in small) / len(small), 3)
        if small else None,
        "offset_error_p90_ms": p_err,
        "offset_error_max_ms": max_err,
        "false_alarm": None if false_alarm is None else round(false_alarm, 3),
        "early": {  # reported only: none of it enters `checks`
            "detection_ge_5s": share(early_big),
            "detection_lt_5s": share([o for o in early if -o["shift_ms"] < GATED_SHIFT_MS]),
            "offset_error_p90_ms": _percentile(early_errors, OFFSET_ERROR_PERCENTILE),
            "offset_error_max_ms": max(early_errors) if early_errors else None,
        },
        "drift_detection": rate("drift"),
        "deleted_flagged": rate("deleted"),
        "extra_flagged": rate("extra"),
        "checks": checks,
        "passed": all(checks.values()),
    }


def run(pairs: Sequence[tuple[str, dict, dict]], seed: int,
        min_words: int = MIN_ANCHOR_WORDS) -> dict:
    """``pairs``: (song, ear, correct record). Every case of every song, in one
    seeded random order."""
    rng = random.Random(seed)
    sheets = {song: sheet_from_record(rec) for song, _, rec in pairs}
    ears = {song: e for song, e, _ in pairs}
    songs = [song for song, _, _ in pairs]
    cases = [c for song in songs for c in cases_for(song, sheets[song], rng)]
    # the extra paragraph is another song's lines (seeded; none with one song)
    donor = {song: sheets[rng.choice([s for s in songs if s != song])] if len(songs) > 1 else []
             for song in songs}
    rng.shuffle(cases)
    outcomes = []
    for case in cases:
        damaged = inject(sheets[case.song], case, donor[case.song])
        result = check(damaged, ears[case.song], min_words)
        outcomes.append(evaluate(case, damaged, result))
    return {"seed": seed, "songs": len(songs), "gate": gate(outcomes), "outcomes": outcomes}


# --- inputs ----------------------------------------------------------------------

def jamendo_records(root: Path) -> list[tuple[str, dict]]:
    """(song id, correct record) per JamendoLyrics song: its human line starts,
    sections at SECTION_GAP_MS silences (the dataset has no paragraphs)."""
    from audit.anchors import _gap_sections
    from benchmarks.datasets import load_jamendo

    out = []
    for song in load_jamendo(root):
        starts = song.line_starts_ms
        ends = []
        it = iter(zip(song.words, song.word_ends_ms, strict=True))
        n_words = [len(t.split()) for t in song.line_texts]
        for n in n_words:
            last = None
            for _ in range(n):
                last = next(it)[1]
            ends.append(last or 0)
        sections = _gap_sections(starts, ends)
        sheet = [SheetLine(i, text, start, sec, 90.0)
                 for i, (text, start, sec) in enumerate(zip(song.line_texts, starts, sections,
                                                             strict=True))]
        out.append((f"jam-{song.stem}", record_of(sheet)))
    return out


def main(argv: list[str] | None = None) -> int:
    from audit.paths import EAR_DIR

    parser = argparse.ArgumentParser(description="Injected sheet errors: the anchor gate.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    man = sub.add_parser("manifest", help="correct records from JamendoLyrics + a manifest")
    man.add_argument("--jamendo", type=Path, required=True)
    man.add_argument("--out", type=Path, required=True)
    man.add_argument("--ears", type=Path, default=EAR_DIR,
                     help="ear root: <ears>/jam-<stem>/whole.json")
    go = sub.add_parser("run", help="inject, check, gate")
    go.add_argument("--pairs", type=Path, required=True,
                    help="manifest: [{id, ear, record}] (paths relative to it)")
    go.add_argument("--seed", type=int, default=1)
    go.add_argument("--json", type=Path)
    go.add_argument("--noisy", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "manifest":
        args.out.mkdir(parents=True, exist_ok=True)
        entries = []
        for song, rec in jamendo_records(args.jamendo):
            path = args.out / f"{song}.record.json"
            path.write_text(json.dumps(rec, ensure_ascii=False) + "\n")
            entries.append({"id": song, "record": path.name,
                            "ear": str((args.ears / song / "whole.json").resolve())})
        (args.out / "manifest.json").write_text(json.dumps(entries, indent=1) + "\n")
        print(f"{len(entries)} records -> {args.out / 'manifest.json'}")
        return 0
    base = args.pairs.parent
    pairs = []
    for entry in json.loads(args.pairs.read_text()):
        ear_path, rec_path = base / entry["ear"], base / entry["record"]
        if not ear_path.exists():
            print(f"skip {entry['id']}: no ear at {ear_path}", file=sys.stderr)
            continue
        pairs.append((entry["id"], json.loads(ear_path.read_text()),
                      json.loads(rec_path.read_text())))
    result = run(pairs, args.seed, NOISY_MIN_ANCHOR_WORDS if args.noisy else MIN_ANCHOR_WORDS)
    print(json.dumps(result["gate"], indent=1))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0 if result["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
