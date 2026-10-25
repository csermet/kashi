# The windowed clock — four GPU rounds and what shipped (Faz 9, 2026-10-23)

**Question.** The windowed aligner ran on a 21 ms frame step where the model's
real hop is 20 ms, and windows pushed the next line's opening words onto a word
parked in their padding (seam piles). Both fixes existed behind switches since
7cf1f26. Do they make timings better on human-annotated audio, and what do the
lateness offsets become once the clock is right?

**Verdict.** Yes, and by more than any change since windowed alignment itself.
Shipped as pipeline **2.28.0** (server 0.32.0): exact stride and seam yield on by
default, plus a 70 ms windowed lead. The 2026-08 "+80 ms lateness of singing"
turned out to be mostly the 21 ms clock: on the exact clock MMS needs no offset
to speak of over the whole track (best within 30 ms of 0), and the English
table changes sign on the windowed path.

| | before (2.27, production) | after (2.28 + refitted table) |
|---|---|---|
| English, windowed, PCO@0.1 / @0.3 | 0.585 / 0.902 | **0.731** / 0.905 ¹ |
| English, whole track, PCO@0.1 | 0.775 | **0.835** (held-out) |
| MMS deu/eng/fra/spa, windowed, PCO@0.1 / @0.3 | 0.568 / 0.917 | **0.758** / 0.930 |
| MMS windowed, mean word MAE | 189 ms | 157 ms |
| seam piles, 79 songs (English 20) | 15 lines (3) | 3 lines (0) |

¹ The shipped configuration (whole-track table plus the 70 ms lead), scored post
hoc on the r2 dump; the dedicated windowed fit r4 scored 0.733 / 0.905. The MMS
row is a real run of the shipped configuration (r5-all79: windowed MMS takes
exactly the lead). A confirmation run of the released code is listed under Open.

## Setup

