# Kashi × YouTube Music — Pre-Build Verification Report
(All fetched/searched 2026-07-07. Current Chrome stable = 150, released 2026-06-30, so every version threshold below is already live, not upcoming.)

## 1. Facts established (source + date)

**Item 1 — MAIN-world mediaSession:** CONFIRMED. web-scrobbler's injected MAIN-world script (`youtube-music-dom-inject.ts`) reads `navigator.mediaSession.playbackState` and `.metadata` (title/artist/album/artwork) directly and relays via `window.postMessage()` to the ISOLATED content script. It doesn't poll on a timer — it re-reads on a `MutationObserver` watching `#play-pause-button` and `.content-info-wrapper`. Source: raw.githubusercontent.com/web-scrobbler/web-scrobbler/master/src/connectors/youtube-music-dom-inject.ts.

**Item 2 — video element/timeupdate:** CONFIRMED, ISOLATED-world readable, plain `<video>` (no shadow-root barrier). pear-desktop's `song-info-front.ts` listens `seeked`/`playing`/`pause`/`volumechange` directly on the element. Chrome's own throttling doc explicitly recommends `timeupdate`/`ended` over `setTimeout` chains because media events are exempt from background-tab intensive throttling. Source: developer.chrome.com/blog/timer-throttling-in-chrome-88; raw.githubusercontent.com/pear-devs/pear-desktop/master/src/providers/song-info-front.ts.

