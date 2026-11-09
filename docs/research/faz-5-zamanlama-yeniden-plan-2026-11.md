# Faz 5 — the timing re-plan after the perceptual audit (2026-11-08)

**Status: plan — decided 2026-11-08 evening (owner, in session): option 1, timing v3
on the current chain, run as a fast loop (see "Owner's decision"); no code yet, no
pipeline change, no tag.** (Later: 2.30.0 is live since 2026-11-09, see "2.30.0 live".) Written in plan mode from the audit side-path's records
(`perceptual-audit-2026-11.md`, `yerel-model-denetim-2026-11.md`, the 2.29 / 2.29.1
paragraphs of `faz-9-pencere-saati-2026-10.md`) and the branch `worktree-dinleme-4-olcum`.
The next hand-off note is this one's successor once the owner has chosen a path.

## Where things stand

- **Live:** pipeline 2.29.0 (server image 0.33.1 on both the API and the worker,
  read from the cluster 2026-11-08). The archive refresh to 2.29 has been on hold
  since 2026-10-31.
- **Built and measured, not shipped:** pipeline 2.29.1, in two halves. The first
  half (word lengths back after line QA, 234f455) is in `main` through PR #9,
  untagged. The second half (a shifted lrclib section moves before alignment,
  `pipeline/sheet_shift.py`, 3120e80 + fc04715) is only on the branch; its PR is
  on the owner's agenda. The owner's word (2026-11-08): tag and refresh day wait,
  other work continues. This plan assumes 2.29.1 ships soon but does not depend on it.
- **The audit side-path is finished:** Faz 0-4b (tool, 96 moments, `audit.compare`,
  `audit.run_local`), Faz 3.5 (local ear and summarizer trial, every gate but the
  token fit failed), and the owner's listening set (29 labelled moments, 2026-11-07).

## What the owner heard, and what 2.29.1 reaches

24 heard errors on 2.29, moment-level classes from the frozen reading
(`yerel-model-denetim-2026-11.md`, "Listening set"):

| heard class | moments | what it is | 2.29.1 | left after 2.29.1 |
|---|---|---|---|---|
| lyrics source | 8 | GOSSIP's record 17-19 s late (2), Got 2 Luv U's verses 3-5 s late (3), Enemy's sheet from another mix (3) | the sheet shift moves GOSSIP −18.96 s (lines 1-24) and Got 2 Luv U −3.72 s (lines 16-82); noticeable-line share 0.83 → 0.19 and 0.77 → 0.25 | Enemy: another mix's verse, same length; no timing rule can see it |
| line starts 0.5-1 s off | 10 | 6 late, 4 early, 8 songs; the same in 2.28 and 2.29 at 7 of 10 | untouched (every word start identical on 20 of 20 songs) | **all 10** — the largest remaining group |
| duration collapse | 5 | 3 piles the owner saw, 2 words that never light after a pile | word lengths back (0 ms words 7.6 % → 2.5 %, under 60 ms 22.3 % → 5.7 %) | the piled **starts**: 47 runs of 3+ words starting within 60 ms on the pilot set (2.28: 18); no end repair moves a start |
| wordless line | 1 | *Öp*'s chorus line, unworded although the ear spells it in order | untouched | 1 |

So 2.29.1 answers 5 of 24 directly and softens the duration class; 14-16 heard
errors remain, and 10 of them are line starts.

### The ten line starts, by mechanism

Read from the frozen claims (`scratch/audit/review.jsonl`, numbers, no audio) next
to the owner's answers. Four mechanisms plus one that is not timing; the letters are
used below.

