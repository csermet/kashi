# Per-Line Soft-Offset Spike — NO-GO (Faz 6 P2, 2026-07-18)

**Verdict: NO-GO.** The productionize package (P8, pipeline 2.8.0) is
dropped. Bench harness stays (`benchmarks/softoffset.py`,
`--line-postprocess`) for future re-runs.

## Hypothesis

On the windowed path, a meaningful share of word-start error might be
line-common drift: the whole line sits early/late relative to its lrclib
anchor while intra-line word spacing is roughly right. If so, shifting each
line (rigidly, words included) by a smoothed anchor delta should recover
word precision that production line-QA (which only hard-snaps >2.5 s
outliers) leaves on the table.

## Design

- Bench-only transform after `align()`, before trim: per-line delta =
  anchor − CTC line start, median-filtered (window 5); two modes —
  `soft-median` (step: each line takes its filtered delta) and `soft-pl`
  (piecewise-linear interpolation at line starts). Lines stay rigid; a
  monotonic clamp forbids order inversions.
- Matrix: {none, soft-median, soft-pl} × anchor jitter {0, 250, 500} ms,
  jamendo subset8, full-mix, windowed (star_frequency edges), current
  pipeline. Jitter simulates crowd-sourced lrclib stamp noise.
- **Honest-evaluation note:** the original acceptance idea ("cases PCO
  improves") was CIRCULAR — case metrics compare against the same lrclib
  anchors the offset derives from. The gate was moved to the Jamendo
  word-level axis before any run: GO required PCO@0.3 ≥ +5 points at
  jitter 250 AND no regression elsewhere.

## Results (jamendo subset8, word starts)

| config | MAE ms | PCO@0.3 |
|---|---|---|
| none j0 | 176.5 | **0.8916** |
| soft j0 | 171.2 | 0.8739 |
| none j250 | 198.0 | **0.8930** |
| soft j250 | 199.5 | 0.8687 |
| none j500 | 257.7 | **0.8807** |
| soft j500 | 305.1 | 0.7557 |

(`soft-median` and `soft-pl` produce identical numbers here: every jamendo
line carries an anchor, so interpolation at anchored line starts collapses
to the step function. The distinction only matters with sparse anchors.)

## Why it fails

1. **Line-start error and word error are largely independent.** Even with
   PERFECT anchors (j0) the correction lowers PCO@0.3 by 1.8 points: after
   the windowed+edges pipeline, lines whose starts drift a little still
   carry words that are individually better-placed than a rigid shift of
   the whole line. The line-common component the hypothesis needed was
   already captured by windowing + line-QA.
2. **Noise injection dominates at realistic jitter.** At j500 the transform
   feeds anchor noise straight into word timings: PCO collapses 88.1 →
   75.6 and MAE inflates 258 → 305 ms — the exact failure mode the
   plan's risk section predicted, now measured.
3. The measured gate was **−2.4 points at j250** against a +5 requirement.

## Consequences

- P8 (soft-offset productionize) is dropped from Faz 6.
- The remaining word-precision levers are the known ones: human word data
  (lyricsfile — grown by OUR publishes, the "listen→like→report" loop) and
  future alignment-model work. The windowed quality metric now says so
  honestly via `quality_basis` (P1).
- Run artifacts: `benchmarks/results/2026-07-18-p2-*.json` (9 runs).
