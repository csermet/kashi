# Alignment chain — state of the union (Faz 8 P1/P2, 2026-08-05)

**Status:** measurement + code audit, no implementation. Input for the Faz 8
decision on whether — and where — to replace the alignment method.

**Why this exists.** Faz 7 closed with an accepted complaint: documents that
report `quality_score` near 1.0 still drift audibly at word level. Faz 8's
brief (Caner, 2026-08-04) explicitly dropped the old-image A/B comparison and
replaced it with *"read the code, then research new methods."* This report is
the first half — what the shipped chain actually does, and what the archive
says about it. Field data is the live `kashi` database on 2026-08-05:
**253 processed documents, 888 jobs, 835 telemetry events**.

---

## 1. The chain as shipped (server 0.18.0 / pipeline 2.15.0)

Orchestrator: `worker/process.py:523 process_job()`.

| # | Stage | Code | Note |
|---|---|---|---|
| 1 | Audio fetch | `pipeline/audio_source.py:24` → `download.py:69` | yt-dlp; duration re-measured with ffprobe (`download.py:60`) |
| 2 | **Client-edit gate** | `process.py:533-547` | `\|hint − audio\| > 30 s` ⇒ permanent `alignment_failed` — §4 |
| 3 | Lyrics | `lrclib.py:211 fetch_lyrics()` | ≤5-request ladder; synced beats plain (`choose_record` `:325`) |
| 4 | Human-sync fast path | `lyricsfile.py:48` | skips CTC entirely; `quality_basis="human"` |
| 5 | Separation | `process.py:165` | Mel-Band RoFormer `kim_ft_unwa`, `separation_mode=always` |
| 6 | Nightcore | `process.py:424`, `nightcore.py` | rubberband slow-down, one rescale afterwards |
| 7 | **Anchor viability** | `process.py:238-248` | `\|record_duration − wav\| > 5 s` ⇒ anchors dropped |
| 8 | **Forced alignment** | `alignment.py:227 align()` | MMS-300m CTC, windowed by lrclib stamps |
| 9 | **Line QA** | `line_qa.py:288 apply_line_qa()` | drift snap, word-drop, degrade-to-line, sustain trim |
| 10 | Postprocess | beats / energy / fx / palette | `fx.select = "density/1.2"` |
| 11 | Document | `document.py:49` + `:202` validate | jsonschema hard gate, etag, upsert |
| 12 | Client | `overlay/src/main/kashi-server-logic.ts:54` | `QUALITY_GATE = 0.5` strips *all* words below it |

**The aligner** is `MahmoudAshraf/mms-300m-1130-forced-aligner` via the
`ctc-forced-aligner` wrapper (git-pinned), torch/torchaudio 2.9.1 CPU,
`KASHI_ALIGN_DEVICE` default `cpu`. Language comes from `langid.py` per job;
it is not configurable. **An "anchor" is one lrclib `[mm:ss.xx]` line start**
used as a window edge, so a CTC lock loss cannot propagate past it
(`windows.py`, `PAD_MS=350`, `MIN_STAMPED_LINES=4`, `MIN_STAMPED_FRACTION=0.8`).

### Load-bearing constants

| Constant | Value | Where | Governs |
|---|---|---|---|
| `_QUALITY_LOW/HIGH_MEAN` | 0.02 / 0.15 | `alignment.py:91` | the prob→quality ramp — §3.3 |
| `DRIFT_THRESHOLD_MS` | 2500 | `line_qa.py:32` | line flagged ⇒ **its words are deleted** |
| `MAX_FLAGGED_FRACTION` | 0.5 | `line_qa.py:38` | whole doc degrades to line mode |
| `MIN_WORD_DENSITY` | 0.30 | `line_qa.py:50` | border-case word drop |
| `TRIM_SUSTAIN_FACTOR` | 3.0 | `line_qa.py:83` | sustain trim (2.3.0) |
| `ANCHOR_CLOCK_TOLERANCE_S` | 5.0 | `process.py:73` | anchor-drop (2.4.1) |
| `CLIENT_EDIT_MISMATCH_S` | 30.0 | `process.py:80` | the archive blocker — §4 |
| `QUALITY_GATE` | 0.5 | overlay | word-mode on/off at the client |

