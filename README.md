# Kashi (歌詞)

Transparent, always-on-top lyrics overlay for your desktop — with word-level karaoke sync.

Play music on YouTube Music in your browser; Kashi shows the lyrics in a draggable,
click-through overlay anywhere on your screen. Songs processed by the (optional,
self-hostable) server get word-by-word karaoke highlighting, beat-synced effects and
album-art color themes; everything else falls back to line-level synced lyrics from
[LRCLIB](https://lrclib.net).

> **Status: Phase 4 complete.** The Electron overlay renders line- and word-synced
> lyrics for YouTube Music via the browser extension, with lrclib as the zero-setup
> source. The optional self-hosted server pre-processes tracks into word-timed
> documents (yt-dlp → vocal separation → lrclib-anchored forced alignment →
> beats/palette), including nightcore/sped-up reuploads (auto speed-factor
> detection). The effect engine themes the overlay from album art via OKLCH tone
> mapping and adds word easing, sustained-vowel sweeps, ad-lib styling and
> beat-synced pulses — all tunable from the tray (levels, theme scope, box
> opacity, timing offset). Packaged releases (Phase 5) are still ahead.

## How it works

```
┌─ Your desktop ──────────────────────────────────────┐
│ Chrome: music.youtube.com                           │
│  ├─ MAIN-world script (mediaSession metadata)       │
│  ├─ content script (position via timeupdate)        │
│  └─ extension service worker ──ws://127.0.0.1──┐    │
│                                                ▼    │
│ kashi-overlay (Electron)                            │
│  ├─ transparent / click-through / always-on-top     │
│  ├─ line-level lyrics from LRCLIB (works serverless)│
│  └─ word-level data from kashi-server (optional) ───┼──► self-hosted
└─────────────────────────────────────────────────────┘    kashi-server
                                                           (FastAPI + worker:
                                                            align lyrics, beats,
                                                            palette — audio deleted
                                                            after processing)
```

| Component | Path | Stack |
|---|---|---|
| Browser extension | `apps/extension` | Chrome MV3, TypeScript, Vite + CRXJS |
| Desktop overlay | `apps/overlay` | Electron, TypeScript, electron-vite |
| Processing server | `apps/server` | Python 3.12, FastAPI, Postgres |
| Data contracts | `packages/schemas`, `packages/protocol` | JSON Schema (single source of truth) |

## Development

Prereqs: Node.js 22 LTS + pnpm 11 (via corepack), Python 3.12 + [uv](https://docs.astral.sh/uv/).

```bash
pnpm install && pnpm build      # extension + overlay + contracts
cd apps/server && uv sync && uv run pytest
```

Windows (overlay/extension testing):
1. `pnpm install`, then `pnpm --filter kashi-overlay dev` to run the overlay.
2. `pnpm --filter kashi-extension build`, then load `apps/extension/dist` as an
   unpacked extension at `chrome://extensions` (Developer mode).

## License

[MIT](LICENSE)

## Troubleshooting

- **The translucent box seems to change opacity while you scroll or interact
  (screenshots always look fine):** check your MONITOR's own HDR setting. A
  monitor doing SDR→HDR expansion (monitor HDR on, Windows HDR off) re-tone-maps
  content dynamically and translucent overlays visibly shift with it. Fix: turn
  HDR off on the monitor, or enable HDR in BOTH Windows and the monitor (a real
  HDR signal disables the monitor's dynamic expansion). Field-diagnosed 2026-07;
  this happens after the GPU framebuffer, so no application can prevent it.
- **The box fades when idle for a few seconds (Windows):** fixed in overlay
  0.2.9 (Chromium's native window-occlusion tracker is disabled for Kashi). If
  you still see residual flicker over videos, try the tray option
  *Fix video flicker (software render)*.
- **`Unable to move the cache` errors on startup:** harmless and fixed in 0.2.9+
  (Kashi no longer uses Chromium disk caches). If the WS port drifts to 17891,
  an old Kashi instance is still running — kill it; 0.2.8+ prevents this with a
  single-instance lock.
