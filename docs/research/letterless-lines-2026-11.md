# Letterless sheet lines and the sheet's own word texts (2026-11-19; the default since pipeline 2.36.0, 2026-11-21)

**Question.** A synced sheet marks music with a line that has no letter and no digit ('♪'). The
pipeline gave that line to the aligner like a sung one, so the stored document carried a word on
it: on the romanize=False path (Turkish) `_fit_to_vocab` fills the token with the letter "a" and
regroup stored that filler as the word, timed wherever the model heard an a-like sound (the owner:
"a single letter must never stand alone"); on the uroman path one '♪' word spanning the line. The
overlay hides such words on its own since 2026-11-19 (`packages/view/src/document.ts`,
`isSungText`); the document was still wrong at the source. The same romanize=False path stored
every word as the aligner's normalised form, not the sheet's token.

**Answer.** Two pipeline settings, both shipped OFF (production behaviour, `pipeline_version`
2.35.0 still names what runs) and measured on the archive with the preview's `--set` before a
release turns them on:

- `letterless_lines_wordless`: such a line is never given to the aligner and is written as a line
  without words, placed on its stamp by line QA.
- `word_text_from_sheet`: a word's stored text is the sheet's token wherever the aligner was given
  another form of it; every timing step still reads the aligner's form.

Both are on by default since pipeline 2.36.0 (2026-11-21), after two archive runs and the owner's
decision: see "The second archive run and the flip" at the end, and its rollback.

## Measured first (39 local songs, main's documents)

- **Letterless lines:** 7 in 3 songs, every one '♪'; 0 letterless tokens inside a sung line.
  VMn4mN7TeAk (Turkish) lines 16, 21, 34 stored one word "a" each (900, 350, 350 ms; line score
  0.0); GgGPuuGNPAE lines 3, 20 and K1FlAphL2p8 lines 12, 14 (uroman) stored one '♪' word over the
  whole line (2.3-5.9 s, line score 1.0).
- **Stored text against the sheet's token, romanize=False path:** 384 words in 230 of the 416
  worded lines, all 9 Turkish songs; each word counted once, by everything it lost (5 of the
  words below lost punctuation too). Punctuation alone 371 words / 225 lines / 9 songs (commas,
  parentheses, question marks, hyphens, quotes, one exclamation mark); a dotted capital I stored as
  I 8 words / 8 lines / 2 songs; an accent folded (â) 3 / 3 / 1; a letter outside the checkpoint's
  vocabulary dropped 2 / 2 / 1 (Cyrillic letters typed into a Turkish sheet). Apostrophes: none
  lost (the Turkish checkpoint's vocabulary has "'", so suffix apostrophes were always kept); no
  case change besides İ, no digit. Every stored text equals `_fit_to_vocab(sheet token)`: the map
  is positional and one to one on all 230 lines.
- **What the '♪' token held:** each is a line of a two-line window ({i, i+1}, once {i-1, i}) whose
  two stamps give both lines a V1 region. Its token took frames at the window's edge (edges pass)
  and behind its own star (segment pass); two of Öp's "a" were placed by the whole-track vote, one
  before its own window's slice. Without it, what can move: the sung line of its window, the line
  before it (a held end, the vote), and two document-wide steps that counted it: line QA's median
  offset (Öp's three deviated +780, +1 330 and -1 450 ms) and restore_held_ends' median ms per
  character (a '♪' over 2-6 s was one sample).

## The rules as built

**Letterless lines** (`alignment.sung_text`: a Unicode letter or digit, the overlay's
`isSungText`). `_given_lines` marks the sung lines; the window plan and its V1 regions are cut
from every sheet line and stamp as before, so every slice is the one main cuts and the evidence
serves (below); each window pass is given only its sung lines with their regions
(`_kept_regions`), a window of none is not aligned; the whole-track pass gets the sung texts; the
first-heard rule counts the line as 0 words (its QA-offset estimate then skips it, as line QA
does); regroup expects 0 words and returns an `unaligned` line with no words over the gap it sits
in (previous word's end to next word's start), which `shift_result` keeps on the shifted words;
line QA (`_place_unaligned`, `_settle`) places it as a snapped line's start is placed (stamp + QA's
median offset), never before the line before it ends (its start, if that line was snapped) nor
past the next sung line's start, and ends it at the next line's start; it counts in no median, no
flag, no MIN_REFERENCE_LINES and no agreement (`judged`), in line mode it is placed like every
line; `restore_held_ends` stops a held end at its start. A sheet with no sung line aligns as
before (nothing else to time). (As first built; rounds 2-4 below changed the placement: the marker
is 0 ms, cuts only from its stamp and never below a word's floor, and the restore passes over
it.)

**Deliberately not converted:** `plan_windows`' MIN_WORDS still counts a '♪' as a word and
`line_regions`' "two or more stamped lines" still counts its stamp. Converting either would cut
other windows, i.e. other evidence slices, and send the archive back to a download; the stamp is
a real line boundary, and keeping it keeps the next sung line's ±800 ms region. The sheet shift
reads the sheet (a '♪' has no letter, it is never an anchor): unchanged.

**Sheet texts.** regroup gets the form the aligner was given of a token (`_fit_to_vocab` with the
checkpoint's vocabulary on the romanize=False path, the token itself on the uroman path); a line
whose every word text is the form of the sheet's token at its position gives each differing word
the token as `sheet_text`, any other line keeps the aligner's texts and is counted in the job's
`sheet texts:` log line. `align_and_review` swaps them in as its last step
(`with_sheet_texts`): every timing step before it (the median ms per character, the ad-lib
respread, the initial classes) reads what it read before, the effects and the document read the
sheet. The overlay draws a word-sync line from its word texts, so Turkish word-sync lines show the
sheet's punctuation and İ again.

**The preview's rules.** When the build ran with `letterless_lines_wordless` on (by default or by
`--set`), W3 (a line that loses its word timings) and W2 with its review list judge no letterless
line: a '♪' holds no lyric word and is nobody's voice returning. Its move is still listed. With
the setting off they judge every line exactly as 0.43.0 does (the 39 songs: main, dark, candidate
and letterless-line arm against main's documents, 156 comparisons and verdicts equal to 0.43.0's
code; on, only the 3 songs with a letterless line change, `keep` (W3) to `refresh`). Every row
also reports, without judging, line QA's median offset and its way out (`qa`: clean, snapped,
line, unreferenced), stored and built.

