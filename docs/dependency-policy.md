# Dependency policy — the upgrades that need a human

Until 2026-07-27 these rules lived in `renovate.json` and a bot enforced them.
The bot is gone (see *Why no bot* below), so they live here now. Nothing
enforces them automatically anymore — read this before bumping anything in the
list.

Exact versions live where they belong: `pnpm-lock.yaml`, `uv.lock`,
`pyproject.toml`, `package.json`. This file only records **which upgrades are
traps**, and why.

---

## Never bump without a human

### `ctc-forced-aligner` — pinned to a git SHA, do not touch

It has no releases; we depend on a specific commit. A digest bump silently
changes alignment code, and alignment quality is the product.

The trap: the PEP 508 git URL registers the dependency under the **repo slug**,
not the package name. A name-only guard let PR #1 slip through once. If you
ever reintroduce automated updates, match all three:

```
ctc-forced-aligner
MahmoudAshraf97/ctc-forced-aligner
https://github.com/MahmoudAshraf97/ctc-forced-aligner
```

Belt and braces: **no git-ref / git-tag digest bumps anywhere in this repo.**
The aligner is the only git-pinned dependency, so any such bump is this one
wearing a different hat.

### `torch` / `torchaudio` — pinned to the pytorch-cpu index

`ctc-forced-aligner` carries no torch pin of its own, so ours is the only thing
holding the resolution steady. Bump only together with an **alignment smoke
test** — a real song end to end, compared against a known-good output.

### `electron` majors — Windows QA first

Transparent + click-through windows are a historically fragile area: v38 broke
click-through, v41 broke frameless borders on Linux. Linux does not support the
`forward` parameter at all, so the real test surface is **Windows**.

Before any Electron major: search the issue tracker for `transparent`, then run
a manual overlay smoke test on Windows. Known blocks at the time of writing —
**do not take Electron 43** (one-week-old at pin time) and **do not take
electron-builder 27** (native ESM major, still beta).

---

## Deliberate cadences

### `yt-dlp` — monthly, never pin-and-forget

Stable channel, bumped **monthly**. This is a deliberate middle ground:

- Pin-and-forget is unsafe — the 2026.3.17+ line patched real CVEs (cookie
  leak, command injection, code execution).
- Nightly is unsafe as a default — it is **break-glass only**, for when
  YouTube breaks and the stable fix is late.

The weekly CI canary is what makes monthly acceptable: it catches extraction
breakage early, independent of the release cadence.

### `@crxjs/vite-plugin` — locked pin, no silent majors

The maintenance crisis is resolved (new maintainer, active), but bus factor is
still 1. Keep the pin explicit.

---

## Why no bot

Renovate ran from 2026-07-04 to 2026-07-27 and was removed. On the GitLab side
it updated `package.json` without updating `package-lock.json`, which broke a
sibling project's `main` build; the root cause was a missing GitHub token in
the bot, which meant it could not resolve the Node toolchain and so never ran
npm at all. It also proposed unfiltered major upgrades.

The rules above were the *good* part of that setup — worth keeping, not worth a
bot that needed weekly triage to stay honest.

If a bot is ever reintroduced, it must have a real CI gate behind it: a merge
request pipeline that actually runs `pnpm install --frozen-lockfile` and the
test suite. A green pipeline that runs nothing is worse than no pipeline.
