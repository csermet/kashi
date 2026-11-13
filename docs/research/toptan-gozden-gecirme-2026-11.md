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
