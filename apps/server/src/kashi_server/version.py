"""Pipeline versioning: MAJOR bumps invalidate processed documents (plan R-3)."""

# 2.0: hizalama-v2 — Kim MelBand vocal separation on by default + lrclib-
# anchored windowed alignment. Word timings change wholesale, so the archive
# re-processes on first listen (old docs keep serving until then).
# 2.0.1: windowed-path quality = anchor agreement (prob ramp invalid there).
# 2.0.2: nonlexical/ad-lib lines block-shift onto their lrclib anchor (ear test).
# 2.0.3: lrclib free-text q= fallback rung — finds remix/extra-credit records
# the structured search misses (the Wet case); plausibility-guarded.
# 2.1.0: per-line `adlib` flag in the document (additive) + ad-lib word spans
# redistributed across the line (Faz 4 aesthetics groundwork).
# 2.2.0: nightcore — title/duration-ratio detection (+ explicit ingest
# options), rubberband slow-down for alignment, post-QA rescale onto the
# played clock; alignment.speed_factor finally carries r.
# 2.2.1: reviewer hardening — nightcore detection candidates pass the same
# plausibility guard as the q= rung; honest provenance for caller lyrics
# (lyrics_source="caller", no fake lrclib id).
# 2.2.2: field fix — nightcore uploads live on CHANNEL "artists" ("Syrex"),
# so detection plausibility is title-only + one title-only query retry;
# clean_title also strips (Lyrics)/Official-style noise tokens.
# 2.2.3: wrong-song hardening (field: "Come On Eileen" served as "Come On
# Now") — CTC-prob gate on detected nightcore lyrics, significant-token title
# containment, duration-less q= last chance (Mor/Gasolina), Turkish-I-safe
# casefold tokens, \b clean_title markers, record-own-ratio r, usable=extract.
# 2.2.4: escape hatches live on the r=1 flow too (lyrics_text always wins,
# original_title repairs the lookup title); nightcore lyrics resolve BEFORE
# the rubberband stretch; explicit-r sanity misses fail honest instead of
# silently reverting; ffprobed (fractional) download duration feeds the
# sanity gate and detection ratio; referenceless QA path rederives ad-libs.
# 2.3.0: word-END sustain trim (Faz 5 P1 ear-test fix: words no longer hang
# past their sung duration into gaps; only ever shortens, tempo-adaptive cap,
# ad-lib lines exempt) + alignment.qa repair-provenance block and per-line
# words_derived flag in the document (additive; the lrclib publish gate and
# field debugging read them).
PIPELINE_VERSION = "2.3.0"
PIPELINE_MAJOR = 2