Both Faz 8 suspects are alive and behaving as designed: **sustain trim** only
ever shortens a word (0.16 trims per line on average, max 51 in one document),
and **anchor-drop** fires only on a >5 s clock disagreement.

---

## 2. What the archive looks like

253 documents. `pipeline_version` spread: 2.15.0 ×104, 2.8.0 ×47, 2.4.0 ×69,
2.4.1 ×12, 2.14.2 ×16, others ×5. Effects: 98 carry `density/1.2`, 14 carry
`density/1.1`, **141 carry no `fx` block at all**.

**Plain-line ratio** (lines that reach the client with no word timing):

| basis | docs | lines | plain | plain % | per-doc mean | median | p90 |
|---|---|---|---|---|---|---|---|
| `anchors` | 154 | 8656 | 814 | 9.4 % | 10.9 % | 0.0 % | 26.0 % |
| *(pre-2.5.0, unlabelled)* | 82 | 4436 | 560 | 12.6 % | 14.3 % | 7.1 % | 37.9 % |
| `ctc-probs` | 14 | 798 | 117 | 14.7 % | 13.0 % | 0.0 % | 42.2 % |
| `human` | 3 | 162 | 6 | 3.7 % | 2.6 % | 0.0 % | 6.3 % |

The median document is clean; the damage is concentrated in a tail. **123 of
253 documents (49 %) have at least one word-stripped line.** 46 documents
carry 215 `words_derived` lines — ad-lib spans re-derived by character length,
i.e. *invented*, not measured.

QA totals: 600 flagged lines, 620 density drops, 2231 sustain trims, and a mean
absolute global offset of 282 ms on the anchored path — but **2000 ms on the
`ctc-probs` (no-anchor) path**. Whole-audio alignment is, in the field, about
seven times further off the clock than the anchored path.

---

## 3. Measurement honesty — three separate defects

This is the core Faz 8 finding, and it is worse than "the score does not
measure word feel". The score is not merely blunt; in two of its paths it is
*mislabelled*, and in a third it is *biased upward by failure*.

### 3.1 `quality_basis` lies on every line-mode document

`document.py:135-139` decides the basis from `align_result.windowed` alone:

```python
"quality_basis": "human" if … else "anchors" if align_result.windowed else "ctc-probs"
```

But both line-mode exits preserve `windowed` while producing a **prob-based**
number:

- `alignment.py:165 _line_only_fallback()` — reached when
  `regroup_words_into_lines()` returns `None`; returns
  `quality_score=quality_from_probs(all_probs)` with `windowed=plan is not None`.
- `line_qa.py:254 _degrade_to_line()` — reached when more than half the lines
  are flagged; returns `quality_score=result.quality_score`, i.e. **the pre-QA
  score, unchanged**. Nothing about the degradation is priced in.

So a document with **zero word timings** can be stamped `quality_basis:
"anchors"` and `quality_score: 1.0`. Field evidence — all ten line-mode
documents in the archive:

| artist | title | q | basis | flagged / lines |
|---|---|---|---|---|
| AnythingBecomeMoe | ヤラララ | **1.00** | anchors | 17 / 57 |
| BABYMETAL | BxMxC | **1.00** | anchors | 32 / 42 |
| BABYMETAL | Gimme Chocolate!! | **1.00** | anchors | 17 / 40 |
| Creepy Nuts | Mirage | **1.00** | anchors | — |
| MindaRyn | Like Flames | **1.00** | anchors | 5 / 47 |
| ZXKAI et al. | BATIDAO FUNK | **1.00** | *(pre-2.5.0)* | 25 / 42 |
| Hadise | Şampiyon | 0.987 | *(pre-2.5.0)* | 27 / 46 |
| BABYMETAL | KARATE | 0.942 | anchors | 6 / 41 |
| AnythingBecomeMoe | ヤラララ (dup) | **1.00** | *(pre-2.5.0)* | 13 / 57 |
| James | Monster | 0.462 | ctc-probs | — |

