# fx-layer spike results (Faz 6.5 P3) — GO/NO-GO for the effect-layer window

**Status: AWAITING FIELD RUNS** (spike code + runbook:
`apps/overlay/spikes/fx-layer/`). Caner runs the matrix on the Windows
field machine + the Mac dev machine, asynchronously; P4-P6 proceed
meanwhile — only the P7 decision waits for this document.

## GO gate (fixed before measuring)

At **300 particles**, on BOTH machines: p95 frame < 16.7 ms AND process
CPU < 10% (no meaningful GPU heat) AND transparency + click-through +
always-on-top survive a 5-min soak AND sleep/monitor changes don't crash.
Any miss ⇒ NO-GO: P7 drops, the DOM span-pool pattern stays the permanent
path.

## Matrix — Windows (saha)

| engine | particles | p95 (ms) | ~fps | worst (ms) | CPU % | notes |
|--------|-----------|----------|------|------------|-------|-------|
| pixi   | 100       |          |      |            |       |       |
| pixi   | 300 ⭐    |          |      |            |       |       |
| pixi   | 1000      |          |      |            |       |       |
| canvas | 100       |          |      |            |       |       |
| canvas | 300       |          |      |            |       |       |
| canvas | 1000      |          |      |            |       |       |

Durability: YTM-fullscreen ☐ · click-through ☐ · transparency holds ☐ ·
sleep/wake ☐ · monitor change ☐ · thermals ☐

## Matrix — macOS (dev)

| engine | particles | p95 (ms) | ~fps | worst (ms) | CPU % | notes |
|--------|-----------|----------|------|------------|-------|-------|
| pixi   | 100       |          |      |            |       |       |
| pixi   | 300 ⭐    |          |      |            |       |       |
| pixi   | 1000      |          |      |            |       |       |
| canvas | 100       |          |      |            |       |       |
| canvas | 300       |          |      |            |       |       |
| canvas | 1000      |          |      |            |       |       |

Durability: YTM-fullscreen ☐ · click-through ☐ · transparency holds ☐ ·
sleep/wake ☐ · monitor change ☐ · thermals ☐

## Verdict

**GO / NO-GO:** _(pending)_ — rationale:

## Known background

Windows transparency × hardware acceleration is a known Electron conflict
class (Kashi already disables `CalculateNativeWinOcclusion` and Chromium
disk caches in the overlay for related reasons); PixiJS v8's
ParticleContainer benchmark headroom (1M @ 60fps on an M3) is ~1000x our
scale but was never measured in a transparent always-on-top window — hence
this spike (plan risk R3: measure, then lock technology).
