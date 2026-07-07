# Contributing to Kashi

Early-stage project — expect churn. The data contracts in `packages/schemas`
and `packages/protocol` are the source of truth; read them before touching
anything that crosses a component boundary.

## Setup

- Node.js 22 LTS, pnpm 11 (`corepack enable`), Python 3.12, [uv](https://docs.astral.sh/uv/).
- `pnpm install && pnpm build` — TS side (contracts, extension, overlay).
- `cd apps/server && uv sync && uv run pytest` — Python side.

## Rules that will come up in review

- All schema-bound timings are **integer milliseconds** (`_ms` suffix).
- Schema v1 is additive-only; production parsers tolerate unknown fields.
- If you change `packages/schemas/*.schema.json`, run `pnpm codegen` and commit
  the regenerated output (CI enforces drift).
- Extension: the WebSocket lives in the service worker, never a content script.
- Overlay: no `innerHTML` with dynamic data; Electron hardening flags stay on.

`.claude/agents/` contains project agents for [Claude Code](https://claude.com/claude-code)
users: `kashi-reviewer` (contract/pitfall review) and `ytm-scout` (YouTube Music
integration research). Their checklists double as the review criteria above.