## Evidence

A replay needs nothing the archive has not recorded. The candidate never cuts a new slice: its
windows and regions are main's, the whole-track pass and the sheet shift read the whole track as
before, and onsets, the level envelope and the mix features do not depend on the text. On the 39
songs (the worker's `process_job`, emission caches, every audio reader forbidden in a replay):
the evidence recorded by main's own code (90950f8) served the candidate on 39 of 39 songs
(`rebuilt from stored evidence, no download`) and gave the documents the candidate builds from
the audio (off) on 39 of 39; off, record and replay under the candidate are equal on 39 of 39; no
arm asked the emission cache for a slice it had not seen. The other direction is not symmetric:
a window that owns only letterless lines is not aligned under the candidate, so evidence recorded
under it lacks that slice and main's code could not replay such a song (0 such windows in the 39).

## Bars and results (39 songs, bars written and hashed before any candidate number)

Arms: main (90950f8), the branch with its shipped defaults, the candidate (both settings), each
setting alone. The bars and their hash live with the working files (`bars-39.md`); none was
changed after its number.

- **Shipped dark:** the branch with its defaults equals main on 39 of 39 documents; a seeded
  differential of main's regroup, shift, line fallback, line QA, held-end restore, window passes
  and preview rules against the branch's (3 000 cases a family, values on every threshold) is
  equal everywhere and catches each of seven one-line mutations of the new code.
- **Evidence:** pass (above).
- **Reach:** pass. None of the 7 letterless lines carries a word; every line count and text is
  main's.
- **Locality, letterless lines alone: FAILED as registered.** The 36 songs without one are
  identical; in the 3 with one, every change sits on a letterless line or a line next to it but
  one: GgGPuuGNPAE line 37, an ad-lib line that line QA shifts onto its anchor, moved +10 ms with
  its words, because QA's median offset went 0 → 10 ms once the letterless lines stopped counting
  in it (VMn4mN7TeAk's went 20 → 15 ms, K1FlAphL2p8's stayed 80; no other line there is placed by
  the offset). The bar allowed no start outside the neighbours; the mechanism had been named in
  the measurement, not in the bar.
- **Neighbours:** pass. The letterless lines moved by -1 315 to +1 465 ms (now stamp + QA
  offset, ending at the next line's start); two neighbouring words changed: GgGPuuGNPAE line 2's
  last word -170 ms (its window's edge pass no longer has the '♪' after it) and VMn4mN7TeAk line
  33's last word ends 300 ms later (the filler word right after it had stopped its held end; the
  '♪' line now starts 1.5 s later, on its stamp).
- **Sheet texts alone: FAILED as registered.** Every time, line text and field other than word
  texts is main's in all 39 songs, the 30 uroman songs are identical, and every one of the 384
  measured words now carries the sheet's token, with 0 lines kept to the aligner's form; but
  three more texts changed than the 384: VMn4mN7TeAk's letterless lines' filler "a" became the
  sheet's '♪' (which the letterless-line rule removes in the candidate). The bar counted sung
  lines' words only.
- **Independence:** pass. The candidate is the letterless-line arm's timing with the sheet-text
  arm's word texts, in every song.
- **Fluency, per song, over sung lines:** pass, no song worse; in fact no counter moved in any
  song (words dwelling < 120 ms 877, 0 ms words 106, sung lines without words 1, words out of
  order 0, lines whose first word flashes 145, new piles 0, before and after).
- **The archive step's own tool** (`preview --set` both, over main's documents and evidence,
  `--confirm-all`): pass. 11 refresh (the 9 Turkish songs and the 2 other songs with a letterless
  line), 28 unchanged, 0 kept, 0 review, `ladder_same` true on 39, `refresh_ids` empty under one
  fingerprint, every table unchanged; 7 line starts moved > 100 ms (the 7 letterless lines), 4
  > 500 ms, none > 1 500 ms; its documents equal the worker's replay on 39 of 39.
- **Human truth:** not run, and it cannot differ: the shipped bench's 79 songs (3 383 lines) hold
  no letterless line, so the letterless-line mask is all-true (the unmasked path above), and all
  79 align through uroman, whose word texts are the sheet's tokens, so no sheet text is set. (The
  hand-built Turkish set `jamendolyrics-tr`, not part of the shipped bench, has Öp's three '♪' as
  timed tokens.)

