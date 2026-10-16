# LyricFix — lyrics lookup that survives an unavailable lrclib (2026-10)

**Status: DECIDED (v2, 2026-10-16) — in progress. §1-§3 are the v1 analysis; §4 records the
v2 decision that supersedes the v1 package order.**

Field complaint (Caner, recurring): when lrclib answers 503, the overlay shows
"Lyrics unavailable (network)" and never asks again for the rest of the song. On
2026-10-16 lrclib failed for roughly half an hour, including from a plain `curl`
at home, so at least part of it was a real outage. The ask: keep looking in the
background while the song plays, find out whether we are being rate-limited, and
build a lasting method instead of another retry tweak.

## 1. What actually happened (measured)

### Our own request volume is tiny

Both workers' logs, 2026-10-13 05:00 → 2026-10-16 08:45 UTC (≈ 3.5 days, httpx
request lines):

| Endpoint | 200 | 404 | 429 | 503 |
|---|---|---|---|---|
| `/api/get` | 17 | 7 | 6 | 6 |
| `/api/search` | 18 | — | — | 3 |

57 requests in total, **at most 16 in any hour**. A job costs 1–5 lrclib requests
and a worker finishes one job every ~10–12 minutes.

The failures cluster in two windows:

- **2026-10-15 11:30–12:19 UTC — 429s** at about six requests per hour, with 200s
  interleaved. Two jobs ended `failed (rate_limited)`.
- **2026-10-16 08:09–08:30 UTC — 503s**, again interleaved with 200s. The `/api/get`
  → 200 then `/api/search` → 503 pattern repeats: search is the expensive query and
  goes first when the server is short on capacity.

