# LyricFix — lyrics lookup that survives an unavailable lrclib (2026-10)

**Status: DECIDED (v2, 2026-10-16); round 2 (2026-10-22) shipped the worker and the record gate;
round 3 (2026-10-26) moved the plain lookup before the download.
§1-§3 are the v1 analysis; §4 records the v2 decision that supersedes the v1 package order;
§5 is round 2 (mirror memory, worker deferral, degenerate records, and two corrections to §2);
§6 is round 3 (deferrals after the download, the breaker's window, next-track prefetch, the
lrclib app id, the mirror's memory limit, upstream notes).**

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

**Placement gate: passed on the spinning disk.** The same copy, inside an ext4 image
file on the large NTFS data disk (loop-mounted), cold random samples:

| Endpoint | p50 | p95 | max |
|---|---|---|---|
| `/api/get` (600 tracks, 599 found; one malformed bench query got 400) | 47–53 ms | 69–75 ms | 264 ms |
| `/api/search` (200 tracks) | 323–361 ms | 850–861 ms | 991 ms |

Both pass the gate (`/api/get` p95 < 300 ms), and the copy stays on the spinning disk. Moving
the 1.4 GB search index to SSD did not help search (p95 800 ms): the time goes to
loading candidate records from the spinning disk, not to the index. Search only runs
when the exact `/api/get` misses, and the overlay's mirror timeout (1.5 s) covers it.

**Operational lesson (cost us evictions).** The trim's final `VACUUM` writes a second
full copy next to the first. Run on a Kubernetes node's root disk, it pushed that disk
to 86 % and the kubelet evicted pods for ~15 minutes until the vacuum finished. Never
stage or trim on a node root disk; use the data disk (or a machine outside the cluster),
and keep any disk the kubelet watches under 80 %.

### P2 result (mirror deployed, 2026-10-16)

The stock lrclib server is built in the homelab CI from the pinned upstream commit (image scan:
0 known vulnerabilities). It runs as a single-replica StatefulSet, because SQLite allows one
writer. The data volume is a static host-path volume on the data disk. A LAN-only load-balancer
address serves it, and a network policy admits only the home subnet, the Kashi worker and the
uptime probe; the mirror itself has no egress at all. It is never published through the public
gateway. Ready about 100 s after sync. Over the LAN: `/api/get` p95 47 ms (200 tracks), search
p95 ~1.3 s on a small cold sample. A search that exceeds the overlay's 1.5 s mirror timeout simply
falls through to lrclib.net. The uptime probe is green; its alert is warning-only, since clients
fall back to lrclib.net on their own.

Next time, stage the dump on an ext4 SSD rather than the NTFS disk. The read speed
of the staging disk sets the pace of both the unpack and the trim. Plain lyrics are kept
on the synced rows. Dropping them would roughly halve the copy, but the server pipeline
reads them, and size is not the constraint.

## 5. Round 2 (2026-10-22)

Round 1 left P3 (worker) and P4 (search-ladder gaps). Re-measured against the live mirror
first, which changed both.

### Findings (read-only measurements)

1. **The mirror was OOM-looping.** Killed at its 4 GiB limit about every 80 minutes since it
   went live (88 times in six days), with nothing but uptime probes reaching it (5 requests a
   minute). Resident memory grew from ~290 MiB in ~285 MiB steps, about every 10 minutes, all of
   it anonymous heap (`smaps`: 3.2 GB anonymous vs 0.01 GB file-backed one hour in). Each restart
   meant a cold cache, which is the "mirror answered in 5 s" seen on 2026-10-18. Upstream
   defaults: a pool of 16 SQLite connections, each with `PRAGMA cache_size` 256 MiB, which caps
   at exactly 4 GiB.
2. **P4's two "known gaps" were not gaps.** Against the mirror, the server ladder resolves "Got 2
   Luv U" (155 records) and "Get Jinxed" (38 records) for every plausible hint variant. Both had
   failed on lrclib's 503 day. §2 e's "filed under the wrong artist" and "143 s vs 155 s" were
   wrong.
3. **A title-only rung is dangerous.** On 3 000 random mirror tracks, a genuinely different
   artist has the same title within ±3 s for 46.4 % of them, and exactly one such stranger
   (what a "unique match" rule would silently accept) for 14.5 %; with no duration, 65.8 %. The
   server's r=1 path has no wrong-song gate, and the CTC score is saturated at 1.00 for 51 % of
   the archive, so alignment would not catch a wrong text either. Rejected (olu-fikirler.md).
4. **`lyrics_not_found` inventory** (120 days, 143 distinct tracks): 68 resolve today (they failed
   on outage days or with absurd hint durations) and 75 are genuinely absent (about 60 of them
   AMV, nightcore, remix, cover or bootleg uploads). Nothing to build; the 68 go into the next
   re-queue wave.
5. **A cold mirror search is slow**: `/api/search` takes 4-8 s on the first touch of a popular
   title (hundreds of records read off the spinning disk), milliseconds warm. `/api/get` is
   milliseconds either way.
6. **lrclib records rot.** Record 1009665 ("Never Gonna Give You Up") is now the single line
   "probe", in the dump too, and a one-line "day day day" record replaced "Party Rock Anthem" at
   quality 1.0 in the 2026-10-16 wave. Neither the server nor the overlay checked for minimal
   content. The archive (339 documents): stamped lines p2 = 20; words per minute p2 = 22.7,
   p5 = 37.4.

### P2.1 — mirror memory hotfix

A control run of the same image on a three-record database, same environment and the same
probe traffic, stayed flat for 34 minutes (16 MiB) with the default pool of 16 **and** with a
pool of 1, while the live pod grew from 292 to 3 156 MiB over the same window. The growth is
data-dependent: per-connection page caches filling from the 107 GB database, not connection
recycling or the allocator on their own. Fix: pool 2 (two clients at most: overlay and worker),
32 MiB cache per connection, mmap unchanged, more headroom while it is measured. Acceptance:
resident-memory slope ≤ 50 MiB/h over 2 h and no restart for 24 h.

**Result:** after the change the pod opened at 42 MiB (the old one opened at 292 MiB and added
~285 MiB at the tenth minute). The slope was +0.2 MiB/h over the first 2 h and +0.1 MiB/h over
the following 6 h. **Accepted after 24 h** (2026-10-23 17:57Z): no restart, resident memory flat
(24 h slope −0.01 MiB/h, peak 43.8 MiB). One uptime-probe sample of 1 440 failed, at exactly
24 h after start: upstream runs its daily `PRAGMA optimize` synchronously inside the async
runtime, which stalled the server for ~30 s on the 107 GB database (its once-a-minute metrics
line came 30 s late). That costs one ~30 s fall-through to lrclib.net a day. It was invisible
before only because the pod never lived a day.

### P3 — worker: lrclib.net → mirror → defer (server 0.31.0)

- One gateway for every lrclib call in the worker. A transient lrclib.net failure (429, 5xx,
  403, timeout) asks the mirror. A mirror miss while lrclib.net is unavailable is a new
  transient type, `lrclib_unavailable`, never `lyrics_not_found`: the dump lags lrclib.net, and
  a permanent miss would take the 7-day block. A clean lrclib.net 404 never asks the mirror.
- A breaker opens after two consecutive lrclib.net failures, for max(60 s × 2^(n−1) capped at
  15 min, Retry-After); then one probe. A single failure carrying Retry-After pauses lrclib.net
  for that long without opening, so Retry-After is honoured on every path.
- While lrclib.net is off limits, a plain job whose lyrics the mirror cannot supply is
  **deferred before the download**: re-queued on a 10 min → 4 h schedule with its attempt
  refunded, for up to 24 h, then failed as transient `network`. Nightcore-marked jobs keep the
  old order (their lookup needs the audio's duration); they are rare.
- 250 ms between lrclib.net requests; `Retry-After` on every busy error; request counts by
  target, endpoint and status (timeouts included). The dead third retry delay is gone
  (`[60, 300]`).
- Proof: 10 DB-backed worker tests on a throwaway Postgres 17 (deferral with zero download
  calls, attempt refunded, the widening schedule, the patience boundary exactly on the
  second, caller lyrics and nightcore paths) plus 21 gateway/breaker tests. 18 guard mutations,
  each killed by a test: the 2-failure threshold, `max(backoff, Retry-After)`, the Retry-After
  pause, the busy-type check, `lrclib_unavailable` on a mirror miss (fetch and search), the
  shared request pacer, the digits-only Retry-After parse, the attempt refund, the
  pre-download check, the patience `<`, the defer branch, the schedule index, the cleared
  error note, the caller-lyrics exemption and the transient type. Publishing still targets
  lrclib.net only (contract test).
- **Field, first minutes after the rollout (2026-10-22 19:03Z), during a live lrclib.net 503
  spell:** the first job's original-song search failed on lrclib.net and missed on the mirror;
  it was deferred 600 s with its attempt refunded (`attempts` 0, `deferrals` 1). The breaker
  opened on the second failure. The next job took its lyrics from the mirror (42 synced lines,
  multi-artist rung) and finished as a normal word-sync document (quality 0.76): the first
  document a lrclib outage did not cost. lrclib.net answered from the same pod a minute later,
  so the 503s were lrclib's weather, not our egress.
- **First 9.5 hours** (2026-10-22 19:03Z → 10-23 04:30Z, lrclib.net still flapping): 47
  documents completed; their lyrics came 38 times from the mirror and 15 times from lrclib.net.
  The breaker opened 23 times. 12 deferrals, 2 of them before the download; nothing reached the
  24 h patience limit. Of the 17 jobs that had failed on lrclib 503s earlier that day and were
  re-queued, 15 completed, one turned out genuinely absent (`lyrics_not_found` once lrclib.net
  answered, after two deferrals) and one failed in alignment for an unrelated reason. The late
  deferrals are the design's known edge: with 503s interleaved with 200s, each success closes the
  breaker, so the next job downloads before it meets the next failure.
- The request counter's `endpoint` label arrived in Prometheus as `exported_endpoint`: the
  Prometheus Operator adds its own `endpoint` target label. Renamed to `route` in 0.31.1.

### P4 — record acceptance (pipeline 2.27.2, overlay 0.29.1)

The title-only rung and the "release-length mismatch → text only" rule were dropped (findings
2 and 3). In their place, the record gate from finding 6, on both twins: a synced record with
fewer than 4 stamped lines, or under 8 words per minute of its own duration (records of a minute
or more), is unusable like an instrumental one, and the ladder moves on. On the server a miss
caused only by rejection stays an ordinary `lyrics_not_found`. In the overlay such a miss is not
negative-cached, since lrclib may repair the record, and a rotted record already cached as a
hit is asked again. `LOOKUP_LADDER_VERSION` 3. The overlay's mirror timeout went from 1.5 s to
2.5 s (finding 5): `/api/get` is milliseconds, and a cold search beyond that still falls through
to lrclib.net.

## 6. Round 3 (2026-10-26)

### Deferrals were landing after the download (measured)

Worker logs, P3 rollout to 2026-10-26 (two pod lifetimes, ~90 hours):

| | count |
|---|---|
| lrclib.net answers 200 / 503 / 404 | 140 / 104 / 53 |
| deferrals (`lrclib_unavailable`) | 33 |
| of them after the YouTube download | **30** |
| downloads / repeat downloads of the same video | 198 / **45** |
| breaker opens (cooldowns 60-480 s) | 50 |
| worst single job | 10 deferrals, 10 downloads |

§5 blamed the breaker's rule ("each success closes it"). The rule was part of it: after a busy
answer the next answer was 200 about as often as 503, so "two failures in a row" rarely happened.
The bigger cause was cadence. One worker finishes a job every ~12 minutes, and every measured
cooldown had already run out when the next job was claimed. The pre-download check only ran while
the breaker was open, so it almost never ran: the job downloaded, then met the 503. Replaying the
second pod's log (16 deferrals) against a sliding-window rule alone caught 7 to 13 of them at best
(15-60 min windows), because a single probe meets a 503 only ~40 % of the time. 30 of the 33
deferrals were plain jobs (no nightcore marker; "slowed", "AMV" and "remix" are not markers); the 3
nightcore ones always come after the download, so round 3 can save at most 27 of the 30.

### Change (server 0.32.1)

- **Plain jobs run the whole lrclib ladder before the download** (composite-title fallback and
  caller lyrics included). Nothing in it reads the audio: the hints are the job's own plus
  `options.original_title`. A busy lrclib.net with a mirror miss now defers with nothing
  downloaded. The answer is kept and reused after the download, so the ladder still runs once per
  job; a `lyrics_not_found` is remembered, not raised, and is replayed into the by-ear rung, which
  needs the audio. Nightcore-marked jobs keep the download-first order (their lookup needs the
  audio's duration); in the measured window they were 3 of the 33 deferrals. The old open-breaker
  mirror check is gone (the gateway goes mirror-first by itself while the breaker is open).
- **The breaker counts busy answers in a 30-minute window**, not in a row. A success closes it but
  does not empty the window, so one more busy answer while earlier ones are still inside re-opens
  it at once, on the next backoff step; the backoff resets only on a success with a clean window.
  Window and threshold are settings (`lrclib_breaker_window_s`, `lrclib_breaker_failure_threshold`).
  A half-open breaker that nobody probes for a whole window closes by itself (one busy answer
  hours later is not a failed probe). Both settings are bounded (window > 0, threshold ≥ 1). New
  gauge `kashi_lrclib_window_failures` (refreshed only when the worker asks lrclib: it keeps its
  last value while the queue is empty, so do not alert on it); the breaker now logs when it closes
  and on each busy answer below the threshold. With the lookup ahead of the download, the breaker
  no longer decides whether a job downloads; it decides how soon lookups go mirror-first.
- A cancel that lands during the pre-download lookup wins over the deferral (checkpoint first).
- Side effects:
  - a job whose download fails permanently, or whose hinted duration fails the client-edit gate,
    now spends its 1-5 lrclib requests first; during an outage it defers instead of failing, and
    after the 24 h patience it fails as transient `network` rather than its real (blocking) error.
    A later listen may enqueue it again; the real error then blocks it. Rare, bounded;
  - a transient download failure re-runs the ladder on each of its (at most 3) attempts;
  - the `lyrics for ... via ...` log line now comes before the yt-dlp lines;
  - a mirror answer with the breaker open is one mirror request instead of two, but it is also
    final: the round-2 open-breaker path asked again after the download and could still reach
    lrclib.net's `lyricsfile` fast path. Mirror copies carry no `lyricsfile`, so such a job goes
    through CTC (as every mirror-sourced job already did in round 2).
- Remaining late deferrals: the by-ear rung's candidate search still runs after the download (only
  after a genuine `lyrics_not_found`), and so does every nightcore job's lookup.
- Latent, not changed here: a mirror-sourced document is filed as `lyrics_source="lrclib"`, and
  the publish gate only blocks `lyricsfile` documents, so publishing one could put a machine
  alignment on a record that carries human word timings on lrclib.net. No path reaches it today:
  the tray's "Report good sync" entry was removed on 2026-07-24 and the backend is dormant. Close
  it before that entry comes back.
- Proof: 25 new test functions, 5 adjusted, 2 removed with the dead mirror-only check (DB-backed
  worker tests on a throwaway Postgres 17), and 25 guard mutations, each killed: the pre-download
  call; reuse of the answer; the returned (not raised) miss, twice (returned as nothing, re-raised
  early); the replay inside the by-ear `try`; the plain-job gate; the `lrclib_unavailable`-only
  deferral; the `precheck` label; the full ladder instead of a bare fetch (composite title, caller
  lyrics); the commit after the deferral (a test was blind to it until it rolled back the session
  the way the worker loop does); the cancel checkpoint; the threshold `>=`; window eviction and its
  boundary; the window surviving a success; the backoff reset only on a clean window; the half-open
  lapse, its boundary and a too-early variant; both setting bounds; the close log; the busy-answer
  log; the settings wiring; and the gauge refresh.

### Next-track prefetch (overlay 0.30.0, extension 0.1.17)

The live complaint was a first listen with no lyrics for the first seconds. A song's lyrics can be
fetched while the previous one plays, if the client knows what comes next.

- **Signal.** YouTube Music's own queue store, `#queue`'s `queue.store.store.getState().queue`
  (items, automix items, selected index, autoplay, shuffle, repeat mode), the same signal
  pear-desktop and ytmdesktop read. It lives in the page's world, so the extension's MAIN-world
  bridge subscribes to it (re-read every 5 s as a safety net, debounced, posted only on change).
  Media Session has no "next track"; the queue DOM carries video ids only in element properties.
  The content script re-validates the post (at most two entries, video ids, bounded text and
  duration) and sends a new, additive protocol message `queue_changed` (no protocol version
  bump; an older overlay drops it with a log line). Predictions: the next items after the
  selected one, then automix while autoplay is on; repeat-all wraps; repeat-one and the end of
  the queue with autoplay off predict nothing. Shuffle is trusted: a live check (2026-10-26,
  shuffle on, 25 items) showed YTM writes the shuffled order into the queue (the playing track
  at index 0, the index moving 0 → 1 on the transition, and the predicted track the one that
  played).
- **Budget.** Only while the current track plays, past its first minute (half of a track shorter
  than two minutes), never during an ad. The next track gets the server document, then the
  mirror → lrclib.net chain once (plus the live ladder's duration-less second try on a miss, as
  the queue's length is rounded to the second); the one after gets the server document only. No
  retries, one run per track at a time, at most four prefetches per playing track (a page could
  forge well-formed hints), results held 30 minutes by video id. A prefetch never arms the enqueue gate (only listening enqueues) and never
  emits telemetry. Asking the server to process upcoming tracks early was rejected: a job takes
  ~12 minutes, the next track starts within ~3, and it would mean a YouTube download for a song
  that may never play.
- **Hand-over.** The live lookup takes the warmed answer once. A server document is shown as is.
  lrclib lines are checked against the real announce (the different-edit check) and shown at
  once; the server is still asked, so its document replaces them and its 404 arms the gate as
  before. The prefetch has its own abort signal and is never cancelled when its track starts, so
  the lrclib client's in-flight sharing cannot fail the live lookup; a prefetch still in flight
  when its track starts is claimed, the live ladder goes ahead, and the late result is dropped.
  Each started track logs `prefetch <server|lrclib|none|cold|in-flight> for <key>`: the hit-rate
  measure. A warmed server document is shown without revalidation (at most 30 minutes old); a
  reprocess inside that window shows on the next play. It is always on, with no switch in the
  UI (owner's call); `prefetch_next_track: false` in the settings file is a hidden escape hatch.
- **Proof.** 52 guard mutations across the queue parser, the content-script validation, the
  service worker's forwarding, the prefetch decisions, the prefetcher and the orchestrator, each
  killed. Two end-to-end runs: the real extension in Chromium against a fake YTM page with a
  queue store (initial prediction, a reorder, shuffle and repeat-one emptying it, a malformed
  forged post dropped); and the real overlay with lrclib.net answering 503 throughout: the next
  song was warmed from the mirror at the current song's 60 s mark, and after the switch its 40
  lines were on screen 0.5 s later (the overlay's own debounce) with no request for it at all.

### Lrclib-App-Id (registered 2026-10-26)

lrclib now offers optional application registration. The client-side model gives a public
`Lrclib-App-Id` ("not a secret, safe to ship in a client"); the server-side model gives a secret
key that a desktop client could not use, and the choice is permanent. Its docs promise higher
limits for registered clients (~180/min per IP vs ~90 for an identified User-Agent), while the
portal and the 2026-09-30 terms say registration "currently provides no additional rate limits".
At our volume the limit is not the point; the contact channel is (lrclib can reach the owner
instead of blocking the User-Agent). The owner registered one client-side application, "Kashi".

Both twins send the id to lrclib.net only, never to the mirror (the overlay's lrclib.net client,
the server's primary transport). If lrclib answers 401 to it (revoked or unknown), the client
drops it for the rest of the process, logs once, and repeats that request without it (paced like
any other on the server), so anonymous access keeps working. 10 guard mutations, each killed.

### Mirror memory limit (2026-10-26)

Three days after the round-2 fix, with no restart: resident memory peaked at 43 MiB and stayed
flat. The container's working set averaged 651 MiB (p95 2 310, max 2 810) and its cgroup usage
filled up to the old 6 GiB limit, but that is the kernel's page cache of the 107 GB database
(reads plus the 2 GB mmap): reclaimable, so a lower limit means a smaller hot cache, not an OOM
kill. The limit goes to 2 GiB (private IaC change), near the working set's p95; `/api/get` stays in
milliseconds, and a cold search beyond the overlay's 2.5 s budget falls through to lrclib.net.

The uptime probe failed 3 times in those 3 days, each exactly 24 h, 48 h and 72 h after the process
started: the daily `PRAGMA optimize` stall from §5, confirmed as daily.

### Upstream notes (drafts; the owner sends them)

Two notes for lrclib upstream, written for its tracker. Facts checked on 2026-10-26: the repository
has no contributing guide or issue templates, Discussions are enabled, maintenance jobs are clap
subcommands (`backfill-*`), no issue mentions the optimize stall, and Discussion #120 covers the
dump's size (trimmed by hand there, no script).

#### Draft A — GitHub issue (sent as lrclib issue #135, 2026-10-26)

**Title:** Daily `PRAGMA optimize` blocks the async runtime for ~30 s on a large database

**Body:**

Hi, thanks for publishing the server and the dumps. I run the stock server (main @ `04369d3`)
as a private, read-only mirror on a trimmed copy of the 2026-10-12 dump (synced rows only,
~107 GB, spinning disk). Once every 24 hours it stops answering for about 30 seconds.

What I see:

- An uptime check hitting `/api/get/<id>` once a minute fails exactly once a day, always at
  the same minute: 24 h, 48 h and 72 h after the process started (three days so far, no other
  failures). Restarting the process moves the minute.
- The once-a-minute metrics log line arrives ~30 s late at that moment.
- Memory and CPU are otherwise flat.

That timing matches `spawn_optimize_pragma` in `server/src/background.rs` (L104-116 at
`04369d3`): a `tokio::time::interval` of 24 h whose task calls `state.pool.get()` and
`conn.execute_batch("PRAGMA optimize;")` synchronously on the async executor. On a database
this size the optimize takes ~30 s, and while it runs the server does not respond. (I have not
profiled which part of the runtime is starved; the synchronous call on an executor thread is
the obvious suspect. The routes already use `tokio::task::spawn_blocking` for their queries,
e.g. `routes/search_lyrics.rs:178`.)

Possible fixes, in case they help:

1. Run the optimize inside `tokio::task::spawn_blocking`, like the routes do.
2. Optionally set `PRAGMA analysis_limit` (e.g. 400) before it, which bounds the work
   SQLite does per index on a big database.

The startup `PRAGMA optimize=0x10002` in `db.rs` runs before the server listens, so it is not
affected. I know lrclib.net itself may run newer code than the public repository, so this may
already be different there. Happy to test a patch on the mirror.

---

#### Draft B — GitHub Discussion (category: Show and tell)

**Title:** trim.py: a synced-only, lyricsfile-free copy of the dump for self-hosted mirrors (262 GB → 107 GB)

**Body:**

Following up on #120 (dump size): I needed a private mirror that answers one question,
"synced lyrics for this track", and the full 2026-10-12 dump unpacks to ~262 GB, most of it
lyrics history and the per-row `lyricsfile` YAML. So I wrote a small stdlib-only Python script
that makes a trimmed copy the stock server starts on without changes:

- keeps every track whose **latest** lyrics row has synced lyrics, and exactly that one lyrics
  row per track; track and lyrics ids are unchanged, so a mirror answer and an lrclib.net
  answer name the same record;
- drops `lyricsfile` (and zeroes `has_lyricsfile` / `has_word_sync`);
- copies the full schema, triggers and `PRAGMA user_version`, so the server's migration check
  passes (it refuses a database that is ahead of it).

On the 2026-10-12 dump: 33.7 M tracks → 21.5 M, 106.8 GB, about 2 h 20 min, bound by the
sequential read of the source on a spinning disk. The stock server (main @ `04369d3`) starts on
it in about a second and builds its Tantivy index in ~3.5 min. `/api/get` matched lrclib.net on
every record I compared (29 of 29 same id).

Script: https://github.com/csermet/kashi/blob/main/tools/lrclib-mirror/trim.py (MIT, like the
rest of that repository). Usage: `python3 -I trim.py SOURCE.sqlite3 DEST.sqlite3`; the source is
opened read-only and immutable. One warning from experience: the final `VACUUM` writes a second
full copy, so stage it on a disk with room for both.

If a trim step like this would be useful upstream, for example as a subcommand next to the
`backfill-*` ones, I'm glad to port it.

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
