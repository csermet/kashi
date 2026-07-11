"""Compare a processed document's line starts against lrclib's synced times.

Acceptance tool for line-QA regressions (never ships in the image). Exit 1 when
any line inside the checked window deviates from its lrclib reference by more
than the threshold after median-offset correction. Thin shell since P1: the
matching and statistics live in benchmarks.metrics.

Usage (live):
    uv run python scripts/compare_lrclib.py \
        --server-url https://kashi.example.com --api-key ksh_... \
        --source youtube:RxUZLmN5RsY --lrclib-id 12212948 \
        --window 29:65 --threshold-s 2.5

Usage (offline/hermetic — e.g. a saved document against a saved .lrc):
    uv run python scripts/compare_lrclib.py --doc-json doc.json --lrc-file ref.lrc
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parents[1]))
from benchmarks.metrics import format_line_report, line_start_report  # noqa: E402
from kashi_server.pipeline.lrclib import _parse_synced  # noqa: E402


def _fetch_document(args: argparse.Namespace) -> dict:
    if args.doc_json:
        return json.loads(Path(args.doc_json).read_text())
    response = httpx.get(
        f"{args.server_url.rstrip('/')}/v1/lyrics/{args.source_type}/{args.source_id}",
        headers={"Authorization": f"Bearer {args.api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _fetch_synced(args: argparse.Namespace) -> str:
    if args.lrc_file:
        return Path(args.lrc_file).read_text()
    response = httpx.get(
        f"https://lrclib.net/api/get/{args.lrclib_id}",
        headers={
            "User-Agent": "kashi-server-dev/compare_lrclib (+https://github.com/csermet/kashi)"
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("syncedLyrics") or ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("KASHI_API_KEY"),
        help="defaults to $KASHI_API_KEY (keeps the key out of ps/history)",
    )
    parser.add_argument("--source", help="type:id, e.g. youtube:RxUZLmN5RsY")
    parser.add_argument("--doc-json", help="read the processed document from a file instead")
    parser.add_argument("--lrclib-id", type=int)
    parser.add_argument("--lrc-file", help="read the synced .lrc text from a file instead")
    parser.add_argument("--window", default=None, help="start:end seconds, e.g. 29:65")
    parser.add_argument("--threshold-s", type=float, default=2.5)
    args = parser.parse_args()

    if args.doc_json:
        args.source_type = args.source_id = None
    elif args.server_url and args.source:
        if not args.api_key:
            parser.error("--api-key or $KASHI_API_KEY is required for server fetches")
        args.source_type, _, args.source_id = args.source.partition(":")
    else:
        parser.error("either --doc-json or --server-url + --source is required")
    if not args.lrc_file and args.lrclib_id is None:
        parser.error("either --lrc-file or --lrclib-id is required")

    lines = _fetch_document(args)["lines"]
    synced = _parse_synced(_fetch_synced(args))
    if not synced:
        print("lrclib record has no synced lyrics — nothing to compare against")
        return 2

    window = None
    if args.window:
        lo, _, hi = args.window.partition(":")
        window = (float(lo) * 1000, float(hi) * 1000)

    report = line_start_report(
        [(line["start_ms"], line["text"]) for line in lines],
        synced,
        threshold_ms=args.threshold_s * 1000,
        window_ms=window,
    )
    print(format_line_report(report, threshold_ms=args.threshold_s * 1000))
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
