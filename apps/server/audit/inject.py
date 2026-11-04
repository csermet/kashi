"""Faz 3.5 Step 1 on injected errors: JamendoLyrics sheets made wrong on purpose.

The human-timed JamendoLyrics lines are a healthy sheet on the song's own
clock; each trial damages one copy in a known way and asks the anchor check
(``audit.anchors``) whether it sees it:

  none      unmodified                        a shift or wrong_sheet alarm is a false alarm
  global    every stamp +2 / +5 / +10 / +20 s detection; offset error
  drift     stamps stretched, last line +5 / +10 s   detection
  verse     VERSE_LINES consecutive lines moved 3-20 s, random sign   detection (a shifted
            section over the block); offset error
  deleted   VERSE_LINES consecutive lines removed     reported: unsheeted_singing / wrong_sheet
  extra     VERSE_LINES lines of another song appended reported: unsung_lines / wrong_sheet

Gates (``audit.gates``): detection >= 90 % for shifts >= 5 s (2 s reported),
median offset error <= 0.5 s over detected global and verse shifts, false
alarms <= 5 % on unmodified songs. Trials run in a seeded random order.

    # once, in the audit image with the worker env (the checkpoint per language):
    python -m audit.inject ears --jamendo-root benchmarks/data/jamendolyrics \\
        --stems benchmarks/data/stems/kim-base --out ../../scratch/audit/jamendo-ears
    # then, anywhere:
    python -m audit.inject run --jamendo-root benchmarks/data/jamendolyrics \\
        --ears ../../scratch/audit/jamendo-ears --json ../../scratch/audit/step1-inject.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import statistics
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from audit import gates
from audit.anchors import ALARMS, SheetLine, check, placements, unique_lines

logger = logging.getLogger("audit.inject")

VERSE_LINES = 4
EXTRA_GAP_MS = 4000      # the appended paragraph starts this long after the last stamp ...
EXTRA_STEP_MS = 4000     # ... one line every this long
VERSE_STEP_MS = 100      # verse shift sizes are drawn on this grid
SEED = 35


@dataclass(frozen=True)
class Trial:
    song: str
    kind: str                       # none | global | drift | verse | deleted | extra
    size_ms: int = 0                # global/verse: the shift (sheet later = +); drift: last line
    block: tuple[int, int] | None = None   # the touched lines in the MODIFIED sheet (inclusive)

    @property
    def gated(self) -> bool:
        return self.kind in ("global", "drift", "verse") and \
            abs(self.size_ms) >= gates.INJECT_GATED_MIN_MS


# --- damage -------------------------------------------------------------------

def shift_all(sheet: Sequence[SheetLine], ms: int) -> list[SheetLine]:
    return [SheetLine(ln.text, ln.start_ms + ms) for ln in sheet]


def drift(sheet: Sequence[SheetLine], end_ms: int) -> list[SheetLine]:
    """Stamps stretched about the first line so the last one moves ``end_ms``."""
    first, last = sheet[0].start_ms, sheet[-1].start_ms
    if last == first:
        return list(sheet)
    rate = end_ms / (last - first)
    return [SheetLine(ln.text, ln.start_ms + round((ln.start_ms - first) * rate)) for ln in sheet]


def verse_block(n_lines: int, rng: random.Random) -> tuple[int, int] | None:
    """A block of VERSE_LINES lines with at least one line on either side."""
    if n_lines < VERSE_LINES + 2:
        return None
    first = rng.randint(1, n_lines - VERSE_LINES - 1)
    return first, first + VERSE_LINES - 1


def shift_block(sheet: Sequence[SheetLine], block: tuple[int, int], ms: int) -> list[SheetLine]:
    lo, hi = block
    return [SheetLine(ln.text, ln.start_ms + (ms if lo <= i <= hi else 0))
            for i, ln in enumerate(sheet)]


def delete_block(sheet: Sequence[SheetLine], block: tuple[int, int]) -> list[SheetLine]:
    lo, hi = block
    return [ln for i, ln in enumerate(sheet) if not lo <= i <= hi]


def append_lines(sheet: Sequence[SheetLine], texts: Sequence[str]) -> list[SheetLine]:
    t = sheet[-1].start_ms + EXTRA_GAP_MS
    return list(sheet) + [SheetLine(text, t + k * EXTRA_STEP_MS) for k, text in enumerate(texts)]


def trials_for(song: str, sheet: Sequence[SheetLine], donor: Sequence[str],
               rng: random.Random) -> list[tuple[Trial, list[SheetLine]]]:
    """Every trial of one song, with its damaged sheet. ``donor``: another
    song's line texts for the extra paragraph."""
    out: list[tuple[Trial, list[SheetLine]]] = [(Trial(song, "none"), list(sheet))]
    for ms in gates.INJECT_GLOBAL_MS:
        out.append((Trial(song, "global", ms), shift_all(sheet, ms)))
    for ms in gates.INJECT_DRIFT_END_MS:
        out.append((Trial(song, "drift", ms), drift(sheet, ms)))
    lo_ms, hi_ms = gates.INJECT_VERSE_MS
    for _ in range(2):
        block = verse_block(len(sheet), rng)
        if block is None:
            break
        ms = rng.randrange(lo_ms, hi_ms + 1, VERSE_STEP_MS) * rng.choice((-1, 1))
        out.append((Trial(song, "verse", ms, block), shift_block(sheet, block, ms)))
    block = verse_block(len(sheet), rng)
    if block is not None:
        # after the deletion, the lines either side of the hole
        out.append((Trial(song, "deleted", VERSE_LINES, (block[0] - 1, block[0])),
                    delete_block(sheet, block)))
    if len(donor) >= VERSE_LINES:
        extra = append_lines(sheet, donor[:VERSE_LINES])
        out.append((Trial(song, "extra", VERSE_LINES, (len(sheet), len(extra) - 1)), extra))
    return out


