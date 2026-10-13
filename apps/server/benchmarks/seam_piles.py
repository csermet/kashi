"""Count seam piles in stored documents (Faz 9, 2026-10-13).

A seam pile is a line whose first two or more words have zero length while the
previous line's last word starts at (or within 120 ms of) that same instant:
the previous window left a word in its padded tail, and `reconcile_seams`
pushed this line's correctly placed opening words forward onto it.

usage: python -m benchmarks.seam_piles <documents.json>

<documents.json> is a JSON array of {"id", "pv", "method", "lines"} objects —
one per word-synced document (`lines` as stored, `method` from
`alignment.method`). Prints the totals, the split by alignment method and by
pipeline minor version, and the worst documents.
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

#: How close the previous line's last word must start to count as the push.
SEAM_TOLERANCE_MS = 120
#: Zero-length words at a line start that make it a pile (one can be a blink).
MIN_PILE_WORDS = 2


def leading_zero_words(words: list[dict[str, Any]]) -> int:
    count = 0
    for word in words:
        if word["end_ms"] - word["start_ms"] > 0:
            break
        count += 1
    return count


def seam_piles(lines: list[dict[str, Any]]) -> list[int]:
    """Indices of the lines that open with a seam pile."""
    piles = []
    for i in range(1, len(lines)):
        words = lines[i].get("words") or []
        previous = lines[i - 1].get("words") or []
        if not words or not previous:
            continue
        if leading_zero_words(words) < MIN_PILE_WORDS:
            continue
        if previous[-1]["start_ms"] >= words[0]["start_ms"] - SEAM_TOLERANCE_MS:
            piles.append(i)
    return piles


def main(path: str) -> None:
    docs = json.loads(Path(path).read_text())
    by_method: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_version: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    worst: Counter[str] = Counter()
    lines_total = piles_total = 0
    for doc in docs:
        lines = doc.get("lines") or []
        piles = seam_piles(lines)
        method = "windowed" if "windowed" in (doc.get("method") or "") else "whole"
        minor = ".".join((doc.get("pv") or "?").split(".")[:2])
        lines_total += sum(1 for line in lines if line.get("words"))
        piles_total += len(piles)
        by_method[method][0] += 1
        by_method[method][1] += bool(piles)
        by_version[minor][0] += 1
        by_version[minor][1] += bool(piles)
        by_version[minor][2] += len(piles)
        if piles:
            worst[doc["id"]] = len(piles)
    hit = sum(1 for doc in docs if seam_piles(doc.get("lines") or []))
    print(f"{len(docs)} documents, {lines_total} worded lines")
    print(f"seam piles: {piles_total} lines in {hit} documents")
    for method, (n, affected) in sorted(by_method.items()):
        print(f"  {method:9} {affected}/{n} documents")
    for minor, (n, affected, count) in sorted(by_version.items()):
        print(f"  pipeline {minor:6} {affected}/{n} documents, {count} lines")
    print("worst:", ", ".join(f"{vid} ({n})" for vid, n in worst.most_common(8)))


if __name__ == "__main__":
    main(sys.argv[1])
