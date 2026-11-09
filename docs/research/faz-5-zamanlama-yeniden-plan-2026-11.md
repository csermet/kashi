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
to that path. This is 2.29.1 behaviour, worth a look before the tag. **Fixed in 2.30.1
(below).**

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

## 2.30.1 — the sheet shift carries the neighbours it overtakes (2026-11-09)

Branch `worktree-sheet-shift-pencere`, merged to `main`; not tagged, not deployed.
On the live 2.30.0 neither pilot song runs this path today: GOSSIP takes lrclib's
word-timed file and skips alignment, and Got 2 Luv U now finds another record (see
"2.30.0 live"). The fix counts for every song that goes through CTC with a moved sheet.
On JamendoLyrics that path lost the windows half the time, and GOSSIP is back on it if
the fast path goes off (question 42). The version number 2.30.1 is used by this change, so
the fast-path switch, if it comes, takes the next one.

**Cause, measured.** `windows.plan_windows` returned None for both songs because the
shifted stamps were out of order (`_stamps_usable`'s monotonic check). The sheet shift
moves a section's anchored lines only, and the unanchored lines just before it kept
stamps later than the section's new first stamp:

- GOSSIP: line 0 ("Welcome to the city of lies") stays at 31.72 s, line 1 moves to
  15.98 s. Line 0 is unique and the ear places it (−17.45 s), but its offset is 1.34 s
  from line 1's, past the section's 1 s agreement, so it is a lone anchor and dropped.
- Got 2 Luv U: lines 14 and 15 (two of the 16 intro "Got 2 Luv U" repeats, no
  anchor) at 21.81 / 23.08 s, line 16 moves to 20.83 s.

Whole-audio alignment then hid a bigger error. Line QA snapped GOSSIP's line 0 onto its
stale stamp (31.61 s, 17 s late), and lines 1-7 got that line start too, with their
words at 16-31 s. The overlay picks the displayed line by line start
(`view-logic.lastStartedAt`), so in 2.29.1 and 2.30.0 the first verse never appears.
`audit.score` measures from the first word and does not see it.

**Where those lines are sung.** The whole ear hears GOSSIP's line 0 at 14.40 s
(−17.45 s from its stamp, the section's offset −18.96 s). It hears Got 2 Luv U's intro
as 16 repeats from 0.22 s to 19.86 s; the 16 stamps moved by the section's −3.72 s land
one for one on them (line 0 clamped to 0). So the overtaken lines are late by about the
section's offset. Blanking their stamps (the first idea) would not reach them: GOSSIP's
line 0 would become a stampless leader in line 1's window, whose slice starts at 15.63 s,
after the line is sung.

**The rule (`sheet_shift._carry_overtaken`).** A stamped line just outside a moved
section whose stamp the move overtakes (later than the next stamp for a section moved
earlier, earlier than the previous one for a section moved later) moves by the
section's median. The walk goes outward while the order stays broken. It passes over
stampless lines and stops at any line of an anchored section, moved or not. A stamp
equal to the moved one is in order and stays. GOSSIP's second chorus (3-5 s late,
unanchored) stays where it is: its stamps are in order, so nothing carries it. That is
the case the 2.29.1 note warned about (moving it would leave it 15 s early). One test, both
directions and the equal-stamp boundary. It fails when the carry is removed, when `<=`
becomes `<`, and when the forward branch is dropped. A second test holds the stop: two
sections whose ears disagree leave the stamps out of order rather than move an anchored
line against its own placement. It fails without the stop.

**JamendoLyrics (79 songs, the audit's ears; human stamps as truth).** Each song was run
unmodified, with the whole sheet shifted ±5 / ±15 s, and with one 4-line verse shifted
±5 / ±15 s at a seeded position. The pipeline's `shift_sheet` ran old vs new:

| | 2.30.0 | 2.30.1 |
|---|---|---|
| false alarms (unmodified) | 0 of 79 | 0 of 79 |
| whole-sheet ±5 / ±15 s found | 74/79 each | 74/79 each |
| verse +15 / +5 / −15 / −5 s found | 28 / 36 / 30 / 30 of 79 | same |
| offset error median / p90 | 64 / 125 ms | same |
| moved sheets whose stamps the windows accept | **185 of 420** | **388 of 420** |
| lines left > 1 s from the human stamp | 5 749 | 4 394 |
| carried lines: closer / farther | – | 1 355 / **0** |

So the fallback was not a two-song accident: a moved sheet reached the windows less than
half the time. The smallest first overtake was 23 ms, and that carry was right too. All
32 moved sheets still refused are verse trials that were out of order before the move
(the injected verse overlaps its neighbours; 266 of the 316 verse sheets are), which is
not this rule's case. The stop at anchored sections never fired: the same run without it
gives identical stamps.

**Pilot songs (`audit.run_local`, same stems and records as the 2.30.0 arm
`v3/<id>.fin`).** Both now log `windowed alignment` (GOSSIP 21 windows, 1 neighbour
carried; Got 2 Luv U 39 windows, 16 carried), and `quality_basis` goes from the
whole-audio `probs+anchors` to the windowed `anchors`.

| noticeable lines (`audit.score` placements) | 2.29.1 = 2.30.0 | 2.30.1 |
|---|---|---|
| GOSSIP, from the first word (`audit.score`) | 5 of 26 (0.19) | 8 of 28 (0.29) |
| GOSSIP, from the line start (what the overlay shows) | 13 of 26 (0.50) | 8 of 28 (0.29) |
| GOSSIP line 0 (unplaced by the score; ear 14.40 s) | 31.61 s, 17 s late | 14.38 s |
| Got 2 Luv U, from the first word | 8 of 26 (0.31) | 9 of 26 (0.35) |
| Got 2 Luv U, from the line start | 9 of 26 (0.35) | 9 of 26 (0.35) |
| Got 2 Luv U intro, 16 repeats against the ear's 16 (unplaced by the score) | 15 noticeable, 2.7-4.0 s late | 4 noticeable: line 2 0.32 s late, lines 5-7 0.5-1.5 s early (line 5 is a pile) |

The task's acceptance line ("`audit.score` share not worse than 2.29.1's") is **not met
as written**: GOSSIP gains 3 first-word misses, Got 2 Luv U 1. All of them sit where the
sheet is late and nothing anchors it: GOSSIP's second half (lines 31-39, the same
misses 2.29.0's windowed run had) and Got 2 Luv U's tail after line 82 (lines 84-94,
3.4-5.3 s late; 2.29.0 had 3.6-5.3 s). The whole-audio path got a few of those right by
accident, because it ignores the stamps. On what the overlay shows, both songs are better
or equal, and the two largest errors are gone: GOSSIP's first verse never showed, and
Got 2 Luv U's intro ran three repeats behind. Merged on that reading. The unanchored late
remainder is a separate problem: no unique line there, and moving it without an anchor
is the 15 s mistake above.

On the windowed path V3 now runs on both songs: 1 and 12 piled runs are given back to
`edges`. Runs of 3+ starts within 60 ms per line still go from 0 to 1 (GOSSIP line 37)
and from 0 to 2 (Got 2 Luv U lines 5 and 57), because whole-audio alignment had none.

**The other 18 songs.** On the cached whole ears, the new `shift_sheet` returns the
same stamps as before on all 18, because none has a moving section. Three were run again
end to end against the 2.30.0 arm with `audit.compare --same-within-ms 20`: SexyBack
0 of 648 words moved, New Rules 0 of 470, Deli Oğlan 0 of 242, all three "same within
20 ms". Snapshot 8949493 through `audit.run_local` (`kashi-audit` image, CPU, shared
with the V1 worker: 7-23 min a song); documents in `scratch/audit/runs/v301/`, scripts
and the Jamendo harness (with its two outputs) in `~/Documents/krediler/kashi-v301-betikler/`.
Gate: pytest 1110 passed (DB tests against a throwaway Postgres 17; the one skip is the
alignment smoke test, which needs the alignment extra), ruff, pyright and
check-versions all clean.

**Found on the way.** `audit.score` reads a line's time from its first word, so a document
whose line starts disagree with their words (GOSSIP's lines 1-7 above) scores clean.
Scoring from the line start is a one-line change in `audit.score.line_timings`. It would
change every earlier number, so it is left for its own decision.

## 2.31.0 measured — timing v3, second round (V1), 2026-11-09

Branch `worktree-zamanlama-v1` (on top of 2.30.1). **Not merged: the acceptance line is
not met as written (4 of the 10 heard line starts, see below).** Not tagged, not
deployed, no refresh. 20 pilot songs through `audit.run_local`, same stems and records
as the 2.30.0 arm (`v3/<id>.fin`) and, for the two shifted sheets, the 2.30.1 arm
(`v301/<id>.s1`); together the 2.30.1 baseline `v1/<id>.b301`. Documents, logs and
compare outputs in `scratch/audit/runs/v1/`, scripts in
`~/Documents/krediler/kashi-v1-betikler/`. The emission passes are cached per audio slice
(`emcache_run.py`: V1 changes Viterbi, never emissions; a cached run equals a fresh one
byte for byte but `generated_at`), so a second arm costs ~20 s a song instead of 3-27 min.

**What shipped as code.**

- **The region.** In a window that owns two or more stamped lines, each stamped line's
  first letter starts within `LINE_REGION_MS` of its own stamp, both ends inclusive, on
  the document clock (`windows.line_regions`). The task wording was "no word before its
  own stamp − R or after the next stamp + R". Read on the 2.30.0 documents (931 worded
  lines in shared windows, 18 songs), that upper bound binds **1** line, so it could not
  reach the named moments (SexyBack's "Get your sexy on" sits 1.57 s after its stamp
  but inside "next stamp + 1.2 s"); the start bound binds 9 at 1 200 ms. The start bound
  implies the wording's bounds: the next line's region caps every word of this one, and
  the slice caps the window's last line.
- **In the trellis, not after it.** `pipeline/trellis.py` is ctc_forced_aligner's
  Viterbi (`forced_align_impl.cpp`, 0.3.0) in numpy, plus forbidden (state × frame)
  rectangles: the line's first letter may not be emitted before its region, and no state
  before it, blanks included, may hold after it. The library runs first; only a pass
  whose path breaks a region runs again with the cuts (`alignment._hold_line_starts`), on
  both window passes, each on its own clock (`edges` and `segment` differ by the 70 ms
  lead). No path → the library's stands (logged `infeasible`: 0 on the pilot set).
- **The padded start.** The first finished document showed the one adjustment this round
  took: in New Rules 0:08 V1 put "One" on its stamp (0.7 s, 2.30.0 had it on "Talkin'" and
  line QA snapped it back), and the `edges` pass then started "Talkin'" at 4.92 s for a
  letter at 8.94 s, because the library's span takes half the blank before a word. Line QA
  moved the whole line onto the stamp (+3.95 s) and broke every word after the first. A
  bounded line's reported start that lies before its region is now its letter's frame.
- **Proof.** The compiled Viterbi and the numpy one agree byte for byte on 3 000 random
  cases, ties included (a `slow` test repeats 500). Seeded differential harness in the
  worker image: the 2.30.0 `alignment` module next to the new one over fake emissions
  (a linear model on real frame counts), 60 songs of short repeated lines with English
  by-initial offsets, through hybrid, vote, unpile, seams and the shift: with V1 off and
  with a region that never binds, **60 of 60 equal 2.30.0**; at 1 200 ms 322 passes cut,
  0 infeasible. Boundary tests at 1 199 / 1 200 / 1 201 ms on both sides of the region,
  through the clock conversion. 16 one-line mutations (bounds, rounding, inclusive check,
  lead sign, offsets, cuts, the band's repeat rule, the wiring, the padded start), each
  caught by `tests/test_line_regions.py`.
- **The value.** 1 200 and 800 ms, the two the task allowed. Same moments fixed, same
  known-good labels; 800 also lands BxMxC 0:13 and SexyBack's third "You ready?" on their
  voices, and noticeable lines go 101 → 95 against 101 → 101. **800 ships.**
  `benchmarks/run.py --line-region-ms` (0 = off, reproduces 2.30.x) is ready for the bench.

**Numbers, 2.30.1 → 2.31.0 (20 songs, the final arm `v1/<id>.v1g`: a snapshot equal to 42ea397 but two comments).**

| | 2.30.1 | 2.31.0 |
|---|---|---|
| noticeable lines (`audit.score`), of placed | 101 / 675 | **95 / 679** (at 1 200 ms: 101 / 677) |
| lit words / worded lines | 8 505 / 1 178 | 8 730 / 1 213 |
| line QA: flagged / dropped for density | 13 / 30 | 0 / 0 |
| 0 ms words / under 60 ms | 1.7 % / 4.3 % | 1.5 % / 4.3 % |
| runs of 3+ starts within 60 ms, per line / stream | 16 / 26 | 15 / 22 |
| line QA nudges / response shifts / voice returns | 13 / 4 / 0 | 14 / 3 / 0 |
| first words torn from their line (> 1 s and > 3× the line's later gaps) | 23 | 32 |

The two shifted songs alone: noticeable lines GOSSIP 8 → 9, Got 2 Luv U 10 → 11 (10 and
10 at 1 200). Their unshifted tails are 3-5 s late and nothing anchors them (2.30.1
above): V1 holds those lines near wrong stamps, which is the rule's limit.

`audit.compare` 2.30.1 → 2.31.0 with `--audio` (GOSSIP's 2.30.1 document is labelled
2.30.0, so its moments read `n/a:version`):

| verdicts | known-good: broken? / moved / kept | known-bad: fixed? / worse? / changed / unchanged |
|---|---|---|
| review (96) | 3 / 2 / 39 | 12 / 3 / 10 / 18 |
| listening (30) | **0** / 2 / 3 | 6 / 1 / 2 / 10 |

- **The three review `broken?` are moments the owner heard as wrong.** BxMxC 0:10.2
  (review: "BxMxC 8.53 sits on a shout"; owner: "0:07 BxMxC starts here, yours is a bit
  late"; 2.31.0 puts it at 7.11), BxMxC 0:46.5 (review "ok", numbers only; owner "wanna
  wanna wanna be piled"; 2.31.0 unpiles it 3 → 2) and Deli Oğlan 1:00.3 (review "ok",
  numbers only; owner "0:55 ohhh, 0:59 ohhh"; the second "oh" moves 54.50 → 58.73). The
  listening set reads all three `fixed?`. Strictly, the "review `broken?` 0" line fails.
- **The `worse?` labels.** Hey Mama 2:44.8 (listening): "Banging" leaves the moment's
  window, 166.74 → 168.03; the ear measured the old one 1.19 s early, the new one is on
  its stamp. Crazy In Love 2:47.0 (median +15 ms, ear "better"). New Rules 3:23.2: one
  more piled word next to it (8 → 9), real. GOSSIP 2:10.1: two words pile (0 → 2), real.
- **Lines that light again.** Line QA dropped 30 lines for density on 2.30.1 and none on
  2.31.0: crammed lines now sit near their stamps and keep their words. Among them New
  Rules' "(Out of my mind, out of my mind)" (13.88-14.58; the ear hears it at 13.95-14.6;
  the owner asked for it), Crazy In Love's 0:15-0:20 "Uh oh" lines (the owner's 0:18.5
  "no word movement"), Deli Oğlan's five lines after 1:57, nine BxMxC lines and two of
  SexyBack's chorus repeats at 3:21-3:23.

**Against the plan's named moments: 4 of the 10 heard line starts read `fixed?`.**

- Fixed: SexyBack 2:30.9 (the whole chain: "Get your sexy on" 147.26 → 145.91, the three
  "You ready?" 148.74 / 150.78 / 152.84 against the ear's 148.72 / 150.78 / 152.83), BxMxC
  0:13.2 (14.42 → 12.98; and 0:07, 8.53 → 7.11), New Rules 3:10.8 (the first "don't"
  191.24 against the ear's 191.25; the other three stay 0.6-0.7 s late: no letters), Deli
  Oğlan 1:00.3.
- Changed: Deli Oğlan 1:56.7 ("Hadi" stays 0.7 s early; the next line lights now).
- Not V1's: Hey Mama 0:08.9 and the hooks (inside one line), Take It Off 1:52.6 (a pile
  `edges` makes too), Stressed Out 1:36.4 and Crazy In Love 1:58.0 (structure the sheet
  does not carry, class E), New Rules 2:54.4 (right on these inputs, as in 2.30.0).
- Also moved: New Rules 3:20.0 (review, late): the first "don't" 200.91 → 198.75 (ear
  199.04); the others stay late, and "Don't be his friend" is now crammed into 0.5 s.
- Got 2 Luv U 2:15's four crammed repeats were opened by 2.30.1 (windowed again, ~1.2 s
  apart); V1 leaves them.

So the acceptance line "≥ 6 of 10" is not met: 4 from V1, none of the ten from 2.30.0's
compare. Per the task, the branch waits; the owner decides.

**What the screen will show.** lrclib's word-timed files bypass alignment on 8 pilot
songs in production, SexyBack and New Rules among them ("Found at the 2.30.0 rollout").
Their V1 changes reach the screen only if the fast path goes off (question 42). On the
other 12 V1 changes BxMxC, Deli Oğlan, Crazy In Love, Hey Mama, CEO, Heavenly, Take It Off
and Öp.

**Found on the way.**

- Holding only the first letter can tear the first word from the rest when the rest's
  evidence lies later (Crazy In Love 0:22: "Uh" at 22.75, the other eight words at
  24.33-25.07; Heavenly 0:59, CEO 0:08, New Rules 3:20, Got 2 Luv U's late tail). Torn
  first words 23 → 32; a guard (the line's last word in a region too, or the library's
  path when the cut tears) is unmeasured.
- The `edges` pass starts a word after silence halfway into it everywhere, not only in
  V1's lines; the hybrid hides it where `segment` agrees within 400 ms. Unmeasured beyond
  V1's floor.
- 2.31.0 holds lines to the sheet. Where the sheet is late and unanchored (the shifted
  songs' tails) that is the wrong place, which the plain-lyrics road (9.7) would address.

**Next.**

1. The owner's listen of the moved moments (`scratch/audit/caner-dinle-6.md`, 8
   moments), after a tag and the 20-song pilot refresh, both the owner's call. Two rows
   need the fast path off to mean anything.
2. The eng20 bench before any tag (GPU desktop): `--anchor-jitter-ms 400` with
   `--line-region-ms 0 / 800 / 1200`. Jittered stamps are V1's main risk and this round
   could not measure it.
3. The tear guard above, if the listen confirms the torn first words are heard.

## 2.31.0 merged, lrclib's word files off — server 0.35.0 (2026-11-09)

**The owner's two calls (2026-11-09 evening, relayed by the plan session).** V1 merges
as measured: 4 of the 10 heard line starts is under the bar of 6, but nothing heard or
reviewed fine broke and the 30 lines line QA used to drop for density come back. The
lyricsfile fast path goes off (question 42, this note's recommendation). `main`
5a5e4d3 (the switch) and f49fdd9 (server 0.35.0, pipeline 2.31.0, which also carries
the untagged 2.30.1).

**The switch.** `settings.lyricsfile_fast_path`, default `False` (`LYRICSFILE_FAST_PATH=true`
turns it back on). The worker still parses the chosen record's file and, when it would
have qualified, logs `lyricsfile present (N lines, M with words) — fast path off,
aligning`; the record then goes through separation, CTC and line QA like any other. No
document field: the raw file was never stored, so the witness is that log line plus
lrclib itself (the record id is in `alignment.lyrics_source_id`). Guard proven by
mutation: without the gate, `test_lyricsfile_is_aligned_like_any_record_by_default`
fails. The `lyricsfile.py` docstring no longer calls the file "strictly better".

**Found while switching it off.**

- lrclib's `/api/search` now serves the field too (the 2.4.4 finding, "search never
  carries it", is out of date) plus a `hasWordSync` flag. "New Rules" / Dua Lipa: 19 of
  20 search records carry word sync, compilations and live-lounge editions included.
  That breadth fits word times lrclib generates itself rather than crowd timing; not
  verified, and it matters for any later agreement gate.
- `choose_record` still ranks a word-file record first inside the synced class. With
  the fast path off that tie-break buys nothing and can trade duration proximity away;
  with 19 of 20 records carrying a file it is neutral there. Left as is (no measured
  case); revisit with the agreement gate.
- The overlay's own lrclib client (`apps/overlay/src/main/lrclib.ts`) reads only
  `syncedLyrics`; it never used the file. Nothing to change there.

**Bench.** Not run before the tag. The desktop GPU was serving the 2.30.0 archive wave,
and cnr-intel is a cluster node (4 cores, load ~3): the 1B English aligner over eng20 in
three configurations would load the control plane for hours. The tag did not wait (the
owner: fast loop, zero broken measured). It runs when the GPU is free, from the repo
root on the desktop: `--dataset jamendo`, eng20, `--windowed --anchor-jitter-ms 400`,
`--line-region-ms 0 / 800 / 1200`; PCO@0.1/@0.3, gross, end MAE, hygiene, line-start
noticeable share. Results go into this section.

**Refresh plan (the owner, via the plan session).** The 2.30.0 archive wave stopped at
~107 of 181. The whole archive refreshes on 2.31.0 in one pass once the gitops MR is
merged and the desktop GPU worker runs the 2.31.0 image: the 20 pilot songs first (the
first document inspected at once: `ready: pipeline 2.31.0`, the skip log line,
selection stats), then the 109 file songs, the ~71 the wave did not reach, and the ~107
it refreshed to 2.30.0 (for V1). Feeder `scratch/toplu-2.31/toplu.sh`, LIVE_MAX 2.

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
