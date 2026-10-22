"""Write the pop set's valid region into its `verified` column (Faz 9 scope trap).

The annotation tool exports `verified=1` on every row, but the annotator only
corrected what they were asked to look at. Everywhere else the "truth" is the
pipeline's own 2.26.0 output, so a plain run over the set scores the pipeline
against itself (the first pop number, PCO 0.931, was invalid for exactly this
reason). Until now the rule lived only in prose (hand-off note, "KAPSAM
TUZAĞI"); here it is data, and every tool that already honours `verified`
(benchmarks.run, benchmarks.lateness) honours it with no change.

The rule, per song:
- Uptown Funk, Shape of You, Rock It for Me: only the LINES that carry a
  correction;
- Stressed Out, Safari: from the start up to the last corrected word.

Regions were reconstructed on 2026-10-18 from the annotations against the
2.26.0 packets (a start moved by more than 1 ms counts as a correction) and
reproduce the hand-off's Stressed Out figures exactly. Safari's packet words
stop lining up with the CSV rows after line 6 (11 packet lines carry no
words), so its region is fixed by index rather than re-derived.

usage (after eval-pop/make_jamendo_root.py rebuilt the set):
    python -m benchmarks.pop_valid [--root benchmarks/data/jamendolyrics-pop] [--check]
"""

import argparse
import csv
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).parent / "data" / "jamendolyrics-pop"

#: stem -> ("lines", 0-based line indices) or ("words", first, last word row).
REGIONS: dict[str, tuple] = {
    "Mark_Ronson_-_Uptown_Funk": ("lines", (16, 18, 20, 22, 47, 49, 51, 53)),
    "Ed_Sheeran_-_Shape_of_You": ("lines", (27, 28, 29)),
    "Caravan_Palace_-_Rock_It_for_Me": ("lines", (1, 9, 11, 13)),
    "twenty_one_pilots_-_Stressed_Out": ("words", 0, 137),
    "Serena_-_Safari": ("words", 0, 52),
}

#: What each region must come to. A different count means the annotation or
#: the built set moved under the rule, and the rule must be re-derived first.
EXPECTED_WORDS = {
    "Mark_Ronson_-_Uptown_Funk": 42,
    "Ed_Sheeran_-_Shape_of_You": 22,
    "Caravan_Palace_-_Rock_It_for_Me": 23,
    "twenty_one_pilots_-_Stressed_Out": 138,
    "Serena_-_Safari": 53,
}


def line_indices(rows: list[dict]) -> list[int]:
    """0-based line index per word row: a row with a `line_end` closes its line."""
    out, line = [], 0
    for row in rows:
        out.append(line)
        if row.get("line_end", "nan") not in ("", "nan"):
            line += 1
    return out


def valid_mask(rows: list[dict], region: tuple) -> list[bool]:
    if region[0] == "lines":
        keep = set(region[1])
        return [line in keep for line in line_indices(rows)]
    _, first, last = region
    return [first <= i <= last for i in range(len(rows))]


def apply(root: Path, *, check: bool = False) -> int:
    """Rewrite `verified` in place (or only check). Returns the valid word total."""
    total = 0
    for stem, region in REGIONS.items():
        path = root / "annotations" / "words" / f"{stem}.csv"
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        mask = valid_mask(rows, region)
        # A row the builder already excluded (out of order) stays excluded.
        kept = [m and row.get("verified", "1") != "0" for m, row in zip(mask, rows, strict=True)]
        count = sum(kept)
        if count != EXPECTED_WORDS[stem]:
            sys.exit(f"{stem}: {count} valid words, expected {EXPECTED_WORDS[stem]} — re-derive")
        total += count
        print(f"{stem:34} {count:4d} valid of {len(rows)}")
        if check:
            continue
        for row, ok in zip(rows, kept, strict=True):
            row["verified"] = "1" if ok else "0"
        fields = ["word_start", "word_end", "line_end", "verified"]
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows({key: row.get(key, "") for key in fields} for row in rows)
    print(f"total {total} valid words" + (" (check only)" if check else ""))
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--check", action="store_true", help="count only, write nothing")
    args = parser.parse_args()
    apply(args.root, check=args.check)


if __name__ == "__main__":
    main()
