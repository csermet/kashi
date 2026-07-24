# Diagnostic telemetry → server (Faz 6.7 sketch, Caner's idea 2026-07-24)

**Status: IDEA / design input — not built.** Caner: "Kashi'yi açtığım an tüm
loglar bir yerde dursun — hangi PC, hangi OS, hangi sürüm, hangi şarkı,
hangi lyric aldı, ne oldu — hepsi sunucuya bassın. Sunucu bilgisi zaten
config'te var." A proper plan round opens this (candidate: **Faz 6.7 =
telemetry + biriken bug-fix'ler**).

## Why

Field bugs like the "wrong timing, fixed by a YTM refresh" one (Faz 6.5
open item) are near-impossible to diagnose from a soft verbal report. The
overlay ALREADY produces timestamped log lines (`window.kashi.log(...)` →
main process console); today they only live in Caner's terminal. If a
server is configured, those lines + structured events should stream to it,
so a bug is one query away instead of a repro hunt.

## Shape (first-cut, to refine in the plan round)

- **Gate:** only when `server_url` + `server_api_key` are set — same
  condition as lookups. No server → byte-for-byte today's behavior (the
  serverless contract). It's the user's OWN self-hosted server; the data is
  theirs. A tray "Send diagnostics" toggle (default ON when server set) is
  the likely opt-out.
- **Transport:** overlay main process batches events (say every 5–10 s or N
  events) → `POST /v1/telemetry` (Bearer, like ingest). Fire-and-forget,
  never blocks playback, drops on failure (diagnostics must never degrade
  the app). A per-session UUID correlates a run.
- **Session envelope (once at open):** app version, OS + version, arch,
  electron/chromium version, display count/size, settings snapshot
  (effect_level, theme_scope, offset), server_url host.
- **Events (the existing log stream, structured):** `track_changed`
  (videoId, title, artist, duration_ms, id-source player-api/url),
  `lyrics_outcome` (source kashi-server/lrclib/none, quality, doc pipeline
  version, sync word/line, speed_factor), `position_anomaly` (slew/snap
  magnitudes — would directly catch the wrong-timing bug), `error`/`watchdog`.
- **Server side:** new `telemetry` table (session_id, ts, kind, payload
  jsonb) + retention (e.g. 30 days) + a Grafana "Field diagnostics" panel
  (which OS/version, lyric-source mix, anomaly rate per song). Pairs with
  the existing `kashi-postgres` datasource pattern.

## Open questions (plan round)

- Extension side: does it need to emit anything new, or does the overlay's
  view suffice? (Extension is frozen at 0.1.11 — keeping it out is a plus.)
- Volume/retention/PII: song titles + ids are already sent for lookups, so
  no NEW exposure — but confirm scope and a purge path.
- Privacy default: ON-when-server-set vs explicit opt-in.
- Does this subsume the ntfy/session-notification habit or complement it.

## Bundled bug: "wrong timing, fixed by a YTM refresh" — ROOT CAUSE FOUND

ytm-scout round (2026-07-24, sourced) localized a strong root-cause
hypothesis (mechanism-level UNVERIFIED, but the code gap is real and the
precedent is solid):

**The asymmetric position-staleness gap.** `content/index.ts` guards
DURATION staleness at a track switch (`freshDurationMs()` returns undefined
until `durationchange` fires for the new source) but has **no equivalent
guard for POSITION**. `maybeAnnounceTrack()` sends a
`positionEvent('position')` right after the announce, reading
`video.currentTime` unconditionally — and at that instant currentTime can
still be the OLD track's value (or a cumulative offset under YTM's gapless
playback, or a frozen swapped `<video>` element). The overlay's
`PositionClock.update()` then accepts the FIRST post-`track_changed` report
as the anchor with NO delta check (`if (!hasAnchor || isSeek) setAnchor`).
So a wrong first position becomes the anchor → lyrics scroll at the wrong
time until a real seek/large delta resnaps — and a YTM **refresh** clears
all state, which is why it "fixes it".

Precedent: pear-desktop sets `elapsedSeconds = 0` on every `videodatachange`
(never trusts currentTime at a switch); Spotify's Web SDK has the analogous
"first event after skip carries wrong data" class. No scrobbler documents an
explicit guard, but pear-desktop's 0-assumption is the low-risk pattern.

**Proposed guards (extension 0.1.12 — currently FROZEN, so this is a
deliberate un-freeze in 6.7):**
1. *Sanity clamp (near-zero risk, mechanism-independent):* before the
   announce-accompanying `pos`, if `freshDurationMs()` is defined and
   `position_ms > duration + ~2000`, skip THAT event — the next `timeupdate`
   (~250 ms) brings the real value.
2. *Mid-session-only defer:* if `announcedVideoId !== null` (a real
   mid-session change, NOT cold-start/refresh) AND `freshDurationMs()` is
   undefined, skip that `pos` too. The cold-start/refresh path
   (`announcedVideoId === null`) stays byte-for-byte — the behavior Caner
   relies on today ("refresh fixes it") is preserved.
3. *Diagnostic log:* add raw `video.currentTime` to the announce log line —
   the next repro capture then reveals WHICH mechanism (cumulative offset /
   frozen element / transient glitch). This is exactly what telemetry above
   would capture as a `position_anomaly` event.

Side effects: none to auto-advance/shuffle/seek — only the single
announce-accompanying position report can be delayed ~250 ms–1.5 s (within
YTM's own dataupdated-fallback window; imperceptible since lyrics are already
in "searching" state at a track change). Full report + sources: the
ytm-scout round output.

**OPEN QUESTION for Caner:** does he have YTM **Premium**? Gapless playback
is Premium-only (2026) — if yes, the cumulative-buffer-offset mechanism is
likely; if no, "frozen element"/"transient glitch" lead. The proposed guards
work either way (they neutralize the symptom regardless of mechanism), but
this sharpens the root-cause confirmation.

## Relation to open bugs

Faz 6.7 could bundle: this telemetry + the "wrong timing" fix (root cause
above) + the macOS window placement follow-through (0.10.2
`enableLargerThanScreen` — verify on Caner's Mac) + any P3-spike-driven
effect-layer work + accumulated nits. The telemetry and the timing fix are
mutually reinforcing: telemetry's `position_anomaly` events confirm the fix
in the field.