Nine of ten sit above the client's 0.5 gate while carrying no word timing at
all. `BxMxC` flags 32 of its 42 lines and still reports a perfect score.

### 3.2 Survivor bias: dropping bad lines *raises* the score

`line_qa.py:398` computes the final score from `surviving_probs` — the words
that were **not** deleted:

```python
words_per_line = [[] if i in flagged_set or i in density_dropped else words …]
surviving_probs = [w.prob for chunk in words_per_line for w in chunk]
quality_score=_quality(result, refs, flagged_set | density_dropped, surviving_probs)
```

On the non-windowed path `_quality` returns `quality_from_probs(surviving_probs)`
outright (`line_qa.py:427-428`). The pool is exactly the set of words that
passed QA, so **the more a document fails, the more confident the remainder
looks**. Worked field example — Tarkan, *Öp*: `ctc-probs`, **quality 1.0**,
19 of 40 lines flagged, 20 of 40 lines shipped with no words, global offset
−10 965 ms. A document that is eleven seconds off its own clock and half
word-less advertises a perfect score.

Aggregate: 44 `anchors` documents with at least one word-stripped line still
score ≥ 0.9; the mean score of *damaged* anchored documents is 0.855 against
1.000 for clean ones — a 0.145 penalty for damage that is sometimes total.

### 3.3 The ramp is calibrated for a pipeline we no longer run

The `0.02 → 0.15` ramp (`alignment.py:81-92`) was measured on **full mixes**
on 2026-07-10: correct lyrics 0.078, wrong lyrics 0.029, clean speech 0.32.
Since then `separation_mode` defaults to `always` — every production document
is aligned on **separated vocals**, whose mean prob sits near the clean-speech
end. The ramp saturates:

**130 of 253 documents (51.4 %) score exactly 1.00.** Another 13 sit in
0.95–0.99. A metric where half the population is pinned to the ceiling has no
resolving power left; it can only ever say "not obviously broken".

### 3.4 What the score *does* honestly measure

On the anchored path with words surviving, `1 − damaged/referenced` is a real
statement: *this fraction of lines landed within 2.5 s of their lrclib stamp*.
That is a **line-level, 2.5-second-resolution** claim. Word onsets are never
compared to anything. The code says so itself (`line_qa.py:421-426`) and so
does the schema (`processed-track.v1.schema.json:62`). Nothing in the shipped
pipeline measures the thing Caner is complaining about.

**The benchmark harness does** — `apps/server/benchmarks/` computes PCO/MAE
against JamendoLyrics (79 songs) — but it never runs on field tracks, and no
field document carries a PCO-style number.

---

## 4. Non-Latin lyrics fall out of word sync

`regroup_words_into_lines()` (`alignment.py:107-123`) refuses to emit word
timings unless `sum(len(line.split())) == len(aligned_segments)`. The aligner
romanizes and splits with `split_size="word"`; for scripts that do not
delimit words with spaces the two counts cannot agree, so the document takes
the `_line_only_fallback` exit.

| script class (non-ASCII line ratio) | docs | line-mode | mean plain % | mean score |
|---|---|---|---|---|
| Latin-only | 150 | 1 | 9.6 % | 0.899 |
| non-Latin heavy (>30 %) | 78 | **9** | **18.8 %** | **0.933** |
| accented Latin | 25 | 0 | 5.9 % | 0.938 |

Nine of the ten line-mode documents are non-Latin-heavy. The group has twice
the plain-line ratio of the Latin group **and a higher average score** — the
metric is inverted exactly where the output is worst. Japanese is the clearest
case (BABYMETAL ×3, ヤラララ ×2, Creepy Nuts), and Caner's library is heavy in
anime/J-pop, so this is not an edge case here.