What the overlay shows differently: a Turkish word-sync line is drawn from its word texts, so it
gets its punctuation and İ back (e.g. HMTGkRQm7jU line 22, xvvCQdrDzmg line 0, VMn4mN7TeAk line
2); a '♪' line starts on its stamp and lasts until the next line (the overlay already drew it
without a word since 2026-11-19).

## The archive step and the release order

The candidate's code ships dark: both settings default to False, `pipeline_version` stays 2.35.0,
and a worker logs any env value that turns one on (`pipeline_overrides`). The deployed image's
preview measures the candidate on every song with evidence:

    python -m kashi_server.worker.preview --lyrics stored --confirm-all \
        --set letterless_lines_wordless=true --set word_text_from_sheet=true

Its rows and summary carry the fingerprint of the two settings and its `refresh_ids` is empty (a
reprocess runs the worker's own settings); each `--set` value is validated by the `Settings`
model itself and the model's value is what runs; `--documents DIR` writes the built documents
into `DIR/set-<fingerprint>/` beside an `OVERRIDES.json`; the summary also names what the
environment already overrides (`env_pipeline_overrides`). Human truth and clips follow the usual
order. A second release then turns both defaults on with `PIPELINE_VERSION = "2.36.0"` and its
note, and the deployed image's ordinary preview lists the ids to refresh.

**The flip must not happen without** (the reviewer's list, 2026-11-19; the coordinator owns it;
what became of each item is under "The second archive run and the flip" below):

- the archive `--set` preview, with the per-song QA-offset delta and any change of QA's way out
  (both in every row);
- counts over the archive: windows that own only letterless lines; letterless tokens inside sung
  lines on the romanize=False path; songs whose stamped sung lines fall below
  `MIN_REFERENCE_LINES`;
- an end-to-end `align()` test with the real seam, vote and first-heard functions over a
  letterless-only window between sung windows;
- the flip as code default + version bump only, never by an env value: a song first recorded
  under the candidate with a letterless-only window lacks that slice, and needs a full job if the
  flip is rolled back;
- what follows the document: unaligned lines are written with score 0.0 where uroman '♪' lines
  had 1.0; the `quality_basis` naming; Turkish word texts newly satisfying the lrclib publish
  word-text guard (`pipeline/publish.py`, `pipeline/lyricsfile.py`) - the publish wave is
  decided before any refresh.

## The archive step (2026-11-20) and the hold fix

**The archive** (server 0.44.0, the candidate shipped dark). Step 0, the plain preview of 400
songs: the 342 rows expected `unchanged` are unchanged; the only verdict changes are the two named
beforehand (6cvfHn9qeEw now builds: `keep`, W2 + W4; k89YEt8hocI `review` -> `unchanged`). Step 1,
the candidate (`--set` both) on the 343 songs the plain run calls unchanged, against bars
registered before: no failure or full job; on the 297 songs without a letterless line no line
start, QA offset, QA exit or fluency counter differs (the sheet texts move no time on the
archive); on the 46 songs with one (84 letterless lines) |Δ QA offset| <= 40 ms (mostly 0-10), one
QA exit snapped -> clean; no veto, no review; but words dwelling < 120 ms 5 140 -> 5 141
(LO71pC20BDI): that bar failed, and the candidate stays off. 39 songs refresh by text alone.

**What no veto read.** In 32 of the 84 cases the last word of the sung line before the letterless
line changes (23 songs), mostly by ±10-825 ms, but c9xGRpozwYU line 62's last word went from
2.55 s to 20.18 s (the letterless line moved from 181 170 to 198 800 ms, 40 ms before the next
sung line), 16GpicX09gk line 32 1.21 -> 8.68 s, 5YH4YCqcPjI line 51 0.17 -> 4.82 s,
DxC-PpNdwxo line 19 2.60 -> 3.30 s, fxHjlCBHuzA line 1 0.52 -> 1.20 s; and LO71pC20BDI line 32's
last word starts 1.13 s earlier (the extra short dwell).

**Why.** A segment-starred pass puts a star before every word and none after the last, and
ctc_forced_aligner's `get_spans` pads the last span to the end of the trailing blank: a window
pass's last word gets every frame to the slice's end. `hybrid_star_words` takes the segment row
(end included) whenever the two passes' starts agree, so only the whole-track vote (its end, the
rest as `held`, restored to at most 4 s) or regroup's clip at the next word's start bounds it.
On main the letterless line's filler "a" was that last word whenever the line shared a window
with the sung line before it; without it the sung word is last and runs over the interlude. Then
the letterless line, placed "never before the line before ends", was pushed onto that end.
Reproduced with the job's own `_viterbi` on synthetic emissions (one 12 s slice, two sung words,
music to the end): with the filler the last sung word is (1 500, 1 640) ms with 2 240 ms held;
without it (1 500, 11 980); and exactly the same (1 500, 11 980) for a sheet with no letterless
line at all, i.e. this is what main does at any long gap inside a window. Main's own documents
show it: on the 39 songs, of 84 line-final words before a gap of 3 s or more, 12 last over 4 s, the
longest 17.76 s (mamKQo4yM8Q line 39). On the 39 songs every letterless line already sat on its
stamp (6 of 7 open their window), so none was pushed.

