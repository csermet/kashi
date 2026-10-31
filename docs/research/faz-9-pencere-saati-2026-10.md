# The windowed clock — six GPU rounds and what shipped (Faz 9, 2026-10-23 / 10-26)

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
- ~~A confirmation run of the released code~~ — done for 2.29.0 (round 6).
- ~~Per-language MMS leads~~ — moot since 2.29.0: the hybrid and the vote place
  most words on the whole-track clock, where MMS wants 0..+10 ms (round 6).
  Japanese and Turkish take the lead and the hybrid unmeasured.
- ~~The star-topology experiment (finding 7)~~ — rounds 5-6, shipped as 2.29.0.
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

**Third vote: the whole track** (simulated with the round 3/4 whole-track dumps; held
out). After the hybrid, take the whole-track time when it lies within 400 ms of it:
English 20 0.779/0.891/0.916 -> **0.804/0.902/0.920**; MMS deu 0.909 -> 0.932, eng
0.776 -> 0.798, fra 0.858 -> 0.873, spa 0.890 -> 0.904 at 100 ms; +1 point at 200 ms;
gross errors flat. One extra whole-track pass per song: cheap on a GPU, about a
minute on the CPU worker. It belongs in the same package.

## Round 6 — the hybrid and the vote in the real code (2026-10-26)

**Implementation, opt-in** (`window_star="hybrid"`, `whole_vote=True`; defaults
unchanged, so the pipeline version is too). Per window one emission pass and two
Viterbi passes (`alignment._align_slice_hybrid`; ctc_forced_aligner's emissions
are text-independent and its Viterbi does not mutate them). Per word
`windows.hybrid_star_words` keeps the `segment` time where it lies within 400 ms
(inclusive, whole ms) of the `edges` time plus the 70 ms lead; after the seam
passes `windows.whole_track_vote` does the same against one whole-track pass,
then the seams are re-clamped. Word probs stay the `edges` ones (line QA and the
quality gate were calibrated on them); only timings move.

