# fx-layer spike (Faz 6.5 P3) — measurement runbook

Answers ONE question with numbers: can a **separate transparent,
click-through, always-on-top Electron window** run a particle layer at
60 fps without eating the machine? The result gates P7 (the real effect
layer). Dev-only: not part of the workspace, never built, never shipped;
`nodeIntegration` is deliberately on here so `require('pixi.js')` works
without a bundler.

## Setup (once, per machine)

```
cd apps/overlay/spikes/fx-layer
npm install
```

## The ~12-minute path: ONE cell, BOTH platforms

Spend the budget on PLATFORM COVERAGE, not on matrix cells. Rendering a
transparent, always-on-top window is an OS-specific job — Windows goes
through DWM + ANGLE→D3D, macOS through CoreAnimation + ANGLE→Metal — and
Kashi's own bug history is proof: every rendering/transparency bug so far
(occlusion fade, disk-cache locks, DPI drag creep, the monitor-HDR mystery)
was Windows-only, while macOS produced a different class entirely (window
clamping, signing). A pass on one platform says almost nothing about the
other.

**Run Windows FIRST** — it carries the risk (transparency × hardware
acceleration is the historically fragile combination there). If Windows
passes, macOS very likely passes too (friendlier compositor, strong GPU);
the reverse does not hold.

On EACH machine, one cell, ~5 minutes (the HUD counts and flips to
"SOAK DONE"). Walk the durability checklist below WHILE it runs, then quit
with Ctrl+C.

```
npm start -- --mode=pixi --particles=300
```

Why not 30 seconds: Kashi's history is full of "fine at first, degrades
later" bugs, and an M2 only starts thermal-throttling after a few minutes
under load. The soak is not idle waiting — it is when you run the checklist.

Note two numbers: **p95** from the HUD, and the spike's **process CPU**
(macOS: Activity Monitor / Windows: Task Manager, the `electron` group).


### Optional extras (NOT needed to decide — tuning detail only)

Run these later, and only if you want headroom/fallback data:

```
npm start -- --mode=pixi   --particles=1000   # how much headroom is left
npm start -- --mode=canvas --particles=300    # the fallback engine's baseline
```

If the pixi@300 cell FAILS, the canvas@300 cell becomes worth running —
it answers "is it WebGL that's unhappy, or transparent-window rendering in
general?"

## Durability checklist (once per machine, any mode @300)

While it runs:

- [ ] YTM full-screen: particles stay visible ON TOP, no flicker
- [ ] Clicks pass through everywhere (click a window/desktop icon under it)
- [ ] Transparency holds — the layer never turns into a black rectangle
- [ ] Sleep → wake: comes back rendering (no crash, no black box)
- [ ] Monitor unplug/replug or resolution change: survives
- [ ] GPU temperature/fan: nothing dramatic (subjective is fine)

## GO gate (fixed BEFORE measuring — plan P3)

At **300 particles**, on BOTH machines (Windows saha + Mac dev):

- p95 frame time **< 16.7 ms** (60 fps), AND
- spike process CPU **< 10%**, no meaningful GPU heat, AND
- transparency + click-through + always-on-top survive the 5-min soak, AND
- sleep/monitor changes don't crash it.

Any miss ⇒ **NO-GO**: P7 drops, the DOM span-pool pattern (P1/P2) stays
the permanent path. "It failed" is a perfectly good spike outcome.

## Recording results

Paste numbers + checklist into `docs/research/fx-layer-spike-2026-07.md`
(template ready) — or just send them in chat and Claude will fill it in.