The overlay's share could not be measured from the server in this round (the
telemetry read was not run). It is bounded from the code instead: the overlay
calls lrclib only when the server has no document for the track, keeps every
positive answer forever and every miss for 7 days, and during an outage stops at
the first failing request of an attempt. That is three requests per track at
most. Also, the "lyrics ERROR" outcome emits **no telemetry at all** (the
ladder's final `send({error:true})` has no `emit`), so its frequency is
invisible today. Package 1 fixes that.

### The 429s are lrclib's, not ours

- lrclib's documented per-client budget (maintainer, issue #84, 2026-02) is about
  **20 requests/second**, enforced at the network edge. We send about 16 per *hour*.
- **lrclib issue #130, opened 2026-10-15**: "Intermittent 429s on `/api/get` at
  very low request rates". One reporter sends 2 requests per minute, another an uptime
  probe every 5 minutes. Both see 429s interleaved with successes from the
  same day as ours. The maintainer replied on 2026-10-16 that he is *tuning the
  rate-limiting configuration right now*, and that the limit "could be applied to
  proxied Cloudflare IPs instead of clients' real IPs". That would make it a
  bucket shared with strangers.
- The 503s are the documented `ServerOverloaded` answer ("temporarily busy; retry
  after the `Retry-After` header, usually 1 second").

So the hypothesis "our server waves and the overlay share one home IP and get
throttled together" is **rejected**. Our volume is three to four orders of magnitude
below the limit, and the throttling hit other clients the same day. lrclib is a free,
single-maintainer service that is being re-tuned under load. Outages and spurious
429s will recur, and we cannot fix that. What we can fix is how much each one costs us.

### lrclib's client rules (from the API docs, verbatim points)

- Identify the client in `User-Agent` (we do).
- On 429 the client **must** honour `Retry-After`; ignoring it "may result in a
  temporary ban". The 429 body is not guaranteed to be JSON (edge-generated).
- Send requests sequentially with a 200–500 ms gap during batch work.
- New and optional: register an application and send `Lrclib-App-Id` (not enforced
  yet). Registering may help if per-application limits arrive later.
- A missing track "can be picked up by the background lyrics fetching service and
  may become available in a later request". Our 7-day negative cache fits that.

### What it costs us today

| Where | Behaviour during an lrclib outage | Cost |
|---|---|---|
| Overlay (`lookup-orchestrator.ts`) | 3 attempts at 0 / 2 / 6 s, then `error: true` for the rest of the song. 429 and 503 are treated like a timeout, and `Retry-After` is never read. | The whole song has no lyrics, even if lrclib recovers 10 s later. |
| Worker (`worker/process.py`) | Downloads the audio **before** the lyrics lookup. `max_attempts = 3`, so only the 60 s and 300 s delays are ever used. **The configured 900 s third delay is dead.** | A ~30-minute outage fails every job that reaches lrclib, after up to three YouTube downloads each (bot-detection exposure). Network and rate-limit failures are transient, so they are *not* 7-day blocked, but they need a fresh enqueue. |
| Reprocess waves | No awareness of lrclib health. | Waves had to be paused by hand on 2026-10-16. |

## 2. Options

| | Option | For | Against | Cost | Verdict |
|---|---|---|---|---|---|
| a | **Overlay: keep retrying while the track plays** (back-off that honours `Retry-After`, "searching / retrying" status instead of the error, lyrics appear when lrclib returns) | Fixes the complaint directly. Overlay-only. The `kashi:lyrics` payload is main→renderer internal, so no schema change. Works in serverless mode too. | Needs a shared cooldown so a skip chain during an outage does not multiply requests. | ~½ day incl. tests, plus a Windows/Mac rebuild | **DO (P1)** |
| b | **Server as lrclib cache/proxy for the overlay** | One egress point, shared cache. | The overlay reaches lrclib only for tracks the server has **no** document for, and those are exactly the tracks the server never looked up. With one or two listeners plus the overlay's permanent cache, the hit rate is near zero. It also adds a server dependency to the serverless path. | ~1 day | **NO (for now)** — revisit only with many listeners. |
| c | **Own lrclib mirror** (MIT server + published SQLite dump; "mirror first, lrclib.net on miss") | The pipeline becomes independent of lrclib's uptime and rate limits. Same matching code, so the same answers. | The latest dump (2026-10-12) is **47.8 GB gzipped**, about 81 GB unpacked in Jan 2026 and growing, so plan ~150 GB peak. No resumable download (issue #44). Dumps are irregular and only the latest is listed. A dump can be newer than the public server code (issues #106/#107, `DatabaseTooFarAhead`). On replicated block storage it multiplies by the replica count, so it belongs on a single node's local disk (rebuildable from the dump). A mirror miss on a fresh release still needs lrclib.net, and the miss must not be negative-cached as a real "not found". No explicit data licence on the dumps page; treat it like the API (private use by our own pipeline, no redistribution). | 1–2 days + ~150 GB disk + refresh in each maintenance window | **DEFER — decision gate after P2 metrics** |
| d | **Worker: circuit breaker, defer without downloading** | Stops the YouTube downloads wasted during an outage. Waves gate themselves. Deferral does not burn the 3-attempt budget. | Needs DB-backed tests. The breaker is per worker process (each worker spends a request or two learning). | ~½–1 day | **DO (P2)** |
| e | **Search-ladder gaps** (artist mismatch, release-length mismatch) | Recovers records that lrclib *has*: "Got 2 Luv U" is filed under the wrong artist, "Get Jinxed" is 143 s vs our 155 s. | Small reach: of 68 `lyrics_not_found`, ~45 are fan edits with no lyrics anywhere (YTM has none for 66 of 68). Every rung is also a false-match risk and must be built twice (server + overlay twin). | ~½ day + measurement | **DO LATER (P4)**, separate from availability |

Why "lyrics before download" is not proposed: nightcore detection, the
client-edit gate, the Lyricsfile fast path and the by-ear rung all read the
downloaded audio's duration. Moving the lookup ahead of the download would change
all four. The breaker gets the same saving (no download while lrclib is down)
without touching the pipeline order.

## 3. Plan (packages, each with acceptance and proof)

### P1 — Overlay: patient lookup (fixes the complaint)

- `lrclib.ts`: failures carry a type (`busy` for 429/503 with the parsed
  `Retry-After`, `network` for timeout/DNS, `bad-request` for 400/422), not a
  bare `Error`.
- `lookup-orchestrator.ts`: after the quick tries, keep retrying **while the track
  is current**, on a widening schedule (e.g. 15 s → 30 s → 60 s → 120 s, capped)
  that never undercuts `Retry-After`. The state is process-wide: while lrclib is
  "busy", a new track waits for the shared cooldown and does not fire its own burst.
  When an answer arrives mid-song, it is shown, unless the server self-heal already
  put up a document (R-8 stays).
- Renderer: "Searching lyrics… (lrclib busy, retrying)" while retrying, and the
  error text only if retrying is impossible.
- Telemetry: `lyrics_outcome {source:'error'}` and `{recovered:true, after_s}`.
- Test seam: a dev-only override for the lrclib base URL (the option exists in
  `LrclibClient`, but nothing sets it today).

**Accept:** with a fake lrclib that answers 503 for N minutes and then 200, lyrics
appear on the same track with no track change. No request goes out before
`Retry-After`. Requests per minute stay bounded during an outage, even across a
skip chain. A track change stops the old retries. Each guard is proven by mutation.
Then one packaged-overlay E2E run against the fake lrclib (outage toggled live).

### P2 — Worker: lrclib circuit breaker + metrics

- The breaker opens after consecutive 429/503/network failures from lrclib and
  stays open until the cooldown ends (honouring `Retry-After`). It then half-opens
  with one probe.
- While it is open, a claimed job is **deferred**: re-queued with a later
  `next_attempt_at`, **no download, attempt not consumed**. A cheap pre-download
  lrclib check per job feeds the breaker.
- Fix the dead third delay, either `max_attempts` 4 or a two-entry ladder. Decide
  in review.
- Metrics: `kashi_lrclib_requests_total{endpoint,code}` and
  `kashi_lrclib_breaker_open`, plus one Grafana panel. Warning-level only, never a
  phone alert.

**Accept** (DB-backed tests on a throwaway PG 17): during a fake outage, the
download seam is called **0 times**, `attempts` is unchanged and jobs carry a future
`next_attempt_at`. After recovery they finish normally. Each guard is proven by
mutation. Live check: the next wave's first finished document plus the metric shows
the codes.

### P3 — Decision gate for the mirror (option c)

After about four weeks of P1 + P2 data, decide on a mirror only if lrclib
unavailability still costs us noticeably, e.g. breaker open for more than about 2
hours per week, or recurring user-visible retries over 5 minutes. A prerequisite check
before any GO is free local disk on the worker's node for the ~150 GB peak.

### P4 — Search-ladder gaps (separate, smaller)

- Title-only rung (artist dropped) with strict plausibility: title covers ±3 s on
  the overlay, and alignment-gated text-only use on the server.
- A server-only rule for a release-length mismatch: use the record's **text**
  (alignment sets the clock; the wrong-song gate stays), never its stamps. The
  overlay must not do this: its clock *is* the record's stamps.
- Bump `LOOKUP_LADDER_VERSION` and fix both twins.

**Accept:** "Got 2 Luv U" and "Get Jinxed" resolve. A re-run over the 68
`lyrics_not_found` documents lists every newly matched record for a manual
false-match check.

## 4. v2 decision (2026-10-16) — supersedes the v1 package order

The owner split the problem in two, and the split changes the priorities:

1. **Live listening is the complaint.** A first-listen song must show plain synced lyrics
   within the first 10-20 s. Effects are not expected there. This needs a redundant
   source, not a better retry.
2. **Processing has no hurry.** Retrying lrclib on and off for a day is acceptable; what
   is not acceptable is burning YouTube downloads while lrclib is down.

### Corrections to §2

- **Dump size:** the 2026-10-12 dump unpacks to ~262 GB (SQLite header: 4096-byte pages
  × 63.9 M pages), not ~81 GB. The growth is the per-row `lyricsfile` word-timing YAML
  added in June 2026. We only need rows with synced lyrics, so the mirror runs on a
  **trimmed copy** (`tools/lrclib-mirror/trim.sql`; size measured in P0).
- **Dump/server compatibility:** the dump carries `user_version = 8`; the public server
  must be built from main at `04369d3` (2026-10-15) or later. There is no published
  container image; we build one from a pinned commit. The self-hosted server does **no**
  external fetching (removed 2026-08-07), which keeps the chain licence-clean. It writes
  to the database (WAL, migrations, daily `PRAGMA optimize`) and builds a Tantivy search
  index beside it on first start.
- **Resumable download:** `db-dumps.lrclib.net` now honours HTTP ranges (`curl -C -`).

### Decision

- **Own mirror: GO**, as the primary source for the overlay (LAN, short timeout) and the
  fallback for the worker. lrclib.net stays the worker's primary (freshest data,
  `lyricsfile`) and the overlay's fallback. Contribute-back publishing stays on lrclib.net
  through its own setting.
- **Placement:** a large, non-critical, rebuildable dataset belongs on cheap local disk,
  not replicated block storage. The chosen disk is NTFS mounted through FUSE, which is a
  poor host for SQLite WAL, mmap and a Tantivy index, so the database lives inside an
  **ext4 image file on that disk, loop-mounted**. P0 benchmarks that path against NVMe
  with the same database; the gate is `/api/get` p95 < 300 ms with no I/O errors.
- **Overlay:** mirror → lrclib.net chain, queried **in parallel** with the server; the
  first answer is shown and a server document always replaces lrclib lines. Failures are
  typed (busy with `Retry-After`, network, bad request) and retried in the background
  while the track plays, through a process-wide cooldown.
- **Worker:** lrclib.net → mirror → **defer without downloading** (attempt refunded) on a
  widening schedule inside a 24 h patience window; a mirror miss during a lrclib.net
  outage is transient, never `lyrics_not_found`.
- **Declined:** YTM's own lyrics (Musixmatch/LyricFind-licensed; conflicts with the
  licence-clean chain and the public repo), NetEase/Musixmatch/Apple/Tidal scrapers
  (terms of service), server-as-proxy (near-zero hit rate, see §2 b).

