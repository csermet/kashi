# Dead ideas — measured and rejected

Ideas that were tried **and measured** and did not survive. Each line says what was tried, what
the measurement showed, and where the full record lives. Do not re-propose one of these without
new evidence (a new data set, a new model class, a changed constraint) — and say which.

## Word timing / alignment

| Idea | Result | Record |
|---|---|---|
| Snap word starts to vocal onsets | Onset distance does not separate right from wrong words (hits 40 ms / band 48 ms / misses 55 ms); every usable policy scored below baseline (0.597 → 0.54-0.59). Onsets stay as **evidence** in the arbiter, not as a corrector. | faz-9-devir-askerlik-2026-08.md |
| Per-song offset | Oracle ceiling +0.016 PCO@0.1, helps 3 of 20 songs. | faz-9-devir-askerlik-2026-08.md |
| Position-based extra offset (line-first word) | Best remaining +10 ms, +0.005 PCO — noise. | faz-9-devir-askerlik-2026-08.md |
| "Inflated gap" clamp on line-first words | In-sample 17.1% → 8.9% early, but leave-one-song-out gets **worse** (29.5% → 23.6% of moved words; median −222 ms → +183 ms): early errors become late errors. | faz-9-devir-askerlik-2026-08.md |
| Hunting tail / gross errors | 15 of 22 broken lines are ad-libs or exact repeats (ambiguous from text alone); true error 7/868 = 0.8%. | faz-9-devir-askerlik-2026-08.md |
| Beat grid | 87,063 words at beat / 8th / 16th: median 0.235-0.255 vs random 0.25 — words do not sit on the grid. | faz-9-devir-askerlik-2026-08.md |
| Repetition median | Same line ×8: 7 of 8 repeats are wrong **the same way**; a median confirms the wrong answer. | faz-9-devir-askerlik-2026-08.md |
| "Empty line + long tail" signature, document-level suspicion ratio | Present in broken and healthy lines alike; ratios do not separate songs. | faz-9-devir-askerlik-2026-08.md |
| Rigid per-line shift toward the lrclib anchor (soft offset) | −2.4 points PCO@0.3 at realistic jitter (needed +5). | soft-offset-spike-2026-07.md |
| Separation mix-back 0.15 | Measurably hurts alignment; default is 0. | hizalama-v2-benchmark-2026-07.md |
| Re-calibrating the CTC quality ramp | Score vs true PCO@0.3: Pearson +0.36. Constants are not the problem; mean CTC probability does not carry the information. The score is a coarse gate (0.2), not a judge. | hizalama-zinciri-durum-2026-08.md |
| "Vowel-initial words are late because note onsets land on vowels" | Refuted by measurement (vowel +112 / fricative +87 / plosive +62 / sustained +59 ms). The per-class correction stays because it was measured; the explanation was dropped. | faz-8-degerlendirme-ve-faz-9-plani.md |
| Second opinion from a weaker aligner (Qwen3-ForcedAligner, windowed) | Independent enough (P1 passed) but false alarms 10.3% > 5% bar; no sweep rescued it. A second aligner weaker than the primary cannot give a clean suspicion signal. | this file (Faz 8.1 measurement; its hand-off plan was lost) |
| Second opinion from the same model family (XLS-R 1B vs MMS) | Correlation +0.945 — not an independent witness. | this file (Faz 8.1 round 4) |
| Fine-tuning on song data | NO-GO: no legally usable singing data; pseudo-labels inherit our own bias. (Label-prior LoRA on speech data remains a conditional option.) | denetim-ve-strateji-2026-08-12.md |
| Timestamp adaptation in the overlay (`t' = a·t + b` between edits) | Mathematically unsolvable client-side: only the target's total duration is known (one equation, two unknowns; trailing silence changes the total but not the map). Needs ≥2 independent anchors on the target timeline → server-side only. | roadmap.md (constraint) |
| Client duration as a video-vs-song selector | Dead end; shipped behaviour stays honest-fail + upload escape. | video-song-substitution-memo.md |

## Lyrics coverage

| Idea | Result | Record |
|---|---|---|
| YouTube auto-captions for songs lrclib misses | 8 of 8 misses had no captions at all (manual or automatic). | this file (2026-08-13 coverage audit) |
| "More audio is better" for by-ear matching | Three 30 s slices scored worse than one 45 s slice (+0.088 vs +0.134): spreading reaches instrumental regions. | this file (by-ear rung, server 0.29.2) |
| Tuning a threshold on easy examples only | A 0.30 by-ear threshold would have rejected the cover the rung exists for (Heathens 0.296). Calibrate on the hard case. | this file (by-ear rung) |

## Measurement traps (they produce fake wins)

- The annotation tool exports `verified=1` on **every** line, but only the lines the annotator was
  pointed at were checked. Measure only the valid region (corrected lines; or up to the last word
  that differs from our output). An earlier PCO of 0.931 was invalid for this reason.
- The Turkish eval set is valid at PCO@0.3 only; PCO@0.1 and MAE cannot be derived from it.
- Pool per song, not per word, and report leave-one-song-out: a fitted number is not a gain.
- Tests written relative to a constant hid an unmeasured gate for months — pin the value.

## Overlay / effects

- A "position envelope" test passed while particles were hidden by the mask: guard
  **visibility** (never-seen fraction), not position.
- The perceptual floor guard (brightness × alpha) does not measure edge sharpness; shape choices
  need their own lock.
- A budget test pinned to a fixed burst count became a vacuum test when a profile changed —
  derive expectations from the budget and the profile.

## Licence-dead (permissive-only dependency policy)

Not usable for shipped models or training: MMS forced-aligner weights and `diffq` (CC-BY-NC),
the Japanese karaoke fine-tune of MMS (inherits NC), DALI / MTG-Jamendo / most singing corpora
for training, madmom weights (CC-BY-NC-SA; use basic-pitch for onsets), `whisper-timestamped`
(AGPL), pykakasi and KoNLPy (GPL-3). Singing-specific aligners are licence-dead as a category
(trained on NC data). The current chain and its measurements: `lisans-temiz-zincir-2026-08.md`.

## Rejected by decision (not by measurement)

- **Tauri instead of Electron** for the overlay: Tauri has no hover-forwarding for click-through windows
  (tauri#6164) and stock macOS "above full-screen" does not work (tauri#11488, not planned). Electron's
  `setIgnoreMouseEvents({forward: true})` + `setAlwaysOnTop('screen-saver')` +
  `setVisibleOnAllWorkspaces({visibleOnFullScreen: true})` are first-class.
- **A separate full-screen effect window**: the overlay stays one fixed window in a free corner of the
  screen; effects draw inside it, around the lyric box.
- **A persistent install id in telemetry**: rejected as a privacy boundary; device inventory is derived
  in SQL (`DISTINCT ON (reported_by, os, arch, display_size)`).

Also rejected earlier: `allin1` (allin1-viability-2026-07.md), the embedding line-theme layer as a
default (embed-threshold-calibration-2026-07.md).
