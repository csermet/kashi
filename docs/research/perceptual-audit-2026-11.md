# Perceptual audit of the pilot set (2026-11, Faz 3)

**Status: measured — input for the timing re-plan (Faz 5); no pipeline change.** The 20-song pilot
set was reviewed moment by moment with the audit tool (`apps/server/audit/`, see its README), both
document versions side by side: pipeline 2.29 against the version each song had before (2.28 for
twelve songs, 2.27 for eight). The headline is not a timing number. **The worst failures on the set
come from the lyrics source, not from the aligner**: one lrclib record is synced 17-19 s late, one
song gets the lyrics of a different mix, one carries a 3-4 s late verse. 2.28 → 2.29 is a small net
gain where it shows at all; piles under ~0.4 s, the thing 2.29 made more of, are not what a viewer
sees.

## Method

Per song: the finder's top three moments plus one seeded control moment, each rendered (separated
vocal spectrogram, the aligner checkpoint's greedy letters, both versions' word boxes, the overlay
strip) and read numbers-first, picture second; one verdict per moment with a numeric claim, written
before the owner listens. Moments already reviewed in Faz 1-2 (New Rules, Crazy In Love, Got 2 Luv U,
part of Instruction and Stressed Out) were reused rather than re-read. Where a moment repeated a
pattern already read on a picture of the same song, or where ten or more of the ear's letters agreed
with the boxes within 100 ms, the verdict was taken from the numbers and the record says so.

The lesson of the owner's blind rounds (2026-10-31) sets the weight of every verdict: a word lit
within ~0.35 s of its sound is not noticed; second-scale line shifts and words that never light are.

## Numbers

96 moments on 20 songs: 45 `ok`, 18 `pile`, 12 `late`, 6 `early`, 6 `missing`, 4 `wrong_text`,
5 `unsure` (5.2 %, bar ≤ 20 %).

| | moments | real error | ok | unsure |
|---|---|---|---|---|
| finder-ranked | 72 | 44 (61 %) | 25 | 3 |
| seeded controls | 24 | 2 | 20 (83 %) | 2 |

Of the two control misses, one is mild (a line lit 0.45 s early after a short dip the finder's
500 ms-silence rule skips) and one is the set's worst document (GOSSIP, below) — **the finder did not
rank it, a control found it.** Where the two versions differ at a moment, 2.29 is better at 11,
the older version at 10, mixed at 1.

`audit.score` (share of post-silence lines whose screen light misses the voice by more than
+220/−330 ms) agrees with the reading at song level: mean 0.26 → 0.23, median 0.17 → 0.15; 9 songs
better by more than 0.03, 1 worse, 10 flat. The three lyrics-source failures are its three highest
values (0.77, 0.83, 0.44); 12 of 20 songs sit below 0.20 on 2.29.

## What a viewer would notice