| | mechanism | moments | evidence |
|---|---|---|---|
| **A** | short repeated lines inside one merged window land one slot off, or cram onto one repeat | SexyBack's three "You ready?" (the first 1.2 s late, the previous line's tail boxed over it); Hey Mama's "hey mama, ma" hooks; Got 2 Luv U's four "Got 2 Luv U" crammed into 0.7 s (counted as a pile) | `windows.py` merges lines until a window is ≥ 4 s and ≥ 3 words, so two-word lines share a window and nothing inside the window holds a line near its own stamp; the arbiter's nudge needs ≥ 3 words of evidence (`MIN_WORDS_FOR_EVIDENCE`) and cannot speak for them |
| **B** | a line after silence starts off the voice's return | New Rules 2:54 (voice back at 174.45 with a held note, the line at 175.08, heard 0.5 s late); BxMxC 0:13 (shout after a short silence, late); BxMxC 2:06 and Deli Oğlan 1:57 (0.6-0.7 s early; a background "eeey" sits between) | `audit.score`'s voice witness (≥ 500 ms silence, return within 1.5 s) already finds these; line QA has a voice-entry walker (`_voice_entry`) but uses it only for parenthetical responses |
| **C** | piled starts from the `segment` / whole-track stars | SexyBack 2:19 ("with" lit 1 s early, "be gone" never visible), Take It Off's stutter, CEO's "I run it like", Crazy In Love's nine words in 0.8 s | words the hybrid or the vote place keep 100-140 ms; a `<star>` before every word swallows the silence; the `edges` pass keeps a 300 ms median and 3 runs on eng20 against the hybrid's 14 |
| **D** | no evidence or held notes: chopped repeats the ear cannot spell, the next line starting inside a held vowel | New Rules 3:07 (four beat-spaced "don't", no letters until the fourth, 0.36-1.0 s early); Deli Oğlan 0:47 ("Hadi" 0.7 s early inside the held "oğlan"); Hey Mama 0:06 (held "woman") | CTC has no consonant to hold the boundary; any placement inside the held region costs the same |
| **E** | structure the sheet does not carry | Crazy In Love 1:58 (a second voice sings the parenthetical mid-line; the document lights it at the end); Stressed Out 1:36 (a stutter "m-m-my" that is not written); the shout in Deli Oğlan 1:57 | not timing: what is sung and by whom differs from the sheet |

A rule set reaches A, B and C, about 7 of the 10 moments plus the crammed repeats
counted under piles. D is partly reachable through A (n identical words, n voice
returns). E is the meaning class and belongs to the Faz 5 candidate below, as labels,
never as times.

## Three ways for the line starts

### 1. Timing v3: three measured rules on the windowed chain — recommended

The chain stays (separation → lrclib-anchored windows → hybrid stars + whole-track
vote → line QA). Three rules, each proven by mutation and by a boundary-case
differential harness before any bench run, shipped together as pipeline 2.30.0.

- **V1 — a line's own stamp holds inside a merged window (A, part of D).** Inside a
  window that owns several stamped lines, each line's words may not start more than
  `LINE_REGION_MS` (start 1 200 ms, the bench chooses) before its own stamp or after
  the next line's, applied as a constraint on the trellis (the Viterbi path is cut to
  the region), not as a shift afterwards. This is not the soft offset (dead: a rigid
  post-hoc shift that injected anchor jitter into words); the words still land on
  acoustic evidence, the region only forbids the wrong repeat. The bench's
  `--anchor-jitter-ms 400` measures the jitter risk directly.
- **V2 — a line after silence starts where the ear hears it start (B).** For a line
  that follows ≥ 500 ms of document silence, if the voice returns within 1.5 s of the
  line's start AND the ear's letters at the return match the line's first letters
  (the `audit.textmatch` rule), the line block-shifts onto the return (the flagged
  rescue's block move; words keep their spacing). Without matching letters nothing
  moves: that is what keeps a background shout from pulling the line (Deli Oğlan,
  Instruction). Onset snapping per word stays dead; this is one line start per
  silence, with text confirmation.
- **V3 — a piled run falls back to the `edges` clock (C).** When the hybrid or the
  vote leaves k ≥ 3 consecutive words starting within 60 ms, those words take their
  `edges` starts (the same mechanism 2.29.1 uses for ends: the pass that keeps
  silence). Measured target: eng20 runs 14 → ≤ 5 with PCO@0.1 within 0.005 of 2.29.1.

Cost: ~3 weeks of implementation sessions in total (V3 ~1 day, V2 ~2 days, V1 ~1 week
with the harness, measurement and the pilot pass around them) plus two GPU bench
rounds (~1 h each on the desktop). Risk: V1 is a change inside the aligner's
decoding; the harness must hold the 2.29.1 output byte for byte when
`LINE_REGION_MS` is infinite.

### 2. Plain-lyrics timing from scratch — not now

Drop the lrclib stamps, align the whole song and take the line structure from the
ear: unique lines placed by the checkpoint's letters (the `sheet_shift` machinery)
become the stamps. What it would buy: independence from lrclib's stamps (the three
worst Faz 3 failures), and a timing path for the archive's line-mode documents
(about 10 of 318, mostly non-Latin) and for records lrclib holds only as plain text.
Why not now: the stamps are right on most lines (a healthy song reads a median of
+20..+90 ms against them), whole-song CTC loses lock on 27 of 79 Jamendo songs
without them (`windows.py`), and the ear's verse-level placement is 0.44
(Faz 3.5 Step 1) — nowhere near a stamp replacement. Estimated 6-8 weeks before it
equals today's output on the healthy 90 %. It stays on the roadmap as 9.7, after
V1-V3 and the meaning checks, reusing their pieces (ear anchors, text-confirmed
voice returns).

