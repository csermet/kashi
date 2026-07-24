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

## Relation to open bugs

Faz 6.7 could bundle: this telemetry + the "wrong timing" fix (whose
root-cause the ytm-scout round is narrowing now) + the macOS window
placement follow-through (0.10.2 `enableLargerThanScreen` — verify on
Caner's Mac) + any P3-spike-driven effect-layer work + accumulated nits.
