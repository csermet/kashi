# allin1 viability check (Faz 6.5 P6) — NO-GO; pivot to librosa Laplacian

**Date:** 2026-07-20 (pre-implementation research round, sourced). Verdict:
**allin1 cannot be installed in a working state today** — the P6 structure
work pivots to a dependency-free librosa segmentation (the plan's named
plan-B), promoted from fallback to primary before a single line was built
on the dead path.

## Why allin1 is dead on arrival (verified, not guessed)

- `allin1`'s model code (`src/allin1/models/dinat.py`) imports NATTEN's
  legacy RPB functional API (`natten1dqkrpb`, `natten2dqkrpb`, …).
  **NATTEN ≥0.20 removed that API entirely** (SHI-Labs/NATTEN#268); every
  NATTEN currently on PyPI (0.21.6 incl.) is ≥0.20 ⇒ guaranteed
  `ImportError` at model load. Pinning NATTEN <0.20 means old sdists whose
  torch 2.9.1 ABI compatibility is unverified and likely broken.
- The upstreams are frozen: allin1's last commit 2023-10-10; its separation
  dependency `demucs` is GitHub-archived (2024-04); madmom needs a git
  commit-pin (`27f032e`, 2024-08) just to build on py3.12. A frozen model
  stack against a moving NATTEN is a supply-chain time bomb, not a
  dependency.
- madmom's PyPI classifiers additionally flag CC BY-NC-SA for bundled
  models — a second reason it never belonged near the MIT repo's extras.

## ⚠️ Supply-chain warning — do NOT touch "openmirlab"

The research round found a GitHub org (`openmirlab`, ~8 months old, near
zero community footprint) publishing `-infer` forks that "fix" EXACTLY this
problem set (all-in-one-infer, madmom-infer, demucs-infer) plus an
`openmirlab-skills` repo explicitly marketed as a Claude Code plugin —
i.e., discovery aimed at AI coding agents. The profile matches a classic
supply-chain / prompt-injection trap. **None of its packages get installed
without a line-by-line human source review, and the "skills" plugin is
never enabled.** Recorded here so a future session doesn't rediscover it
innocently.

## The pivot (what P6 ships instead)

librosa's Laplacian structure segmentation (McFee & Ellis 2014; the
documented librosa gallery method) over beat-synchronous chroma:
recurrence + path affinity → normalized Laplacian → spectral clustering →
segment boundaries with cluster identities. **Zero new dependencies**
(librosa + scipy are already base), CPU-cheap (seconds), fully
deterministic (seeded k-means).

Honest labeling policy: clustering yields REPETITION STRUCTURE, not
semantic roles — so v2 sections only claim what the math supports:
the most-repeated, highest-energy cluster's spans ship as `"chorus"`;
nothing else is labeled (no fake verse/bridge). The energy-derived
`"high"` blocks continue unchanged alongside. The overlay's ramp already
accepts `{high, chorus}` (0.10.0).

This directly attacks the BAD GIRL failure class: boundaries come from
harmonic repetition, not loudness, so a brickwalled master segments fine
(P6 acceptance case unchanged: BAD GIRL must yield ≥1 meaningful section).

## Revisit triggers

- allin1 (or a REPUTABLE successor) ships a NATTEN-current release, or
- the chorus-proxy quality disappoints in the field AND a GPU burst-worker
  path is opened (the 9700X+5070Ti PC — never the RX480; Polaris gfx803
  has no usable ROCm/PyTorch path, evaluated and rejected 2026-07-19).