### Packages

P0 measure (dump, trim, image build, bench 3 TB vs NVMe, parity) ‖ P1 overlay →
P2 mirror deployment (private IaC; LAN-only, never public) → P3 worker → P4 search-ladder
gaps. Acceptance and proof per package are in the owner's plan file; results land here.

### P1 result (overlay 0.29.0, 2026-10-16)

Shipped as described above. Proof: 21 guard mutations, each killed by its test; and an
E2E on the real overlay (headless, fake lrclib.net and fake mirror). With lrclib.net
answering 503 + `Retry-After: 2`, the overlay asked at 0 / 2 / 8 / 23 s and announced
"still trying". When lrclib came back it filled the screen on the same track with no
track change. With lrclib.net still down, a mirror-held track showed lyrics about 0.5 s
after the track change, with zero lrclib.net requests. The review round moved each
source's cooldown between its disk cache and the network, so a cache answer neither
blocks nor clears a server-named wait.

### P0 results (mirror, 2026-10-16)

| Step | Measured |
|---|---|
| Download | 47.8 GB gzip, resumed with HTTP ranges; ~15 min at home bandwidth |
| Unpack | 261.76 GB (header-exact), `user_version = 8`; ~80 min onto a spinning NTFS disk (~55 MB/s, the bottleneck) |
| Trim (`tools/lrclib-mirror/trim.py`) | 33.7 M tracks → **21.5 M** whose latest lyrics are synced; **106.8 GB** (41 %); ~2 h 20 min, mostly the sequential read of the source |
| Stock server (main @ `04369d3`) on the trimmed copy | starts in ~1 s; Tantivy search index builds in ~3.5 min, 1.4 GB; RSS ~2.2 GB |
| `/api/get`, NVMe, 300 random tracks | 300/300 found; p50 1 ms, p95 2 ms (cold first pass: p95 9 ms) |
| `/api/search`, NVMe, 100 tracks | 100/100; p50 27 ms, p95 84 ms |
| Parity with lrclib.net | 29 comparable records, **29 same id, 0 different**. lrclib.net itself answered 503 to 13 of 30 sequential requests (2/s) during the check. |

Next time, stage the dump on an ext4 SSD rather than the NTFS disk. The read speed
of the staging disk sets the pace of both the unpack and the trim. Plain lyrics are kept
on the synced rows. Dropping them would roughly halve the copy, but the server pipeline
reads them, and size is not the constraint.

## Sources

- lrclib API documentation, "Implementation Requirements" / "Rate Limiting" /
  error table (lrclib.net/docs, read 2026-10-16).
- lrclib issues #130 (2026-10-15/16, intermittent 429s, maintainer tuning),
  #84/#85 (2026-01/02, nginx rate limit, ~20 req/s, dump size), #44 (no
  resumable dump download), #106/#107 (dump newer than public server code).
- Dump listing behind lrclib.net/db-dumps (2026-10-16): one file,
  `lrclib-db-dump-20261012T090639Z.sqlite3.gz`, 47.8 GB.
- lrclib server licence: MIT (GitHub API).
- Worker logs of both workers, 2026-10-13 → 2026-10-16 (counts above).
