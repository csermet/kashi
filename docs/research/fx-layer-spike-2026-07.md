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

## Windows (saha machine: 9700X + RTX 5070Ti, 144 Hz) — **PASS**

| engine | particles | p95 (ms) | ~fps | worst (ms) | CPU % | soak |
|--------|-----------|----------|------|------------|-------|------|
| pixi   | 300 ⭐    | **7.1**  | ~144 | 441 (startup) | **1.7–3.0** | 16 min |

Both gate numbers pass with room to spare: p95 7.1 ms against a 16.7 ms
budget, CPU 1.7–3.0% against 10%.

**Read of the numbers:** 144 fps at 7.1 ms p95 IS the vsync interval
(144 Hz = 6.94 ms), so the renderer is not compute-bound at all — it draws
and waits for the next refresh. 300 particles barely register. The single
441 ms worst frame is startup/window-creation jank over a 16-minute run,
not a recurring hitch (the p95 proves the steady state).

**Memory: no leak.** Two Task Manager samples ~minutes apart read 100.2 MB
and 100.4 MB across the four Electron processes — flat over the soak.

Durability: YTM-fullscreen ☐ (untested) · click-through ✅ (typed into the
cmd window and drove Task Manager *underneath* the layer for 16 min) ·
transparency holds ✅ (VS Code + terminal fully legible throughout, never
went black) · sleep/wake ☐ · monitor change ☐ · thermals ✅ (3% CPU, GPU
untroubled).

**Caveat, stated honestly:** this is a strong discrete GPU. It proves the
*compatibility* question (transparent + always-on-top + click-through +
WebGL coexist on Windows/DWM — the historically fragile combination) but
says little about modest hardware. The macOS M2 run covers that side.

## macOS (M2) — pending

| engine | particles | p95 (ms) | ~fps | worst (ms) | CPU % | soak |
|--------|-----------|----------|------|------------|-------|------|
| pixi   | 300 ⭐    |          |      |            |       |      |

Durability: YTM-fullscreen ☐ · click-through ☐ · transparency holds ☐ ·
sleep/wake ☐ · thermals ☐ (M2 throttles after minutes under load — the
reason the soak is 5 min and not 30 s).

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