---

## 5. The blocked archive is a stale-hint artefact, not a different edit

Failed jobs: 192 `lyrics_not_found`, **76 `alignment_failed` — every one of
them the client-edit mismatch**, 41 network, 8 other. The 76 failures cover
**72 distinct songs**; 43 of those already have a document from an earlier
run, so **29 songs are genuinely missing**.

Hint-to-audio ratio on the failures:

| ratio | jobs | (of the 29 truly blocked) |
|---|---|---|
| ≥ 5× | 22 | 6 |
| 2–5× | 26 | 12 |
| 1.15–2× | 27 | 11 |
| ≈ 1× | 1 | — |

The extreme end is not an "edit": LMFAO *Hot Dog* — client claims **3279 s**
(54:39) against 147 s of audio, **22×**. Also 2856 s, 2517 s, 2324 s, 2209 s…

**47 shipped documents carry the same inflated number in `track.duration_ms`**
(`document.py:67` lets the hint win over the measured audio). LMFAO *Hot Dog*'s
document claims 3279 s while its last lyric ends at 121 s — a 27× discrepancy
sitting in the served payload, in `canonical_group()`, and in the
`processed_tracks.duration_ms` column.

**The client is not producing these numbers any more.** Across 236
`track_changed` telemetry events (extension 0.1.12, through 2026-08-04) the
maximum reported duration is under 15 minutes and **not one event exceeds
900 s**. The inflated hints are historical, from the extension generation that
predates the Faz 6.7 P0 position/duration guards. Only one song ever produced
a document *after* its first mismatch failure, which is why the failures look
sticky: `admin_ops.py:29-42` reuses `latest.hints` verbatim on reprocess, so
every retry replays the same stale number and re-earns the 7-day block
(`queue.py:20`).

### ⚠️ Correction (2026-08-05, same day — measured after the fix shipped)

**The paragraph above generalises from the wrong half of the population.** A
reprocess wave over all 29 document-less songs was run on pipeline 2.15.1 and
**28 of 29 failed again on the same gate**. Cross-checking each failure against
current-extension telemetry settles it:

| Class | Count | What it means |
|---|---|---|
| Genuine edit mismatch | **18** | fresh telemetry agrees with the *hint*, not with the download — the browser really is playing a longer edit |
| Stale hint only | **2** | fresh telemetry agrees with the *download*; the failure was a replayed old hint |
| No telemetry | 8 | undecidable from here |
| Removed from YouTube | 1 | `video_unavailable` |

Worked example: `OeuD4xuUzdg` "Another Love x Infinity" — job hint 192 s,
**current telemetry 192 s**, downloaded audio 69 s. The client is right and the
download is a different, shorter stream. The titles of the 18 are decisive on
their own: "Another Love **x** Infinity", "Love Story **x** Golden Brown",
"shameless **x** royalty", "LUNA BALA (Slowed)", "Him & I (slowed to
perfection)" — mashups and slowed edits, precisely the content class where
YouTube substitutes an ATV song stream for the video being played.

**So the gate was right, and the honest-fail verdict of
`video-song-substitution-memo.md` stands.** What the 2.15.1 change actually
buys is narrower than the paragraph above claims:

- it stops an *impossible* hint (above the track ceiling) from being read as
  edit evidence — real, but **none of the 29 had one**; that population is the
  43 songs that already have documents, whose hints run to 3279 s;
- it stops such a hint from poisoning the lrclib duration filter;
- it puts the **measured** duration in `track.duration_ms`, which is what fixes
  the 47 documents carrying an impossible value. **This is the change's real
  field effect.**

The two stale-hint songs need *fresh* hints on reprocess, not a weaker gate —
`admin_ops` replaying `latest.hints` is the actual defect there, and passing
corrected hints explicitly works today.

One case deserves its own note: `7A-MmDSSxxM` "Ara Ara" reports **both** 73 s
and 184 s from the current extension for the same video id at different times.
That is the extension's stale-duration bug caught in the wild, and it is why
"trust the client" cannot be the rule either.