### 3. Other roads, already dead

Render-side minimum display time (measured weak: collapses are placement errors),
LLM timing (Faz 3.5: inconsistent with numbers), per-song or per-position offsets,
onset snapping, repetition medians — all in `olu-fikirler.md`. Nothing new here
reopens them.

## Acceptance — what "better" must mean

The audit's two witnesses (ear letters, voice return) are **inputs** to V2, so a score
built on them cannot judge V2: it would read zero by construction. Hence three layers,
the first on human truth.

1. **Human truth (ships or not).** `benchmarks/run.py` on the desktop GPU, eng20 and
   all 79, `--windowed --anchor-jitter-ms 400`, 2.29.1 against v3 on the same seed:
   PCO@0.1 and @0.3 not lower than 2.29.1 by more than 0.005 per language; gross
   errors (> 500 ms) not up; word-end MAE not above 2.29.1 (unmeasured on the bench
   since 2.29.0: 368 ms); hygiene runs of 3+ down. **New metric, added before the
   first run:** the line-start noticeable share — first word of each line against
   the human start, late > +220 ms or early < −330 ms — must fall on eng20 and on the
   pop valid region. Line starts are what the owner hears; PCO never isolated them.
2. **Named moments (the pilot set).** `audit.run_local` on the 20 songs (stems and
   records cached), then `audit.compare` 2.29.1 → v3 with the verdicts
   (`review.jsonl` + `listening.jsonl`): of the 10 line-start moments ≥ 6 read
   `bad:fixed?`, and `good:broken?` = 0 across the 5 moments the owner heard as fine
   and the 20 `ok` controls. `audit.score` stays a per-song guard for V1 and V3 only.
3. **The owner's ear, 15 minutes.** Only the moments the compare says moved (≤ 10),
   same menu as the fourth list: ≥ 6 of the 10 heard fixed, no new complaint among the
   five that were fine. Not the whole set again.

Shipping follows the standing policy: tag, the 20-song pilot refresh, and a full
archive refresh only if layer 1 shows a large gain (≥ 5 points somewhere) or the
owner calls the pilot change large. The refresh runs on the GPU helper worker
(~1 min/track, ~5 h for the archive).

## The Faz 5 candidate: meaning questions for a local LLM — pre-registration

The owner's candidate (2026-11-08): feed the ear's text and the sheet, section by
section, to the desktop's local LLM (Gemma 4 26B-A4B, as in Faz 3.5 Step 3) and ask
**meaning** questions only. Timing is never asked (Faz 3.5: summarizers were
inconsistent with numbers; no audio LLM can judge it). It replaces the one Faz 3.5
rule that failed on false alarms (A3 `wrong_sheet`, 15 of 79) and gives class E its
labels. Everything below is fixed in `audit/gates.py` before the first run, as in
Faz 3.5; a gate that fails is not re-tuned on the same data.