**One clock.** The first draft carried the lead per word (`edges` +70, `segment`
and whole-track 0) and applied it at the aligner's exit. Review found the seam
passes, regrouping's end clip, the Japanese mora fold and the line-mode fallback
all compare raw starts across rows — on mixed clocks an `edges` word yielded in
front of a `segment` word came out as a zero-length pile again. Shipped instead:
`segment`- and whole-track-placed rows are moved BACK by the lead onto the
`edges` clock as they are chosen, and the lead stays path-wide as in 2.28. Every
pass then compares like with like, and the arbiter's raw clock (`start −
shift_ms`) is the `edges` clock for every word, the clock its onset gate was
last calibrated against.

**Proof.** Switched off, the windowed output equals 2.28.0 byte for byte (seeded
differential harness, 4000 scenarios incl. disagreements exactly on 400 ms after
the lead; whole-track identical under every setting; hybrid and vote never
raise). 24 mutations caught by `tests/test_windowed_align.py` and
`test_alignment_regroup.py`.

**Parity with the simulation** (CPU, one German song from the all79 set, MMS,
`kim-base`, the round 5 settings; results not committed — one song is a
check, not a measurement). `edges`: 367 of 367 word starts equal the round 5
GPU dump exactly. Hybrid: 97.5 % of words within 25 ms of the simulated hybrid
built from the two dumps; with the vote 98.6 % (the rest is the seam passes,
which the simulation never ran on the chosen times). 331 words placed by
`segment`; with the vote 344 taken from the whole track. PCO@0.1 without
offsets on that song: 0.861 `edges` → 0.891 hybrid → 0.924 with the vote.

**GPU confirmation** (Caner's PC, results `2026-10-26-h-*`, round 5 settings:
`kim-base`, jitter 400, correction off; LOSO single offset as in the
simulation; the `edges` baselines are the `2026-10-25-x-edges-*` files):

| set | edges | hybrid | hybrid + vote | simulation (+vote) |
|---|---|---|---|---|
| English 20 (jg1b) | 0.709 / 0.863 / 0.904, 5.0 % | 0.780 / 0.892 / 0.917 | **0.802 / 0.900 / 0.919**, 5.2 % | 0.804 / 0.902 / 0.920 |
| MMS deu | 0.836 / 0.934 / 0.962 | — | **0.932 / 0.966 / 0.972**, 1.6 % | 0.932 |
| MMS eng | 0.708 / 0.852 / 0.902 | — | **0.799 / 0.892 / 0.915**, 5.3 % | 0.798 |
| MMS fra | 0.765 / 0.885 / 0.918 | — | **0.872 / 0.919 / 0.930**, 5.2 % | 0.873 |
| MMS spa | 0.758 / 0.901 / 0.934 | — | **0.904 / 0.942 / 0.949**, 2.9 % | 0.904 |

The real code lands within 0.002 of the simulation everywhere; the vote adds
+2.2 points at 100 ms on English. Of 5693 English words the vote took 5276,
`segment` placed 184 and `edges` kept 233.

**Tables.** `benchmarks.lateness --by-initial` on the English run: base −50,
fricative −50, plosive −20 (shipped −30), sonorant −20, vowel −80, held out
0.816; the SHIPPED table scores 0.817 on the same run (fixed, so held out by
construction) — it stays. MMS per language: deu 0, fra +10, spa +10, eng −30;
under the 20 ms bar, so MMS keeps no offset. The rollout is the image alone.

**As shipped** (the production tables, per-song means): English 0.731 / 0.865
/ 0.905 → **0.817 / 0.901 / 0.920**, gross 5.1 % → 5.1 %; MMS @0.1 deu 0.825 →
0.932, fra 0.759 → 0.863, spa 0.762 → 0.902 (@0.3 up in all). Pop valid region
(278 words, `pop_valid.py`): @0.1 0.622 → 0.633, @0.3 0.896 → 0.899, gross 23 →
22. With a single English offset instead of the table, pop reads 0.612 → 0.586
at 100 ms: five songs, 278 words, about seven words either way.

## What shipped (2.29.0)

- `WINDOW_STAR_DEFAULT = "hybrid"`, `WHOLE_VOTE_DEFAULT = True`, both at 400 ms.
  Switched back (`window_star="edges"`, `whole_vote=False`) the windowed output
  equals 2.28.0 byte for byte (4000 seeded scenarios against a0a72b7).
- No table change; the image ships alone (gitops: the tag).
- Cost: one extra whole-track emission pass per song on the worker.
- Archive: a "large improvement" under the refresh policy (+8 to +14 points
  at 100 ms) — one full refresh, on the GPU helper worker (phase 9.3).

## Field finding — 2.29 collapses word durations (2026-10-31)

The owner's ear test on the 20-track pilot set: lines arrive with most of their
words already lit, the last word goes out without lighting, runs of short
words flash by. Counted on the documents (no audio needed):

| 20 pilot tracks, 8 375 words | 2.28.0 | 2.29.0 |
|---|---|---|
| words with duration 0 ms | 2.0 % | 7.6 % |
| words shorter than 60 ms | 6.5 % | 22.3 % |
| runs of 3+ words starting within 60 ms | 18 | 50 |
| word order reversed inside a line | 0 | 0 |

Mechanism, from the round-6 dumps (eng20): words placed by the `edges`
passes keep a median 300 ms (1.5 % under 60 ms); words taken from `segment`
or the whole-track vote keep 100-140 ms (16-33 % under 60 ms). A `<star>`
before every word swallows the silence between words, so each word keeps
little more than its CTC peak. Onsets improved, ends collapsed.

The bench did see it, and nobody read it. `run.py` scores word ENDS too
(`word_ends`, `over_extension`): on the English 20, `edges` → hybrid + vote
moved the end MAE 318.5 → 367.7 ms (median 147 → 180; `2026-10-25-x-edges-eng20`
vs `2026-10-26-h-eng20-raw`). Every decision in rounds 5-6 was read off PCO,
which scores starts only. `over_extension` counts only LATE ends (> 250 ms),
so it even improved (0.057 → 0.026) while the ends collapsed: no metric counts
zero-length words or EARLY ends. (Corrected 2026-10-31; the first version of
this section said the bench scores onsets only.) A cheap repair is
known (onset from the hybrid/vote, end from `edges`, clipped to the next
onset), but it is deliberately NOT shipped yet. Some complaints from the same
listen exist in 2.28 too (a line late by most of a second, a pile of three
repeated words), so the owner chose to build a perceptual audit first:
per suspicious spot, a picture of the separated vocal (spectrogram) with the
word boxes and frames of the rendered overlay, reviewed by the agent, the
owner only listening where it is unsure. Timing work after phase 9 is
re-planned around that tool. The full archive refresh to 2.29 is on hold.
