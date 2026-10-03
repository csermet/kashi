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
| 8 / 8.1 | Lyric alignment review + licence-clean model chain | done (chain live since 2026-08-12) |
| 9 | Perceived accuracy, English | open — audio downloads unblocked (server 0.30.1) and the 2.27.0 response-onset rule verified in the field on one song; the archive re-scan waits for the owner. Remaining perceived complaints: repeated sections, and word-duration collapse (cheap candidate: a render-side minimum display time per word) |
| 9.5 | Turkish parity (calibration) | planned |
| 10 | Effects (incl. the unfinished "poison" archetype and song-mood-aware effects; leave the lexicon alone until then) | planned |
| 11 | Hardening | planned |

Phase order changes only by the owner's decision.

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
