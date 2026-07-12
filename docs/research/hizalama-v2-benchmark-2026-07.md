# Alignment v2 — benchmark analysis & research synthesis (2026-07-11)

Working document for the hizalama-v2 effort (P1 harness + P2 separation).
Data: `apps/server/benchmarks/results/2026-07-11-*.json` (intel = CPU i5/4c,
ryzen = prod worker profile, pc = RTX 5070 Ti). Some PC sweeps were still
running when this was written; the decision table marks them *(pending)*.

## 1. Where the errors actually are (full-mix baseline, 79 songs)

- **47/79 songs already meet the target** (PCO@0.3 ≥ 0.90) with no separation.
- **13 songs are catastrophic** (PCO < 0.70) — 8 of them English. They drag
  the English mean MAE to 4.6 s while the median-of-songs is 315 ms.
- The catastrophic profile is overwhelmingly **local lock loss**: 11 of 13
  have per-song MedAE ≤ 110 ms — most words are fine, one or two sections
  drift by seconds (the TiK ToK field failure). 27 songs total match this
  profile (MAE > 1 s while MedAE < 150 ms).
- Only 2 songs are globally broken (MedAE 8.8 s / 26.9 s — likely
  lyrics/version mismatch; to be checked, possibly excluded as data errors).
- Metadata correlation: **NonLexical** (la-la/scat) songs average PCO 0.703
  vs 0.871 for the rest. Polyphonic: no measurable effect (0.862 vs 0.863).

**Implication:** separation fixes the *word-precision* layer; the remaining
catastrophic tail is exactly the class that line-anchored **windowed
alignment (P3)** targets. If P3 clears the 27-song lock-loss class, overall
PCO lands ≳ 0.96.

## 2. Separation effect (measured)

| config | PCO@0.3 all | eng | MAE mean | MedAE | note |
|---|---|---|---|---|---|
| **kim-melband + mb0** | **0.9313** | 0.8713 | 526 ms | **41 ms** | winner; ~2.1× RT on worker |
| kim-bleedless + mb0 | 0.9311 | **0.8721** | 508 ms | 41 ms | statistical tie with kim |
| bsr-revive-v2 + mb0 | 0.9242 | 0.8581 | 796 ms | 44 ms | |
| bs-roformer + mb0 | 0.9230 | 0.8511 | 786 ms | 45 ms | ~5.2× RT on worker |
| kim-melband + mb0.15 | 0.9206 | 0.8344 | 1292 ms | 508 ms | mixback pattern repeats |
| bs-roformer + mb0.15 | 0.9189 | 0.8339 | 1247 ms | 493 ms | |
| voc_ft + mb0.15 | 0.9163 | 0.8362 | 1055 ms | 310 ms | fails the TiK ToK case |
| bsr-resurrection + mb0 | 0.9048 | 0.8130 | 649 ms | 47 ms | **higher SDR ≠ better alignment** |
| full-mix (baseline) | 0.8626 | 0.7290 | 1998 ms | 496 ms | |

- **Mixback hurts.** Folding 15 % of the original mix back into the stem was
  meant as artefact insurance; on the full set it *worsens* every headline
  number (word MedAE 493 → 45 ms!). The re-added instrumental dilutes the
  clean-vocal advantage the CTC model needed. → P2 default `separation_mixback`
  flips to **0**; the config knob stays as an escape hatch.
- Field cases (raw aligner, ryzen): TiK ToK chorus lock loss **8 → 0 lines
  with bs-roformer** (PASS) but only 8 → 7 with voc_ft; Rick Astley 6 → 3
  (bsr) vs 6 → 11 (voc_ft — separation can *regress* individual songs).
  Subset-8 aggregate had voc_ft slightly ahead (0.955 vs 0.945) — easy songs
  don't discriminate; the tail does.
- Wall-clock (worker profile, 10-cpu limit, thread-oversubscribed — see §4):
  bs-roformer 5.18× realtime (~18 min/song), voc_ft 0.60× (~2 min/song).
  GPU (5070 Ti): bs-roformer 0.27× realtime; alignment 0.011× (~29× CPU).

