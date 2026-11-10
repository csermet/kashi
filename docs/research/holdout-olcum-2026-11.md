# Hold-out measurement: 20 fresh archive songs against the pilot set (2026-11-10)

**Question.** Every timing rule since 2.27 (2.28 windowed clock, 2.29 hybrid vote, 2.30 V3/V2,
2.31 V1, line QA changes along the way) was measured on the same 20-song pilot set. Does the
gain seen there show up on 20 songs that no rule and no listen has touched?

**Status: measured, listening pending.** This note only prepares the review's first input: the
numbers, the frozen verdicts and a blind listening list. It does not say whether the rules work.
No code, setting, label or document changed. Song names, ids and lyrics stay in `scratch/holdout/`
(git-excluded), per the audit's data rules.

## Selection

Seeded and reproducible (`scratch/holdout/sec.py`, seed 20261110, rules in the file):

- From the 307-song archive dump after the 2.31.0 refresh: not in the pilot set; on 2.31.0 with
  word sync; with a 2.27.x document (the archive before the 2.30 wave) **and** a 2.30.0 document
  (the archive before the 2.31 wave); no song named in any owner listening list. 110 songs qualify.
- Seeded shuffle, greedy fill: English 12, Turkish 5, other 3 (by the aligner's checkpoint:
  English model, Turkish model, MMS); at most 2 nightcore, at most 2 that took lrclib's word file
  on 2.30, at most 2 per artist. Five reserves.
- One pick was dropped for a tool reason, not a YouTube refusal: its id starts with `-`, which
  `audit.stems`' argument parser reads as an option. The first reserve of its language replaced it.
- Result: 12 English, 5 Turkish, 3 on MMS; 1 detected nightcore (speed 1.093); 1 song that took
  the word file on 2.30.

**What the versions are.** The brief asked for 2.28.x and 2.29.0 points. The archive never had
them: 2.28 and 2.29 were refreshed on the pilot set only, so the archive went from 2.27.x straight
to 2.30.0 (110 songs) and 2.31.0. The hold-out therefore compares
**2.27.x (before every rule above) / 2.30.0 / 2.31.0**. The pilot set's "before" point is its
pre-2.29 dump (12 songs on 2.28.0, 8 on 2.27.x); it also has 2.29.0. The older dump
(`faz9-stressed`, lines only) holds 2.4-2.27 documents of the hold-out songs (eleven on 2.19.1);
their versions are listed per song in `ids.json` and not scored.

Stems: `audit.stems` on cnr-intel CPU, one song at a time (435-1015 s each); whole-song ear:
`audit.ear --whole` on the 2.31.0 document's checkpoint (56-182 s). 20 of 20 done.

## Numbers

`audit.score` (lines whose start misses the singing by more than +220 / −330 ms, among the lines a
witness places), hygiene (`benchmarks.metrics.duration_hygiene`), line QA counters. The same ear
and voice onsets serve every version of a song. Script `scratch/holdout/olc.py`, totals in
`toplam.json`.

| | pilot 2.27/2.28 | pilot 2.29.0 | pilot 2.30.0 | pilot 2.31.0 | hold-out 2.27.x | hold-out 2.30.0 | hold-out 2.31.0 |
|---|---|---|---|---|---|---|---|
| noticeable / placed lines | 145 / 654 | 132 / 686 | 95 / 692 | 92 / 679 | 93 / 532 | 87 / 557 | 90 / 556 |
| share, pooled | 0.222 | 0.192 | 0.137 | 0.135 | 0.175 | 0.156 | 0.162 |
| share, per-song mean / median | 0.258 / 0.172 | 0.223 / 0.148 | 0.148 / 0.129 | 0.142 / 0.112 | 0.223 / 0.156 | 0.175 / 0.125 | 0.178 / 0.125 |
| late / early misses | 99 / 46 | 83 / 49 | 49 / 46 | 49 / 43 | 37 / 56 | 26 / 61 | 28 / 62 |
| ear witness (noticeable / placed) | 119 / 591 | 101 / 591 | 72 / 616 | 74 / 607 | 73 / 476 | 74 / 512 | 78 / 515 |
| voice witness (noticeable / placed) | 26 / 63 | 31 / 95 | 23 / 76 | 18 / 72 | 20 / 56 | 13 / 45 | 12 / 41 |
| lines / worded lines | 1213 / 1155 | 1213 / 1159 | 1219 / 1195 | 1213 / 1213 | 993 / 952 | 1049 / 1027 | 1049 / 1036 |
| words | 8364 | 8375 | 8692 | 8730 | 5851 | 6324 | 6348 |
| 0 ms words | 2.01 % | 7.59 % | 1.61 % | 1.41 % | 1.20 % | 1.72 % | 1.43 % |
| words < 60 ms | 6.5 % | 22.3 % | 7.0 % | 4.1 % | 4.3 % | 4.1 % | 3.6 % |
| bunched runs (3+ starts) | 30 | 59 | 45 | 20 | 15 | 19 | 13 |
| seam piles | 10 | 13 | 2 | 3 | 0 | 2 | 0 |
| QA flagged / density dropped / nudged / uncertain | 22 / 54 / 23 / 18 | 21 / 50 / 17 / 17 | 6 / 17 / 9 / 3 | 0 / 0 / 14 / 0 | 24 / 38 / 13 / 21 | 31 / 17 / 13 / 26 | 8 / 12 / 7 / 7 |

Per song, noticeable share changed by more than 0.03 from the earlier version to 2.31.0:

| | better | worse | flat |
|---|---|---|---|
| pilot, 2.27/2.28 → 2.31.0 | 12 | 1 | 7 |
| pilot, 2.29.0 → 2.31.0 | 9 | 0 | 11 |
| pilot, 2.30.0 → 2.31.0 | 5 | 5 | 10 |
| hold-out, 2.27.x → 2.31.0 (19 songs; one 2.27 document has a single line) | 6 | 6 | 7 |
| hold-out, 2.30.0 → 2.31.0 | 4 | 2 | 14 |

By language (pooled share, before → 2.30.0 → 2.31.0):

| | pilot | hold-out |
|---|---|---|
| English | 0.241 → 0.145 → 0.147 (14 songs) | 0.147 → 0.145 → 0.162 (12) |
| Turkish | 0.185 → 0.120 → 0.116 (4) | 0.262 → 0.211 → 0.185 (5) |
| MMS | 0.128 → 0.105 → 0.083 (2) | 0.214 → 0.114 → 0.086 (3) |
| without the nightcore song | 0.222 → 0.137 → 0.135 (20) | 0.149 → 0.132 → 0.126 (19) |

The one nightcore song weighs on the hold-out's English row: its ear-text match places 22 of 72
lines at a median **+4.2 s** (20 of them more than 1 s off) on every version, so the sheet runs
~4 s ahead of the singing. Its score is 18/29 → 17/27 → 23/25 noticeable. This is a lyrics-source
clock fault of the kind the perceptual audit found, not a timing rule. The 2026-11-10 nightcore
drift check counted this document among the five right ones; it read uploader descriptions and
transcripts (right song), not the clock.

**What moved** (`audit.compare`'s pairing; a word moved = start or end > 20 ms):

| | words paired | moved | earlier / later | moved > 250 ms | lines moved / paired | unpaired lines |
|---|---|---|---|---|---|---|
| pilot 2.27/2.28 → 2.31.0 | 8364 | 5810 | 4056 / 1754 | 1224 | 1026 / 1213 | 0 |
| pilot 2.29.0 → 2.31.0 | 8375 | 938 | 811 / 127 | 823 | 210 / 1213 | 0 |
| pilot 2.30.0 → 2.31.0 | 8474 | 3195 | 944 / 2251 | 1306 | 462 / 1195 | 42 |
| hold-out 2.27.x → 2.31.0 | 5806 | 4916 | 3093 / 1823 | 781 | 744 / 991 | 60 |
| hold-out 2.30.0 → 2.31.0 | 6286 | 644 | 301 / 343 | 412 | 173 / 1049 | 0 |

The pilot's 2.30.0 dump includes 8 documents from lrclib's word file (no alignment), which is why
its 2.30 → 2.31 move is large; the hold-out has one such song.

## Listening set (frozen before the owner listens)

`scratch/audit/caner-dinle-8.md` (20 moments, ~30 min, the caner-dinle-4 menu, neutral questions),
key `caner-dinle-8.key.json`, verdicts `scratch/holdout/review.jsonl` (written 2026-11-10T16:49Z,
sha256 in `review.sha256`, read-only). Moment rules (`scratch/holdout/anlar.py`, seed 20261110):
the finder's rank-1 moment on 2.31.0 for the 12 songs with the highest rank-1 score; the 4 lines
whose start moved most from 2.27.x to 2.31.0 (text unchanged, distinct songs); 4 seeded controls
(the finder's own). Each moment was rendered with `audit/moment.sh` for all three versions; the
verdicts were read from the moment numbers (one picture read, marked in its note).

| moments | ok | late | early | missing | pile | unsure |
|---|---|---|---|---|---|---|
| finder-ranked (12) | 3 | 2 | 3 | 2 | 1 | 1 |
| most moved 2.27 → 2.31 (4) | 2 | 0 | 1 | 0 | 0 | 1 |
| seeded controls (4) | 4 | 0 | 0 | 0 | 0 | 0 |

Of the 16 non-control moments, 9 are judged errors on 2.31.0: 8 with no version better (a sheet
clock, wordless lines, a silent parenthetical, a late line, a pile) and 1 where 2.27.x placed the
line on the ear and 2.30/2.31 start it ~0.8 s later. That last `better_version` was written wrong
(`none`) in the frozen file; the correction sits in `review-errata.jsonl`, written 2026-11-10T16:51Z,
before the listen. Both most-moved moments judged `ok` are better on 2.31.0 than on 2.27.x.

## What this cannot measure

- **Turkish and MMS ears are circular.** The ear is the aligner's own checkpoint, so its letters
  agree with the boxes by construction. 8 of the 20 hold-out songs are scored against that ear;
  the English rows are the stronger witness.
- **2.28 and 2.29 on the hold-out.** Not in the archive; producing them would mean running old
  pipeline code locally (`audit.run_local` arrived after 2.29). The hold-out shows the rules'
  combined effect from 2.27 and the 2.30 → 2.31 step only.
- **The ±8 s ear window** misses song-level shifts beyond it (README, known limits). The nightcore
  song's +4.2 s fits inside it; a larger one would not show.
- **Placed lines differ by version** (532 / 557 / 556 on the hold-out), so the shares are over
  different line sets. The 2.27 hold-out has 56 fewer lines and 497 fewer words (one degenerate
  one-line document, one song with 16 lines dropped for density).
- **Small n.** 20 songs, one listen. A per-song change of 0.03 is about one line in a 30-line song.

## Reading (numbers only)

On the pilot set the pooled share fell 0.222 → 0.135 from the pre-2.29 point to 2.31.0; on the
hold-out it fell 0.175 → 0.162 (0.149 → 0.126 without the nightcore song), with 6 songs better
and 6 worse. The hold-out started lower than the pilot set did. Hygiene (0 ms, < 60 ms, bunched)
and line-QA drops improved on both sets from 2.30.0 to 2.31.0. Late misses fell on both sets,
while early misses rose on the hold-out (56 → 62) and stayed near flat on the pilot set (46 → 43).
