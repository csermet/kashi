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
| `segment` star placement alone inside windows | More precise where it finds the word (PCO@0.1 +6 to +7) but gross errors nearly double (English 5.0 % → 7.3 %, MMS 3.8 % → 6.9 %); @0.3 drops. Shipped instead as one half of the per-word hybrid (2.29.0). | faz-9-pencere-saati-2026-10.md (round 5) |
| Client duration as a video-vs-song selector | Dead end; shipped behaviour stays honest-fail + upload escape. | video-song-substitution-memo.md |

## Lyrics coverage

| Idea | Result | Record |
|---|---|---|
| YouTube auto-captions for songs lrclib misses | 8 of 8 misses had no captions at all (manual or automatic). | this file (2026-08-13 coverage audit) |
| "More audio is better" for by-ear matching | Three 30 s slices scored worse than one 45 s slice (+0.088 vs +0.134): spreading reaches instrumental regions. | this file (by-ear rung, server 0.29.2) |
| Tuning a threshold on easy examples only | A 0.30 by-ear threshold would have rejected the cover the rung exists for (Heathens 0.296). Calibrate on the hard case. | this file (by-ear rung) |
| Title rules for a nightcore's original (ordered title equality; "the duration cluster must agree on the artist") | The 9 archive nightcore uploads, 2026-11-10: same-title strangers pass any title rule (Zara Larsson's "Winter Song", Alina Baraz's "Electric (Marian Hill remix)"), and the artist rule refuses only what the CTC gate already refuses plus one right-song document (Nightcall). Both refused uploads' real originals are not in lrclib at all, so no selection rule can bind them. | faz-5-zamanlama-yeniden-plan-2026-11.md, "Nightcore record drift" |
| CTC line scores as a wrong-song detector for stored documents | Share of lines scored below 0.3: wrong-song nightcore documents 0.50 and 0.67, right nightcore documents 0.48-0.65, the other 298 word documents p50 0.49 / p90 0.76. | same |
| Title-only lrclib rung (drop the artist, keep a ±3 s duration) | 3 000 random mirror tracks: a genuinely different artist has the same title within ±3 s for **46.4 %** of them, and exactly one such stranger (the case a "unique match" rule would silently accept) for **14.5 %**; with no duration, 65.8 %. The server's r=1 path has no wrong-song gate (the CTC score is saturated at 1.00 for 51 % of the archive), so alignment would not catch the wrong text either. The two "gaps" it was meant for resolve today; they had failed on lrclib's 503 day. | lyricfix-lrclib-availability-2026-10.md §5 |

## Perceptual audit (Faz 3.5, pre-registered gates)

