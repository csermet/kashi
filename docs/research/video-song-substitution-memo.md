# Video/Song Edit Substitution — Evaluation Memo (Faz 6 P7)

**Status:** analysis + research (Faz 6.5 P8 round below) — no implementation.
Input for the Faz 7 scope decision.

## The problem (field: Sinsirella, wUjSOU0p6f8)

For YTM **video** entries the browser may play a long video edit (451 s)
while yt-dlp fetches the **song** stream for the same id (216 s) — YouTube's
music clients substitute streams server-side. A document timed to audio the
browser never plays is confident nonsense. Since 2.4.2 the pipeline fails
honest when `hints.duration_ms` and the downloadable audio disagree by >30 s
(`CLIENT_EDIT_MISMATCH_S`, both numbers + an exit path in the message).

## Candidate permanent fixes

1. **Client duration authority (cheap, incremental).** The extension already
   ships `duration_ms` with `track_changed`; 2.4.2 uses it as a gate. Next
   step would be using it as a *selector*: when the id yields multiple
   formats/edits, prefer the one matching the client's clock. Reality check:
   yt-dlp exposes formats of ONE edit per id — there is no "other edit" to
   pick. Verdict: no additional win beyond the 2.4.2 gate.
2. **Video→song id mapping.** Resolve the video id to its song counterpart
   (YTM exposes related "song" entries via the page/api the extension
   sees). Extension-side: read the watch-page metadata and send BOTH ids;
   server ingests the song id. Protocol change (additive field), MV3 work,
   and a YTM-internals dependency (ytm-scout research needed — brittle
   surface). This is the real fix candidate.
3. **BYO-audio escape (exists).** The 2.4.2 error message already points to
   `POST /v1/uploads` — the user uploads what they actually hear. Manual but
   universal.

## Recommendation

Keep the honest-fail + upload escape as the shipped behavior. If the class
keeps hurting in the field, pursue (2) in Faz 6.5 with a ytm-scout round on
how reliably the watch page exposes the paired song id; (1) is a dead end.

---

## Faz 6.5 P8 — ytm-scout research round (2026-07-19)

Candidate (2) was researched against live sources. Full chain of evidence in
the round's report; the load-bearing facts:

### Verified surfaces

- **`videoDetails.musicVideoType` is real and stable.** The `/youtubei/v1/
  player` response carries `MUSIC_VIDEO_TYPE_ATV` (canonical song audio) /
  `MUSIC_VIDEO_TYPE_OMV` (official video) / `MUSIC_VIDEO_TYPE_UGC` /
  `OFFICIAL_SOURCE_MUSIC`. Documented in ytmusicapi continuously from 0.17.3
  through 1.12.1 (~3+ years) — a comparatively durable field.
- **In-page access is a sibling of what we already use:** pear-desktop
  (ex-th-ch/youtube-music) reads `#movie_player`'s **`getPlayerResponse()`**
  — the same element whose `getVideoData()` our MAIN-world bridge calls
  today. No new permissions, no extra fetch.
- **The video↔song pairing (`counterpart`) lives on a different surface:**
  `/youtubei/v1/next` (watch playlist). ytmusicapi's `get_watch_playlist()`
  exposes an OPTIONAL `counterpart {videoId, …}` — present only when the
  song/video switcher exists. ytmdesktop's Companion Server API exposes the
  same as `counterparts: Array | null` (live production precedent, Electron).
- **web-scrobbler does NOT solve this at all** (connector reads URL `?v=` +
  mediaSession only) — no browser-extension precedent exists; the pattern is
  proven only in full-page-access Electron apps.

### Unverified / risks

- Whether `counterpart` is populated for FREE (non-Premium) accounts:
  unverified (the audio/video toggle UI is Premium-gated; the JSON field's
  behavior is not documented). Live test with both account types required.
- `getWatchNextResponse()` existing under that exact name on the real
  music.youtube.com player object: unverified (inferred from Electron
  wrappers). Next scout step: `typeof document.querySelector('#movie_player')
  .getWatchNextResponse` live probe.
- YTM's own video↔song pairing heuristic is not always right (user reports,
  technically unverified) — even with a canonical id the 2.4.2 gate must stay.

### Proposed additive protocol fields (Faz 7 input)

- **Phase A (low risk, recommended first):** MAIN-world bridge reads
  `getPlayerResponse()?.videoDetails?.musicVideoType`; new additive
  `hints.music_video_type` (`"ATV"|"OMV"|"UGC"|"OFFICIAL_SOURCE_MUSIC"`).
  Server use: tighten the 30 s mismatch gate for OMV/UGC; ATV keeps today's
  behavior. One line in an already-working script.
- **Phase B (medium risk, needs the live probes above):** resolve the
  current videoId's `counterpart.videoId`; new additive
  `hints.canonical_video_id` (nullable). When present the server tries the
  canonical id first; absent → exactly the 2.4.2 path (no regression).

Extension stays at 0.1.11 for Faz 6.5 — both phases are Faz 7 candidates.