Caner's PC (RTX 5070 Ti), `benchmarks.run --dataset jamendo --separation kim-base
--mixback 0 --dump-words`, windowed with anchor jitter 400 ms unless noted.
Every baseline was re-run rather than read from old result files (the 2026-08
files used mixback 0.15). Results: `apps/server/benchmarks/results/2026-10-23-*.json`
(commits 2239a0b, 158e709, 3d485a5, 5bcd85d).

| run | clock | seams | offsets | set |
|---|---|---|---|---|
| r0 | library (21) | clamp | production | eng20, all79, pop, tr |
| r1 / r1y | exact | clamp / yield | production | eng20, all79, pop |
| r2 | exact | clamp | off | eng20 (refit per initial class) |
| r3 / r4 | exact | clamp / yield | refitted | eng20 (j400 and j0), pop |
| r4-tr-raw, r5-tr | exact | yield | off, +80 | tr |
| r5-all79 | exact | yield | +70 (MMS) | all79 |
| w0 / w2 | whole track | — | production / off | eng20, all79 (w2) |

## Findings

1. **The clock fix only works together with new offsets.** The exact clock with
   the old table (r1) drops English PCO@0.1 to 0.35: the old table pulled every
   word 80-110 ms earlier to undo a lateness that was mostly the stretch. With
   the correction off (r2) the exact clock alone scores 0.689, and the model is
   now EARLY on the windowed path (median −35 ms English, −42..−88 ms MMS).

2. **Windowed and whole-track alignment disagree by a constant ~70 ms.** Fitted
   separately, the best offsets differ by −50..−70 ms for MMS across the four
   languages and by −70..−80 ms in every initial class for the English
   checkpoint. The whole-track path is slightly LATE (English +32 ms median). So
   one table cannot serve both paths unless one path carries a constant. The
   cause is not isolated: the paths differ in star topology (`edges` vs
   `segment`) and in how much context the model hears. Shipped as
   `alignment.WINDOWED_LEAD_MS = 70`, riding with the exact stride; the
   configured offsets now describe the model over the whole track. Checked post
   hoc on the English dumps: the whole-track table plus the lead scores 0.7308
   windowed against 0.7322 for a dedicated windowed fit, and the lead's optimum
   is 70 (50: 0.710, 60: 0.723, 80: 0.729).

3. **The refitted English table** (whole track, leave-one-song-out, w2-eng20-raw):
   base −50, fricative −50, plosive −30, sonorant −20, vowel −80 ms; held-out
   PCO@0.1 0.835 against 0.765 uncorrected and 0.775 for today's table. It goes
   into the deployment's `ALIGN_MODELS` English entry with this release. MMS and
   Turkish keep no offset (MMS whole-track optima: deu 0, eng −20..−30,
   fra/spa +10..+20).

4. **Seam yield is safe and does its job.** r3 and r4 score the same on English
   (0.7322 / 0.7329); piles drop to zero on English and pop and 15 → 3 over all
   79 songs. With anchor jitter 0 the English number is 0.746.

5. **Pop valid region (278 words):** gross errors (> 500 ms) per song are never
   above the baseline (Rock It 0/0, Shape 6/6, Uptown 16 → 14, Safari 1/1,
   Stressed Out 2/2), pooled PCO@0.1 0.529 → 0.597, @0.3 0.892 → 0.888 (one
   word). The song-averaged @0.1 dips (0.579 → 0.545) on the two smallest regions
   (Shape of You: 22 words) — noise at that size, recorded rather than explained.

6. **The Turkish set cannot judge a clock change (scope trap).** In 7 of its 10
   songs, 92-99 % of the words were never touched by the annotator: their "truth"
   is the 2.15/2.19 windowed output itself, stretched clock included (compared
   against `eval/packets`; truth follows the old clock with slope 0.58 against
   0.17 on JamendoLyrics). The old clock therefore scores itself (r0-tr 0.716 @0.1).
   On the 175 hand-corrected words only, the exact clock with +80 ms scores
   0.549 against 0.434 @0.1 and 0.783 against 0.800 @0.3 (within noise at that
   size); the shipped lead gives Turkish +70 on the windowed path.

7. **Lead for later: whole-track MMS is far more precise at 100 ms.** Without
   any offset, whole-track MMS scores PCO@0.1 0.934 (deu), 0.872 (fra), 0.913
   (spa) against 0.836 / 0.765 / 0.758 windowed. Windows exist for robustness
   (over the whole track two English songs average more than 5 s of error;
   windowed, the worst averages 2.6 s), but the `edges` star topology was chosen in 2026-07 on the 21 ms
   clock ("segment cost ~0.13 PCO"). Re-measuring `segment` stars inside windows
   on the exact clock is the obvious next experiment.

## What shipped (2.28.0)

- `EXACT_STRIDE_DEFAULT = True`, `SEAM_POLICY_DEFAULT = "yield"`,
  `WINDOWED_LEAD_MS = 70` (windowed path only, with the exact stride). Switched
  off, the windowed output equals 2.27 byte for byte, and the whole-track path is
  unchanged with the new defaults (seeded differential harness, 4000 scenarios,
  612 whole-track). Nine mutations caught by `tests/test_windowed_align.py`,
  including stamps the window planner declines (they take no lead).
- `shift_ms` carries the lead, so the arbiter's raw clock (`start − shift_ms`) is
  the model's own exact clock. Line QA and arbiter decisions move with it.
- The benchmark's defaults follow the pipeline's (`--no-exact-stride` and
  `--seam-policy clamp` reproduce 2.27); result metadata records the lead.
- `benchmarks.lateness` fits over −200..+200 ms and flags a fit on the grid
  edge: the old −200..+50 grid returned +50 for the early fra/spa windows.

**Rollout.** Image and the English table belong in the same deploy. If the table
lags, English windowed scores 0.670 (old table plus the lead) — still above
today. The table without the image costs English windowed 0.585 → 0.559, so do
not apply it first. After rollout: inspect the first finished document, then
re-process the 63 seam-pile documents as a PILOT and compare each document's
`alignment.qa` counts (flagged, density-dropped, nudged) and worded lines with
its 2.27 version before the rest of the windowed archive. Reason: line QA's
arbiter judges flagged lines on the aligner's raw clock (`start − shift_ms`), and
its onset tolerance (200 ms) and support threshold (0.34) were calibrated on the
21 ms clock. The raw clock is now the exact one, on average ~130 ms earlier on
English windows. No benchmark round ran line QA, so this is unmeasured.

## Open

- First-document inspection and the 63-document pilot (Rollout) before the wave;
  if dropped lines rise, re-calibrate the arbiter's onset gate on the exact clock.
- A confirmation run of the released code (defaults on, refitted English table)
  for result files of the shipped configuration.
- Per-language MMS leads (windowed optima deu +50, eng +40, fra/spa +80) — one
  value serves all four today. Japanese and Turkish take the lead unmeasured.
- The star-topology experiment (finding 7).
- A Turkish truth that is independent of the pipeline (hand-correct at 100 ms
  precision, or mark only touched words as verified, as `pop_valid.py` does).

## Round 5 — star placement inside windows (2026-10-25)

Finding 7 asked whether the windows' `edges` star topology costs precision. Run
with the new `--window-star` switch (9272d7a), correction off, both modes on the
exact clock (results `2026-10-25-x-{edges,seg}-*`, commit dedc422). Each mode gets
its own fitted offset; leave-one-song-out:

| set | edges @0.1 / @0.2 / @0.3, >500 ms | segment |
|---|---|---|
| English 20 (jg1b) | 0.709 / 0.863 / 0.904, 5.0 % | 0.770 / 0.876 / 0.900, 7.3 % |
| MMS, 4 languages | 0.767 / 0.893 / 0.929, 3.8 % | 0.839 / 0.893 / 0.908, 6.9 % |

`segment` places a word it finds more precisely, but nearly doubles the gross
errors: PCO@0.2 (the roadmap's primary metric) is flat and @0.3 drops. Not
shippable alone.

**Per-word hybrid** (simulated from the two dumps): take the `segment` time when it
lies within T of the `edges` time, otherwise keep `edges`. Held out, T = 400 ms (the
middle of a flat 300-500 ms plateau):

| set | edges | hybrid T400 |
|---|---|---|
| English 20 (jg1b) | 0.709 / 0.863 / 0.904, 5.0 % | **0.779 / 0.891 / 0.916**, 5.1 % |
| MMS deu | 0.836 / 0.934 / 0.962, 1.7 % | **0.909 / 0.958 / 0.968**, 1.7 % |
| MMS eng | 0.708 / 0.852 / 0.902, 5.2 % | **0.776 / 0.875 / 0.906**, 5.4 % |
| MMS fra | 0.765 / 0.885 / 0.918, 5.1 % | **0.858 / 0.909 / 0.926**, 5.3 % |
| MMS spa | 0.758 / 0.901 / 0.934, 2.8 % | **0.890 / 0.937 / 0.945**, 2.9 % |

+7 to +13 points at 100 ms, +2 to +4 at 200 ms, @0.3 up, gross errors flat. The pop
valid region (278 words) is neutral: pooled @0.1 0.647 -> 0.640, @0.3 0.892 ->
0.896, gross errors 23 -> 23.

**Mechanism check.** `segment` windows want the whole-track table, not the lead:
fitted with the 70 ms lead included they land at −120 ms (English; net −50 = the
whole-track base) and −80/−90/−50/−70 (MMS; net −10..+20 = the whole-track
optima). The windowed lead is a property of the `edges` topology, as suspected.

**What it would take** (a "large improvement" under the refresh policy): per window
one emission pass and two Viterbi passes (emissions dominate alignment cost);
`segment` words take the configured table, `edges` words the table plus the lead;
per-word selection at T = 400 ms; a GPU confirmation run of the real code; a
pipeline minor bump; then the GPU helper worker (phase 9.3) and one full refresh.
