# Letterless sheet lines and the sheet's own word texts (2026-11-19, candidate for 2.36.0)

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
before (nothing else to time).

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

**The flip must not happen without** (the reviewer's list, 2026-11-19; the coordinator owns it):

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
TRIM_MIN_HOLD_MS 350 ms), or the word's own length when that is shorter (`_natural_ms`,
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
moves, none grows, none falls below its floor, every marker 0 ms and on its (bounded) stamp. 20
one-line mutations, the reviewer's five among them, each fail it.

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

## What is open

- The archive preview with `--set` again, with the hold fix and the `ends` report; human truth's
  verdict (by construction here) and the owner's clips; then the release that turns the defaults
  on.
- Two bars failed as registered (locality: one ad-lib line +10 ms through QA's median offset;
  sheet texts alone: three filler words more than measured). Whether they block is a decision.
- Not converted on purpose: `plan_windows`' word count and `line_regions`' stamp count (evidence
  slices). A letterless TOKEN inside a sung line ('-', '&') is still aligned as the filler "a" on
  the romanize=False path and stored as the sheet's token with `word_text_from_sheet`: none in
  the 39 songs, not counted in the archive.
