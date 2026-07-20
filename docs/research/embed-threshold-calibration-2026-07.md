# EMBED_THRESHOLD field calibration (Faz 6.5 P4) — verdict: the layer defaults OFF

**Date:** 2026-07-20 · **Data:** full archive dump (9,489 uncovered lines
across ~130 documents, `scripts/dump_embed_scores.py` run in the production
worker, mirroring `semantics.py` exactly: keyword-covered lines excluded,
`query:`-prefixed normalized text, prototype centroids) · **Labels:** 200
stratified lines (25 per score band per language), hand-labeled
Y (correct) / B (borderline-defensible) / N (wrong).

## Score distribution — the cosine space is compressed

| lang | n | p50 | p90 | p95 | p99 | max |
|------|------|------|------|------|------|------|
| en | 6,441 | 0.864 | 0.886 | 0.891 | 0.899 | 0.914 |
| tr | 1,712 | 0.880 | 0.898 | 0.903 | 0.912 | 0.920 |

The shipped thresholds (en 0.86 / tr 0.88) sat AT the median: they tagged
**59% (en) / 50% (tr)** of all uncovered lines — nothing like the intended
"conservative, rare, high-confidence" layer.

## Labeled precision — score does not separate right from wrong

Per-band (25 samples each; strict = Y only, lenient = Y+B):

| band | en strict / lenient | tr strict / lenient |
|------|---------------------|---------------------|
| [0.84, 0.86) | 4% / 32% | 4% / 16% |
| [0.86, 0.88) | 16% / 48% | 16% / 40% |
| [0.88, 0.90) | 24% / 52% | 24% / 40% |
| [0.90, +) | 24% / 44% | 20% / 52% |

Population-weighted, above-threshold:

| threshold | en volume → wrong | tr volume → wrong |
|-----------|-------------------|-------------------|
| 0.86 (old en) | 3,786 lines → **51% wrong** | 1,481 → 59% |
| 0.88 (old tr) | 1,191 → 48% | 862 → **58% wrong** |
| 0.90 | 54 → 56% | 121 → 48% |

Precision plateaus around 24% strict / ~50% lenient and does NOT improve
with the score — E5 cosine against category-prototype centroids is not a
usable correctness signal on lyric lines. The P4 acceptance bar (false
positives ≤5% at the chosen threshold) is unreachable at ANY threshold.

## Verdict (the honest one — Faz 6 P2 NO-GO's sibling)

1. **`fx_embeddings` defaults OFF from pipeline 2.9.0.** A wrong line theme
   is worse than no theme (DG6); a layer that is ~half wrong even at its
   own top scores does not meet Kashi's bar. The env flag stays for
   experimentation; warmup keeps gating on it.
2. **`EMBED_THRESHOLD` → 0.90/0.90/0.90** as the harm-reduction floor for
   anyone who enables it anyway (volume drops 98% en / 86% tr vs. old).
3. **The overlay's ambient ring keeps working** — it falls back to the
   line's fx WORD tag (keyword layer, deterministic and precise) when no
   line theme exists (overlay 0.10.0).
4. Word-level tagging is UNAFFECTED: the curated keyword/stem layer (now
   v1.2) was always the precision path and remains always-on.

## What could revive a line-theme layer later

- The runtime-LLM design (docs/research/runtime-llm-tagging-memo.md) — its
  decision triggers explicitly include this gap.
- A trained classifier head over the embeddings (needs labeled data — this
  200-line set is the seed).
- Margin-based scoring (top1−top2) instead of absolute cosine — untested,
  noted for a future spike; the raw dump in the session scratchpad had no
  second-best column, so this round could not evaluate it.

## Reproduction

```
kubectl exec -i -n kashi-server deploy/kashi-worker -- \
    python3 - < scripts/dump_embed_scores.py > embed-scores.tsv
# stratified sample + labels + the stats script: session scratchpad 2026-07-20
```
