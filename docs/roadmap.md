# Roadmap

Direction, targets and standing constraints. Live status and the next concrete step are in the
latest hand-off note under `docs/research/` (start from `docs/research/INDEX.md`). The README's
"Roadmap" section predates this file.

## Target: accuracy, not language parity

- Word timing errors should stay **under 200 ms**: the primary metric is **PCO@0.2** (no fixed
  bar; it should only go up), together with the **median word error < 100 ms** (stretch < 80 ms).
- PCO@0.1 is a soft goal (≥ 0.65). The mean error is not a target — ambiguous ad-libs and
  repeats dominate it.
- Perceived errors are asymmetric (late words are noticed later than early ones; ISMIR 2021
  thresholds ≈ +220 / −330 ms), so "noticeable miss" rates are reported alongside PCO.

## Language order

1. **English** — the system is built and measured here (full-precision ground truth exists).
2. **Turkish** — carried over by calibration once English settles (small hand-annotated set,
   per-language offset/class tables).
3. Others, **Japanese first** (the only CJK language in scope). Japanese needs a kanji → kana
   reading layer before alignment: the romanizer reads kanji as Mandarin.

One general system, calibrated per language.

## Phases

| Phase | Theme | State |
|---|---|---|
| 8-9.3 | Licence-clean chain, windowed clock, LyricFix, GPU helper worker | done — `hizalama-yontem-adaylari-2026-08.md`, `faz-9-pencere-saati-2026-10.md`, `lyricfix-lrclib-availability-2026-10.md`, `docs/gpu-worker.md`; live pipeline 2.29.0 (server 0.33.1) |
| 9 | Perceived accuracy, English | closing — 2.29.1's two halves shipped inside 2.30.0 (live 2026-11-09, pilot set refreshed); the whole archive refreshes once on 2.31.0 (the owner's call, 2026-11-09). What the owner still hears is 9.4-9.6 (`faz-5-zamanlama-yeniden-plan-2026-11.md`) |
| 9.4 | Timing v3: line starts (V3 piled starts fall back to the `edges` clock, V2 a line after silence starts on the text-confirmed voice return, V1 a line's own stamp holds inside a merged window) | **in progress** — 2.30.0 (V3 + V2) **live 2026-11-09** (server 0.34.0): piled-start runs 30 → 13 on the pilot set, nothing reviewed or heard fine broken, 1 of the 10 heard line starts and 1 of 4 heard piles fixed. The pilot refresh matches the local measurement word for word on the 12 aligned songs. The other 8 took lrclib's new word-timed files and skipped alignment (off since 2.31.0). 2.30.1 in `main` 2026-11-09, untagged: a shifted lyrics sheet reaches the windowed path again (JamendoLyrics 185 → 388 of 420 moved sheets). 2.31.0 (V1, line regions cut into the window trellis, 800 ms): noticeable lines 101 → 95 on the pilot set, 4 of the 10 heard line starts fixed (the bar is 6), three review-set "broken" all heard as wrong by the owner; **merged 2026-11-09 on the owner's call** and tagged as server 0.35.0 with lrclib's word-file fast path **off** (#42: a record carrying a file now aligns like any other). **Live 2026-11-09** (gitops MR !169): the pilot set refreshed on the cluster CPU worker, all 20 on 2.31.0 from the lrclib record, no density drops, 8 307 of 8 730 words equal to the local arm (every listening moment equal); archive refreshed 2026-11-10 on the desktop GPU, 303 of 307 on 2.31.0 (lines dropped for density 435 → 165, word-file documents 14 → 0; two nightcore lookups refused by the wrong-song gate, two guard keeps). Next: the owner's listen (`caner-dinle-7.md`, 12 moments: list 6 and four of list 5); eng20 bench with stamp jitter when the GPU is free (`faz-5-zamanlama-yeniden-plan-2026-11.md`, "2.31.0 measured", "2.31.0 merged") |
| 9.5 | Turkish parity (calibration) | after 9.4 — needs an independent Turkish truth first (a hand-corrected 100 ms set; today's set is 92-99 % our own output) |
| 9.6 | Lyrics-sheet meaning checks (local LLM, labels only, pre-registered in the Faz 5 note) | research beside 9.4 on the desktop GPU; enters the pipeline only past its gates |
| 9.7 | Plain-lyrics path (ear anchors as stamps for line-mode and plain records) | after 9.4 and 9.6 |
| 10 | Effects (incl. the unfinished "poison" archetype and song-mood-aware effects; leave the lexicon alone until then) | planned — independent of timing; fills the gaps when 9.4 waits on GPU or owner time |
| 11 | Hardening | planned |

Phase order changes only by the owner's decision.

### Proposed re-phasing after the whole-project review (2026-11-13; applies on the owner's "when")

`docs/research/toptan-gozden-gecirme-2026-11.md` holds the measurements and the reasons. 9.4 closes
with 2.31.0 (the owner's listen: two of six asked moments heard fixed; on fresh songs the rules made
the screen steadier, not more accurate). No further timing rule is proposed. Three lines may run
at once.

| Line | Step | What | Size |
|---|---|---|---|
| timing | A | Pipeline 2.32.0: whole-track vote threshold 400 → 1 200 ms (human truth: eng20 PCO@0.1 0.824 → 0.842, noticeable line starts 0.108 → 0.062), remove four rules that do nothing on fresh songs, cap the ad-lib re-derivation, then one archive refresh | ~3 workdays + owner 15 min |
| timing | B | Measure what ships: production configuration as versioned code defaults, the bench runs the shipped pipeline with line-start and dwell metrics, an archive replay rig, persist and revision guards | ~6-7 workdays |
| timing | D | The ear: own conversion of omniASR 1B and a repeat of its test; then duration-aware decoding in the trellis | ~1.5 + 4-6 workdays |
| source | C | Per-song overrides that survive reprocessing; clock verification (scale refit for nightcore, whole-sheet offset from repeated lines); honest plain-text fallback; the wordless-line policy (replaces 9.6 as written; the LLM label trial is narrowed and parked) | ~6-8 workdays + owner 10-15 min |
| client | E | Measure the 200 ms lead; held-note sweep; fewer knobs; golden frames, then the renderer as a shared `packages/view`; thin extension | ~10-13 workdays + owner 45 min |
| client | F | Android mode (a): probe app, title + artist + duration lookup, minimum app | ~9-14 workdays after E |
| client | G | Effects foundation (word phases, line exit), then the owner's direction (phase 10) | 2-3 workdays, then open |

9.5 (Turkish parity) follows D's first step; 9.7 (plain lyrics) follows C; 11 (hardening) is partly
B.

**Archive refresh policy (owner, 2026-10-25):** the whole archive is re-processed only after a large
improvement (~5-10 % or more, or a change that breaks documents); smaller changes refresh a fixed set
of 10-20 favourite tracks. A full refresh runs on the GPU helper worker (phase 9.3).

## Owner's long-term direction (2026-11-09)

Written down so that no later plan forgets it; details come from the owner when each item
opens, and the whole list is re-phased in the next whole-project review (Fable 5.2 as
orchestrator and advisor, after 2.31.0 is live and measured; brief in the owner's prompt
files). The timing core is the foundation these build on, not the product.

- **Effects, far beyond today's layer:** lyric-video style (the nightcore video idiom) — per-word
  entrances and exits, effects tied to a line's or a word's end, scenes driven by the words, themes
  driven by the song's mood; "take on the whole text-on-screen experience". Phase 10 is its first
  step, not its end.
- **Who is singing:** per-singer lyrics (duets, backing vocals, responses). Semi-automatic is
  acceptable; no clean licensed singing diarization exists today.
- **Android app, two modes:** (a) the desktop overlay's equivalent — a fixed on-screen lyrics box
  over YouTube Music, sizes for a phone, a settings screen for position and size, track detection
  from the system media session; (b) a full-screen show mode (lyrics first, then per-word and
  per-ending effects and scenes). Mode (a) is small and does **not** wait for the timing core: it
  can run in parallel. Known needs: a server lookup by title + artist + duration (the phone has no
  video id), the overlay's view logic shared through a WebView (the twin trap), ~100 ms position
  accuracy from the media session. iOS cannot draw over other apps or read another app's position,
  so iOS means our own player or Spotify. Sideloaded APK; no store.
- **Working principle:** the core (timing), effects and Android are separate lines of work that may
  advance in parallel; the document schema and the server API are the shared contract. Quality and
  "no unnecessary work" set the pace, not the calendar.
- **Standing worry to answer in the review:** "are we burying everything in rules — good on a few
  songs, unproven on all of them?" The review starts with a measurement on 20 fresh songs the owner
  has never listened to and the rules have never seen, plus the human-truth bench.
- **Perceptual priority (owner, 2026-11-13): steady tempo before exact timing.** What stands out
  most on screen is not a word lit at the wrong time but words that rush past (piles, near-zero
  durations, sudden jumps) or long gaps: they read as a bug and start a wave of wrong timing after
  them. A line that keeps its pace but sits off the voice reads as "mistimed", which is far easier
  to accept. Consequences for every later evaluation: count fluency (piled starts, 0 ms and < 60 ms
  words, tempo breaks inside a line, long gaps) next to accuracy, and treat an accuracy gain that
  costs fluency as no gain; where evidence is weak, a placement that keeps the line's pace is a
  candidate against the aligner's best guess (the unmeasured "spread inside the line's own span"
  idea in the Faz 5 note, "2.30.0 measured", Next 4 — not the dead render-side minimum display
  time). The owner also wants an AI reading of these moments. His own proposal for a word too
  short to be seen, in this order: give it more time from the empty space before or after it
  (never over a neighbour), or light it together with its neighbours as one unit, or do not light
  it at all — anything but one word flashing past unreadably. Unmeasured; the 2026-08 display-floor
  measurement (`faz-9-devir-askerlik-2026-08.md`, "Word-duration collapse": 13 of 19 short
  intervals were genuinely fast singing) judged fidelity to the human timing, not readability.
- **Also measured in the review:** the overlay's hidden 200 ms base lead (0.31.0; the owner's ear,
  2026-07) — display-chain latency or taste? The extension's reported position against the audio
  (`docs/research/faz-5-zamanlama-yeniden-plan-2026-11.md`, "The overlay's 200 ms base lead").

## Standing constraints

- **No timestamp adaptation in the overlay.** Mapping one edit's timestamps onto another
  (`t' = a·t + b`) cannot be solved client-side: only the target's total duration is known.
  Adaptation needs at least two independent anchors on the target timeline, i.e. it belongs on
  the server (audio-anchored alignment). Duration is a coarse gate, never a timing axis.
- **One fixed overlay window.** No separate full-screen effect window; effects stay inside the window.
- **Permissive dependencies only.** No share-alike or non-commercial code, weights or assets enter
  the repo; vendored assets carry their licence.
- Ideas already measured and rejected are listed in `docs/research/olu-fikirler.md`.

## Not planned

Chrome Web Store listing, Plex as a source, and macOS notarization are out of scope for now —
please do not re-propose them without the owner.