- **Inputs per section.** Sections are the sheet's blank-line blocks (or eight-line
  blocks when a record has none). Prompt: the section's lines as text; the ear's text
  for the section's span ± 2 s, words in order, no times (CTC letters grouped at the
  ear's gaps; the Qwen3-ASR pieces as a second arm where they exist). Temperature 0,
  one fixed prompt, JSON output with closed enums and a quoted-evidence field.
- **Three questions.** Q1 per song, from the sections: `same_song` /
  `other_version` / `unsure`. Q2 per heard-but-unwritten chunk: `lyric_missing` /
  `background_shout` / `adlib_or_stutter` / `ear_noise`. Q3 per written-but-unheard
  line: `not_sung` / `sung_but_unheard` / `unsure`.
- **Data and gates.** (a) 79 unmodified JamendoLyrics sheets with the existing ears:
  Q1 `other_version` on ≤ 3 songs (≤ 5 %). (b) The Faz 3.5 injected sheets (deleted
  verse, extra paragraph): Q3 `not_sung` on the extra paragraph and Q2 `lyric_missing`
  on the deleted one at ≥ 0.90 paragraph-level recall. (c) Enemy: Q1 `other_version`
  naming the JID section; the hardstyle negative (both ears hear a verse the sheet
  lacks) is reported, not gated. (d) The pilot's three background shouts (Deli Oğlan
  1:57, Instruction 2:37, Öp 2:29) and two stutters (Stressed Out, Take It Off): Q2
  right on ≥ 4 of 5. (e) Three runs on identical inputs: label agreement ≥ 0.95 on Q1
  and ≥ 0.90 on Q2/Q3 (Gemma's Faz 3.5 run-to-run was 0.56 on severity numbers;
  labels are easier, the gate stays).
- **Role if every gate passes.** Q1 `other_version` → a document flag
  (`lyrics_version_mismatch`, additive schema change, codegen) that makes the overlay
  show plain lyrics and lets the lookup try the next record (a rung change:
  `LOOKUP_LADDER_VERSION` bump). Q2 `background_shout` → excluded from V2's voice
  return and from `audit.score`'s voice witness. Q3 `not_sung` → the line ships
  plain and marked. Nothing moves a word.
- **Cost and time.** $0 (local). About 79 songs × ~10 sections × 3 runs on the
  5070 Ti, roughly an hour per run; the ears exist (`scratch/audit/jamendo-ears`,
  the pilot whole ears). ~2 days of an implementation session including the harness.
  Can run beside v3; nothing in the pipeline changes until the gates pass.

## The 2.29.1 refresh condition — measured first

The plan's own condition for a full refresh ("in `audit.compare`, moments worse than
2.28 ≈ 0 and most known-bad moments fixed") has not been measured for 2.29.1:
`scratch/audit/runs/step2291/score.json` is empty, and no compare output exists. The
inputs do: the 2.28 documents (`scratch/favori-seti/once.json`, 20 songs), the live
2.29.0 ones (`sonra.json`), the 2.29.1 arm (`step2291/<id>.new3.json`, 20 songs,
`pipeline_version` 2.29.1), the 20 stems and whole ears. **Step 1 below runs it; the
result goes into this note before anything is tagged.** Expected from the per-song
numbers: `good:broken?` ≈ 0 (every start identical on 18 songs; GOSSIP and Got 2 Luv U
move as read), `bad:fixed?` on the 5 lyrics-source moments and some of the 5
duration moments, `bad:unchanged` on the 10 line starts. If `good:broken?` is not ≈ 0
the tag waits for a reading of each such moment.

**Measured 2026-11-08 (timing v3 session, `--audio`, both verdict files): the
condition holds.** 2.29.1 = the 20 `new3` documents.

| before → 2.29.1 | verdicts | known-good: broken? / moved / kept | known-bad: fixed? / worse? / changed / unchanged | other |
|---|---|---|---|---|
| 2.29.0 | review (96) | **0** / 41 / 3 | 23 / 1 / 21 / 1 | unsure 5, disputed 1 |
| 2.29.0 | listening (30) | **0** / 5 / 0 | 9 / 1 / 13 / 1 | unsure 1 |
| 2.28 | review (70 apply) | 4 / 30 / 3 | 15 / 8 / 10 / 0 | n/a:version 21, unsure 4, disputed 1 |
| 2.28 → 2.29.0 (reference) | review (70 apply) | 10 / 24 / 3 | 12 / 10 / 11 / 0 | same |

