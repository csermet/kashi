# Kashi — notes for Claude Code sessions

Lyrics overlay for YouTube Music: browser extension (`apps/extension`) → Electron overlay
(`apps/overlay`) → optional FastAPI server + worker (`apps/server`). Contracts in
`packages/schemas` and `packages/protocol` are the source of truth (see CONTRIBUTING.md).

## Start here

1. `docs/research/INDEX.md` — why each decision was made.
2. The latest hand-off note in `docs/research/` (currently `faz-9-devir-askerlik-2026-08.md`):
   live versions, open work, first step.
3. `docs/roadmap.md` (targets, language order, constraints) and
   `docs/research/olu-fikirler.md` (ideas already measured and rejected — do not retry them
   without new evidence).
4. `docs/dependency-policy.md` before bumping anything pinned.

## House rules

- Commits use the repo's git identity (the owner). No co-author trailers. Repo language is
  English (code, commits, new docs); some older research notes are Turkish.
- Server gate is four commands, all of them (in `apps/server`, plus the root script): `uv run pytest`, `uv run ruff check .` (read the
  exit code, not the last line), `uv run pyright`, `node scripts/check-versions.mjs`. CI's TS
  path runs locally in the same order: validate → codegen drift → check-versions → typecheck →
  lint → test → build.
- DB-backed tests are **skipped** unless `DATABASE_URL` points at a throwaway Postgres 17
  (`postgresql+psycopg://USER:PASS@localhost:PORT/kashi`). A green run without it proves little.
- `/v1/health`'s version string is cosmetic; `pipeline_version` (`kashi_server/version.py`) is
  the load-bearing field — check that after a rollout.
- Never edit an Alembic migration that has already reached head: environments that applied it
  drift silently. Add a new migration.
- Schema change ⇒ `pnpm --filter @kashi/schemas codegen` and commit the generated output.
- Guards are proven by mutation: remove the fix, the test must fail. Several first-draft guards
  here were toothless until checked this way.
- After a reprocess wave starts, inspect the **first** finished document immediately; the
  selection-stats log line usually names the bug.
- If a unit is introduced (e.g. a "gesture" instead of a word), convert every step that counts.
- The same logic often lives twice (server pipeline vs the overlay's own lrclib client; themed vs
  neutral `.word-fill`). Fixing one does not fix the other — look for the twin.
- Bump `LOOKUP_LADDER_VERSION` whenever a lookup rung changes (negative-cache entries are keyed to it).

## Traps worth knowing

- Shells may export `ELECTRON_RUN_AS_NODE=1`; smoke-test the packaged app with
  `env -u ELECTRON_RUN_AS_NODE`.
- electron-vite must bundle workspace packages (`externalizeDeps.exclude`), sandboxed preloads are
  CJS, transparent windows stay `resizable: false`. macOS builds are ad-hoc signed in
  `apps/overlay/build/after-pack.cjs` (unsigned arm64 builds show "damaged").
- `pnpm approve-builds` can write a placeholder ("set this to true or false") into
  `pnpm-workspace.yaml` — fix it by hand. Ubuntu 24.04 runners lack libfuse2:
  AppImage tooling needs `--appimage-extract-and-run`.
- The extension never auto-updates: rebuild, reload it in `chrome://extensions`, refresh YTM.
- Do not hand-edit `kashi-settings.json` while the overlay runs (it is rewritten on exit).
- CSS custom-property specificity changes silently break theming; when screenshots and rules
  disagree, log `getComputedStyle` from the renderer once.
- Keep the `torchcodec` override in `apps/server/pyproject.toml`; server images 0.8.0, 0.9.0 and
  0.9.1 must not be deployed.
- yt-dlp needs its EJS remote components and a persistent cache, or fresh containers return a
  misleading 403. The canary only fetches metadata, so it cannot see download 403s.
- Postgres `now()` is frozen per transaction — job claim/lease SQL uses `clock_timestamp()`;
  after a raw-SQL claim, refresh ORM objects (identity map is stale).
- Turkish dotless `ı` does not decompose under NFKD. `ctc_forced_aligner` emissions carry a
  `<star>` column: drop it before any argmax.
- The HF cache keeps every model revision (budget ~2× model size); worker peak memory is in
  source separation, not alignment.

Project agents: `.claude/agents/kashi-reviewer.md`, `.claude/agents/ytm-scout.md`.
