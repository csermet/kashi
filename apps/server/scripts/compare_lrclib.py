"""Compare a processed document's line starts against lrclib's synced times.

Acceptance tool for line-QA regressions (never ships in the image). Exit 1 when
any line inside the checked window deviates from its lrclib reference by more
than the threshold after median-offset correction.

Usage:
    uv run python scripts/compare_lrclib.py \
        --server-url https://kashi.example.com --api-key ksh_... \
        --source youtube:RxUZLmN5RsY --lrclib-id 12212948 \
        --window 29:65 --threshold-s 2.5
"""

import argparse
import os
import sys
from statistics import median

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "src"))
from kashi_server.pipeline.lrclib import _parse_synced  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", required=True)
    parser.add_argument(
        "--api-key",
        default=os.environ.get("KASHI_API_KEY"),
        help="defaults to $KASHI_API_KEY (keeps the key out of ps/history)",
    )
    parser.add_argument("--source", required=True, help="type:id, e.g. youtube:RxUZLmN5RsY")
    parser.add_argument("--lrclib-id", type=int, required=True)
    parser.add_argument("--window", default=None, help="start:end seconds, e.g. 29:65")
    parser.add_argument("--threshold-s", type=float, default=2.5)
    args = parser.parse_args()
    if not args.api_key:
        parser.error("--api-key or $KASHI_API_KEY is required")

    source_type, _, source_id = args.source.partition(":")
    doc = httpx.get(
        f"{args.server_url.rstrip('/')}/v1/lyrics/{source_type}/{source_id}",
        headers={"Authorization": f"Bearer {args.api_key}"},
        timeout=15,
    )
    doc.raise_for_status()
    lines = doc.json()["lines"]

    lr = httpx.get(
        f"https://lrclib.net/api/get/{args.lrclib_id}",
        headers={
            "User-Agent": "kashi-server-dev/compare_lrclib (+https://github.com/csermet/kashi)"
        },
        timeout=15,
    )
    lr.raise_for_status()
    synced = _parse_synced(lr.json().get("syncedLyrics") or "")
    if not synced:
        print("lrclib record has no synced lyrics — nothing to compare against")
        return 2

    # Cursor-match by text (same rule as line_qa) so dropped/repeated lines align.
    refs: list[int | None] = []
    cursor = 0
    texts = [text for _, text in synced]
    starts = [start for start, _ in synced]
    for line in lines:
        while cursor < len(texts) and texts[cursor] != line["text"]:
            cursor += 1
        refs.append(starts[cursor] if cursor < len(texts) else None)
        cursor += 1

    deviations = [
        line["start_ms"] - ref for line, ref in zip(lines, refs, strict=True) if ref is not None
    ]
    offset = median(deviations) if deviations else 0.0

    window = None
    if args.window:
        lo, _, hi = args.window.partition(":")
        window = (float(lo) * 1000, float(hi) * 1000)

    failures = 0
    print(f"{'ln':>3} {'aligner':>8} {'lrclib':>8} {'dev':>7} {'dev-med':>8}  text")
    for i, (line, ref) in enumerate(zip(lines, refs, strict=True)):
        if ref is None:
            continue
        dev = line["start_ms"] - ref
        corrected = dev - offset
        in_window = window is None or window[0] <= ref <= window[1]
        bad = in_window and abs(corrected) > args.threshold_s * 1000
        failures += bad
        flag = " <<< FAIL" if bad else ""
        print(
            f"{i:>3} {line['start_ms'] / 1000:>8.2f} {ref / 1000:>8.2f} "
            f"{dev / 1000:>+7.1f} {corrected / 1000:>+8.1f}  {line['text'][:40]}{flag}"
        )

    print(f"\nmedian offset {offset / 1000:+.2f}s, {failures} line(s) over threshold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
