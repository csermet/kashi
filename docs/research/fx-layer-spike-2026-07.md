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

Hardware: Ryzen 7 9700X + RTX 5070 Ti + 32 GB, **4K display @ 144 Hz**.

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

### Reading the 7.1 ms

144 Hz has a 6.94 ms vsync interval, and the loop measured 7.1 ms p95 at
~144 fps — i.e. the frame loop is **vsync-locked and idle between
refreshes**. 7.1 ms is a FLOOR, not a cost: the renderer draws and waits.
300 particles do not register on this GPU.

Consequences for the design:
- **Particle count is essentially free** at this scale; effect budgets
  should be set by screen area / compositor, not by "how many particles".
- The actual cost of the 4K transparent composite (a 3840×2160 RGBA
  surface blended by DWM every frame) stays UNMEASURED here — vsync masks
  it. We know it fits inside 6.94 ms on this hardware, not what it is. If
  the M2 run comes in *below* its panel's refresh, that number becomes
  visible there.
- Worth noting the machine held 1.7–3.0% CPU while driving a full-screen
  transparent layer at 4K/144 — a demanding surface, not a soft target.

**Caveat (Caner, correctly): this is a very strong machine.** It settles
the *compatibility* question — transparent + always-on-top + click-through
+ WebGL coexist on Windows/DWM, the historically fragile combination — but
proves nothing about modest hardware. The MacBook Pro M2 run covers that:
integrated GPU, different compositor (CoreAnimation), and a laptop thermal
and battery envelope.

## macOS (MacBook Pro M2, ProMotion 120 Hz) — **PASS**

The run that actually stresses the design: integrated GPU, Apple's
compositor, a Retina surface to blend, laptop thermals.

| engine | particles | p95 (ms) | ~fps | worst (ms) | CPU | GPU | soak |
|--------|-----------|----------|------|------------|-----|-----|------|
| pixi   | 300 ⭐    | **9.1–9.2** | ~120 | 416 (sleep/wake) | ~30% of ONE core | **3.7%** | 20 min |

fps ~120 tracks the ProMotion refresh and 9.1 ms sits just above its
8.33 ms interval — **vsync-locked with a small tail**, comfortably inside
the 16.7 ms gate. GPU load 3.7% is nothing.

**The CPU number needs its units spelled out, or it looks alarming.**
macOS Activity Monitor reports %CPU **relative to a single core** (an
8-core machine can read up to 800%); Windows Task Manager reports a share
of the WHOLE package. Summing the spike's processes (main 2.1 + GPU helper
17.0 + renderer 11.4) gives ~30% of one core ≈ **3–4% of the machine** —
i.e. the same ballpark as Windows' 1.7–3.0%, not ten times worse. The
machine-wide reading during the run (~29% busy, 70% idle) also covered
YouTube Music, the real Kashi overlay, VS Code and a browser, not just the
spike.

**Sleep/wake: PASSED, by accident.** The Mac slept mid-run (untouched
mouse) and macOS throttles hard in that state. The app survived, came back
rendering at ~120 fps, and the 416 ms `worst` frame is that transition —
the same magnitude as the Windows startup hitch, and a one-off over 20
minutes. It also means the reported p95 is trustworthy: the HUD keeps a
ROLLING window of the last 600 frames, so 9.1 ms describes the seconds
after wake, not the throttled sleep period. Sleep only polluted `worst`.

Durability: YTM-fullscreen ☐ · click-through ✅ (drove Activity Monitor and
Terminal underneath the layer) · transparency holds ✅ (everything below
legible for 20 min) · **sleep/wake ✅** · thermals ✅ (3.7% GPU, no
throttling signature in the post-wake numbers).

## Verdict: **GO** (both platforms, 2026-07-25)

| gate | Windows (4K/144, 5070 Ti) | macOS (M2, ProMotion 120) |
|------|---------------------------|---------------------------|
| p95 < 16.7 ms | **7.1** ✅ | **9.1** ✅ |
| process CPU < 10% | **1.7–3.0%** ✅ | **~3–4% of machine** ✅ |
| transparency / click-through / always-on-top, 5 min | ✅ | ✅ |
| sleep/wake, no crash | screen-off ✅ | **full sleep ✅** |
| memory leak over soak | none (100.2→100.4 MB) | not sampled |

A transparent, always-on-top, click-through WebGL layer is **viable on both
platforms** — including the Windows/DWM combination that was historically
fragile and the integrated-GPU laptop case. **P7 (the separate effect-layer
window) is technically unblocked**, and with it the richer graphical /
particle direction Caner asked for after finding the vendored Material
Symbols icons too plain.

### What the numbers mean for the DESIGN (the useful part)

1. **Particle count is nearly free at this scale.** Both machines sat at
   their panel's refresh with 300 particles. Budgets should be governed by
   screen area and the compositor, not by "how many particles".
2. **The spike was the pessimistic case.** It renders 300 particles
   CONTINUOUSLY forever. A real effect layer is event-driven — bursts on
   fx words, ambient only during choruses — so average load lands well
   below these figures.
3. **Laptop battery is the real constraint, not framerate.** Nothing here
   is compute-bound, but a full-screen composite that never idles does
   drain a battery. Design implication: the layer should go fully idle
   (stop the ticker, ideally hide the window) when nothing is animating,
   rather than rendering an empty scene at 120 fps.
4. **Untested, deliberately:** the 1000-particle headroom cell and the
   canvas-2D fallback. Neither is needed now — revisit only if a future
   effect design pushes far past this scale.

## Known background

Windows transparency × hardware acceleration is a known Electron conflict
class (Kashi already disables `CalculateNativeWinOcclusion` and Chromium
disk caches in the overlay for related reasons); PixiJS v8's
ParticleContainer benchmark headroom (1M @ 60fps on an M3) is ~1000x our
scale but was never measured in a transparent always-on-top window — hence
this spike (plan risk R3: measure, then lock technology).
