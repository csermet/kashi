"""Pipeline versioning: MAJOR bumps invalidate processed documents (plan R-3)."""

# 2.0: hizalama-v2 — Kim MelBand vocal separation on by default + lrclib-
# anchored windowed alignment. Word timings change wholesale, so the archive
# re-processes on first listen (old docs keep serving until then).
# 2.0.1: windowed-path quality = anchor agreement (prob ramp invalid there).
# 2.0.2: nonlexical/ad-lib lines block-shift onto their lrclib anchor (ear test).
PIPELINE_VERSION = "2.0.2"
PIPELINE_MAJOR = 2
