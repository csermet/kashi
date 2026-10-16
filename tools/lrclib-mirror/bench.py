#!/usr/bin/env python3
"""Latency and parity bench for an lrclib mirror (stdlib only).

    python3 -I bench.py --db TRIMMED.sqlite3 --mirror http://HOST:3300 \
        [--n 300] [--parity 30] [--search 30] [--seed 7]

Samples N random tracks from the trimmed database and asks the mirror's
`/api/get` for each, the way a client does it (track, artist, album, rounded
duration). Prints status counts and latency p50/p95/max. Then:

  --search K   K `/api/search` calls (track + artist) — the Tantivy path.
  --parity M   asks lrclib.net the same `/api/get` for M of the samples,
               sequentially with a 500 ms gap (lrclib's batch etiquette), and
               reports how many name the same record id. A mismatch is not
               necessarily a bug — lrclib.net may hold a newer record than the
               dump — but a low rate is.

The database is opened read-only; it may be the live mirror's file.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "kashi-mirror-bench (+https://github.com/csermet/kashi)"


def sample(db: str, n: int, seed: int) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lo, hi = con.execute("SELECT min(id), max(id) FROM tracks").fetchone()
    rng = random.Random(seed)
    out: list[dict] = []
    seen: set[int] = set()
    while len(out) < n:
        row = con.execute(
            "SELECT id, name, artist_name, album_name, duration FROM tracks WHERE id >= ? "
            "ORDER BY id LIMIT 1",
            (rng.randint(lo, hi),),
        ).fetchone()
        if row is None or row[0] in seen or not row[1] or not row[2]:
            continue
        seen.add(row[0])
        out.append(
            {"id": row[0], "track_name": row[1], "artist_name": row[2],
             "album_name": row[3] or "", "duration": round(row[4] or 0)}
        )
    return out


def get(base: str, path: str, params: dict, timeout: float = 15.0) -> tuple[int, float, object]:
    url = f"{base.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read() or b"null")
            return resp.status, (time.perf_counter() - t) * 1000, body
    except urllib.error.HTTPError as err:
        return err.code, (time.perf_counter() - t) * 1000, None
    except (urllib.error.URLError, TimeoutError) as err:
        return 0, (time.perf_counter() - t) * 1000, str(err)


def summary(label: str, results: list[tuple[int, float, object]]) -> None:
    codes: dict[int, int] = {}
    for code, _, _ in results:
        codes[code] = codes.get(code, 0) + 1
    ms = sorted(r[1] for r in results)
    p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
    print(
        f"{label}: n={len(ms)} codes={codes} p50={statistics.median(ms):.0f} ms "
        f"p95={p95:.0f} ms max={ms[-1]:.0f} ms"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--search", type=int, default=0)
    ap.add_argument("--parity", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    tracks = sample(args.db, args.n, args.seed)
    gets = [get(args.mirror, "/api/get", {k: v for k, v in t.items() if k != "id"}) for t in tracks]
    summary("mirror /api/get", gets)
    wrong = sum(1 for t, (code, _, body) in zip(tracks, gets)
                if code == 200 and isinstance(body, dict) and body.get("id") is None)
    if wrong:
        print(f"  {wrong} 200 answers without a record id")

    if args.search:
        searches = [get(args.mirror, "/api/search",
                        {"track_name": t["track_name"], "artist_name": t["artist_name"]})
                    for t in tracks[: args.search]]
        summary("mirror /api/search", searches)

    if args.parity:
        same = differ = failed = 0
        ref_codes: dict[int, int] = {}
        for t, (code, _, body) in list(zip(tracks, gets))[: args.parity]:
            ref_code, _, ref = get("https://lrclib.net", "/api/get",
                                   {k: v for k, v in t.items() if k != "id"})
            time.sleep(0.5)
            ref_codes[ref_code] = ref_codes.get(ref_code, 0) + 1
            if ref_code != 200 or code != 200:
                failed += 1
                continue
            mirror_id = body.get("id") if isinstance(body, dict) else None
            ref_id = ref.get("id") if isinstance(ref, dict) else None
            if mirror_id == ref_id:
                same += 1
            else:
                differ += 1
                print(f"  differs: {t['artist_name']} - {t['track_name']}: mirror {mirror_id} vs lrclib.net {ref_id}")
        print(f"parity vs lrclib.net: same={same} differ={differ} not-comparable={failed} "
              f"(lrclib.net codes {ref_codes})")


if __name__ == "__main__":
    main()
