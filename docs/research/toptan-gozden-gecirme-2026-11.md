# Whole-project review, 2026-11-13: are we drowning in rules, and what comes next?

**Status: measured and decided in outline; the phase order waits for the owner's "when".** No
pipeline, overlay or schema change was made by this review. One orchestrating session, eight
measurement and reading passes run the same day (rule inventory and ablation; server structure;
client structure; alignment-model research; lyrics-source line; the source experiment on fresh
songs; the human-truth bench on the desktop GPU, two rounds). Their working files are outside the
repository (`~/Documents/krediler/kashi-gozden-gecirme/<area>/`, lyric text and audio stay on the
owner's machines); every number below was computed there and is quoted with its n.

## The question

The owner, 2026-11-09 and 2026-11-13: "we keep adding rules; good on a few songs, unproven on all
of them — where did we over-engineer, what would be better, even if the structure has to change?"
and "code only goes so far; in the AI age, can a code + AI system do better?" His priority, stated
on 2026-11-13 (`docs/roadmap.md`, "Perceptual priority"): words that rush past read as a bug, a line
that keeps its pace but sits off the voice only as mistimed.

## Short answer

1. **The worry is half right.** The large, older mechanisms earn their place on songs they were
   never tuned on. Four small, late rules move nothing beyond input noise on 19 fresh songs (where
   they fired on 0-4 songs, so the arms had little power), and one of them traded a late line start
   for an early rest of the line on a moment the owner heard as wrong. The pipeline can lose about
   580 lines at no measured cost there.
2. **The biggest measured gain of the day is one number, not a rule.** Raising the whole-track
   vote threshold from 400 to 1 200 ms passes a pre-stated rule on human truth: eng20 PCO@0.1
   0.824 → 0.842, noticeable line starts 0.108 → 0.062, early line starts 55 → 23; over all 79
   songs 41 better, 3 worse.
3. **We were measuring through keyholes.** The human-truth bench never ran the shipped pipeline
   (of line QA only the end trim, no sheet shift, no held-end restore; the 2026-11-12 run even
   without the production offset table), and the audit's "ear" witness is the same emission pass the
   pipeline's vote consumes. A likely structural reason for "good on a few songs" is here, not in
   any single rule.
4. **Rules made the screen steadier more than they made it more accurate.** On the 20 hold-out
   songs the noticeable-line share moved 0.175 → 0.162 (6 songs better, 6 worse) while the share of
   words visible for under 120 ms fell 0.117 → 0.081 (17 better, 2 worse). That is the direction of
   the owner's own priority; whether the ear hears it as better was not measured, and the metric
   in use could not see it.
5. **Where AI fits.** Not as a runtime judge of timing (measured dead, 2026-11-05/07), not as a
   referee by proxy (lrclib's word files, dead today), not as display cosmetics (weak today).
   What worked today is AI as the measuring workforce: eight passes and ~40 GPU bench runs in one
   evening, with gates written before the numbers. A runtime LLM keeps one narrow, parked candidate
   (per-section "other version" labels on ASR text).
6. **No wholesale rewrite is justified anywhere.** Server core, queue and schema are small and
   stable, with three silent-failure paths in persist and refresh semantics confirmed by test (see
   "Adversarial verification"); the renderer is sound. The one structural investment worth making
   for the planned Android line is extracting the renderer into a shared view package (5-7
   workdays), not a new architecture.

## What was measured

### 1. The rules, switched off one at a time

24 arms × 39 songs (pilot 20, hold-out 19; 936 runs, none failed) on cached emissions, every
switch a monkeypatch in the harness. The local baseline reproduces the live 2.31.0 documents
(pilot 12 of 20 songs identical, 43 of 8 730 words differ on the rest; hold-out 13 of 19, 12 of
5 938 words). Reading rule written before the arms ran: a rule earns its place if removing it
worsens the noticeable-line share or the short-dwell share on the **hold-out** beyond input noise
(1 line, 0.002), or breaks a moment the owner heard as fine.

Hold-out baseline: 67 of 531 lines noticeable (0.126), short-dwell share 0.0635, 10 wordless lines.

| switched off | noticeable lines | short-dwell share | other | verdict |
|---|---|---|---|---|
| star family (hybrid + vote + held ends + V3; the 2.28 clock) | **+25** (13 songs worse) | **+0.0136** | rushed runs +74 | earns its place |
| whole-track vote | +2 | +0.0047 | sets ~88 % of all word starts | earns it (fluency) |
| held ends | 0 | +0.0104 | 0 ms words 0.9 % → 7.0 %; 4 heard-fine moments broken | earns it |
| V1 line regions | +2 | ~0 | 6 lines lose their words; both owner-confirmed fixes on the pilot break | earns it |
| sheet shift | +4 (one song, 19.3 s) | +0.0035 | 5 wordless lines | earns it |
| drift-flag family (flag, arbiter rescue, density drop) | +4 | +0.0010 | **10 lines get their words back** | a trade, see decisions |
| windows (whole-audio alignment instead) | **−17** | +0.0056 | 18 lines lose their words; 8 heard-fine moments broken; 0 ms 0.9 % → 6.2 % | keep (the owner's priority) |
| V3 unpile | 0 | +0.0007 | bunched runs 7 → 13 | hygiene only |
| V2 voice return | no change | | fired 0 times in 39 songs (the archive does not record it) | remove |
| response shift | no change | | fired on 1 song | remove |
| end trim | no change | | ends only | remove |
| nudge | −1 | 0 | pilot: +6 lines without it under the 400 ms vote (none under 1 200 ms, see step A); on one heard-wrong hold-out line it pulled the start 1.81 s onto its stamp — the owner calls that start right and the rest of the line early; the CEO 1:03 attribution first written here was another line's (see "Adversarial verification") | remove |
| ad-lib snap | 0 | 0 | | optional |
| V2 + trim + nudge + response together ("MIN2") | −1 | 0.0000 | no heard-fine moment broken | **holds** |

- **Defect found, first misdiagnosed:** one interjection line on a fresh song spans 23 s, which
  the owner heard as "flashes past, then crawls". The re-derivation has no maximum span, but it is
  not the cause: the span is there with the rule off. The sheet shift moved lines 2-29 of that
  song by +19.3 s and left the two short opening lines behind (too short to be anchors, before the
  moved section, so nothing carries them). The fix belongs in the sheet shift's carry (step C.2);
  the cap shipped in 2.32.0 is cosmetic.
- **Redundancies:** held ends undo what hybrid and vote cut and the end trim caps them again; V3
  undoes piles hybrid and vote make; nudge, ad-lib snap and V1 all pull a line toward its stamp;
  response shift and V2 both block-shift a line onto a loudness return.
- **Justified on the pilot set or one field song only:** V3, V2, held ends, nudge, response shift,
  ad-lib hold and re-derivation. Of these, held ends and V3 hold up on fresh songs; the rest do not.
- **The trap in the witness:** `audit.score`'s ear is `alignment._emit` over the whole stem, the
  same pass the vote, the sheet shift and V2 consume, for English too. A change that moves words
  toward the whole-track pass is graded by its own input. Decisions about the clock therefore rest
  on human truth and the owner's ear, not on `audit.score`.

### 2. Human truth, with the pipeline as shipped

The 2026-11-12 eng20 table in the Faz 5 note (0.748 / 0.755 / 0.751) was run with
`--align-model` and `--align-offset-ms 0`; passing `--align-model` makes the bench skip the
production offset table, so every word carried only the +70 ms windowed lead. Re-run with the
worker's own `ALIGN_MODELS` line (`audit/worker.env.example`; the gitops value was not read),
400 ms stamp jitter, line regions 800 ms. The earlier run reproduces to the last digit (0 of 5 693
words differ), so the difference below is the configuration.

| eng20, 20 songs | PCO@0.1 / 0.2 / 0.3 | gross > 500 ms | noticeable line starts (late / early of 868) | words only the pipeline rushes |
|---|---|---|---|---|
| 2026-11-12 run (no offset table) | 0.755 / 0.895 / 0.924 | 0.048 | 0.122 (44 / 52) | — |
| **production, vote 400** | **0.824 / 0.910 / 0.929** | 0.051 | 0.108 (30 / 55) | 0.0365 |
| production, line regions off | 0.816 / 0.901 / 0.920 | 0.062 | 0.147 (42 / 77) | — |
| vote 800 | 0.839 / 0.927 / 0.945 | 0.040 | 0.066 (27 / 26) | 0.0334 |
| **vote 1 200** | **0.842 / 0.930 / 0.947** | **0.038** | **0.062 (27 / 23)** | **0.0319** |
| vote off | 0.806 / 0.904 / 0.926 | 0.051 | 0.109 | 0.0427 |
| `edges` only (no hybrid, no vote: the 2.28 clock) | 0.737 / 0.873 / 0.913 | 0.051 | 0.168 | 0.0572 |

All 79 songs: production 0.886 / 0.940 / 0.951, line starts 0.082; vote 1 200 0.901 / 0.956 /
0.966, line starts 0.041 (early misses 158 → 47), 41 songs better, 3 worse, 35 flat at @0.1; every
language up. Pop valid region (278 words): 0.596 → 0.608 at @0.1, gross 17 → 15.

- **Two new metrics, defined before the runs.** *Line-start noticeable share*: a line's first
  word later than +220 ms or earlier than −330 ms against the human start. *Dwell*: the overlay
  keeps a word lit until the next word starts, so a word's time on screen is next start − own start.
  Singers themselves put 5.1 % of non-final words under 120 ms; the pipeline shows about as many
  (5.0 %), **but only 27 % of them where the singer is really that fast**: 3.7 % of word pairs are
  rushed by the pipeline alone. That is the owner's complaint as a number.
- **V1 holds on human truth as shipped:** line starts 0.147 → 0.108, PCO@0.1 +0.8 points.
- **What the bench still never exercises:** record choice and real lrclib stamps (it jitters human
  line starts by a flat ±400 ms), the sheet shift, all of line QA except the end trim, and
  `restore_held_ends` (with a one-off patch: word-end MAE 387 → 303 ms, 0 ms words 6.0 % → 1.0 %, so
  the bench's end numbers describe ends production does not ship). Stamp errors beyond 400 ms, where
  the two clocks disagree most, are untested.

### 3. The lyrics source

From the owner's two listens: 8 of 24 heard errors on the pilot set and 4-5 of 13 on the hold-out
are the sheet, not the timing. Reviewed on the 40 songs with ears, 79 healthy JamendoLyrics
sheets, and the LAN mirror for alternates; gates written before each check.

- **A clock-scale check passes its gates.** Theil-Sen slope of heard-against-sheet line times,
  |m − 1| > 0.03 with ≥ 8 placements: 0 false alarms on 32 + 77 healthy songs, injected ×1.05 /
  ×0.95 / ×1.16 found 77 of 77, 76 of 77, 75 of 76. The hold-out nightcore song read as "~4 s
  early" is a scale error (m = 1.21, ~20 s of drift by 1:37; the ±8 s window showed only its start).
- **Wide per-line offsets and per-section coverage fail their gates** as alarms (repeated passages
  place on their earlier copy; per-section CTC text false-alarms on 63 % of healthy sections).
- **The sheet shift fixes more than was known and has one blind spot.** It silently corrected a
  fresh Turkish record that is 19.4 s off on 44 of 46 lines. It cannot move a stretch with no
  unique line: GOSSIP's middle is still ~18 s late on 2.31.0.
- **Two "wrong text" answers of the blind listen are line QA, not the source:** both lines are in
  the record; the density drop shipped them without words. With Öp that is three heard wordless
  lines made by our own rule.
- **Ear-verified record choice: killed by its own pre-registration.** The design switched exactly
  GOSSIP and Enemy among the 39 songs it was drawn from. Frozen and replayed on 60 fresh archive
  songs (41 English / 11 Turkish / 8 other; stems and ears on the desktop GPU, ~31 s a song): the
  switch rules fired on **0 of 60** and the scale flag on 0. In the separate nightcore group (8) the
  scale refit fired once and worked (She Doesn't Mind: lines within 1 s 4 of 18 → 18 of 18; the
owner, blind on a two-row clip, 2026-11-14: the old document "by far too late", the refit "right
on" — the old row is the stored document from before the 2026-11-10 per-song repair). A scan
  of 31 more songs found one document with 6 lines where lrclib holds a fitting 55-line record.
- **What the fresh songs do show:** 3 of 60 documents sit on a sheet that is clearly off (medians
  −20.8 s and −15.2 s, one drifting) with no better record anywhere. About one song in twenty is
  confidently wrong today and nothing in lrclib can fix it.
- **Repairs do not survive.** `admin/reprocess` takes options from the request only and the
  archive feeder sends none, so a per-song repair (caller text, explicit speed factor) is
  overwritten by the next refresh. Read from code, not run.
- **An LLM adds little here.** Whole-song text containment already separates "other version"
  deterministically; the weak input is per-section CTC text, which an LLM would inherit. The
  pre-registered three-question trial is narrowed to one question (per-section "other version", on
  ASR text, against containment) and parked behind the items below.

### 4. A better ear

No shippable model fixes the class the owner minds most (chopped repeats, interjections, held
vowels): every permissively licensed aligner is speech-trained, and legal singing data at scale
does not exist. Three cheap tests remain.

- **omniASR-CTC 1B** (Meta; code and weights Apache-2.0, training data partly undisclosed), tried
  through an unlicensed third-party port, internal evaluation only. Same flags as production on
  eng20: PCO@0.1 0.824 → 0.831 (not robust: 9 songs better, 11 worse), @0.3 0.929 → 0.938, gross
  232 → 196 words, end MAE 387 → 351 ms, short-dwell share 0.050 → 0.035, noticeable line starts
  85 → 74; Turkish set @0.3 0.938 → 0.958 (8 of 10 songs). Its greedy ear hears nothing on some
  songs (0 letter frames on two of six), so the ear's consumers need care. It would also replace
  the CC-BY-NC MMS model that is still the code default for unlisted languages. Shipping needs our
  own conversion of the official weights.
- **Duration-aware decoding in our own trellis** (a per-word length prior inside the Viterbi):
  the one candidate aimed at pace where it is made, 4-6 workdays, unmeasured.
- **MFA (GMM-HMM)** as an ends-and-fluency probe, 2-3 workdays, CPU only, unmeasured on singing.
- Corrections to the record, from primary sources: the Kim vocal model's card was changed to MIT
  by its author (the 2026-08 "no primary source" reading can be corrected; still no licence file);
  basic-pitch's card lists non-commercial training sets, so it is not the "clean alternative" the
  2026-08 notes call it (unused today); the label-prior adaptation kept as a conditional option
  rests on a paper whose gains are for a small TDNN and which reports degradation on larger models.

### 5. Structure

**Server.** The core is small and stable (API 1 115 lines, a SKIP LOCKED queue with leases 365,
six tables, five migrations; untouched since 2026-10-04). The weight is elsewhere:

- The pipeline that runs in production is not in the code. Offset table, separator and structure
  flag live in env values copied by hand into gitops, the GPU helper's env file, the audit's env
  example and the bench flags; the code defaults name a non-commercial model and an unlicensed
  separator; `pipeline_version` covers none of it.
- Measurement tooling is as large as the product (audit + bench + scripts 9 872 lines in the
  repository and ~5 000 outside, against 10 837 for pipeline + worker), and it measures the wrong
  scope (section 2).
- Silent failures: the regression guard compares only the major version, so a stale worker image
  can overwrite a newer document; aligner weights load from the hub's `main`, unpinned; a stored
  document is never refreshed by listening (the overlay enqueues only on a 404); `choose_record`
  still prefers records carrying a word file although the fast path is off; `sheet_shift.py` is a
  copy of `audit/anchors.py`; two blank-id lookups assume `<pad>` (wrong for other checkpoints).
- Dead or switched-off weight: the uploads endpoint, second-pass and mix-back separation modes,
  the embedding layer, the word-file parser, eight Faz 3.5 audit modules whose gates failed
  (~2 400 lines, ~97 tests), ~1 400 lines of old bench probes.

**Client.** Less over-built than the server side (since 2026-08-01: overlay 58 commits, extension
7, server 181). Weight in three places: ~1 150 lines of stacked guards for the gapless bug class,
written before its root fix; a settings matrix (4 effect levels × 4 theme scopes × 2 fill dialects
× sizes) with pixel-identity contracts each; judgment that lives in the extension, the one
component that never updates itself, and is therefore copied into the overlay. The renderer
imports nothing from Electron or Node and already runs in plain Chromium behind a 20-line shim.
Unmeasured display delays sit under the hidden 200 ms lead: a 120 ms ease on every lit word, a
160-340 ms line fade-in, compositor frames.

**Replace wholesale?** No case in any area. A queue-less desktop-only design kills the phone line;
managed GPU meets the residential-IP need of the downloads.

### 6. Two dead ends from the same day

Recorded in `olu-fikirler.md` and the Faz 5 note: lrclib's word-timed files cannot referee the
pipeline (PCO@0.1 0.15 against ours 0.67-0.77 on the same hand-checked words; bulk-made by every
sign), and display-side spacing of rushed words changes little where the placement itself is
wrong. A third joins them here: automatic record switching by ear (section 3).

## Decisions proposed

Each is small enough to state its own proof. Workdays are implementation-session days; "owner"
is the owner's own time.

### A. Pipeline 2.32.0: balance the clock, prune, cap (≈ 3 workdays + owner 15 min)

1. Whole-track vote threshold 400 → 1 200 ms. Measured on the aligner-only bench (the choice among
   the three passing thresholds was made after seeing eng20); the shipped-pipeline effect and stamp
   errors beyond 400 ms are unmeasured until step B's bench. Before the tag, the pilot and hold-out
   arms through `audit.run_local`, and the owner judges blind clips of the heard-fine moments the
   ablation flagged as moved (four).
2. Remove V2, the response shift (with `vocal_energy.py`), the nudge and the end trim: ~580
   production lines, ~25 tests, qa counters keep emitting 0, no schema change. Cost known in
   advance: about 6 noticeable lines on the pilot set, all from the nudge, against one ambiguous
   heard case (a start fixed, the rest of the line early). As built, on top of the 1 200 ms vote
   the removal cost nothing on either set.
3. Cap the ad-lib re-derivation's span (cosmetic, see the corrected defect above).
4. Fix the `<pad>` blank lookups (three sites). The word-file preference in `choose_record` stays
   until its effect on real picks is measured: 0 of 5 real search answers change without it at the
   real durations, and the only observed flips were benign.
5. One archive refresh on the GPU worker afterwards if the pilot reads as the bench does (the
   line-start gain is over the policy's 5 % bar on human truth).

### B. Measure what ships (≈ 6-7 workdays, no listening)

1. Production configuration becomes versioned code defaults; env copies go; `pipeline_version`
   covers it. This needs the owner's yes because it moves values out of gitops.
2. The bench runs the shipped pipeline end to end (offsets, line QA, held-end restore) and reports
   the line-start share and the dwell shares; a jitter arm beyond 400 ms.
3. An archive replay rig: emission cache and local stems as first-class tools, so a rule is
   measured on ~300 songs in about two hours. 139 stems and ears exist after today (40 on the
   server host, 99 on the desktop).
4. Guards: version-ordered persist guard, pinned model revisions, `pipeline_version` in
   `/v1/health`, `jobs.claimed_by`.
5. A fluency number beside every accuracy number, in the audit and in CI (golden frames + dwell
   histogram from the real renderer).

### C. The source line: detect, be honest, remember (≈ 6-8 workdays + owner 10-15 min)

1. A per-song overrides table applied by every reprocess (pinned record, caller text, speed
   factor, offset, plain-only). What the owner or a session fixes once stays fixed.
2. Clock verification after alignment inputs are chosen: the scale check with a refit for
   nightcore uploads; a whole-sheet offset from repeated lines for sheets with no unique line
   (GOSSIP's middle, the −20 s fresh case) — the second is a new, unmeasured idea and gets its own
   pre-registration on the injected-error harness.
3. An honest fallback when the sheet is clearly off and nothing fits: plain text without lit
   words instead of confident karaoke (additive schema flag, overlay support).
4. Wordless lines: the density drop made three of the heard "not lit" moments. Decide with the
   owner's ear whether a doubtful line should light roughly or stay plain (the ablation: 10 lines
   lit again for ~4 more noticeable ones).
5. The narrowed LLM label trial stays parked behind 1-3.

### D. The ear (≈ 1.5 workdays, then 4-6)

1. Convert the official omniASR 1B weights ourselves and repeat today's runs, plus the hold-out
   arms and the owner's blind clips; adopt only if the fluency and line-start gains repeat. It
   settles the non-commercial default for unlisted languages either way.
2. Duration-aware decoding, behind a flag that is byte-identical at weight 0, judged on the
   pipeline-only rushed share and the owner's pace moments.

### E. Client foundations (≈ 10-13 workdays, parallel to A-D from day one)

1. Measure the 200 ms lead: taste from clips, chain latency from a screen recording, two telemetry
   numbers (1.5-2 workdays + owner 45 min).
2. Held-note sweep for an isolated held word (0.5-1 day; the owner asked for it).
3. Fewer knobs: one fill dialect, two theme scopes, three effect levels (1-1.5 days; the owner
   confirms what he uses).
4. Golden-frame harness, then `packages/view`: the renderer as a shared package (5-7 days).
5. Thin extension (3-4 days): raw facts only, judgment in the overlay, pinned extension id.

### F. Android mode (a) (≈ 9-14 workdays after E.4; the probe can start any time)

Probe app first (1-2 days): does the media session expose the video id, how good is its position,
do taps pass through. Then a title + artist + duration lookup on the server (1-2 days) and the
minimum app (6-10 days).

### G. Effects (open-ended, after E.4)

Word phases and a line exit as the foundation (2-3 days); the rest is the owner's direction.

### Parked or closed

- Plain-lyrics timing from scratch (9.7): unchanged, after C.
- Turkish parity (9.5): needs an independent truth set first; omniASR's Turkish result is a reason
  to do D.1 before any calibration.
- Timing rules beyond 2.32.0: none proposed. The next lever inside timing is D.2, not a rule.

## Step A carried out: 2.32.0 measured (2026-11-14)

In `main` as bbae307, tagged `server-v0.36.0` the same night on the owner's advance yes (given
2026-11-14 ~01:30 TR, on the condition that the gate, the differential proof and the human-truth
runs hold; CI on `main` green before the tag). Review step A in five commits: the whole-track vote
at 1 200 ms (`WHOLE_VOTE_T_MS`, worker and bench default); V2, the call-and-response shift, the
sub-threshold nudge and the word-end sustain trim removed (the document keeps
`alignment.qa.nudged / response_shifted / trimmed_ends` as 0; `vocal_energy.py` stays for the
audit's voice witness; the bench keeps its own copy of the trim so its end axis stays comparable);
the ad-lib re-derivation capped; the CTC blank of `transcribe`, `heard_tokens` and the audit's ear
taken as the aligner takes it. Production code 145 lines added, 638 removed; tests 23 removed with
their rules, 5 moved with the bench's trim copy, 10 added. Not in it: `choose_record`'s word-file
preference (unmeasured), any schema or overlay change.

- **Refactor proof.** The review's ablation harness rebuilt the arm "vote 1 200 + V2, response,
  nudge, trim off" on 2.31.0 (its base and vote arms reproduce the review's documents 39 of 39);
  the branch through the same harness with no switches equals it on 39 of 39 songs, word for word
  and as whole documents (qa counters included). Two one-line mutations are seen (34 and 10 songs
  differ). With the blank fix and the cap added, 38 of 39 stay equal; the 39th is the capped line.
- **Blank id.** Byte-identical with the production checkpoints on 5 songs (3 English 1B, 1 Turkish,
  1 MMS): the sheet shift's ear, the audit's whole-song ear and the by-ear transcript. A unit test
  with an omniASR-shaped vocabulary (pad `<s>` = 0, `<pad>` = 1) fails on each old lookup.
- **Ad-lib cap, from the data.** Re-derived spans in the 2.31.0 archive (279 lines): median
  2 170 ms, p99 9 210, largest healthy 10 110; the only two above are runaways (23 075 ms, the Toz
  Pembe hook the owner heard "very slow, lit far too early"; 27 990 ms, a last line run to the end
  of the audio). A span over 12 s gets 2.2 s (the median), a breath before the next line, or from
  its start for a last line. It fires on 2 of 279 archive lines and 1 of 22 on the 39 songs; the
  Toz Pembe line goes from 4.5-27.7 s to 25.2-27.4 s (voice at 23.7-25.8 s). Boundary tests,
  8 of 8 mutations caught. The last-line half is unverified (its one archive case had no stem).
- **Pilot / hold-out against 2.31.0** (same stems and records, `audit.score`): pilot noticeable
  lines 92/679 → 83/683 (8 songs better, 3 worse), short-dwell share 0.100 → 0.094, rushed runs
  591 → 555, tempo breaks 55.4 → 48.6 per 1 000 words, 0 ms words 1.39 % → 1.08 %, bunched runs
  20 → 13; hold-out 67/531 → 60/534 (5 better, 2 worse), 0.064 → 0.059, 286 → 271, 51.2 → 47.0,
  0.93 % → 0.81 %, 7 → 6; worded lines unchanged (1 213, 967), wordless unchanged (0, 10). The
  vote carries all of it; the removals and the cap on top move pilot 85 → 83, hold-out 60 → 60 —
  the ~6 pilot lines the nudge was expected to cost do not appear on top of the new vote. The ear
  witness is the vote's own pass; the independent voice witness reads pilot 18/72 → 23/77,
  hold-out 10/38 → 11/42.
- **Owner-heard moments.** Heard-fine moments moved past +220 / −330 ms: only the vote's four
  (Dynamite 2:52, KTS 0:26, Deli Oğlan 0:49, BxMxC 2:06); blind on two-row clips, the owner
  preferred the new document in all four (2026-11-14). On the four largest vote moves (Good
  Feeling 0:07 +1.81 s, Timber 1:48, Crazy In Love 0:20, Hey Mama 0:56) neither version is right
  (two "both bad", two weak preferences for the old): the change does not fix badly placed lines
  and does not break the good ones. Heard-bad moved the corrective way by the labels 8, the other
  way 2 (Crazy In Love 0:19, Stressed Out 1:36; both the vote's). Two of the eight need care: Good
  Feeling 0:07 moves +1.81 s, back toward the late start the owner had not complained about, and
  the "CEO 1:03" move is the next line's, not the asked one's ("Adversarial verification", 10b-c).
- **Human truth, the defaults with no vote flag** (desktop GPU, production offsets, 400 ms stamp
  jitter, line regions 800 ms): eng20 0.8424 / 0.9296 / 0.9471 at @0.1 / 0.2 / 0.3, all 79
  0.9012 / 0.9557 / 0.9656, pop valid region 0.6079 / 0.8942 / 0.9186 — section 2's vote 1 200
  runs to the last word (0 of 5 693, 21 580 and 2 411 words differ). The bench does not run line
  QA, so the removals and the cap are not on human truth.
- **Lead taste, measured the same night:** six clips of well-timed lines at total leads of 0, 100,
  200 and 300 ms, blind: the owner cannot tell them apart ("every time I listen another one looks
  right"). A constant offset inside that range is below his perception; the overlay's hidden
  200 ms is to be set from a measured chain latency, not from preference.
- **Open:** five blind clips for the owner (the cap on Toz Pembe, with the vote's +665 ms on its
  next line, a line he had called right; four moments the removed rules moved). Found, not fixed:
  one lrclib record carries enhanced-LRC word tags that reach the screen as text; the ad-lib snap
  can push a line past the next line's start (two archive cases). Rollout (gitops image tag) and
  the archive refresh wait for the owner.

## Adversarial verification (2026-11-14, night)

One pass tried to refute the findings above by running something for each (throwaway tests on a
Postgres 17 container, the audit image offline, vitest; working files outside the repository).
Eleven claims: seven confirmed, three partly, two attributions refuted. What it changes:

| claim | verdict | what ran, what it showed |
|---|---|---|
| The regression guard lets a stale worker overwrite a newer document | **confirmed, worse than stated** | real `persist_processed_track`: stored 2.31.0 + incoming 2.29.0 of equal quality → the row becomes 2.29.0; also at a quality drop of 0.2499, at any drop across quality bases, and from an older MAJOR (1.9.0). Nothing orders versions; `claim_next` has no version filter; logged as "persisted" |
| A per-song repair is overwritten by the next refresh | **confirmed end to end** | real admin endpoint and `process_job`, fakes for audio and lrclib: a repair (explicit speed factor 1.1504 + caller text) is replaced by the feeder's option-less reprocess (r 1.3365, lrclib). It survives only if the refresh fails or keeps under half the worded lines |
| Listening never refreshes a stored document | **confirmed** | `/v1/lyrics` serves any stored version with 200; the overlay enqueues only on a 404; no client reads `pipeline_version`. Without an admin action an old document is refreshed never |
| Aligner weights are unpinned | **confirmed; needs code** | neither the loader nor `AlignerChoice` can carry a revision. The "second revision" in the model cache is transformers' background safetensors auto-conversion (`refs/pr/N`), not what is loaded — the "~2× model size" trap in CLAUDE.md is that download |
| `choose_record` still prefers word-file records | **partly** | live, but 0 of 5 real cached search answers change without it at the real duration; it can pick a record up to 3 s farther inside the band; the archive effect is not measurable offline |
| `--align-model` skips the offset table; the bench runs of line QA only the end trim | **confirmed by execution** | a profiled bench run on a stub model: 69 pipeline functions called, none of `apply_line_qa`, `restore_held_ends`, the sheet shift, the arbiter |
| `audit.score`'s ear is the vote's own input | **confirmed, literally** | on 4 English hold-out songs the greedy decode of the vote's cached emissions equals the stored ear token for token; two thirds of ear-placed lines are graded by their own input to within 60 ms |
| Code defaults are not licence-clean | **confirmed** | a scrubbed env runs the CC-BY-NC MMS aligner for every language with no offset table and the unlicensed separator |
| Blank lookups assume `<pad>` | **confirmed, three sites** | already wrong for the default MMS model: the by-ear rung could never accept under code defaults (fixed in 2.32.0) |
| "The ad-lib re-derivation spread a line over 23 s" | **diagnosis refuted** | the 23 s span exists with the rule off; the sheet shift left two short opening lines 19 s behind (corrected above) |
| "The nudge caused two heard errors" | **one partly, one refuted** | Good Feeling 0:07: the nudge pulled the start onto its stamp; the owner calls the start right and the rest early. CEO 1:03: the asked line is identical with and without the nudge; the 505 ms move is the next line's, at the edge of the scoring window |
| Client: active word until the next start; any `chrome-extension://` origin accepted; storage read/write per 4 Hz event | confirmed (the first with its exceptions: line-final words, fill runs, fx holds, 0 ms words) | vitest on the real modules |

Consequences for the order of work:

1. **Before any archive refresh:** the three songs repaired by hand on 2026-11-10 must be left out
   of the feeder's list (or step C.1 must ship first), or the refresh puts them back on the wrong
   speed ratio — including the refit the owner confirmed by ear. And every worker that takes jobs
   must run the new image: an older worker's output overwrites silently. Check each worker's
   `ready: pipeline 2.32.0` line before feeding.
2. **Step B.4's guard** refuses any older `pipeline_version`, majors included, and warns.
3. **Step C.2 gains a concrete first item:** the sheet shift carries unanchored lines next to a
   moved section, and a line's end may not pass the next line's start.
4. The statistical statements above are bounded by their n: "0 false alarms on 109 healthy songs"
   bounds the rate below about 3 %; "about one fresh song in twenty on a wrong sheet" is 3 of 60
   (roughly 1-14 %); several arms named in this note (the combined removal arm, the 1 200 ms vote
   arm of the ablation, the tie-break among bench arms) were post hoc and are labelled so in the
   workers' notes.

## Release 0.37.0: steps B and C.1 together (2026-11-14, prepared, not deployed)

Server 0.37.0, pipeline 2.33.0: `song-overrides` (C.1) and `measure-what-ships` (B) merged on
`release-0.37.0`. Working notes outside the repository (`kashi-gozden-gecirme/surum-0370/`).

- **Ships.** Per-song overrides (pinned record, caller or LRC text, speed factor, sheet offset;
  admin API `/v1/admin/overrides`, `scripts/import_overrides.py`); production configuration as code
  defaults; aligner weights from pinned commits; `pipeline_version` in `/v1/health`; the
  `clock_stats` telemetry kind; the shipped-chain bench. One persist guard for both: an admin
  `force_persist` stores; a document from a newer pipeline version is kept, also against a run
  that applied the song's override; a document built under the override is kept against any run
  that did not apply it, whatever its version; the first build under an override skips the
  quality comparison; then the regression rules. For a song with an override the guard locks the
  override row before it reads the stored document (a per-song mutex; a song without one locks
  nothing, as before). A worker whose database is behind its code waits for the migration before
  its ready line and its first claim.
- **Does not change any document.** 39 of 39 pilot/hold-out songs through the 2.32.0 harness equal
  2.32.0's documents except `pipeline_version`, with the worker env file and with no env at all.
  No schema, overlay or extension change; no archive refresh needed.
- **Migration** `f3a8c2d61b07` (adds `song_overrides`, additive), the single head on
  `b36dfc2aae66`.
- **Rollout order.** (1) The API with its initContainer migration (`alembic upgrade head`) first;
  (2) the cluster worker (a 0.37.0 worker that starts before the migration logs `not claiming
  jobs: database schema behind this worker: expected Alembic revision f3a8c2d61b07, found
  b36dfc2aae66 …` and waits); (3) the GPU helper last, rebuilt at `server-v0.37.0` with no pipeline
  settings (`docs/gpu-worker.md`). Every worker logs `ready: pipeline 2.33.0, code-default
  configuration`; `/v1/health` returns `pipeline_version` 2.33.0. Only then the owner imports the
  three 2026-11-10 repairs (`import_overrides.py --reprocess`; each line must say
  `"job_carries_override": true`). `ALIGN_MODELS`, `STRUCTURE_SECTIONS` and
  `SEPARATION_MODEL_FILENAME` in gitops equal the code defaults but stay for this rollout (a
  rollback needs them; the ready line proves the equality); a later MR drops them.
- **Rollback to 0.36.0 is not an image swap.** 0.36.0's initContainer runs `alembic upgrade head`
  with its own migrations and fails on a database at `f3a8c2d61b07` ("Can't locate revision",
  exit 255, reproduced on a throwaway database). So: export the overrides (`GET
  /v1/admin/overrides`, then each `GET /v1/admin/overrides/<type>/<id>`, kept private: they hold
  lyrics text) into the importer's payload shape; stop the cluster worker and the GPU helper; run
  `alembic downgrade b36dfc2aae66` from the 0.37.0 image (e.g. in the 0.37.0 API pod; it drops
  `song_overrides` and nothing else); then swap the images to 0.36.0, API first (its `upgrade
  head` is then a no-op). The helper rebuilt at `server-v0.36.0` needs its three pipeline env
  lines again. 0.36.0 has neither the version order nor the override rules, so no reprocess wave
  over the songs that had an override until 0.37.0 is back and the export is re-imported.

## Step C.2, first half: the sheet shift carries the lines it leaves behind (2026-11-14)

Branch `sheet-shift-carry` (measured as pipeline 2.32.1 on its own; merged into the timing release
2.34.0 below, where it was renumbered).
Working files, the pre-registration and every score: `~/Documents/krediler/kashi-gozden-gecirme/c2-tasima/`.
The defect is the adversarial verification's claim 10a: Toz Pembe's record is 19.3 s early, the
shift moved lines 2-29 and left the two opening "Ah-ah" lines (too short to anchor, before the
section) at their stamps, so the first line lit 19 s before the voice and crawled.

- **The size, before any change.** On the injected JamendoLyrics harness (79 songs, `audit.inject`'s
  trials plus their mirror: 1 343 trials, 949 with a moved section) 2.32.0 leaves 12 333 stamped
  lines at the old clock next to a moved section, 11 393 of them > 1 s from the human stamp
  (median 4.7 s): 89 % of every line still off in moved trials, and on whole-sheet shifts all of
  them (a sheet moved earlier leaves its tail, one moved later its head; the 2.30.1 carry only
  reaches lines the move overtakes). In the archive (278 stamped r = 1 records with an ear) the
  shift fires on 7 songs and all 7 leave lines behind (118). GOSSIP's "second chorus 3-4 s late"
  (2.29.1) was a ±8 s-window reading: the whole-song ear hears every stamped line after its moved
  section at −19 s, so the "middle" side finding is the same defect.
- **The rule** (`sheet_shift._adopt_left_behind`). A gap (the stamped lines between two found
  sections, or before the first, or after the last) that borders a moved section and still holds a
  line at its old stamp is decided again: a split sends its first lines with the left side and the
  rest with the right, each side applying its offset (a moved section's median; a section that
  stays, the song start and the song end 0). The split keeps the stamps in order (never more out of
  order than 2.32.0), then follows the lines' own evidence — a lone anchor votes for the nearer
  offset; any other line of 12+ letters votes for the side near whose moved stamp (±1.5 s) the ear
  hears its letters (>= 0.55, 0.10 ahead) — then position (before the first section and after the
  last, everything goes with it; between two, the nearer edge stamp). Everything else is 2.32.0's.
  Plus: a re-derived ad-lib line ends a breath before the next line instead of past its start.
- **Pre-registered** (bars and three arms fixed before the new code ran): position + lone anchors
  (P) failed the verse bar — next to an injected verse it pushed 61 healthy lines > 1 s off against
  34 brought back; head and tail only failed it too (39 against 8); with the ear's vote (P2) every
  bar passed, so P2 is the rule: of the left-behind lines > 1 s off, 6 094 of 6 124 come within
  1 s (global and verse trials; whole-sheet shifts 6 060 of 6 090); 12 of 6 106 changed lines end
  farther from the human stamp; next to a moved verse 11 made > 1 s off against 34 brought back;
  unmodified sheets 0 of 79 change; the shift's own detection, false alarms and offset error are
  identical (sections identical on every trial); the windows plan 938 of 949 moved trials (908
  before, none lost). Drift (a scale error) is reported: 1 444 of 5 269 within 1 s, changed lines
  farther 294 of 4 708.
- **Proof that nothing else moves.** A copy of 2.32.0's module against the branch's on 3 000 seeded
  synthetic sheets whose ears hear every long line exactly where it is sung, with offsets and
  jitter exactly on SHIFT_MS, SECTION_TOL_MS, MIN_LETTERS, the overtaken equality, the start
  clamp, the split's tie and the vote's tie (2 501 sheets moved, 2 136 left lines behind, 1 908
  changed): sections identical on all; stamps byte-identical on every sheet where 2.32.0 leaves no
  line behind; every changed line takes one of its gap's two offsets; never more stamps out of
  order. A stricter property added with the harness (only left-behind lines change) failed on 15
  sheets: the rule decides such a gap as a whole, so a line 2.30.1's carry moved there can take the
  other side's offset (on the injected harness 6 lines, all between two moved drift sections, the
  two checked closer to the human stamp). Eleven one-line mutations (the skip, the threshold, the
  disorder bound, the tie, the votes, the start clamp, the end clamp's two bounds, ...) each fail
  the unit tests; the differential also sees the four that touch the identity.
- **Real songs** (the 39 pilot / hold-out songs through the 2.32.0 audit harness, same stems,
  records and cached emissions; 2.32.0 itself reproduces a-2320's documents 39 of 39): 37
  byte-identical. Toz Pembe: line 0 0.33 -> 20.43 s (the owner: "sung at about 0:20"), line 1
  25.18 -> 24.00 s (voice 23.7-25.8 s), no line ending past the next start; the heard-right line 2
  unchanged. GOSSIP: lines 25-37 move 17.1-19.5 s earlier onto the ear (`audit.score` errors 20-600
  ms, one −1.46 s); stampless line 38, which rides in line 37's moved window, goes from 3.7 s late
  to 6.8 s early (its first word now held 144.1-151.2 s), line 39 from 2.7 s late to on time.
  `audit.score`: pilot 83/683 -> 80/689 noticeable, hold-out 60/534 -> 59/533; short-dwell share
  0.0939 -> 0.0895 and 0.0589 -> 0.0584; worded and wordless lines unchanged. Owner-labelled
  moments: none heard fine is touched; Toz Pembe 0:26.8 stays `bad:fixed?`. The archive's seven
  firing songs at stamp level: 83 lines change; the ear (two windows) sides with the new stamp on
  26, the old on 3, neither on 48, 6 too short to tell.
- **Repeated-line consensus (C.2's second idea), measured and failed as registered**, left out: a
  whole-sheet offset from the placements of every long line fires on 0 of 79 healthy and 0 of 158
  verse trials, finds 462 of 474 whole-sheet shifts (error median 70 ms), and in the archive fires
  only on true shifts (4 of 278) — among them **Yalan (dSybIb9CO2s), 13 s late with no unique line,
  which nothing catches today** (fit 0/14 -> 36/36). It failed its two named targets (loops on a
  7 s / 15 s period, where offset 0 is as well supported as a period) and a mis-specified healthy
  set (it counted Toz Pembe's off record as healthy). A fresh pre-registration is a proposal.
- **Open:** the owner's blind clips (Toz Pembe's opening; three GOSSIP moments, one of them line
  38); the archive's two LMFAO songs whose small moves (1.25 / 1.56 s) rest on few anchors and now
  carry more lines (Best Night: 20 lines, the ear 6 for / 3 against). The twin `audit/anchors.py`
  only flags, it never moves a stamp, so it has nothing to carry; the overlay's own lrclib client
  still shows raw stamps.
- **Gate and bench.** pytest 1 180 passed (DB tests against a throwaway Postgres 17; the one skip is
  the alignment smoke test), ruff, pyright and check-versions clean. No human-truth bench run: the
  bench runs neither the sheet shift nor line QA, so it cannot see this change.

## The second pass and the new acceptance order (2026-11-14)

A deeper critical re-reading of this review (working file `derin-analiz/elestirel-okuma.md`,
outside the repository) and an archive-wide run changed three things.

- **2.32.0 shipped in the wrong order.** It went live at 03:01, the archive measurement finished at
  04:01. On 287 archive songs (local 2.31.0 against local 2.32.0 on the same stems; the local run
  reproduces production, 99.82 % of words within 20 ms) the 1 200 ms vote improves the averages
  (short-dwell share 0.0633 → 0.0607, noticeable lines 0.155 → 0.124, 111 songs better / 23 worse)
  and at the same time makes 274 words sit in NEW 0 ms runs across 62 songs: several words lit at one
  instant, the look the owner minds most. The bench could not show it (on JamendoLyrics the vote
  removes more such words than it makes), and the four owner clips were of other moments. The
  mechanism, traced on all 62 songs: not the whole-track pass cramming words into one frame (as
  first written in the night log) but an ORDER violation — the vote moves some words of a phrase to
  the later whole-track time, a neighbour stays on its window time, and the seam pass then squeezes
  the rest onto the voted word (229 of the 274 words); V3 cannot see most of it (its no-gain guard,
  its three-word minimum, and it only knows `edges` starts).
- **Acceptance for timing changes, from now on, in this order:** (1) the archive rig (287 songs
  with cached emissions on the desktop; a post-emission arm costs about a second a song) with
  aggregate bars AND a per-song veto on the worst new moment; (2) human truth with the bench in
  shipped mode, as an accuracy guard only; (3) at most eight blind clips for the owner, drawn by a
  rule written beforehand; (4) the project reviewer on the merge; (5) the tag. Gates are written and
  hashed before any number.
- **Corrections to this review's own claims.** "About one fresh song in twenty sits on a wrong
  sheet" does not hold: two of the three songs are healthy by the archive rig's score and the third
  is a loop; the measured number is 8 of 278 records with a shifted section, 7 of which the sheet
  shift already finds. The lead clips (0-300 ms indistinguishable to the owner) make a chain-latency
  measurement a low priority, not a next step. Every archive refresh re-downloads ~280 songs from
  YouTube: a refresh from the stored stems (no download) is a missing step, ahead of further timing
  work. Flags left for the owner: 46 tracked bench result files carry lyrics word by word (14
  commercial songs; kept for now by his decision, no new ones are added); the non-commercial MMS
  model still serves the languages without their own aligner (42 archive songs).

A second deep pass designed the layer behind the vote guard (working file
`derin-analiz/yanlis-yerlesen-satirlar.md`): on human truth the pipeline-only rushed pairs split into
marginal ones (72 of 154), all-interjection lines that production already spreads (34) and 48 hard
cases, two thirds of them ordinary words; the model is lost where it hears no letters (9 % of
English words leave no letter in the emissions and carry 61 % of the noticeable misses); a word's
sung duration is poorly predicted from text (R² 0.19 leave-one-song-out), so a prior can be a floor
and a tie-breaker, not a point estimate; a blind 120 ms floor inside the line removes the
pipeline-only rushes at 0.002 of PCO@0.1. Its recommendation — a word-duration term in the decoder,
re-decoding only the violated region between two pinned words, byte-identical at weight 0, with a
first experiment and seven pre-stated bars — is not started.

## Timing release 2.34.0 measured (2026-11-14)

`visible-sheet-fixes`, `sheet-shift-carry` and `vote-pile-guard` (candidate A2: the vote refuses a
time that would put a word at or past a neighbour kept on its window time, or squeeze a pair the
windows kept ≥ 120 ms apart) merged on server 0.37.0, line QA's density drop removed (the rule
that emptied the unflagged lines within two lines of a flagged one; flags, snaps and the arbiter
stay, `alignment.qa.density_dropped` is always 0), pipeline 2.34.0, server 0.38.0. Measured in the
order above against pre-registered gates (working files `surum-zamanlama/`).

- **Proof.** The new code equals the harness arm "merged branches + density drop off" document for
  document on the 39 pilot / hold-out songs; a one-line mutation changes 4 documents and fails 4 unit
  tests. Server gate green (1 496 passed, 278 DB-backed against Postgres 17), TS path green in CI
  order.
- **Archive rig, 287 songs, against 2.32.0 (= 2.33.0):** words in new 0 ms runs (vs 2.31.0) 274 →
  49, short-dwell share 0.0607 → 0.0508, rushed runs 4 190 → 3 554, bunched 111 → 71, worded lines
  15 677 → 15 792 (wordless 256 → 140), voice witness 0.308 → 0.311, re-lit lines lit badly 11 of 114;
  every bar passes. Per-song veto (a new run of ≥ 4 words within 60 ms, a line start > 1.5 s farther
  from a witness, a line losing its words): 9 songs — six piles of 4-5 words (identical in the A2 +
  density-off arm), two carry moves (GOSSIP line 36, 19.5 s from the voice witness; Bubblegum Bitch
  line 38, 2.3 s from the ear), one one-word line left plain (Wide Awake). The same veto on 2.31.0 →
  2.32.0 would have stopped 23 songs.
- **Human truth, shipped mode, against 2.33.0:** eng20 ±400 PCO@0.1 0.8434 → 0.8420 (timed) /
  0.8412 → 0.8413 (all), @0.3 0.9520 → 0.9523, line starts 0.0549 both, pipeline-only rushed 0.0272
  → 0.0253; all 79 ±400 @0.1 0.9016 → 0.9021 / 0.8994 → 0.9017, @0.3 0.9683 → 0.9693, line starts
  0.0364 both, rushed 0.0358 → 0.0282; a whole sheet 3 s late (all 79) @0.1 0.6459 → 0.8461, line
  starts 0.3696 → 0.0982, timed lines outside the moved section late 1 002 of 1 027 → 217 of 1 222.
- **The owner's ear, three blind rounds the same day.** (i) The parts on their own (ten clips):
  the carry preferred on both clips (Toz Pembe's opening now at 20.4 s, where he had said it is
  sung; GOSSIP's middle on the voice, with one stampless line 6.8 s early that he also saw); a
  doubtful line lit rather than plain on all three moments he had flagged (Öp, Gentleman,
  BATIDAO); the vote guard 2 clear + 1 weak against 1 (Gimme More 1:33), one "both bad, the text
  does not match". (ii) The build (eight clips drawn by rule: the worst new moment of four vetoed
  songs, Gimme More, the three largest short-dwell gains): the candidate on 3 clear + 1 weak, the
  old document on 2 (Gimme More again; Give It Up To Me), neither on 2. The pre-stated rule (no
  ship if the old row wins 2 of the first four or 3 of the eight) is not tripped. On the two
  vetoed songs whose veto came from a witness (GOSSIP, Bubblegum Bitch) he clearly prefers the
  candidate, so those two vetoes are lifted; and two of the three "largest fluency gain" clips were
  not heard as gains — the short-dwell measure does not predict his preference clip by clip.
- **Reviewer pass on the merge:** nothing blocking. Known and left for a later release, each rare
  and without an archive case: a refused ad-lib snap whose deviation exceeds 2 500 ms falls into the
  drift flag and loses its word; a line can still end past the next line's start when that next
  line is moved earlier afterwards; the tag grammar of the two twins differs on non-ASCII digits and
  a leading BOM; a carried tail stamp has no upper bound at the end of the audio. The overlay half
  of the tag fix ships only with an overlay release.
- **Shipped as** server 0.38.0. Refresh: the 20 pilot and 20 hold-out songs only (the owner: no
  need to refresh everything each time); the seven remaining vetoed songs, Gimme More, the six
  unmeasured nightcore documents and the three hand-repaired songs stay as they are. The whole
  archive waits for the refresh that needs no download.
- **Rolled out and refreshed the same evening.** Both workers logged `ready: pipeline 2.34.0,
  code-default configuration` (the cluster worker and the GPU helper built from the tag). The
  owner's import of the 2026-11-10 repairs ran first: three `song_overrides` rows, and each of the
  three songs rebuilt on 2.34.0 under its override (`lyrics_source: caller`, speed factors 1.0662 /
  1.1289 / 1.1504, every line worded, `document_under_override` true) — so the hand-repaired songs
  did not stay as they were after all; the repair is now what a reprocess reproduces. Then 36
  songs: the pilot's 17 and the hold-out's 19 that are neither vetoed nor nightcore, all 36 on
  2.34.0, no job failed (two downloads answered 403 once and passed on the retry). Documents
  before (2.31.0) → after, numbers only: words whose successor starts within 120 ms 884 → 694
  (share 0.0709 → 0.0555; fewer on 30 songs, more on 1, by one word), 0 ms words 60 → 23,
  wordless lines 10 → 1, the one word starting before its predecessor gone, lines ending past the
  next line's start 9 → 6; no line count and no lyrics record changed. Of 1 925 line starts 75
  moved more than 500 ms, 19 of them the two carries (GOSSIP's lines 25-37 about 19 s earlier,
  Toz Pembe's opening 20.1 s later: the two moves the owner preferred blind). The first finished
  documents were read before the feed went on. Working files `yenileme-2.34/`.

## Release 2.35.0: a first word that waits in silence (2026-11-16)

The owner, on the refreshed documents in the new overlay build: "the line ends and the display
jumps to the next line at once, which then sits there waiting — everywhere", with GOSSIP 2:00 and
2:23 as examples. The display rule was not the cause (a short gap holds the previous line and the
next arrives at most 500 ms early): in the document the line's FIRST word sat glued to the previous
line's end and the rest of the line came seconds later. Working files `kopuk-ilk-kelime/`; the
rule, its numbers and the labelled moments are in the 2.35.0 paragraph of `version.py`.

- **How common, and how often false.** On the 36 refreshed songs 42 of 1 925 lines had a first
  word a second or more ahead of its line (54 on 2.31.0). The same count on human word times
  (79 Jamendo songs, 3 204 lines): the HUMAN times are detached on 71 lines (2.2 %), the pipeline
  on 92 — 61 on both, and of the 31 pipeline-only ones the first word is within 200 ms of the human
  start on 21 and more than 500 ms early on 3. So a detached first word is mostly real (a pick-up
  or held word, sung where it is); the false ones are a small class, and "everywhere" is not
  explained by them. That part of the report stays open; more moments from the owner are needed.
- **Four candidates that read the emissions only.** "Move the first word to where a pass hears its
  first letter" in four tightenings; the fourth passed every bar on the 39 local songs (4 lines
  moved) and then failed the order above: archive rig 25 lines in 18 songs, one song vetoed (a
  one-word line lost its word: the stamp guard was compared on the wrong clock), two more short
  words than 2.34.0; human truth, 6 line starts moved, 4 better and 2 worse (a first word that was
  right pulled to a line whose other words had locked late). The owner's eight blind clips, drawn
  by rule: both of his own GOSSIP moments for the candidate; over the five lines moved through
  continuous singing, the candidate 3 (one weak) and the old document 2. Where the voice is
  continuous nothing in the emissions separates a right move from a wrong one.
- **The fifth, shipped:** the fourth plus an independent witness — the word moves only when the
  vocal stem is silent where it used to wait (the audit voice witness's own line and shortest
  silence), with the stamp guard on the document clock and ad-lib lines skipped. Pre-registered,
  then: 39 songs, 2 lines move (GOSSIP's opening and its 2:24 line, 6.8 s); archive rig, 8 lines in
  6 songs against a stated 8-10, every one on a silent stem, stem silence under the moved starts
  15.4 s → 63 ms, short words 4 861 → 4 860, no veto; human truth at ±400 ms identical to 2.34.0 on
  79 of 79 songs, at +3 s two line starts move onto the human start (−1.22 s → −26 ms, −1.24 s →
  −19 ms). Disclosed: the "9 frames in 10" share was written down after one check's output had
  been seen; the voice-witness bar passed by 0.001 (one line, GOSSIP 2:24, starts 0.5 s after the
  voice returns — the owner chose it blind).
- **Left open on purpose:** the owner's own 2:00 example (voiced) does not move. The labelled
  moments (his five voiced clips, the bench's moves against human truth, two archive lines) are
  listed in `version.py` for the next attempt. Also seen and not addressed: a held word lit as if two words (Best Night 0:50, both
  builds); a moved word can be stored 0 ms long (its display dwell is ≥ 120 ms).
- **What the rule needed changed what is kept.** It reads the stem's level, so the level envelope
  (one byte per 20 ms, exact integer arithmetic, plus the loudest frame's absolute level and a
  zero-crossing count per frame that no rule reads yet) became a runtime signal and part of the
  stored evidence before any archive evidence was recorded
  (`refresh-from-evidence-2026-11.md`); a replay moves the same words without audio (39 of 39).
- **Reviewer** (static read): safe to merge; its points on the envelope (absolute reference, a
  frame count bound to the track, integer-exact steps), the shortest silent span and four toothless
  tests were taken before the release, with every document of the three sets unchanged.

## The archive follows a release without downloads (2026-11-17)

The first use of `refresh-from-evidence-2026-11.md` end to end, on the live archive (425 YouTube
documents, most built by 2.31.0, some as old as 2.8.0). Working files `onizleme/`.

- **Back-fill.** The GPU helper recorded evidence for 400 songs (795 MB, ~25 s a song, no stored
  document touched, no job queued); every row carries the key the cluster worker expects. 12 cannot
  be recorded (8 videos gone, 3 offered at 128 kbps only, 1 the aligner refuses: its text needs
  more frames than a window has) and 13 are never candidates (a nightcore clock). Transient
  download and cookie failures passed on the wrap-round.
- **Preview** (server 0.41.0, in the worker's container, 19 min, 743 lrclib requests, nothing
  written): refresh 269, unchanged 33, keep 52 (vetoes W1 11, W2 12, W3 2, W4 39, overlapping;
  one by the persist guard: every worded line would be lost), review 26 (a line start moved more
  than 1.5 s where the envelope witness cannot judge; an aligner pass in the library's overrun
  region), sheet changed 9, needs a full job 11 (9 whose stored record is not the one the lookup
  finds today, so its window slices are not in the evidence). The four songs the desktop rig had
  vetoed for 2.34.0 and that are in this set came out `keep` again.
- **Refresh.** One canary, then the rest one at a time through the ordinary admin reprocess on
  workers in `EVIDENCE_MODE=replay`: 268 songs in 22 min (4.9 s a song with its lookup), every one
  logged `rebuilt from stored evidence, no download`. **The stored documents' counts equal the
  preview's on 268 of 268.** Before → after on those songs: words whose successor starts within
  120 ms 4 657 → 3 547 (share 0.0509 → 0.0386; fewer on 210 songs, more on 9), 0 ms words 357 →
  141, wordless lines 173 → 79, words starting before their predecessor 12 → 8, lines ending past
  the next line's start 52 → 24. Held back by hand: one `refresh` song with a NEW order violation,
  which no veto names (a veto to add). Refreshed by hand from `review`: GOSSIP, whose two moved
  starts are the ones the owner chose blind.
- **Open.** W4 (a moved line whose first word now flashes) keeps 39 songs on documents that are
  worse by every count; it was written for the lines one rule moves and is applied here to every
  line a five-release jump moves. It was not loosened after seeing the numbers: a narrower W4 is to
  be stated first and checked with a few blind clips. The other 25 `review` songs wait for a look
  (three of them move a start onto the voice's return by 1.5-1.8 s of silence). The aligner
  library's overrun region held 3 of the 400 songs, one of them the song whose back-fill had died
  with `AssertionError: n != i` and passed on the retry.
- **Two days later (2026-11-19): the two open lists, each by a rule written before it was run.**
  *Review:* a song is refreshed when every start that moved more than 1.5 s keeps or loses its
  silent lead and one of them loses at least 1 s of it (the start left a silence for the voice's
  return — the one class the owner preferred blind): 3 of 25 qualified and were rebuilt; the 22
  others (moves through continuous singing, whole sections carried, an overrun pass) wait.
  *W4:* restated as a count — veto when the built document has MORE lines whose first word
  flashes (< 120 ms) than the stored one — with W5, more words starting before their predecessor.
  Of the 29 songs only W4 had kept, the count releases 22, keeps 6, W5 keeps 1. Before any was
  refreshed the owner saw six blind clips from three of them drawn by rule, each song's worst W4
  line and the line where the built document gains most (the per-clip row draw had put the built
  document on top in all six, so the order was balanced by position before he saw them): on the
  worst lines he preferred the built row once, the stored one "very slightly" once and could not
  tell once; on the gains the built row twice (once "by a margin") and the stored one by "very
  little" once. The registered rule (gains for the built row >= clear losses) held, so W4 as a count
  is adopted and the songs were rebuilt; `preview_rules.py` still carries the old W4 until the next
  server release.
- **A mistake in that refresh, and the rule it leaves.** The 22 ids were fed from rows the preview
  had judged in `stored` lyrics mode only (the ladder confirmation runs for `refresh` rows, and
  these were `keep`). For one song the lookup now picks another lrclib record: its window slices
  were not in the evidence, the worker ran a full job, and a sheet nobody had previewed was stored
  (46 lines → 33). The feeder stopped there (it halts on anything not rebuilt from evidence), but
  the document had changed; a running job cannot be cancelled. It was put back by pinning the old
  record in a per-song override. **Every id handed to a reprocess must have been previewed in
  ladder mode with the same document coming out** — the remaining ids were, and replayed. (The
  restoring job ran in full on the CPU worker: 224 of 225 word starts equal the GPU-recorded
  replay's, one differs by 20 ms.)
- **The same evening: whole lines moved through continuous singing.** Six of those 22 had been
  `review` under the full verdict order (a line start moved more than 1.5 s where the envelope
  witness cannot judge; the old W4 veto had hidden it) and were refreshed with the rest — the
  second gap of the day in what a kept row shows (closed in 0.42.0: a row always carries its
  review reasons). Five of them move through continuous singing, the class one rule's first-word
  moves had left a coin toss. Registered before the clips: six blind clips from the five songs
  (each one's largest move, Low twice), order balanced by position; the class is accepted for a
  refresh when the built row is clearly preferred on at least two clips more than the stored one.
  He chose the built row clearly on four (Low twice "by a margin", Krazy, Break Your Heart), the
  stored row on none, and called Shots 3:29 and LET ME IN 0:27 wrong on both. So none of the five
  goes back, and of the waiting `review` songs the 15 whose moves stay within the 3 s the clips
  covered were previewed in ladder mode: 14 passed every veto (W4 as a count, W5) and were rebuilt,
  each stored document equal to its preview; one stays (flash count up). Section carries of 10-16
  s (three songs) and a 19-line move were not what he judged and wait. A seventh clip put NO
  FEAR!'s two lrclib records side by side: both read wrong to him at the opening; the pin stays.
  2.35.0 documents in the archive: 307 of 425.

## Lines of work and order

Three lines can run at once; they share only the document schema and the server API.

| line | order | first step |
|---|---|---|
| timing and measurement | A → B → D | A.1's pilot arms and clips |
| source | C (C.1 can start beside A) | the overrides table |
| client | E.1-E.3 now → E.4 → F, G | the lead measurement |

Recommended start: A at once (it is measured and small), E.1-E.3 beside it, B before any further
timing change, C.1 early because every later repair depends on it.

## What this review could not verify

- Production values (gitops, the live worker's env) were not read; the env example stands in.
- The vote change's effect on real lrclib stamp errors beyond 400 ms, and on the pilot's heard-fine
  moments, is unmeasured until A.1's clips.
- Song-sampling noise on 19-20 songs is not estimated; one song can move 1-3 lines.
- The source experiment's rest-of-archive scan stopped at 31 of 189 songs (one download refused).
- GOSSIP's middle and the newly found off-sheet songs were read from the ear, not listened to.
- The repair-overwrite risk and several server findings are read from code, not run.
- omniASR was measured through a third-party port; the official weights may differ.
- Device-level claims for Android and every "estimated" display delay are unverified.
