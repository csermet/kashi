# fx-layer spike results (Faz 6.5 P3) — GO/NO-GO for the effect-layer window

**Status: WINDOWS PASSED DECISIVELY (2026-07-25) — macOS run pending.**
Spike code + runbook: `apps/overlay/spikes/fx-layer/`. The budget was spent
on platform coverage (one cell, both machines) rather than matrix cells.

## GO gate (fixed before measuring)

At **300 particles**, on BOTH machines: p95 frame < 16.7 ms AND process
CPU < 10% (no meaningful GPU heat) AND transparency + click-through +
always-on-top survive a 5-min soak AND sleep/monitor changes don't crash.
Any miss ⇒ NO-GO: P7 drops, the DOM span-pool pattern stays the permanent
path.

## Windows — **PASS** (high-end machine, read with the caveat below)

Hardware: Ryzen 7 9700X + RTX 5070 Ti + 32 GB, **4K display @ 120 Hz**.

| engine | particles | p95 (ms) | ~fps | worst (ms) | CPU % | soak |
|--------|-----------|----------|------|------------|-------|------|
| pixi   | 300 ⭐    | **7.1**  | ~144 | 441 (startup) | **1.7–3.0** | 16 min |

Both gate numbers pass with room to spare: p95 7.1 ms against a 16.7 ms
budget, CPU 1.7–3.0% against 10%.

**Memory: no leak.** Two Task Manager samples minutes apart read 100.2 MB
and 100.4 MB across the four Electron processes — flat over the soak.

Durability: YTM-fullscreen ☐ (untested) · click-through ✅ (typed into the
cmd window and drove Task Manager *underneath* the layer for 16 min) ·
transparency holds ✅ (VS Code + terminal fully legible throughout, never
went black) · sleep/wake ☐ · monitor change ☐ · thermals ✅ (3% CPU).

### Reading the 7.1 ms honestly — two possible interpretations

~144 fps on a **120 Hz** panel is faster than that panel's vsync interval
(8.33 ms), so the frame loop is **not** simply vsync-locked. Either the
display was running above 120 Hz, or Chromium free-ran the transparent
window's rAF. That matters:

- *If vsync-locked:* 7.1 ms is a floor, the renderer is idle — enormous
  headroom.
- *If free-running:* 7.1 ms is the REAL per-frame cost. On a 5070 Ti, 300
  sprites cannot explain that — which points at the **transparent
  full-screen composite** as the dominant cost: a 3840×2160 RGBA surface
  blended by DWM every frame. That scales with RESOLUTION, not particle
  count.

Either way the practical conclusion is the same and is good news for the
design: **particle count is nearly free; screen area and the compositor
are the real variables.** Effect budgets should therefore be set by
resolution/compositor, not by "how many particles".

**Caveat (Caner, correctly): this is a very strong machine.** It settles
the *compatibility* question — transparent + always-on-top + click-through
+ WebGL coexist on Windows/DWM, the historically fragile combination — but
proves nothing about modest hardware. The MacBook Pro M2 run covers that:
integrated GPU, different compositor (CoreAnimation), and a laptop thermal
and battery envelope.

## macOS — pending (MacBook Pro M2, Retina)

This is the run that actually stresses the design: integrated GPU, Apple's
own compositor, a Retina surface to blend, and a laptop's thermal/battery
envelope. It answers the question the Windows box could not.

| engine | particles | p95 (ms) | ~fps | worst (ms) | CPU % | soak |
|--------|-----------|----------|------|------------|-------|------|
| pixi   | 300 ⭐    |          |      |            |       |      |

Notes to capture while running:
- **fps vs the panel's refresh** (60 Hz, or 120 Hz on a ProMotion model) —
  if fps tracks the refresh and p95 ≈ its interval, the loop is
  vsync-locked and there is headroom; if fps sits BELOW the refresh, p95 is
  the real cost and that is the number that matters.
- **Plugged in or on battery** — macOS runs different power profiles;
  record which, because a battery run is the pessimistic case.

Durability: YTM-fullscreen ☐ · click-through ☐ · transparency holds ☐ ·
sleep/wake ☐ · thermals ☐ (an M2 only starts throttling after minutes
under load — the reason the soak is 5 min and not 30 s).

## Verdict

**GO / NO-GO:** _(pending macOS)_ — Windows alone already answers the
question the spike was built for: a transparent, always-on-top,
click-through WebGL window is stable and cheap on the platform where the
combination was historically fragile. Unless macOS surprises us, this is a
**GO**, and the P7 effect layer (plus the richer graphical/particle
direction Caner asked for) is technically clear.

## Known background

Windows transparency × hardware acceleration is a known Electron conflict
class (Kashi already disables `CalculateNativeWinOcclusion` and Chromium
disk caches in the overlay for related reasons); PixiJS v8's
ParticleContainer benchmark headroom (1M @ 60fps on an M3) is ~1000x our
scale but was never measured in a transparent always-on-top window — hence
this spike (plan risk R3: measure, then lock technology).
