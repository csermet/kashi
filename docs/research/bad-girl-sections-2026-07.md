# Why BAD GIRL (UJ6mMotRd1M) has zero energy sections

*2026-07-19 — Faz 6.5 P0 investigation. Verdict: behavioral limit of the
energy proxy confirmed; the fix is real structure analysis (P6/allin1).*

## Live data (document @ pipeline 2.8.0)

Energy envelope: 330 samples @ 2 Hz (~165 s). Distribution of published
values (0–1 scale): min 0.00, p25 **0.88**, p50 **0.94**, p70 **0.97**,
p90 0.99, max 1.00, std 0.131.

Re-running the exact `energy.py` section mechanics (3 s moving average,
70th-percentile threshold, ≥8 s minimum) on the published curve:

- smoothed P70 threshold = **0.963**
- above-threshold runs: 9, durations (s): 8.0, 7.5, 7.5, 7.5, 7.0, 6.0,
  4.0, 2.0, 0.5
- longest run sits exactly at the 8 s boundary on the quantized ints; on
  the unquantized float curve the worker saw it fall just short → 0
  sections survive the filter. `sections` is absent from the document.

## Root cause

The master is heavily compressed ("loudness war" brickwalling): the track
plays near its own ceiling almost throughout, so the per-track-normalized
envelope saturates. The 70th-percentile threshold then lands *inside the
plateau's noise band* — the smoothed curve crosses it every few seconds,
fragmenting the "high" state into 2–8 s slivers that all miss the 8 s
minimum. No parameter tweak fixes this class cleanly: lowering the
threshold or the minimum would spray sections across quiet tracks.

## Disposition

Known behavioral limit of the energy-derived chorus proxy (documented in
`energy.py`'s docstring as a v1 limitation). The overlay degrades
gracefully (energy ramp still works; no section-driven dynamics). The real
fix is functional structure analysis — Faz 6.5 **P6** (`structure` extra,
allin1); this track is P6's acceptance case: *BAD GIRL must yield ≥1
meaningful section*.