**This does not retire the 2.4.2 gate.** The Sinsirella class
(`video-song-substitution-memo.md`) is real and the memo's verdict — honest
fail plus the upload escape — stands for genuine stream substitution. What the
data shows is narrower and fixable: a hint of 3279 s for a 147 s track is not
a credible claim about *any* edit (it exceeds `max_track_duration_s = 1200`,
above which the pipeline refuses to process audio at all), and a stale hint
must not be replayed into a reprocess as if it were fresh evidence.

---

## 6. One more live signal

`position_anomaly` is the single largest telemetry category: **343 events,
246 of them `unexplained_snap`** (plus 80 `user_seek`, 17 `past_track_end`).
The overlay is detecting position jumps it cannot account for and snapping,
several times per session. Whatever Faz 8 chooses for the alignment method,
this is a *playback-clock* signal, independent of alignment quality, and it
belongs in the same conversation as "the lyrics feel late".

---

## 7. What this means for the Faz 8 decision

1. **We cannot currently tell whether a new method is better.** No field
   metric measures word onsets, and the one that exists is at its ceiling for
   half the archive. Any method comparison has to run through
   `apps/server/benchmarks/` (PCO/MAE, 79 songs) or a new field metric.
2. **Three defects are fixable without changing the method at all**: the
   mislabelled basis (§3.1), the survivor bias (§3.2), and the saturated ramp
   (§3.3). Doing this first is what makes a later method comparison legible.
3. **Non-Latin word sync is a structural gap** (§4), not a tuning problem —
   the token-count identity cannot hold for CJK. Any candidate method should
   be scored on this explicitly.
4. **The archive blocker is a metadata bug, not an alignment bug** (§5) and
   should not be bundled with the method decision.
5. **The 2.5 s drift threshold defines the whole QA layer** — flag, snap,
   drop, degrade all hang off it. It is a line-level tolerance being used as
   the guardian of a word-level product.

Method-candidate research (STT/ASR, rhythm- and onset-based placement, hybrid
onset-snapping, and the MMS-300m licence question) is the second half of Faz 8
and is recorded separately.

---

# Addendum (2026-08-06) — the benchmark answers §3.3, and the answer is worse

A full 79-song JamendoLyrics sweep on pipeline **2.18.1**, identical config to
the 2026-07-12 reference (`kim-melband`, mixback 0, windowed, 400 ms anchor
jitter, RTX 5070 Ti):
`benchmarks/results/2026-08-06-pc-2.18.1-kim-win-j400.json`.

## No regression — to the decimal

| metric | 2.0.0 (07-12) | 2.18.1 (08-06) |
|---|---|---|
| word MAE mean | 191.2 ms | **191.2 ms** |
| word MAE median | 131.1 ms | **131.1 ms** |
| word MedAE mean | 89.2 ms | **89.2 ms** |
| PCO@0.1 / 0.2 / 0.3 / 0.5 | .5668 / .8168 / .9150 / .9554 | **identical** |
| failures | 0 | **0** |

Everything shipped between those versions — the duration-hint fix, the metric
honesty work, the video-intro offset, the aligner seam, the Japanese path — is
byte-neutral on Latin-language word accuracy. That is the intended result: the
Japanese routing is gated on the detected language, the seam defaults to the
same checkpoint, and the metric changes live in line QA, which this harness
does not run by default.

## §3.3 measured: the prob ramp does not predict accuracy

The benchmark bypasses line QA, so its `quality_score` is exactly the raw CTC
prob ramp — the third defect, the one left unfixed because recalibrating a
constant without measurement would have been a guess. Now there is a
measurement, 79 songs against ground truth:

| correlation | Pearson | Spearman |
|---|---|---|
| `quality_score` vs PCO@0.3 | **+0.364** | +0.285 |
| `quality_score` vs word MAE | −0.222 | −0.322 |

