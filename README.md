# Kashi (歌詞)

Transparent, always-on-top lyrics overlay for your desktop — with word-level karaoke sync.

Play music on YouTube Music in your browser; Kashi shows the lyrics in a draggable,
click-through overlay anywhere on your screen. Songs processed by the (optional,
self-hostable) server get word-by-word karaoke highlighting, beat-synced effects and
album-art color themes; everything else falls back to line-level synced lyrics from
[LRCLIB](https://lrclib.net).

> **Status: early development.** Nothing usable yet — the skeleton, data contracts and
> CI are being laid down. See `docs/` as it fills in.

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
