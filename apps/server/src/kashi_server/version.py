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
PIPELINE_VERSION = "2.2.1"
PIPELINE_MAJOR = 2