**Item 3 — yt-navigate-finish:** Still fires, but neither actively-maintained reference (web-scrobbler YTM connector, pear-desktop) uses it as the primary now-playing signal. **DELTA:** pear-desktop instead listens for `videodatachange` on `#movie_player` (the player's own Polymer/API element, exposes `getVideoData()`/`getPlayerResponse()`), which fires per-track including radio/autoplay advance without a full page nav — closer to the exact signal Kashi needs. Recommend using both.

**Item 4 — selectors/ads:** Verified live against current `youtube-music.ts` connector source:
- `ytmusic-player-bar` (player bar), `.ytmusic-player-bar .time-info` (progress text), `#progress-bar` (progress element, pear-desktop), `ytmusic-like-button-renderer #button-shape-like button[aria-pressed="true|false"]`.
- **DELTA (correction):** ad flag is `.ytmusic-player-bar.advertisement`, not `.ytmusic-player.advertisement` as baseline stated — the class is added to the player-bar element itself. Both repos pull title/artist/album via mediaSession/`getPlayerResponse()`, **not** DOM text selectors — no current, reliable DOM text selector for those fields was found; don't build a fallback on scraped text nodes without live re-verification.

**Item 5 — LNA loopback WebSocket:** CONFIRMED with important precision. LNA prompt for public-origin→local/loopback fetches shipped default-on Chrome 142; extended to gate WebSockets around Chrome 147 (developer.chrome.com/blog/local-network-access; github.com/webflow/mcp-server/issues/124). Chrome extensions team (Patrick Kettner) on chromium-extensions groups confirms **background/service-worker (`chrome-extension://`) requests are not gated by the LNA prompt** as long as correct `host_permissions` exist — there was a regression (crbug 435246545) blocking even correctly-permissioned extensions, **fixed in Chrome ≥144.0.7512.0** (already long shipped as of 150). Separately confirmed (WICG local-network-access issue #60 + Chrome's chromium.org content-script-fetch policy, in force since Chrome 85/87, 2020): **content-script-issued fetch/WebSocket calls are attributed to the page's origin** (music.youtube.com), not the extension — so they ARE subject to LNA/CSP as page traffic. Sources: groups.google.com/a/chromium.org/g/chromium-extensions/c/pUDh8RiTjJk; github.com/WICG/local-network-access/issues/60; chromium.org/Home/chromium-security/extension-content-script-fetches.
→ **Architectural conclusion: the WS bridge must be opened exclusively by `background.js` (service worker), never by the content script.**

**Item 6 — world:"MAIN" support:** Static declaration in `manifest.json` `content_scripts[].world` supported since **Chrome 111** (2023); current official docs describe it plainly with no workaround caveat. **DELTA (stale-assumption correction):** an old Aug-2022/Chrome-102-era post claiming static declaration was broken (needing `chrome.scripting.registerContentScripts` from the SW instead) predates the Chrome 111 fix — treat as resolved, declare statically. Source: developer.chrome.com/docs/extensions/reference/manifest/content-scripts.

**Item 7 — chrome.alarms minimum:** CONFIRMED lowered to **30 seconds** as of Chrome 120 (was 60s), deliberately matched to the 30s SW inactivity window (Chrome 116+). Source: developer.chrome.com/docs/extensions/reference/api/alarms; developer.chrome.com/docs/extensions/how-to/web-platform/websockets.

**Bonus finding:** `th-ch/youtube-music` renamed to **`pear-devs/pear-desktop`** (rename ~Sept–Dec 2025, to preempt a Google DMCA concern) — same project/community, update references when searching its issues/commits.

## 2. Deltas vs baseline (flagged)
- Ad selector: `.ytmusic-player-bar.advertisement`, not `.ytmusic-player.advertisement`.
- Add `videodatachange` on `#movie_player` as primary track-change signal alongside `yt-navigate-finish`.
- world:"MAIN" static manifest declaration is fully supported today, no dynamic-registration workaround needed.
- LNA: SW-exemption confirmed but was briefly broken by a Chrome bug, fixed Chrome 144 — already resolved.
- chrome.alarms floor is 30s (not 60s), since Chrome 120.
- Reference repo `th-ch/youtube-music` → `pear-devs/pear-desktop`.

## 3. Ready-to-use list

**(a) Selectors**
```
ytmusic-player-bar
.ytmusic-player-bar .time-info
#progress-bar
.ytmusic-player-bar.advertisement        /* ad flag */
ytmusic-like-button-renderer #button-shape-like button[aria-pressed="true"]
document.querySelector('video')
document.querySelector('#movie_player')  /* for videodatachange, getVideoData() */
```

**(b) Events**
```
document: yt-navigate-finish, yt-navigate-start   (page-level nav)
#movie_player: videodatachange                    (primary track-change signal)
video: timeupdate, seeked, playing, pause, volumechange
MAIN world: MutationObserver on #play-pause-button, .content-info-wrapper
            → re-read navigator.mediaSession → window.postMessage()
```

**(c) manifest.json**
```json
{
  "manifest_version": 3,
  "permissions": ["scripting", "alarms"],
  "host_permissions": ["*://music.youtube.com/*"],
  "background": { "service_worker": "background.js" },
  "content_scripts": [
    { "matches": ["*://music.youtube.com/*"], "js": ["isolated-bridge.js"], "run_at": "document_start" },
    { "matches": ["*://music.youtube.com/*"], "js": ["main-world-inject.js"], "world": "MAIN", "run_at": "document_start" }
  ]
}
```
- Open the WS to `127.0.0.1` only from `background.js`. Keep message cadence <30s (WS traffic itself, or `chrome.alarms.create('keepalive', {periodInMinutes: 0.5})` as backup floor).
- No localhost/loopback host_permission entry needed/exists for WS in manifest — the SW-origin exemption applies regardless, per confirmed Chrome team statement above.

## 4. Risks / unverified
- No live-DOM inspection was performed (no browser tool available this session) — selectors are sourced from current reference-repo code, not directly observed on music.youtube.com today. Treat as high-confidence, not first-hand-verified.
- `videodatachange` on `#movie_player` for YTM specifically is inferred from pear-desktop's shared song-info provider; not independently confirmed it fires identically on music.youtube.com vs youtube.com watch pages — verify empirically before relying on it solely.
- "Local Network Access split permissions" (chromestatus.com/feature/5068298146414592, splitting into `local-network`/`loopback-network`) — could not retrieve full body; unverified whether/how this later affects the extension-exemption behavior. Re-check before shipping if LNA prompts unexpectedly appear.
- No ad skip-button/container selector found in current sources (likely out of scope — ad-boolean via `.advertisement` class should suffice for an overlay).
- WebNowPlaying's own live `manifest.json` could not be fetched directly (404 on guessed path); the SW-owned-socket architecture claim rests on the corroborating Chrome-team/policy sources above, not a direct read of their file.

No local files were created or modified — this was a read-only research session (WebSearch/WebFetch only).
