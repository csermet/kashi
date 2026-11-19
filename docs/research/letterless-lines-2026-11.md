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

**The preview's rules.** W3 (a line that loses its word timings) and W2 with its review list judge
no letterless line: a '♪' holds no lyric word and is nobody's voice returning. Its move is still
listed.

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
reprocess runs the worker's own settings). Human truth and clips follow the usual order. A second
release then turns both defaults on with `PIPELINE_VERSION = "2.36.0"` and its note, and the
deployed image's ordinary preview lists the ids to refresh.

## What is open

- The archive preview with `--set` (the step above), human truth's verdict (by construction
  here) and the owner's clips; then the release that turns the defaults on.
- Two bars failed as registered (locality: one ad-lib line +10 ms through QA's median offset;
  sheet texts alone: three filler words more than measured). Whether they block is a decision.
- Not converted on purpose: `plan_windows`' word count and `line_regions`' stamp count (evidence
  slices). A letterless TOKEN inside a sung line ('-', '&') is still aligned as the filler "a" on
  the romanize=False path and stored as the sheet's token with `word_text_from_sheet`: none in
  the 39 songs, not counted in the archive.