1. **Lyrics source, version independent (3 songs).**
   - *GOSSIP* — the lrclib record is badly synced: verse 1, chorus 1 and verse 2 sit 17-19 s after
     the singing (the ear hears "welcome to the city of lies" at 0:14; the line is at 0:32), chorus 2
     3.4-4.4 s, only the last line lands. Same length as the audio (168.3 s), so no duration check
     can see it. The finder's ear-text check placed 11 (median −3.5 s): its window is ±8 s; at ±30 s it places 26
     of 42 with a median of **−18.9 s**.
   - *Enemy* — the document carries JID's verse (17 lines at ~8 syllables/s) where the stem holds a
     sung, not rapped, vocal: smooth harmonic stacks with dips every ~0.4 s, a 1.5 s held, decaying
     note under nine of his words, and an ear transcript of other lyrics (it even spells the
     document's own bridge line, "I swear that I'll never be a saint", 50 s early). Most likely the
     video is the solo mix whose new bridge replaces the feature verse. Same length again (173.4 s).
     Queued for one owner listen.
   - *Got 2 Luv U* — known from Faz 2: lrclib's patois verses 3-4 s late (wide-window median −4.0 s).
2. **Line ↔ utterance shifts at the seconds scale (both versions).** "Stay with me" lit 0.65-0.8 s
   after it is sung because the previous line's tail is boxed over it; three separated "You ready?"
   assigned one slot late (the first ~1.2 s late, the last one's "You" 0.9 s early); a refrain whose
   "with" is lit ~1 s early while "be gone" never shows; a line change 0.7 s early inside a held note.
3. **Words that never light.** A chorus tail ("hey mama, ma") piled at 0 ms on the next line's start
   (~1 s late); two chorus lines of *Öp* shipped wordless (line score 0.0) although the ear spells
   them letter by letter in order — the aligner's evidence was there, QA threw it away.
4. **Piles below ~0.4 s** (18 moments): stutters, call-and-response hooks whose lead and echo overlap,
   chanted tails. Real, counted by hygiene metrics, mostly invisible on screen. 2.29 makes more of
   them; it also spreads some that 2.28 piled (a hook line shown 0.8 s instead of 0.44 s).

## The tool: caught, missed, fixed

- **Fixed (this round):** the MMS checkpoint's CTC blank is `<blank>` = 0 with a separate `<pad>` = 1;
  the ear took `<pad>` and wrote a `<blank>` "letter" on every blank frame, so the Spanish and
  Japanese songs placed 0 lines. `audit.ear.blank_id_of` (+ test, proven by mutation). Spanish now
  places 30 of 65 lines at a median of 0 ms. The production twin, `alignment.transcribe`, has the
  same `vocab.get("<pad>", 0)` but is only ever called with the English checkpoint today; it breaks
  the day it is called with MMS.
- **Finder false positives, two classes:** a voice "return" with no letters at all (wordless outros,
  instrument figures and beat-locked stabs that leak into the stem, held wordless notes, breaths) —
  about half of the 25 `ok` finder moments; and look-alike refrains fooling the ear-text matcher.
  Requiring some ear activity for `orphan_voice` / `line_vs_voice` would likely remove most of the
  first (untested).
- **Blind spots:** the ±8 s ear-text window (GOSSIP); version mismatches do not move the median at
  all — the signal there is a low share of placed lines (Enemy 16 of 57), which pitched or processed
  vocals also produce (a hardstyle track: 11 of 40). Japanese cannot be matched: the ear is
  romanised, the lyrics are in script.
- **Circular evidence:** for Turkish and the MMS languages the ear *is* the aligner's checkpoint, so
  its letter times equal the boxes by construction; only the letters (what is heard, in which order)
  are independent. English uses the same checkpoint too, but its documents carry an offset table, so
  agreement there is less trivially guaranteed — still not an independent witness.
- Cosmetic: CJK glyphs render as boxes (no CJK font in the audit image).

## Inputs for the re-plan

1. **Verify lrclib anchors by ear, per song, before trusting them.** A wide-window ear-text median
   (|median| > 1 s) flags GOSSIP and Got 2 Luv U and nothing else on the set; the emissions it needs
   are already computed by the aligner. A low placed-line share is a weaker, second signal for a
   wrong-mix lyrics sheet.
2. **Rescue wordless lines the ear can read** (the *Öp* case) instead of shipping them plain.
3. **Weight what is seen:** seconds-scale line shifts, wrong lines and never-lit words first; sub-0.4 s
   piles last. `audit.score` already measures the first group; hygiene counts the last.
4. 2.29's regression is real in hygiene numbers but not visible at this set's moment level; whether to
   ship 2.29.1, a timing v3 or plain-lyrics timing from scratch is a decision for the re-plan, on
   these numbers. *(2026-11-08: 2.29.1 is built and measured, see `faz-9-pencere-saati-2026-10.md`,
   "Pipeline 2.29.1": word lengths back, and item 1 for whole-sheet shifts.)*
5. **Candidate (owner, 2026-11-08): meaning questions for a local LLM, never timing.** Feed the ear's
   text (CTC or Qwen3-ASR) and the sheet section by section to the desktop's Gemma (no API limit):
   is it the same song or another mix's lyrics (to replace A3's `wrong_sheet` rule, 19 % false
   alarms), sung lyric or a background shout, written but never sung. Timing stays with rules (Faz
   3.5: summarizers were inconsistent with numbers). Pre-register before adopting: JamendoLyrics 79,
   the injected deleted/extra paragraphs, the other-mix verse.

## Data

Verdicts and per-song notes are kept outside the repository (they name a private song list and quote
lyrics); this note carries numbers only. Four moments wait for one owner listen each (Enemy's verse,
a chant repeat with no line, a chorus over what may be an instrumental break, a chant start).