The listening records carry no `better_version`, so against 2.28 all 30 are
`n/a:version`. Against the live 2.29.0 nothing heard-fine or reviewed-fine breaks;
the "changed" rows are the ends coming back (words moved 0 of n). Against 2.28 the
four `broken?` are all 2.29.0's and stay from it (2.29.1 moves no start there): two
in Gasolina (0:29.9, 2:23.8, piled 0 → 4 and 2 → 6), New Rules 1:10.2 (one word
350 ms earlier, the line's ear error 143 → 113 ms) and BxMxC 0:46.5 (two 0 ms
words); three of four are numbers-only, and the piles are class C, V3's target.
2.29.1 cuts 2.29.0's ten `broken?` against 2.28 to these four. The one `worse?` in
each verdict file is GOSSIP's second chorus (2:10.1, 2:34.2): it is unanchored and
stays 3-5 s late after the sheet shift (ear error 5 360 → 4 850 ms; reading "same"),
as the sheet-shift paragraph of the Faz 9 note says. No reading blocks the tag.

## Found at the 2.30.0 rollout: lrclib's word-timed files bypass the pipeline (2026-11-09)

Reported by the rollout session after the pilot refresh on 2.30.0. lrclib.net now serves
its `lyricsfile` field (human word sync) on many records, and the worker's fast path
(`process.py`, "lyricsfile fast path … CTC skipped"; `pipeline/lyricsfile.py` calls it
"strictly better than anything CTC can derive") takes it as is: no separation, no
alignment, no line QA, quality 1.0. On the 20 pilot songs **8 took the file** (Stressed
Out, New Rules, SexyBack, Sorry For Party Rocking, GOSSIP, Mor, Gasolina, Instruction);
the 12 that went through CTC match the local v3 arm word for word on 11 (Got 2 Luv U
chose another record). In the archive **109 of 290 candidates** carry a file. The files
are lrclib's own, not Kashi's publications (they carry `language` / `instrumental`
fields and their times differ from our documents).

Measured, `audit.compare` live 2.29.0 → live 2.30.0 with the verdicts: every one of
the **20 `good:broken?`** labels on the review set comes from 6 of the file songs; the
listening set shows 5 `bad:fixed?` (Stressed Out, New Rules, GOSSIP) and SexyBack
2:18.6 piles 8 → 16. So the "strictly better" assumption in the module docstring is
not what the pilot set shows: the crowd word times fix some heard errors and break
more reviewed-good moments, and they are served without any check against the audio.

Consequences for this plan:

1. **The fast path is a fourth timing source the plan did not count.** With it on, v3
   (and V1) never touch 40 % of the pilot set and ~38 % of the archive; every
   acceptance number above is about the CTC path only.
2. **Decision asked of the owner by the rollout session (its question 42):** keep the
   file as is, or switch the fast path off and align every record ourselves. This
   note's recommendation: **off for now** (one setting, pipeline 2.30.1 or 2.31.0,
   then the archive refresh covers all 290), and the file kept as a **witness**, not a
   source, until it is measured: on the 8 pilot songs, file word starts against our
   v3 starts and against the ear (`audit.score` line timings); a later gate may take the
   file only where it agrees (for example, 80 % of words within 150 ms of our starts)
   and fall back to CTC elsewhere. That measurement is a half-day job and belongs
   before any decision to trust the file again.
3. The listening list for 2.30.0 (`scratch/audit/caner-dinle-5.md`) carries three
   optional file moments (10-12) so the owner's ear enters this comparison too.
4. `olu-fikirler.md` gets a row only after the measurement, not from this reading.

## Proposed roadmap rows (applied after the owner's answer)

Phase order changes only by the owner's decision; this table is the proposal that the
answer below approves or changes. Rows 8/8.1, 9.2 and 9.3 collapse to one "done"
line each pointing at their notes; the Faz 9 row's history moves here.