## 3. Research synthesis (3-agent sweep, 2026-07-11, sourced in agent logs)

**Positioning.** Our measured MMS + BS-RoFormer PCO@0.3 0.919–0.923 on
JamendoLyrics MultiLang is above anything publicly documented on that
benchmark (MIREX 2024 best open submissions: AAE 0.58–0.65). Systems with
better published *English* numbers (DSE, HCLAS-X, DAFx25 CRNN) are closed
in-house-data models with no released code/weights. Verdict: **backbone stays
MMS-CTC; no credible public replacement.**

**Windowed alignment (P3) is validated by literature:** hierarchical
line-then-word alignment is standard in the strongest systems; Demirel 2021
segments at anchors for a second pass; DAFx25's own error analysis recommends
a line-level stage to kill outliers; KGLW's failure analysis shows repeated
lines cause unbounded error propagation — external anchors bound it by
construction. lrclib-as-anchor is novel engineering. Caveats adopted into the
P3 design: generous ±pad, window-level confidence check, whole-song fallback,
overlapping windows (melisma bleeds across line boundaries).

**P5 fallback retargeted:** LyricsAlignment-MTL (2022) is frozen; its README
redirects to **LyricsAlignment-Multilingual** (ISMIR 2025 LBD, DALI-v2,
checkpoint released). Gate condition unchanged (only if windowed MMS misses
MAE < 0.2 s / PCO@0.3 > 90 %) — with 0.919–0.923 already measured
pre-windowing, it will likely never fire.

**Separation candidates (verified loadable in audio-separator 0.44.3):**

| model | MVSEP Multisong vocal SDR | CPU cost | note |
|---|---|---|---|
| ep_317 (current default) | 10.87 | 1× | filename's "12.97" is an older test set |
| bs_roformer_vocals_resurrection_unwa | 11.33–11.36 | ~1× | best same-cost upgrade |
| bs_roformer_vocals_revive_v2_unwa | — | ~1× | best *bleedless* score (40.07) — bleed correlates with CTC lock loss |
| **mel_band_roformer_kim_ft_unwa** | ~11.0–11.1 | **~0.3–0.4×** | dim 384/depth 6; roformer-class quality at Voc_FT-adjacent cost |
| UVR-MDX-NET Voc_FT | ~9.7–10.2 | ~0.1× | −1.6 dB vs ep_317; failed the TiK ToK case |

Registry `overlap` gotcha (verified in source): for roformer models overlap is
a **step size in seconds** (default 8 = fastest); copying `overlap=2` from
MSST-style docs makes runs ~4× slower for ~0.05 dB. We use the default.
Optional ~1.5–2× more: `segment_size=401` + `override_model_segment_size` (small
quality hit; A/B first).

**Speed quick wins (ranked, worker = 5600G):**
1. **Thread hygiene** — torch ignores cgroup quotas and spawns 12 threads
   inside a 5–10 cpu limit (oversubscription). Set `OMP_NUM_THREADS` = cpu
   limit on worker + bench Jobs. Free 1.5–3× on every torch stage. *(Bench
   template fixed 2026-07-11; prod worker env goes into the P3 gitops MR.)*
2. Stage caching — already shipped in the harness (raw stems cached).
3. Benchmark job-parallelism (5 × single-thread processes) — for sweeps only.
4. Windowed alignment doubles as an alignment speed win (~30–50 %: skips
   instrumental sections, smaller O(T²) attention windows).
5. ONNX emissions backend (`onnx-community/mms-300m-1130-forced-aligner-ONNX`,
   int8 317 MB): swap only `generate_emissions()`, keep the rest; ~1.2–1.5×
   fp32 on Zen 3 (no VNNI — measure int8 before adopting). Bonus: drops the
   C++-compile dependency. Backlog, not urgent.
6. Conditional separation policy (cheap model first, escalate on low align
   confidence) — likely obsolete if Kim MelBand holds up (§4).
7. torchaudio `forced_align`/MMS_FA: **not deprecated after all** (kept per
   pytorch/audio#3902) but offers ~zero speed gain (Viterbi is 1–2 % of
   runtime) — staying on ctc-forced-aligner.