# --- reading one trial -----------------------------------------------------------

class PlacementCache:
    """Whole-song placements depend on a line's text and the ear only, never on
    its stamp: computed once per song and text."""

    def __init__(self, ear: dict):
        self.ear = ear
        self.by_text: dict[str, tuple[int, float, float] | None] = {}

    def for_sheet(self, sheet: Sequence[SheetLine]) -> dict[int, tuple[int, float, float]]:
        out = {}
        for i in unique_lines(sheet):
            text = sheet[i].text
            if text not in self.by_text:
                found = placements([sheet[i]], self.ear, [0])
                self.by_text[text] = found.get(0)
            hit = self.by_text[text]
            if hit is not None:
                out[i] = hit
        return out


def _overlaps(section: dict, block: tuple[int, int]) -> bool:
    return section["first_line"] <= block[1] and section["last_line"] >= block[0]


def read_trial(trial: Trial, result: dict) -> dict:
    """detected, and the offset error where the trial has one offset."""
    flags = set(result["flags"])
    row: dict = {**asdict(trial), "flags": sorted(flags), "detected": None, "offset_error_ms": None}
    if trial.kind == "none":
        row["false_alarm"] = bool(flags & ALARMS)
        return row
    if trial.kind in ("global", "drift"):
        row["detected"] = "shift" in flags
    elif trial.kind == "verse":
        assert trial.block is not None
        row["detected"] = "shift" in flags and any(
            s["shifted"] and _overlaps(s, trial.block) for s in result["sections"])
    elif trial.kind == "deleted":
        row["detected"] = bool(flags & {"unsheeted_singing", "wrong_sheet"})
    elif trial.kind == "extra":
        row["detected"] = bool(flags & {"unsung_lines", "wrong_sheet"})
    if row["detected"] and trial.kind in ("global", "verse"):
        lo, hi = trial.block if trial.block else (0, result["lines"] - 1)
        kept = [a for a in result["anchors"] if lo <= a["line"] <= hi
                and a["line"] not in result["left_out"]]
        if kept:
            estimate = statistics.median(a["delta_ms"] for a in kept)
        elif result["shift_kind"] == "activity":
            estimate = result["activity"]["offset_ms"]
        else:
            estimate = None
        if estimate is not None:
            # a sheet moved +x is heard x earlier than it says: delta = -x
            row["offset_error_ms"] = round(abs(estimate + trial.size_ms))
    return row


def summarize(rows: Sequence[dict]) -> dict:
    def rate(items: list[bool]) -> float | None:
        return round(sum(items) / len(items), 3) if items else None

    gated = [r["detected"] for r in rows if Trial(r["song"], r["kind"], r["size_ms"]).gated]
    errors = sorted(r["offset_error_ms"] for r in rows if r["offset_error_ms"] is not None)
    unmodified = [r["false_alarm"] for r in rows if r["kind"] == "none"]
    out = {
        "trials": len(rows),
        "gated_shifts": len(gated),
        "detection": rate(gated),
        "detection_2s": rate([r["detected"] for r in rows if r["kind"] == "global"
                              and r["size_ms"] == gates.INJECT_REPORTED_MS]),
        "detection_verse_under_5s": rate([r["detected"] for r in rows if r["kind"] == "verse"
                                          and abs(r["size_ms"]) < gates.INJECT_GATED_MIN_MS]),
        "detection_deleted": rate([r["detected"] for r in rows if r["kind"] == "deleted"]),
        "detection_extra": rate([r["detected"] for r in rows if r["kind"] == "extra"]),
        "offset_error_median_ms": round(statistics.median(errors)) if errors else None,
        "offset_error_p90_ms": errors[min(len(errors) - 1, int(0.9 * len(errors)))]
        if errors else None,
        "false_alarm": rate(unmodified),
        "unmodified": len(unmodified),
    }
    out["gates"] = {
        "detection": out["detection"] is not None
        and out["detection"] >= gates.INJECT_DETECTION_MIN,
        "offset_error": out["offset_error_median_ms"] is not None
        and out["offset_error_median_ms"] <= gates.INJECT_OFFSET_ERR_MS,
        "false_alarm": out["false_alarm"] is not None
        and out["false_alarm"] <= gates.INJECT_FALSE_ALARM_MAX,
    }
    out["passed"] = all(out["gates"].values())
    return out