| Phase | Theme | State |
|---|---|---|
| 8-9.3 | Licence-clean chain, windowed clock, LyricFix, GPU helper worker | done — `hizalama-yontem-adaylari-2026-08.md`, `faz-9-pencere-saati-2026-10.md`, `lyricfix-lrclib-availability-2026-10.md`, `docs/gpu-worker.md` |
| 9 | Perceived accuracy, English | closing — 2.29.1 built and measured (word lengths back, shifted sheets moved); tag and pilot refresh on the owner's call; what the owner still hears is 9.4-9.6 (`faz-5-zamanlama-yeniden-plan-2026-11.md`) |
| 9.4 | Timing v3: line starts (V1 stamp regions, V2 text-confirmed voice return, V3 pile fallback) | next — acceptance in the Faz 5 note; ships as pipeline 2.30.0 |
| 9.5 | Turkish parity (calibration) | after 9.4 — needs an independent Turkish truth first (a hand-corrected 100 ms set; today's set is 92-99 % our own output) |
| 9.6 | Lyrics-sheet meaning checks (local LLM, labels only, pre-registered) | research beside 9.4 on the desktop GPU; enters the pipeline only past its gates |
| 9.7 | Plain-lyrics path (ear anchors as stamps for line-mode and plain records) | after 9.4 and 9.6 |
| 10 | Effects (incl. the unfinished "poison" archetype, song-mood-aware effects) | planned — independent of timing; may run when 9.4 waits on GPU or owner time |
| 11 | Hardening | planned |

Why timing first: every later phase measures against the timing (Turkish calibration,
effects keyed to words), and the owner's own complaints are all timing or lyrics.
Effects and hardening do not wait on anything and fill the gaps.

## Owner's decision (2026-11-08, in session)

**Option 1, timing v3 on the current chain — and faster than the sequence below.**
The owner's words, condensed: a few weeks is too long; do not go this tested and
cautious; take more risk for speed and for the chance of an unexpected quality gain;
try things, grow the test count as you go instead of measuring 100 songs after every
step; quality still matters at the end, so not a toy on a handful of songs either.

What changes in the method:

- **The loop is the 20-song pilot set, not the bench.** Each rule change runs
  `audit.run_local` on the 20 (stems and records cached) and is read through
  `audit.compare` against 2.29.1 with the verdicts plus the hygiene counts, the same
  day. The JamendoLyrics bench (human truth) runs **once per tag**, not per rule.
- **V3 and V2 first, shipped together as 2.30.0 within days**, after one pilot compare
  and the owner's 15-minute listen of the moved moments. V1 (the trellis region) follows
  as its own tag; it is the one change that needs the differential harness.
- **One proof per rule, not a ceremony:** one test that fails when the rule is removed
  (the house mutation rule stays, because toothless guards were the pattern here), no
  boundary harness for V2 and V3.
- **The test set grows with the rules:** 20 → the next favourite-set songs the owner
  names → the bench, in that order. A rule that reads well on 20 goes to the tag; the
  bench decides whether the archive follows (the refresh policy is unchanged).
- The roadmap rows above are applied with this decision.

## 2.30.0 measured — timing v3, first round (V3 + V2), 2026-11-09

Branch `worktree-zamanlama-v3` (96e3e06, on top of fc04715, so it carries 2.29.1's
sheet-shift half too). Not tagged, not deployed, no refresh. 20 pilot songs through
`audit.run_local`, same stems and records as the 2.29.1 arm (`step2291/<id>.new3`),
CPU, ~7 min a song; scripts in `~/Documents/krediler/kashi-v3-betikler/`.

**What shipped as code.** V3 (`windows.unpile_starts`): a run of 3+ words starting
within 60 ms (inclusive, the bench's rule) that the hybrid or the vote made takes its
`edges` starts. The first version clamped those starts between the neighbours, and the
first finished song showed why that was not enough. In CEO 1:03 the word after the pile
("a") was squeezed too, the clamp piled "it like" onto it again, and the named moment did
not move. So the block now carries along the words after it that the `edges` starts
overtake, and it moves only when the block plus both neighbours come out with fewer piled
words. Where `edges` piles too, or a clamp would pile the block onto a neighbour, nothing
moves. This was the round's one allowed adjustment; the 60 ms / k = 3 threshold stayed,
because the remaining piles are `edges`' own (see below). V2
(`line_qa.shift_to_voice_return`): a line after >= 500 ms of silence (the end the
previous word gets back after QA, `held` included) moves as a block onto the voice's
return when the return is 400-1500 ms away AND the sheet shift's ear (`heard_tokens`,
no second pass) hears a 3-letter run of the line's first six letters, starting in its
first three, from 100 ms before to 600 ms after the return. The 400 ms floor: on the
pilot set's healthy lines the energy return sits 115-360 ms before the aligner's
calibrated start (consonant and breath); the heard errors were 500-1000 ms. Each rule
has one test that fails when it is removed; V3's carry and its no-gain guard each have
one more.

**Numbers, 2.29.1 → 2.30.0.**

| | 2.29 | 2.29.1 | 2.30.0 |
|---|---|---|---|
| runs of 3+ starts within 60 ms, per line | 47 | 30 | **13** |
| the same, whole stream (bench rule) | 59 | 44 | 28 |
| 0 ms words / under 60 ms | 7.6 % / 22.3 % | 2.7 % / 5.6 % | 2.3 % / 5.3 % |
| line QA nudges / response shifts / voice returns | 17 / 3 / – | 15 / 3 / – | 14 / 3 / 0 |

`audit.compare` 2.29.1 → 2.30.0 with `--audio`:

| verdicts | known-good: broken? / moved / kept | known-bad: fixed? / worse? / changed / unchanged |
|---|---|---|
| review (96) | **0** / 1 / 43 | 6 / 1 / 2 / 37 |
| listening (30) | **0** / 0 / 5 | 3 / 2 / 1 / 18 |

The fixed piles: CEO 1:03 ("I run it like a CEO" back to 63.09 / 63.13 / 63.23 / 63.37 /
63.48, the spread the review called right in 2.28), Hey Mama 1:41 and 2:44.8, Crazy In
Love 0:59, Heavenly 0:56, Sorry For Party Rocking 1:58. The two `worse?` labels both
move toward the reviewed place. Hey Mama 0:08.9: "be" 8.90 → 8.69, the loudness-backed
spot the review gives for 2.28. SexyBack 2:18.6: "'head" 138.65 → 138.47, the ear's
"goheby" is at 138.50, but it now starts with "(go" and "be gone with" stays piled.
The lost nudge is in Enemy (3 → 2), a wrong-text song.

**Against the plan's named moments: 1 of 10 line starts and 1 of 4 piles.**

- **V3 (C).** CEO 1:03 is fixed. Take It Off 1:52.6, SexyBack 2:19 and Crazy In Love
  0:18 are not: `edges` piles the same words, so falling back to it gains nothing. A
  debug dump confirmed this for each; for example, all three clocks put "Got me hoping"
  in one frame.
- **V2 (B).** It moved nothing:
  - New Rules 2:54 already reads right on these inputs: the local 2.29 and 2.29.1 runs
    start the line at 174.52 s, where the live 2.29.0 document has 175.08 s (input
    difference, not code).
  - BxMxC 0:13 and 2:06 are Japanese lines: no Latin letters to match.
  - BxMxC 2:06 also has no gap any more once 2.29.1 gives the ends back.
  - The one candidate the rule found (SexyBack 3:33, −1.17 s) was taken first by the
    response rule.
  - Deli Oğlan 1:57 and Instruction 2:37 stay unchanged, as the guard intends.
- **The other line starts** (A and D, e.g. SexyBack's "You ready?" chain) are V1's.

So the acceptance line "≥ 6 of the 10 heard line starts fixed" is not met by this round,
and could not be: it was written for V1 + V2 + V3 together.

**Found on the way.** After the sheet shift, GOSSIP and Got 2 Luv U run whole-audio
alignment: the shifted stamps no longer pass the window planner, so neither song
reaches the hybrid, the vote or V3. Their piles (Got 2 Luv U's crammed repeats) belong
to that path. This is 2.29.1 behaviour, worth a look before the tag.

**Next.**

1. The owner's 15-minute listen of the 9 moved moments
   (`scratch/audit/caner-dinle-5.md`). It needs 2.30.0 documents on the server, so it
   follows the tag and the 20-song pilot refresh (the owner's separate yes).
2. The eng20 bench once before the tag (GPU desktop): `--no-unpile` reproduces 2.29.1
   for the A/B.
3. V1 (stamp regions) for the line starts.
4. A candidate for the piles `edges` shares: none of the three clocks separates them, so
   only a spread inside the line's own span could. That is a new idea, not in
   `olu-fikirler.md`, and unmeasured.

## 2.30.0 live — 2026-11-09

Tagged `server-v0.34.0` (ac6a82e) on the owner's go, without the eng20 bench of
"Next" 2; gitops MR !168; API and cluster worker on 0.34.0, both workers log
`ready: pipeline 2.30.0`. The 20 pilot songs were refreshed at 17:44-18:01 UTC, mostly
on the GPU helper (~45 s a song): 20 of 20 on 2.30.0. **12 went through the aligner, and 11
of those equal the local `fin` arm word for word** (Take It Off and Crazy In Love at 98-99 %
of starts within 20 ms). Got 2 Luv U now takes another lrclib record (7835188, windowed,
q 1.0). Against the live 2.29.0 documents, no known-good moment on these 12 is `broken?`.

**The other 8 skipped alignment:** lrclib's new word-timed `lyricsfile`, see "Found at the
2.30.0 rollout" above. Because of that, rows 8-12 of `caner-dinle-5.md` judge those files,
not V3 or V2 (two original rows plus three added ones; key `caner-dinle-5.key.json`). The
archive refresh feeder (`scratch/toplu-2.30/`, API only) holds the 109 file songs until the
owner answers question 42.

## Sequence (as planned before the decision; the loop above overrides the pace)

1. **Measure the 2.29.1 refresh condition (now, cnr-intel, ~1 h).** `audit.compare`
   2.28 → 2.29.1 and 2.29.0 → 2.29.1 with both verdict files, `--audio`; write the
   four class counts into this note. Also run the bench's hygiene over the arm.
2. **Bench metric (half a day).** Add the line-start noticeable share to
   `benchmarks/metrics.py` (asymmetric thresholds, per song, LOSO-safe), pin it in a
   test, and rerun eng20 for 2.29.1 so v3 has its baseline, ends included.
3. **V3, then V2, then V1 (~3 weeks),** each: harness with boundary cases → mutation
   → bench on eng20 → the next rule. Ship together as 2.30.0.
4. **Pilot pass (1 day + the owner's 15 min):** `run_local` on the 20, `compare`
   against 2.29.1 with the verdicts, the owner listens to the moved moments only.
5. **Tag and refresh** on the owner's yes (not started by this plan).
6. **Beside 3:** the meaning-check pre-registration and its runs on the desktop GPU
   (~2 days), reported here as a Faz 3.5-style table.

## Decision requested from the owner (answered above: option 1)

One question, two options. Either answer also approves the roadmap rows above under
that option.

- **Timing v3 on the current chain (recommended):** three measured rules, ~3 weeks
  plus two GPU bench rounds; ≥ 6 of the 10 heard line starts must be fixed with
  nothing heard-fine broken; roadmap 9.4 → 9.5 → 9.6 → 9.7 → 10 → 11.
- **Plain-lyrics timing from scratch:** drop the lrclib stamps for ear anchors,
  ~6-8 weeks before parity on healthy songs; also serves the ~3 % line-mode archive;
  the lock-loss class (27 of 79) returns until the anchors mature; 9.7 moves ahead of
  9.4.

Neither answer tags 2.29.1 or starts a refresh; those stay the owner's separate yes.

## What this plan could not verify

- The task brief named "Pipeline 2.29.1" sections at the end of the Faz 9 note; they
  exist only from `main` 402aff5 on (the local checkout was behind until this
  session pulled it).
- The bench has not run 2.29.1 at all; its end MAE and hygiene for 2.29.1 come with
  step 2.
- Which of the 10 line-start moments V1-V3 reach is read from the frozen claims, not
  from a run; the ≥ 6 of 10 bar is set so that the reading can be wrong on four.