## 4. Decisions (P2 — settled 2026-07-12 on the full night matrix)

- **Model default: `kim-melband` (mel_band_roformer_kim_ft_unwa.ckpt).**
  Best PCO/MAE/MedAE of all nine configs, fixes the TiK ToK field case
  (0 lines over; voc_ft leaves 7), ~2.1× realtime on the worker profile
  (BS-RoFormer: ~5.2×). kim-bleedless is a statistical tie — sticking with
  the variant the case run validated. resurrection's higher SDR did NOT
  translate to alignment quality (0.9048) — rankings must be measured on the
  actual downstream task.
- **`separation_mixback` default → 0** (measured on bsr AND kim pairs; the
  0.15 "insurance" costs ~0.01 PCO and 10× MedAE).
- `separation_mode` default flip (off → always) lands with P3 in 2.0.0
  (one archive-reprocess wave), together with the worker `OMP_NUM_THREADS`
  env and a PVC bump sized at download-time verification.

## 4b. P3 first measurements (windowed alignment, night of 2026-07-11/12)

- Core shipped dark (windows.py + anchor path in align(), flag off).
- **Windowing alone (full-mix!) fixes TiK ToK** (8 → 0 lines over) and takes
  Rick to its best (6 → 1–2). The lock-loss killer works as designed.
- First subset run exposed a real mechanism bug: with per-window forced
  alignment, real words stretched over pad/inter-line gaps (PCO 0.93 → 0.79
  even with PERFECT anchors). Fix: `star_frequency="edges"` inside windows —
  absorbing stars at both slice ends. After the fix: subset-8 PCO 0.89 with
  **MAE halved vs plain** (367 → 176 ms), robust to ±400 ms anchor jitter.
- **ACCEPTANCE MET (2026-07-12, pc-kim-win-j400, full 79 with ±400 ms anchor
  noise): word MAE 191 ms (< 200 target), PCO@0.3 91.5 % (> 90 target),
  PCO@0.5 95.5 %, MedAE 89 ms.** vs plain kim: PCO@0.3 dips 0.931 → 0.915 on
  easy songs (window-edge cost) while MAE mean collapses 526 → 191 ms — the
  catastrophic tail (the field complaint) is gone. Production-config field
  cases: TiK ToK 0 lines over, Rick 1 (raw full-mix: 8 and 6).
- **Acceptance-tour catch → pipeline 2.0.1:** the calibrated CTC-prob quality
  ramp is INVALID on the windowed path — r=0.36 vs true accuracy across 79
  songs; 10 of the 13 sub-0.5 scores had PCO@0.3 ≥ 0.88 (cluster Rick: clean
  timings, score 0.0137) — which would wrongly push good documents under the
  client's 0.5 word-mode gate. Windowed documents now score by lrclib-anchor
  agreement (share of referenced lines line_qa did not flag/drop); the
  whole-audio path keeps the prob ramp. The 0.5 client contract is unchanged.
- Known trade-off (reviewer, 2026-07-12): on the windowed path line_qa loses
  most of its power BY CONSTRUCTION — aligner starts are confined to windows
  derived from the same lrclib stamps it audits against, so deviations ≈ 0.
  A genuinely wrong lrclib record (matching duration, or no duration field)
  is no longer visible to QA; residual guards are the monotonic-stamp check,
  the ±3 s duration match, and plan_windows' stamp-viability gates. line_qa
  fully covers the whole-audio fallback path. Accepted: the benchmark shows
  the windowed path removes the failure class line_qa existed to patch.

## 5. Eval hardening backlog

- JamendoLyrics++ (80 newly annotated songs, 20+ genres) — add as a second
  dataset to guard against overfitting decisions to the 79.
- MUSDB18 word timestamps (45 songs, clean stems) — lets us split
  separation-artifact error from aligner error.
- Investigate the 2 globally-broken songs (Avercage_-_Embers,
  Pure_Mids_-_The_Leader) for lyrics/version mismatch; exclude if confirmed.
- Add 2–3 of Caner's own songs to `cases.yaml` (P1–P3 acceptance requires it).