**43 of 79 songs score exactly 1.00**, and inside that group the real PCO@0.3
runs from 0.746 to 1.000 — one of them is 1045 ms off on average. Meanwhile 13
songs score **below the client's 0.5 word-mode gate** while their median real
PCO@0.3 is **0.907**. The worst case cuts both ways at once:

| song | score | real PCO@0.3 | real MAE |
|---|---|---|---|
| Die_Revolution_gehört_Dir! | **0.26** | 0.942 | 119 ms |
| 1_Freak_-_Automatisch_Gekommen | 0.43 | 0.970 | 106 ms |
| Fantasma_-_Los_Rombos | **1.00** | 0.773 | **1045 ms** |

### The gate's actual trade

Only **2 of 79** songs are genuinely bad (PCO@0.3 < 0.70), and one of them
already scores 0.00 — any threshold catches it. Scanning the threshold:

| gate | good documents destroyed | bad caught | bad escaping |
|---|---|---|---|
| 0.3 | 3 | 1 | 1 |
| **0.5 (shipped)** | **11** | 2 | 0 |
| 0.7 | 17 | 2 | 0 |

**The shipped gate sacrifices 5.5 good documents per bad one it catches**, and
14 % of accurate documents lose their word sync in the field for no reason.

### What follows

Recalibrating the 0.02/0.15 anchors would not fix this. A metric correlating
at r = 0.36 with the truth does not have a constant problem — the CTC mean
probability simply does not carry the information. The conclusion is the
opposite of a tuning task:

- On the `ctc-probs` path the score should stop acting as a gate. It is
  measurement-grade honest about *confidence* and useless about *accuracy*.
- The `anchors` path is better founded (it compares against an independent
  clock) and is what the archive mostly uses — 154 documents against 14.
- The real replacement is the arbiter layer: cross-model agreement, onset
  distance, VAD coverage. Those are independent evidence; the prob ramp is
  the model grading its own homework.

Scope note: this measures the whole-audio path. Windowed documents recompute
to anchor agreement in line QA, so the 14 % figure bounds the `ctc-probs`
population, not the whole archive.

---

# Signal hunt (2026-08-06) — no single signal is the answer; their independence is

`benchmarks/results/2026-08-06-pc-signals.json`, same 79 songs, candidates
written next to the ground-truth error and ranked by `benchmarks/correlate.py`.

## Ranking

| signal | Spearman → PCO@0.3 | verdict |
|---|---|---|
| `onset_within_200` | **+0.579** | more than double the incumbent |
| `onset_within_50` / `_100` | +0.533 / +0.525 | strong |
| `onset_median_ms` | +0.508 | strong |
| `duration_outlier_frac` | **+0.455** | strong, and free of audio |
| `prob_frac_below_01` | +0.306 | marginal |
| `prob_mean` | +0.296 | **beats the shipped score** |
| **`quality_score` (shipped)** | **+0.264** | the bar |
| `prob_p10` | +0.142 | knows almost nothing |

Two results worth stating on their own:

- **The ramp destroys information.** Raw `prob_mean` (+0.296) outranks the
  ramped `quality_score` (+0.264) built from the very same numbers. Clamping
  at a 0.15 mean throws away the ordering — which is why 43 of 79 songs share
  an identical 1.00.
- **The tail hypothesis failed.** `prob_p10` was the guess that the
  distribution knows more than its mean; at +0.142 it knows *less*. Measured,
  discarded.

## The confound, checked

`onset_within_200` correlates +0.738 with onset DENSITY, and density itself
predicts accuracy (+0.389) — dense songs are easier to land on by chance. So
the raw +0.579 is partly a genre effect. Removing density:

| | Spearman |
|---|---|
| raw | +0.579 |
| partial, density removed | **+0.469** |
| partial, onset count removed | +0.491 |

The signal survives its own confound and still beats the incumbent by a wide
margin. Worth remembering when it is eventually normalised: **per-song onset
density belongs in the denominator.**

## …and then the gate test inverted the ranking