**The fix** (`line_qa._place_unaligned`, `_cut_at_unaligned`): a letterless line never lengthens a
sung word. It is a 0 ms marker (its end is its start); when the line before it is an ordinary sung
line whose last word starts before the stamp (+ QA's offset), the stamp wins and that line's words
that reach past it, and its end, are cut there; otherwise it starts no earlier than the line before
ends, as before (a stamp before that last word starts says nothing about where the music begins).
A word starting at or after the marker is never cut (a snapped line's rescued words can lie past
it). The held-end bound stays the marker. Against the sheet without the letterless line a word can
only end earlier; against main a word whose held end the filler stopped can now reach the stamp
(DxC-PpNdwxo's +700 ms is of that kind). The marker is 0 ms for the overlay's sake
(`packages/view/src/view-logic.ts`): a line ending at the next line's start takes that line's
500 ms lead-in and, over a long interlude, shows a static '♪' instead of the interlude view; a
40 ms line flashes for two frames. A 0 ms marker leaves the gap a gap (held up to 5 s, the
interlude beyond, the next line led in). The schema sets no minimum and none is needed; one case
remains: a stamp at or past the next sung line's start puts the marker on that start, and the
marker then takes that line's lead-in (the '♪' shows for 500 ms).

**The removed token and its neighbours.** On the 39 songs one neighbouring word's start moved by
more than 100 ms (GgGPuuGNPAE line 2, -170 ms; the stem is silent at both places, -85 to -127 dB,
the voice returns 5.7 s later, so the envelope cannot judge it). In the archive's 46 songs (84
letterless lines): 5 words in 4 songs next to a letterless line (+825, -170, -1 130, +220,
+280 ms) and 1 elsewhere (-160 ms); their evidence is not readable from here. The token took
frames; without it the window's last words are re-placed. Sized, not fixed.

**The preview sees it now** (`preview_rules.ends_report`, every same-sheet row's `ends`, report
only, no veto, PREVIEW_FORMAT 7): words whose duration grew by more than 1 s, the line-final word
that grew most (line, old and new duration), sung line ends moved by more than 500 ms, and starts
of the words next to a letterless line moved by more than 100 ms. With the production setting its
verdicts equal 0.44.0's (156 pairs of the 39 songs).

**The 39 songs with the fix** (bars written and hashed before the numbers; main 2730a0d recording
its evidence, the branch's arms through `process_job`): every bar passes. The branch with its
defaults equals main 39/39 (and main 0.44.0 equals 0.43.0's documents 39/39); with the fix no
sung word's duration grows by more than 500 ms against main (one grows at all: VMn4mN7TeAk line
33's last word +300 ms, its held end no longer stopped by the filler); the unit test of the
archive's shape cuts the stretched word at the stamp and never later than the sheet without the
letterless line; main's evidence serves the candidate 39/39 and off == record == replay; no
letterless line carries a word; neighbour line starts within 1 600 ms (the letterless lines
themselves -1 315 to +1 465 ms), word moves GgGPuuGNPAE line 2 -170 ms and the +300 above;
timing and texts independent; fluency counters not worse in any song; the preview with `--set`:
11 refresh, 28 unchanged, no veto, `ends` on every row. Against the earlier
candidate only the 7 letterless lines' ends changed (each is now its start): on these songs no
letterless line had been pushed. Upper bound of the fix's reach in the archive's 46 songs: 9 of the
84 letterless lines sit exactly at the end of the sung line before them with its last word grown
(the six largest above among them); the archive preview with `--set` will say which move.

A seeded differential against 0.44.0's line QA (3 000 cases, unaligned lines after stretched last
words, stamps on and either side of the last word's start, the stretched end and the next line's
start) holds: identical without an unaligned line; no sung word starts elsewhere or ends later
(shorter only through the cut and, elsewhere, through the held-end cap's document median, 83
cases); every unaligned line a 0 ms marker on its stamp whenever the stamp lies after the last
word's start and no later than the next sung line; no word 0.44.0 gave a length comes out 0 ms
(the first draft cut a snapped line's rescued words lying after the marker to 0 ms; fixed).
Each of four one-line mutations of the fix is caught by it.

**Round 3: a floor, and the order** (the reviewer's read of the fix). The cut had no floor: a
stamp just after an ordinary last word's start left it 1 ms long, the mirror image of the hold
and the owner's first-ranked failure (a word that flashes past), invisible to every row field.
The aligner's end of a pass's last word is no evidence either way: it runs to the slice end, or
it is a true short end. So the marker now never cuts a sung word below its floor and never
lengthens one; the floor is the pipeline's own notion of a word's natural length, the held-end
restore's measure (the document's median ms per character times the word's characters, at least
TRIM_MIN_HOLD_MS 350 ms: 1x the median, where the restore lets a held end grow to 3x it, capped at
4 s), or the word's own length when that is shorter (`_natural_ms`,
`_floor_end`): a stamp inside the last word puts the marker at the floor's end instead. On the
two document sets the natural length of the line-final words before a letterless line is
350-1 003 ms (median 417) on the 39 songs and 350-2 250 ms (median 354) in the archive's 46;
main's filler had left 25 of the archive's 84 shorter than that, the 0.44.0 candidate 59 longer.
The envelope (option ii) was sized on the 39 songs' seven cases: where the stem's voice first
falls silent for 150 ms after the word starts lies before the word's end once (VMn4mN7TeAk line
33, 156 000 against 156 260), near it twice, and seconds after the marker in three (backing
vocals keep the stem voiced): as a bound it needs the floor beside it, and line QA does not hold
the envelope today (align() does; `apply_line_qa` would take it from `align_and_review`, which
already reads it from the evidence). It is the honest answer for main's ordinary long-gap holds
too, so it belongs to that candidate, not this one.

Also: the next aligned line bounds the stamp before it is compared with the last word's start
(a snapped next line before that word no longer cuts the line before it); markers are placed
once, after the ad-lib shifts and the snaps, with the words as they then stand (an ad-lib
shifted onto its anchor had kept a 12.5 s hold when placed from the words before the shift),
and an ad-lib snap passes over a marker (`_snap_end`), so a marker no longer refuses a snap
0.44.0 allowed nor feeds the arbiter a cut span; a degraded (line) document writes its markers
0 ms as well. All of it is pinned in the repository (`tests/test_letterless_marker.py`): each
bound exactly, and a seeded comparison of every document with the same document without its
letterless lines (1 500 cases, stamps on and either side of every bound): no sung word's start
moves, none grows, none falls below its floor, every marker 0 ms and on its (bounded) stamp; ad-lib
lines aside (`rederive_adlib_words` respreads an ad-lib over the span to the next line, which the
marker now is, as stored documents read their filler line) and line documents (no words) not
compared. That comparison stopped at line QA: the held-end restore after it was not in it, which
round 4 found and fixed. 20 one-line mutations, the reviewer's five among them, each failed it at
that commit; round 4 re-states them on its own code.

The preview reports both directions (`letterless`, every row, report only, FORMAT 8): per
document the line-final words before a letterless line, the shortest of them, how many under
120 / 200 ms and over 4 s, every line-final word held over 4 s before a break of 3 s or more
(the next candidate's baseline), markers within 500 ms of or on the next line's start or on the
previous line's end (stamps are not in documents: a marker on a bound did not sit on its stamp),
sung lines ending before their last word starts, 0 ms sung lines and words, and QA's ad-lib
shifts, rederives and flags, each stored -> built; on one sheet also each shortened line-final
word with, from the stem's envelope, the voiced share of the span cut off (near 0 a cut hold,
near 1 a cut sung word).

On the 39 songs (bars written and hashed before the numbers): every bar passes. The branch with
its defaults equals main 39/39; with both settings no sung word grows by more than 500 ms (one
grows at all, VMn4mN7TeAk line 33's last word +300 ms) and none is shortened at all, let alone
below its floor; main's evidence serves 39/39 and off == record == replay; the earlier bars hold;
the preview with `--set`: 11 refresh, 28 unchanged, no veto, `ends` and `letterless` on every row
(two markers that main's filler had put on the previous line's end now sit on their stamps; the
break-hold baseline: 12 line-final words held over 4 s before a break of 3 s or more, in 8 of the
39 songs, unchanged); the branch's verdicts with the production setting equal 0.44.0's on all 156
pairs. The documents equal round 2's: on these songs neither the floor nor the new order binds.
Whether they bind in the archive is the next `--set` preview's to say (its `letterless` report).

**Round 4: the restore, the degraded path, the count** (the reviewer's second read; its own
generators, 28 837 monotone and 37 548 wild documents, each compared with the same document
without its letterless lines). Dark was safe (80 000 seeded documents without a letterless line:
0 differences); what it found is candidate-only.

- *The floor did not cover the held end.* The held-end restore runs after line QA and stopped
  every held end at a marker. A stampless marker, or one bounded or placed after the line before,
  sits on that word's TRIMMED end, so the word kept its trimmed length: 60 ms where the sheet
  without the '♪' gives 860 ms; in the reviewer's monotone run 12 497 uncut words lost held
  length (up to 3 999 ms), 1 154 ended under 200 ms where the marker-free document gives 200 or
  more. Fixed by order: the restore now passes over every marker (as if the sheet had none), and
  the markers settle after it (`_settle_markers`). Each marker carries how line QA placed it
  (`LineTiming.placed`: on its stamp, lifted to the floor, bounded by the next line, after the
  line before, stampless). Only a marker on its stamp ("stamp", "floor") cuts: it cuts the
  restored word before it at itself, never below the floor on the restored length, and moves to
  the end of a word it cannot cut (a rescued word that starts after it). Any other marker cuts
  nothing and follows the line before to its restored end.
- *The degraded path* bounded no marker by the next aligned line, and the clamp then lifted the
  sung lines after it (a sung line at 4 991 instead of 3 490; 161 of 2 317 degraded documents).
  It now chains each sung line's end to the next SUNG line, as without the markers, clamps that
  timeline, and places each marker against it: on its stamp when that lies after the start of
  the sung line before (which then ends there), otherwise at that line's end, never past the next
  sung line. A snapped line, likewise, now ends at the next sung line's start.
- *Smaller:* the word cut follows only a marker on its stamp (round 3 cut any word reaching past
  a marker, also where the placement had decided not to cut: the reviewer's 7779-8579 line beside
  an ad-lib anchored at 7780); a marker the clamp lifted is "previous", not "stamp"; the median
  is read for the floor only when a line is unaligned (dark by construction).

What the comparison with the marker-free document guarantees now, AFTER the restore, pinned in
the repository by a seeded generator (`tests/test_letterless_marker.py`, 3 000 documents:
monotone and jumbled stamps; stampless, consecutive, first and last markers; snapped lines with
rescued and dropped words; ad-lib neighbours; line documents): every sung word outside an ad-lib
line starts where the marker-free document starts it, never ends later, and is never shorter than
min(its marker-free length, its floor), the floor stated in the test on its own (max(350 ms,
characters x the lower of the two medians line QA and the restore read)); every sung line starts
where the marker-free one does (line documents included), ends no later, and ends before its last
word starts only where the marker-free one does; markers are 0 ms, never past the next sung line,
never before a marker before them, and one without a stamp never cuts. The one carve-out is
ad-lib lines: `rederive_adlib_words` holds an ad-lib to the next line's start and respreads its
words, and the next line is the marker, exactly as stored documents read their filler line.

A document with a letterless line now says how its markers were placed (`alignment.qa.markers`,
kind -> count; logged once per document); a document without one carries nothing new (like
`uncertain`, the key is not among the schema's listed `qa` properties, which allow others: no
contract change). The preview's `letterless` report (FORMAT 9) adds per document: line-final
words before a marker whose hold it stopped (the marker on the word's end, the word under its
natural length or under 200 ms), markers that start before the previous sung line's last word
starts or ends (with the document's `uncertain` beside them), `marker_kinds`, and on one sheet
`final_unpaired` (shortened finals `cut_voiced` leaves out because their start moved),
`nonlast_words_cut_at_marker`, and for a line-sync build `line_sync_sung_starts_moved`.

Proof. The dark ship: the branch's line QA and restore equal 0.44.0's on 30 000 seeded documents
without a letterless line (values on and either side of every threshold the module reads, with
and without onsets, line documents, stampless and unpaired sheets; 11 183 with a snapped line,
1 911 degraded); the harness fails on three one-line changes to that path and passes the two
changes that are equivalent there by construction. The repository's tests kill every mutation
named by either review and this round's own: round 3's re-stated on this code (19 of its 20; the
order of the next line's bound and the floor no longer changes a document, since a bounded marker
cuts nothing) and 31 new, each failing `pytest` (a word starting on a marker cut, the cut and the snapped placement without the
median, the restore stopping at a marker, no settling with or without a median, settling below the
floor or past the next line, the degraded path chaining through markers or letting a stampless
marker cut, the count, each new report field). On the 39 songs, against bars written and hashed
before the numbers (12 bars): every bar passes. Dark equals main 39/39 and no dark document
carries the count; with both settings no sung word grows by more than 500 ms (VMn4mN7TeAk line 33's
last word +300 ms, as in round 3) and none is shortened; main's evidence serves 39/39 and off ==
record == replay with 0 cache misses; the preview with `--set` (FORMAT 9 on every row): 11 refresh,
28 unchanged, no veto; the production setting's verdicts equal 0.44.0's on all 156 pairs. All
seven markers sit on their stamps (`markers`: 3 / 2 / 2 "stamp"), so the documents equal round 3's
line for line; only the new count differs. The songs here do not reach the paths this round
changed; the archive's `--set` preview is what will (its FORMAT 9 fields say how often).

**Round 5: one median, and the contract** (the reviewer's third read: dark safe on 400 000
seeded documents through line QA, the restore, the sheet texts and the QA counts; round 4's
findings closed). The dark release the archive is measured on must carry the final candidate
code, so the rest went in before it.

- *Which median the restore reads.* In round 4 line QA had already cut the word before an
  on-stamp marker when the restore computed its median ms per character, so "as if the sheet had
  none" held for the bounds but not for the median (on paper: an even count of measured words
  whose upper middle is the cut one moves the median 275 -> 162.5, and a held word in another
  line is restored to 1 950 ms where the marker-free document gives 3 300). Now line QA only
  places the markers and cuts nothing, no word and no line end: every sung line leaves it as the
  sheet without letterless lines leaves it, so the restore reads that document's median and
  bounds, and the settle after it cuts with the same median. One value, the marker-free one, for
  the restore and for the floor.
- *What a marker cuts.* Only the last word of the line before a marker on its stamp, when that
  word starts before the stamp and reaches past it: at the stamp, or at the word's floor when
  that is later (the marker then moves there, "floor"); the line ends there too. When the line's
  last word starts at or after the stamp (a snapped line's rescued words), the stamp is no end of
  that line: the marker follows it and cuts nothing ("previous"). A marker moved by the marker
  before it is "previous" too, and a cutting marker bounded by the next line keeps its kind (it
  did cut).
- *The restore's bound* reads no word chunk past the last line (0.44.0's arithmetic exactly;
  alignment never builds such a document, a test pins it).
- *The contract.* `alignment.qa.uncertain` (on every document since 2.19.0) and
  `alignment.qa.markers` (an object of the five kinds, present only with a letterless line) are
  declared in `processed-track.v1.schema.json`, optional and additive, and in the generated TS
  type. The overlay reads nothing from `qa` (its mapper takes `speed_factor` and `quality_score`
  from `alignment`); the protocol package does not carry it.

The seeded comparison is now exact (4 000 documents, held ends on every word, line documents,
both sides of the median's 8 words): line QA's output equals the marker-free one for every sung
line outside an ad-lib; after the restore every such word equals the marker-free one except the
cut last word, which ends exactly at max(stamp, its start + min(its length, its natural length
on the marker-free median)), and its line ends at that marker; every other line ends as
marker-free.

Proof. Every mutation named by either review and this round's own dies in `pytest`: the two
call-site median mutants are gone with their call sites (line QA reads no median now); their
successors, the settle's floor without the median and the restore handing the settle none, die
in `test_when_the_next_line_starts_inside_the_floor_the_word_keeps_its_floor` and
`test_the_snapped_path_cuts_after_the_restore_at_the_restore_s_median` (ten 4-character words at
200 ms per character: "tonight" keeps 1 400 ms, not 350); the chunk past the last line in
`test_the_restore_reads_no_chunk_past_the_last_line`; the relabel in
`test_a_marker_moved_by_the_marker_before_it_is_no_longer_on_its_stamp`. On the 39 songs, against
bars hashed before the numbers (`bars-39-r5`): every bar passes; dark equals main 39/39 to the
byte (generated_at aside) and main's and the dark documents validate against the old schema and
the new; the candidate documents validate against the new one, carry the count, and equal round
4's on all 39 songs (all seven markers on their stamps after the line before ended: nothing was
cut there before either, and the median is unchanged); the production verdicts equal 0.44.0's on
156 pairs. CI's TypeScript path (install, validate, codegen, versions, typecheck, lint, test,
build) is green with the generated type committed.

## The second archive run and the flip (2026-11-20/21)

The final candidate shipped dark in server 0.45.0. **A plain preview** under 0.45.0: 343 of the
343 rows expected unchanged are unchanged, and the other 57 keep their verdict and detail (dark
equals 0.44.0 on the archive). **The candidate** (`--set letterless_lines_wordless=true
word_text_from_sheet=true`, fingerprint d95316c0eb6e0de7) on those 343, against bars registered
before the run (two working files with hashes, 2026-11-20T14:45:19Z and T15:35:00Z):

- the 297 songs without a letterless line are untouched in every time, QA value, counter and end;
- the 46 with one (83 lines made wordless): |delta QA offset| <= 40 ms, no QA exit lost; no
  consistency counter and no count of line-final words under 120 ms goes up in any song; markers
  on their stamps 81, after the line before 2, at a floor 1, bounded by the next line 0,
  stampless 0; line-final words before a marker under 120 ms 6 -> 2, under 200 ms 10 -> 6, held
  ends stopped 10 -> 3, over 4 s 0 -> 0; no marker within 500 ms of the next sung line; no
  line-sync sung start moved; 39 songs refresh by their sheet text alone. The first run's three
  holds (4.8 / 8.7 / 20.2 s) are 1.7 / 1.67 / 3.02 s.
- Per-song vetoes, within the registered limits (each rule <= 10 %, their union <= 20 %): 7 of
  46 - 4qv16WcOCYA, 5YH4YCqcPjI, Di2RuX_2FnI, LO71pC20BDI (a line end moved more than 500 ms or a
  final hold grew more than 500 ms); LO71pC20BDI (a dwell counter up); 4qv16WcOCYA, GgGPuuGNPAE,
  LO71pC20BDI, h1Zn0aHceVM (a start next to a marker moved more than 100 ms); hTWygWXZ0FY (a final
  word cut by 436 ms where the stem is voiced). Those songs keep their stored documents.
- **One hard bar failed:** "no sung word grows by more than 1000 ms against the stored document".
  5YH4YCqcPjI L51's line-final word grows 170 -> 1725 ms: the stored 170 ms was the filler cutting
  the word, and the candidate gives the marker-free value (a "previous" marker). The bar measured
  against a stored document that is itself wrong at that spot; it stays recorded as failed.
- Read against the stored documents, the 46 built ones differ in 32 sung word starts (19 by
  <= 10 ms; 6 by more than 100 ms, five next to a marker, one elsewhere: P_LNW8IDHZg L26,
  -160 ms) and 209 word ends (181 by <= 50 ms, the median without the filler words; 5 by more
  than 500 ms, all in the vetoed songs). Nothing that no bar looked at.

**The decision** (the owner, 2026-11-21): flip, with the seven vetoed songs (5YH4YCqcPjI among
them) left on their stored documents; 78 of the 85 `refresh` rows are refreshed from evidence.
lrclib publishing happens only on a person's click in the tray; a refresh publishes nothing.

**The flip** (pipeline 2.36.0): both code defaults on, the version and its paragraph, nothing
else in the pipeline. On the 39 local songs under the new defaults (no setting touched): off ==
record == replay 39/39, 0 cache misses; main 0.45.0's evidence (recorded under 2.35.0) replays
39/39 with every audio reader forbidden; every document says 2.36.0, validates against the
schema, and equals the round-5 candidate document of its song (the code the archive run measured
with `--set`) apart from `pipeline_version` and `generated_at`: what ships is what was measured.

**The reviewer's list, as it ended:** the archive `--set` preview with the per-song QA-offset
delta and QA's way out: done, above. The flip as a code default and version bump only: done
(pipeline 2.36.0, no env value). The publish question: settled by the above (no refresh
publishes). The counts over the archive (letterless-only windows, letterless tokens inside sung
lines, songs whose stamped sung lines fall below `MIN_REFERENCE_LINES`), the end-to-end `align()`
test over a letterless-only window with the real seam, vote and first-heard functions, and the
score / `quality_basis` of wordless lines are not recorded in this note; the owner's decision
did not wait for them, and they stay open below.

**Known limits of the rule, accepted** (the reviewer's two open shapes; neither occurs in the 46
built documents): an ad-lib line stretched past 12 s before an on-stamp marker keeps its stretch
(`rederive_adlib_words` reads the marker as the next line, the carve-out of the seeded
comparison); a wordless snapped line is not ended by a marker after it.

**Baseline for the next candidate**, from the same rows: line-final words held over 4 s before a
gap of 3 s or more, 68 in the 343 songs, in 45 songs, unchanged by this candidate.

### The flip and its rollback

- **Rollback:** the previous image tag (server 0.45.0, pipeline 2.35.0 with the code dark). No
  migration belongs to the flip, so the image tag alone undoes it. `LETTERLESS_LINES_WORDLESS=false`
  and `WORD_TEXT_FROM_SHEET=false` in a 2.36.0 image build what 2.35.0 built, but the version then
  no longer names the pipeline (the ready line says `env overrides ...`): use the tag.
- Documents already refreshed under 2.36.0 stay as they are until reprocessed.
- A song FIRST recorded under 2.36.0 whose sheet has a window of only letterless lines has no
  evidence for that window (it is not aligned): under an older image it needs a full job, not a
  refresh from evidence.
- A GPU helper image built before server 0.46.0 runs the 2.35.0 chain while a newer worker runs
  2.36.0: rebuild it before its next use (`docs/gpu-worker.md`). Its documents would carry its own
  pipeline version, so a mix is visible, but do not mix.
- The preview measures the rollback the other way now: `--set letterless_lines_wordless=false
  --set word_text_from_sheet=false`.

## What is open

- The reviewer's items above that are not recorded here (archive counts, the end-to-end
  `align()` test over a letterless-only window, the wordless lines' score and `quality_basis`).
- The removed token's effect on a neighbouring word's alignment (32 sung word starts differ in
  the 46 songs, six by more than 100 ms).
- Line-final holds before long gaps in general (68 over 4 s in the 343 songs): the next
  candidate's.
- Two bars failed as registered (locality: one ad-lib line +10 ms through QA's median offset;
  sheet texts alone: three filler words more than measured). Whether they block is a decision.
- Not converted on purpose: `plan_windows`' word count and `line_regions`' stamp count (evidence
  slices). A letterless TOKEN inside a sung line ('-', '&') is still aligned as the filler "a" on
  the romanize=False path and stored as the sheet's token with `word_text_from_sheet`: none in
  the 39 songs, not counted in the archive.
