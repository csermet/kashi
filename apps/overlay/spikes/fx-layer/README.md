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

## Run the matrix

Each cell: start, let it run ~5 minutes (the HUD counts and flips to
"SOAK DONE"), note **p95** from the HUD and the **process CPU** from Task
Manager (Windows: the `electron.exe` group of this spike / macOS: Activity
Monitor). Quit with Ctrl+C in the terminal.

```
npm start -- --mode=pixi   --particles=100
npm start -- --mode=pixi   --particles=300     <- the GO-gate cell
npm start -- --mode=pixi   --particles=1000
npm start -- --mode=canvas --particles=100
npm start -- --mode=canvas --particles=300
npm start -- --mode=canvas --particles=1000
```

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