Cheapest threshold that catches every genuinely bad song, counting the good
documents destroyed to get there:

| signal | PCO<0.7 (2 bad) | PCO<0.8 (6 bad) |
|---|---|---|
| `quality_score` | 7 | **cannot catch them at all** |
| `onset_within_200` | **11** | 39 |
| `duration_outlier_frac` | 3 | 64 |
| **onset + duration** | **1** | 28 |
| onset + duration + quality | **0** | 37 |

**The best-correlating signal is a worse gate than the incumbent.** Ranking
well on average and isolating the worst cases are different jobs, and the
first does not imply the second. Building the arbiter on "highest correlation
wins" would have been a mistake — the same shape of mistake as shipping an
unvalidated ramp.

## Why the combination works: they are blind to different songs

Rank of the six worst documents under each signal (0 = flagged worst, 79 =
looks perfect):

| song | real PCO | quality | onset | duration |
|---|---|---|---|---|
| Avercage — Embers | 0.61 | **0** | 12 | **2** |
| Pure Mids — The Leader | 0.66 | 8 | **0** | 4 |
| Ridgway — Fire Inside | 0.73 | 14 | 44 | **1** |
| Pluie d'entre deux saisons | 0.75 | 57 | **1** | 69 |
| Fantasma — Los Rombos | 0.77 | 57 | 26 | 21 |

*Pluie* is invisible to the shipped score (57/79) and to duration (69/79),
and the onset signal ranks it the second worst document in the set. *Ridgway*
is the mirror image. **The signals fail independently**, which is exactly the
property an arbiter needs and exactly what a single number cannot have.

Combined ranking reaches **Spearman +0.644**, against +0.264 today.

## Honest limits

- **Two bad songs is not a validation set.** "Zero false positives" at
  PCO<0.7 is a fact about n=2 and must not be quoted as a rate.
- **At PCO<0.8 nothing works**: 28 good documents destroyed at best. That is
  not a tuning failure, it is a sign that **document-level gating is the wrong
  frame**. A document is not uniformly good or bad — the archive's own damage
  is concentrated in a tail of lines, and 49 % of documents carry at least one
  stripped line while the median document is clean.
- The next measurement is therefore **per line**, not per song: mark uncertain
  lines instead of killing documents. That is also the only version that helps
  the field, where the complaint was never "this song is bad" but "these words
  drift".

---

# Per-line signals (2026-08-06) — real, but not a gate

`benchmarks/results/2026-08-06-pc-lines.json`: the same sweep at line scale.
**3383 lines**, of which **162 (4.8 %) are genuinely bad** (PCO@0.3 < 0.5).
A real population, unlike the two bad songs the document-level test rested on.

## Ranking

| signal | Spearman → line PCO@0.3 |
|---|---|
| `onset_within_200` | **+0.399** |
| `onset_within_100` | +0.350 |
| `silence_frac` | **+0.345** |
| `onset_median_ms` | +0.302 |
| `prob_mean` | +0.183 |
| `line_score` | +0.173 |
| `duration_outlier_frac` | +0.165 |

Two notes on reading this. The +0.285 "bar" printed by the tool is a
**song-level** number and does not transfer — at line scale there is no
incumbent to beat, because the shipped pipeline uses no per-line quality
signal at all. And correlations are lower everywhere simply because a line
holds a handful of words; less data per unit, more noise.

