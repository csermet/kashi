# Runtime LLM tagging — design memo (Faz 6.5 P4, decision input for a later phase)

**Status: DESIGN ONLY — no implementation.** Caner's call (2026-07-19):
this phase ships the OFFLINE lexicon-expansion tool; the ingest-time LLM
layer is designed here and decided later with field data from v1.2.

## What it would be

A third tagging layer behind lexicon+embedding: at ingest time, lines that
neither layer covered are sent to an LLM ("label this lyric line with one
of these N categories or `none`"), and the verdicts are baked into the
document like every other fx tag — computed once server-side, replayed
forever client-side.

## Why it is NOT in this phase (the two hard problems)

1. **Determinism.** The document contract promises two runs produce
   byte-identical fx. temp=0 + pinned model narrows variance but does not
   eliminate it (provider-side model updates, sampling ties, tokenizer
   drift). Any reprocess could silently change tags — the exact class of
   surprise the fx system was designed to exclude.
2. **Operational surface.** The worker gains a network dependency + an API
   key (netpol currently allows worker egress for YouTube/lrclib only), a
   per-song marginal cost, and a failure mode ("LLM down") in a pipeline
   whose enrichment steps are all local and best-effort.

## Design sketch (if/when it lands)

- **Config:** `fx_llm_enabled: bool = False` (+ url/key/model envs). OFF
  default is a hard requirement — self-hosters must never need a key.
- **Scope:** line-level theme tags only (same contract as the embedding
  layer, `fx.lines`); word attribution stays the keyword layer's job.
  Closed label set = current lexicon category ids + `none`; anything else
  from the model is dropped (the overlay's tag charset gate would drop it
  anyway).
- **Determinism containment:** pin provider+model+version in the engine
  string (`keywords+e5@rev+llm:<model>@<date>`); temp=0, max_tokens tiny,
  majority-of-3 on disagreement; cache verdicts by
  `(model, lexicon_version, line_text)` in a DB table so a reprocess
  replays cached verdicts instead of re-asking (this, not temp, is the
  real determinism fix: ask once, remember forever).
- **Cost model:** ~40 uncovered lines/song × ~30 tokens ≈ 1-2k tokens/song
  with a small model — negligible per song, nonzero per archive wave;
  the verdict cache makes waves cheap after the first.
- **Netpol:** one extra egress rule for the worker (or an egress proxy);
  the API pod stays closed.
- **Failure posture:** identical to palette/beats — LLM error ⇒ no extra
  tags, document ships.

## Decision triggers (revisit when…)

- v1.2 + calibrated thresholds still leave a visible per-song "dead lines"
  gap in the field (the layers' coverage is measurable: fx.lines count vs
  line count in Grafana), OR
- multilingual coverage beyond EN/TR becomes a goal (lexicon curation cost
  scales linearly with languages; an LLM layer does not).

If neither trigger fires, this memo stays a memo.
