# fx-lexicon expansion playbook (the offline LLM tool, Faz 6.5 P4)

How the lexicon grows without an LLM ever entering the runtime: the model
drafts, a deterministic gate rejects mechanical mistakes, a human curates,
CI re-checks the shipped file forever. Runtime stays 100% deterministic
(two runs → byte-identical fx), self-hosters are never affected.

## The loop

1. **Draft (LLM, offline).** Feed the prompt template below plus the
   CURRENT `pipeline/data/fx_lexicon.yaml` to any capable model. Output:
   proposed additions per category + at most a handful of new categories.
2. **Gate (deterministic).**
   `uv run python scripts/expand_lexicon.py draft.yaml`
   — schema shape, id charset (the overlay's tag gate twin), Turkish stem
   discipline (≥4 chars, pre-normalized İ/I forms), duplicate and
   cross-category collision detection. ERRORs block; warnings are
   curation flags.
3. **Curate (human).** Caner accepts/rejects entries; taste and
   false-positive risk are HIS call, not the model's. Every new category
   costs an icon + tint on the overlay side — keep new categories rare.
4. **Ship.** Bump the lexicon `version` (minor for additions), run
   `pnpm codegen`-adjacent checks (none needed — lexicon is data), commit.
   `tests/test_lexicon_lint.py` keeps the shipped file honest in CI, and
   a lexicon content change is a pipeline minor bump (behavior changes).

## Prompt template (v1, tune freely)

> You are expanding the fx lexicon of a lyrics-effects engine. Below is
> the current `fx_lexicon.yaml`. Matching rules: `keywords_*` are exact
> full-word matches after normalization; `stems_*` are prefix matches of
> at least 4 characters; Turkish stems must survive vowel narrowing and
> consonant softening (write the invariant prefix, e.g. `patl`), irregular
> inflections go to `variants_tr`; everything must be written lowercase
> pre-normalized (Turkish: İ→i, I→ı BEFORE lowering). One word belongs to
> exactly ONE category. `prototypes_*` are short definition sentences (not
> bare words) used as embedding centroids.
>
> Propose: (a) for each existing category, up to N genuinely common
> pop/hip-hop/EDM/rock lyric words (EN slang/AAVE welcome) as
> keywords/stems, (b) at most K new categories with full blocks. Flag any
> word that could belong to two categories instead of guessing. Output
> valid YAML in the file's exact format.

## Threshold calibration (the other P4 leg)

`EMBED_THRESHOLD` (semantics.py) ships conservative (en 0.86 / tr 0.88).
Calibration: dump per-line embedding scores from the archive, label a
~120-150 line EN+TR eval set, sweep 0.80-0.92 per language, pick from the
precision/recall curve with false-positives ≤5%. Report:
`docs/research/embed-threshold-calibration-2026-07.md`.