| Idea | Result | Record |
|---|---|---|
| Wrong-sheet alarm from coverage (A3: a readable unexplained block **and** ≥ 25 % of the singing unexplained **and** ≥ 25 % of the lines unheard) | False alarms on 15 of 79 unmodified Jamendo songs (gate 5 %) and on 3 healthy pilot songs plus the hardstyle negative. The whole-sheet shift check beside it passed its error gate (median 64 ms) and never false-alarmed. | yerel-model-denetim-2026-11.md, "Faz 3.5 results" |
| One-verse shift detection by unique-line sections (A1) | 61 of 140 verse shifts ≥ 5 s found (0.44), against 0.94 for whole-sheet shifts. | same |
| Whisper large-v3-turbo as a lyrics-free second ear (mix, CPU, segment-timed) | Places more lines than the CTC ear (0.645 vs 0.500), but 19.8 % of its text pieces sit where the CTC ear hears no letters (gate 5 %), and one healthy song reads +569 ms. | same |
| Qwen3-ASR-1.7B as a lyrics-free second ear (stem, desktop GPU, segment-timed) | Places more lines than the CTC ear (0.678 vs 0.500) and reads both shifts, but 15.0 % of its text pieces sit where the CTC ear hears no letters (gate 5 %) and two healthy songs read +620 / +1,183 ms. Those pieces are the same ones Whisper fills and mostly carry sheet lines, so the silence rule may be what fails at segment level; new evidence that would reopen it: a finer cut or a per-character silence rule, pre-registered. | same |
| Gemma 4 26B-A4B (local, 16k) as the per-song summarizer | Numeric probe 4-6 of 20 (gate 19), severity Spearman 0.28-0.40 (gate 0.5), run-to-run agreement 0.56 (gate 0.90). | same |
| Opus 5.5 Batches as the per-song summarizer, as gated | Passes recall, precision, severity and the probe in every run, fails only run-to-run agreement (0.77 numbers, 0.78 with pictures; gate 0.90). New evidence that would reopen it: the owner listening-set accuracy, under a new pre-registration of the agreement rule. | same |
| lrclib's word-timed `lyricsfile` as a referee (score pipeline changes on ~100 songs without a listen) | 2026-11-13, gates G1-G5 written before the fetch: independent (0 of 159 usable files match a Kashi document within 20 ms on half their words) and plentiful (159 of 307 archive records) and clean by median (0 ms share 0.59 %), but less accurate than what it would judge: on the pop hand truth (2 songs, 151 valid words) PCO@0.1 0.15 / @0.3 0.60 / gross 14.6 % against Kashi's 0.67-0.77 / 0.99 / 0.7 % on the same words (the accuracy gate itself unmeasurable: 3 of the 5 records are line-only); owner-labelled moments n < 10 per class; by the ear its line starts are no better than 2.31.0's (English pilot 0.145 vs 0.150) and its ranking of versions agrees with `audit.score` 11 times against 8. The files look bulk-made, not crowd-timed (one schema on 158 of 159, byte-identical across 15 records of one song, 121 of 135 inconsistent with lrclib's publish path; not proven). The 2.31.0 decision to stop using the file as a source stands on more than the pilot set. Would reopen: a pre-registered gross-disagreement (> 1 s) alarm test (28 such lines: the ear sides with the file on 14, with the document on 1). | faz-5-zamanlama-yeniden-plan-2026-11.md, "lrclib's word file as a referee"; `scratch/hakem/` |
| Ear-verified record choice: switch to another lrclib record of the same song when it explains ≥ 0.10 more of the whole-song ear text, or when the chosen record places < 50 % of its lines within 1 s and an alternate ≥ 80 % | Drawn from 39 songs, where it switched exactly GOSSIP and Enemy; frozen and replayed 2026-11-13 on 60 fresh archive songs it fired on **0 of 60** (kill criterion K3, pre-registered), also 0 with the mirror's folded title lookup (post hoc). Fresh songs with a bad sheet (3 of 60, medians −20.8 s and −15.2 s) have no better record in lrclib. The clock-scale check from the same work passed its gates (0 false alarms on 109 healthy songs) and its refit fixed the one nightcore case it fired on. Would reopen: a larger candidate source than lrclib. | toptan-gozden-gecirme-2026-11.md, section 3; working files outside the repository |
| Display-side spacing of rushed words as the answer to "it looks like a bug" (borrow free time / even 120 ms steps / 180 ms steps), on the moments the owner complained about | 2026-11-13, six complained moments + one control as four-row clips of the built renderer with the vocal stem: the owner sees no clear difference ("most of these places are badly broken, the word does not arrive right anyway"); only the one clean pile (CEO 1:03) looks clearly better at 180 ms. The overlay keeps a word lit until the next word starts, so lengthening a word's end changes nothing on screen. Archive: 6 080 of 101 957 words are visible for under 120 ms. Not measured: mild crowding inside otherwise well-placed lines, where a small polish may still pay. | same note, "Readability trial"; `scratch/audit/runs/okunurluk/` |

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
for training, madmom weights (CC-BY-NC-SA; basic-pitch was named as the alternative, but its model card lists
non-commercial training sets — read 2026-11-13, check before any use), `whisper-timestamped`
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