def run_trials(songs: Sequence[tuple[str, list[SheetLine], dict]], seed: int = SEED) -> list[dict]:
    """``songs``: (name, healthy sheet, whole-song ear). Donor paragraphs come
    from the next song of the list."""
    rng = random.Random(seed)
    work: list[tuple[Trial, list[SheetLine]]] = []
    for k, (name, sheet, _) in enumerate(songs):
        donor = [ln.text for ln in songs[(k + 1) % len(songs)][1]] if len(songs) > 1 else []
        work += trials_for(name, sheet, donor, rng)
    rng.shuffle(work)
    ears = {name: ear for name, _, ear in songs}
    caches = {name: PlacementCache(ear) for name, ear in ears.items()}
    rows = []
    for trial, sheet in work:
        result = check(sheet, ears[trial.song], caches[trial.song].for_sheet(sheet))
        rows.append(read_trial(trial, result))
    return rows


# --- JamendoLyrics ---------------------------------------------------------------

def jamendo_sheet(song) -> list[SheetLine]:
    return [SheetLine(t, s) for t, s in zip(song.line_texts, song.line_starts_ms, strict=True)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Faz 3.5 Step 1 on injected errors.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ears = sub.add_parser("ears", help="whole-song ears of the bench's Jamendo stems")
    ears.add_argument("--jamendo-root", type=Path, required=True)
    ears.add_argument("--stems", type=Path, required=True, help="the bench's stem dir")
    ears.add_argument("--out", type=Path, required=True)
    run = sub.add_parser("run", help="the trials and the gates")
    run.add_argument("--jamendo-root", type=Path, required=True)
    run.add_argument("--ears", type=Path, required=True)
    run.add_argument("--seed", type=int, default=SEED)
    run.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from benchmarks.datasets import load_jamendo

    songs = load_jamendo(args.jamendo_root)
    if args.cmd == "ears":
        from audit.ear import hear_audio
        from kashi_server.pipeline.alignment import resolve_aligner

        args.out.mkdir(parents=True, exist_ok=True)
        for song in songs:
            dest = args.out / f"{song.stem}.json"
            stem = args.stems / f"{song.stem}.wav"
            if dest.exists() or not stem.exists():
                continue
            model = resolve_aligner(song.language).model_name
            dest.write_text(json.dumps(hear_audio(stem, model, label=song.stem)) + "\n")
            logger.info("%s: ear with %s", song.stem, model)
        return 0
    loaded = []
    for song in songs:
        path = args.ears / f"{song.stem}.json"
        if path.exists():
            loaded.append((song.stem, jamendo_sheet(song), json.loads(path.read_text())))
    if not loaded:
        print("no ears found", file=sys.stderr)
        return 2
    rows = run_trials(loaded, args.seed)
    summary = summarize(rows)
    print(f"{len(loaded)} songs, {summary['trials']} trials (seed {args.seed})")
    verdicts = {k: gates.verdict(v) for k, v in summary["gates"].items()}
    print(f"  detection, shifts >= 5 s: {summary['detection']} of {summary['gated_shifts']} "
          f"(gate >= {gates.INJECT_DETECTION_MIN}) -> {verdicts['detection']}")
    print(f"  offset error median {summary['offset_error_median_ms']} ms, p90 "
          f"{summary['offset_error_p90_ms']} ms (gate <= {gates.INJECT_OFFSET_ERR_MS}) -> "
          f"{verdicts['offset_error']}")
    print(f"  false alarms {summary['false_alarm']} of {summary['unmodified']} unmodified "
          f"(gate <= {gates.INJECT_FALSE_ALARM_MAX}) -> {verdicts['false_alarm']}")
    print(f"  reported: 2 s shifts {summary['detection_2s']}, verse < 5 s "
          f"{summary['detection_verse_under_5s']}, deleted verse {summary['detection_deleted']}, "
          f"extra paragraph {summary['detection_extra']}")
    print(f"Step 1 injected errors: {gates.verdict(summary['passed'])}")
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "trials": rows}, indent=1) + "\n")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
