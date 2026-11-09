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
| 9 | Perceived accuracy, English | closing — 2.29.1 built and measured (word lengths back after line QA; a shifted lrclib section moves before alignment); tag and the 20-song pilot refresh on the owner's call; the archive refresh stays on hold until a tag's bench run shows a large gain. What the owner still hears is 9.4-9.6 (`faz-5-zamanlama-yeniden-plan-2026-11.md`) |
| 9.4 | Timing v3: line starts (V3 piled starts fall back to the `edges` clock, V2 a line after silence starts on the text-confirmed voice return, V1 a line's own stamp holds inside a merged window) | **in progress** — 2.30.0 (V3 + V2) built and measured on the 20-song pilot set 2026-11-09 (branch `worktree-zamanlama-v3`): piled-start runs 30 → 13, nothing reviewed or heard fine broken, 1 of the 10 heard line starts and 1 of 4 heard piles fixed; tag, eng20 bench and pilot refresh on the owner's call; V1 next (`faz-5-zamanlama-yeniden-plan-2026-11.md`, "2.30.0 measured") |
| 9.5 | Turkish parity (calibration) | after 9.4 — needs an independent Turkish truth first (a hand-corrected 100 ms set; today's set is 92-99 % our own output) |
| 9.6 | Lyrics-sheet meaning checks (local LLM, labels only, pre-registered in the Faz 5 note) | research beside 9.4 on the desktop GPU; enters the pipeline only past its gates |
| 9.7 | Plain-lyrics path (ear anchors as stamps for line-mode and plain records) | after 9.4 and 9.6 |
| 10 | Effects (incl. the unfinished "poison" archetype and song-mood-aware effects; leave the lexicon alone until then) | planned — independent of timing; fills the gaps when 9.4 waits on GPU or owner time |
| 11 | Hardening | planned |

Phase order changes only by the owner's decision.

**Archive refresh policy (owner, 2026-10-25):** the whole archive is re-processed only after a large
improvement (~5-10 % or more, or a change that breaks documents); smaller changes refresh a fixed set
of 10-20 favourite tracks. A full refresh runs on the GPU helper worker (phase 9.3).

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
