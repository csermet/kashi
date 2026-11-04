"""The anchor check (``audit.anchors``) on real songs: one song, or the Faz 3.5
Step 1 pilot gates.

    python -m audit.anchor_check <id> --doc <dump> [--record <lrclib json>]
    python -m audit.anchor_check --pilot --doc <dump> --expect <expect.json> [--json out]

The sheet is the document's lines (windowed documents follow their stamps) or,
with ``--record``, the lrclib record's own stamps. The ear is
``ear/<id>/whole.json``. One song writes ``runs/<id>/anchors.json``.

``expect.json`` (kept outside the repository: it names songs) maps an id to
what Faz 3 read there; ids it does not name are expected clean:

    {"<id>": {"expect": "shift", "offset_ms": -18900, "label": "..."},
     "<id>": {"expect": "none", "label": "named negative"},
     "<id>": {"expect": "wrong_sheet", "label": "only after the owner's listen"}}

Gates (``audit.gates``): an expected shift is flagged and its offset lands
within PILOT_OFFSET_TOL_MS of the reading (sign included); an expected wrong
sheet raises wrong_sheet; every other song raises no alarm.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from audit import gates
from audit.anchors import ALARMS, check, sheet_from_document, sheet_from_record


def song_offset(result: dict) -> int | None:
    """The offset a shift flag stands on: the shifted section with the most
    anchors, or the activity fit when A1 found no section."""
    shifted = [s for s in result["sections"] if s["shifted"]]
    if shifted:
        return max(shifted, key=lambda s: s["anchors"])["median_ms"]
    if result["shift_kind"] == "activity":
        return result["activity"]["offset_ms"]
    return None


def pilot_verdict(result: dict, expect: dict | None) -> tuple[bool, str]:
    """(passes, why) for one song against its expectation (None = clean)."""
    flags = set(result["flags"])
    expect = expect or {}
    want = expect.get("expect", "none")
    if want == "shift":
        offset = song_offset(result)
        if "shift" not in flags or offset is None:
            return False, "no shift flagged"
        reading = int(expect["offset_ms"])
        error = abs(offset - reading)
        return (error <= gates.PILOT_OFFSET_TOL_MS,
                f"shift {offset:+d} ms, reading {reading:+d} ms, off by {error} ms")
    if want == "wrong_sheet":
        return "wrong_sheet" in flags, "wrong_sheet " + ("raised" if "wrong_sheet" in flags
                                                         else "not raised")
    alarms = sorted(flags & ALARMS)
    return not alarms, "clean" if not alarms else "alarm: " + ", ".join(alarms)


def _ear(video_id: str) -> dict | None:
    from audit.ear import whole_path

    path = whole_path(video_id)
    return json.loads(path.read_text()) if path.exists() else None


def summary_line(video_id: str, result: dict) -> str:
    cov = result["coverage"]
    secs = ", ".join(f"L{s['first_line']}-{s['last_line']} {s['median_ms']:+d} ms "
                     f"({s['anchors']})" for s in result["sections"]) or "none"
    act = result["activity"]
    return (f"{video_id}: anchors {len(result['anchors'])}/{result['anchor_candidates']} "
            f"sections [{secs}] drift {result['drift_ms']} ms; activity "
            f"{act['offset_ms']} ms x{act['stretch']} ncc {act['ncc']}; lines heard "
            f"{cov['lines_matched']}/{cov['lines_checked']}, unmatched singing "
            f"{cov['unmatched_sung_share']}; flags {result['flags'] or '-'}")


def main(argv: list[str] | None = None) -> int:
    from audit.docs import documents_by_id
    from audit.paths import RUNS_DIR

    parser = argparse.ArgumentParser(description="Whole-song anchor check (Faz 3.5 A1-A3).")
    parser.add_argument("id", nargs="?")
    parser.add_argument("--doc", type=Path, required=True)
    parser.add_argument("--record", type=Path, help="check this lrclib record's stamps instead")
    parser.add_argument("--pilot", action="store_true", help="the Step 1 pilot gates")
    parser.add_argument("--expect", type=Path, help="pilot expectations (see the docstring)")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    docs = documents_by_id(json.loads(args.doc.read_text()))
    if not args.pilot:
        if not args.id or args.id not in docs:
            print("give an id that the dump holds", file=sys.stderr)
            return 2
        ear = _ear(args.id)
        if ear is None:
            print(f"{args.id}: no whole-song ear (audit.ear --whole)", file=sys.stderr)
            return 2
        sheet = (sheet_from_record(json.loads(args.record.read_text())) if args.record
                 else sheet_from_document(docs[args.id]))
        result = check(sheet, ear)
        out = RUNS_DIR / args.id
        out.mkdir(parents=True, exist_ok=True)
        (out / "anchors.json").write_text(json.dumps(result, indent=1) + "\n")
        print(summary_line(args.id, result))
        return 0
    expect = json.loads(args.expect.read_text()) if args.expect else {}
    rows, passed = [], True
    for video_id, doc in sorted(docs.items()):
        ear = _ear(video_id)
        if ear is None:
            continue
        result = check(sheet_from_document(doc), ear)
        ok, why = pilot_verdict(result, expect.get(video_id))
        passed &= ok
        label = (expect.get(video_id) or {}).get("label", "")
        print(f"{'pass' if ok else 'FAIL'}  {label[:20]:20} {why}")
        print("      " + summary_line(video_id, result))
        rows.append({"id": video_id, "passed": ok, "why": why, "result": result})
    missing = sorted(set(expect) - {r["id"] for r in rows})
    if missing:
        passed = False
        print(f"FAIL  expected songs without a document or ear: {', '.join(missing)}")
    print(f"Step 1 pilot ({len(rows)} songs): {gates.verdict(passed and bool(rows))}")
    if args.json:
        args.json.write_text(json.dumps({"passed": passed, "songs": rows}, indent=1) + "\n")
    return 0 if passed and rows else 1


if __name__ == "__main__":
    sys.exit(main())