**`line_score` at +0.173 vindicates an inherited decision.** Line QA
deliberately does not flag on it ("measured docs contain lines with perfect
timing and a 0.00 score"). That was recorded as a field observation; it is now
a measurement against ground truth, and it holds.

Also: `duration_outlier_frac` was the strong second at song level (+0.455) and
collapses here (+0.165). It is a property of a document's overall shape, not
of one line — worth keeping at the scale where it works rather than assuming
signals transfer between scopes.

## Detection curve — the honest limit

Flagging the worst X % of lines by signal, what fraction of the 162 bad lines
does it catch?

| signal | worst 5 % | 10 % | 20 % | 30 % |
|---|---|---|---|---|
| `onset_within_200` | **35 %** | 48 % | 58 % | 65 % |
| `onset_median_ms` | 35 % | 48 % | 62 % | 69 % |
| `silence_frac` | 26 % | 41 % | 56 % | 64 % |
| onset + silence | 28 % | 41 % | 61 % | **81 %** |
| `line_score` | 17 % | 33 % | 55 % | 74 % |
| *random* | *5 %* | *10 %* | *20 %* | *30 %* |

**Seven times better than chance at the top** — the signals genuinely know
something. And nowhere near a separator: catching 80 % of bad lines costs
flagging 30 % of every line in the archive.

## What this settles

**The arbiter must MARK, not DELETE.** That was a design preference three days
ago; it is now the only reading the data supports. A signal that is 7× better
than chance is worth acting on softly — de-emphasise, defer to the line clock,
raise the bar for effects — and is nowhere near good enough to justify
destroying word timings, which is what today's threshold does on the lines it
flags.

**The free signals are now exhausted.** Everything measurable from librosa and
the aligner's own output has been measured. The remaining untested lever is
the one that was always theoretically strongest: **a second aligner**.
Cross-model disagreement is the only evidence that is independent of *both*
the audio heuristics and the incumbent model, and it is the one candidate this
rig has not yet been able to score.

That converges with the licence problem rather than competing with it. The
seam takes CTC checkpoints (`AutoModelForCTC`), so **one permissively licensed
CTC checkpoint would serve both purposes at once** — the commercial escape
from CC-BY-NC, and the second opinion the arbiter needs. Finding it is the
next concrete task, and it is a search, not a build.

---

# The arbiter in the field (2026-08-09) — and the defect the first run exposed

Three of the archive's most-damaged documents, reprocessed on each version.
"Rescued" = flagged by the drift threshold, then kept and marked `uncertain`
because the audio vouched for the words.

| | flagged | rescued | wordless lines |
|---|---|---|---|
| before the arbiter | 30 | — | **90** |
| **2.19.0** (first field run) | 30 | **30 — every one** | 59 |
| **2.19.1** (after the fix) | 31 | **26** | **64** |

**The first run rescued everything it was asked about**, which is not a
judgement, it is an abstention. Reading the log showed why: *"To fight, to
fight, to fight"* survived at **onset support 0.00 and coverage 1.00** — the
audio placed not one of its words near singing, and coverage vouched for it
anyway.

The cause is that the two signals measure different things. **Coverage
measures a line's SHAPE; onsets measure its PLACE.** A line dragged somewhere
wrong keeps its shape perfectly, so requiring both to condemn made coverage a
veto over the only evidence that could see the error.

Checked against ground truth before changing anything: on 3206 scored lines,
the 21 with **zero** onset support were **67 % genuinely bad** (median PCO
0.25, median error 578 ms) against a 4 % base rate — sixteen times the
background. That is not weak evidence needing corroboration; it is a verdict.

So 2.19.1 makes exactly one asymmetry: **zero support condemns on its own**,
while *partial* support still needs coverage to agree. The rule is exactly
zero, not "low" — one word landing on an onset means the line is somewhere
real, and a single supported word returns it to the corroboration path.

Field effect: the same documents now keep 26 of 31 flagged lines and drop the
five the audio places nowhere. **26 lines got their word timings back** across
three songs — timings the old rule deleted — while the genuinely misplaced
ones still go.

The log is the audit trail, per line, with the numbers that decided it:

```
line  1 'This is war'                  support 1.00  coverage 1.00  kept
line 10 'To the edge of the earth,'    support 0.33  coverage 0.98  kept
line 33 "Don't believe me, just watch" support 1.00  coverage 0.54  kept
```

Limits worth keeping in view: three documents is a demonstration, not a rate;
`uncertain` is presentation-only until a client reads it; and the arbiter
still has no third signal — cross-model disagreement remains unmeasured at
word level and therefore out of production.
