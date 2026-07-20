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
# 2.3.1: lrclib reachability fixes (Faz 5 P2) — multi-artist hints split on
# locale conjunctions (" ve ", &, commas, feat.) and retry with the primary
# artist + any-part plausibility (Drift Barbie/Señorita class); lrclib 4xx
# classified permanent instead of burning retries (61-min-mix 400 case), 429
# maps to rate_limited; the ingest API rejects over-cap durations up front.
# 2.4.0: Lyricsfile-READ (Faz 5 P3) — human word sync from lrclib's
# lyricsfile field is consumed AS-IS on the plain r=1 flow: separation,
# langid, CTC and line QA are skipped, the document rides the human clock
# (method lrclib-lyricsfile/1.0, lyrics_source "lyricsfile", quality 1.0).
# One record-selection policy (choose_record: lyricsfile-words > synced >
# plain + duration proximity) replaces the previous three; lyrics resolve
# BEFORE separation so a doomed lyrics_not_found no longer pays for stems.
# 2.4.1: different-edit anchor gate (field: a "video" upload's lyricless
# intro shifts every lrclib stamp — windowed anchors searched the wrong
# places and warped the whole doc). When the chosen record's own duration
# disagrees with the decoded audio by >5s, anchors drop and whole-audio
# alignment absorbs the offset; line QA still snaps via its median offset.
# 2.4.2: client-edit mismatch gate (field: a YTM "video" id played as a
# 451s clip in the browser while yt-dlp fetched the 216s SONG stream —
# music player clients substitute streams for video ids). When the client-
# reported duration and the downloadable audio disagree by >30s the job
# fails honest with both numbers instead of shipping a document timed to
# audio the browser never plays.
# 2.4.3: lyricsfile upgrade probe on the get rung (closure-e2e finding —
# /api/get returns ONE record, so the primary rung could never see a
# sibling carrying human word sync; the feature was unreachable in the
# wild). A get hit without word-level data now pays exactly one extra
# search request and upgrades only when a sibling probes word-level.
# 2.4.4: 2.4.3's upgrade probe REVERTED — lrclib /api/search never carries
# lyricsfile content (verified live: every hit returns the field empty and
# word-sync records may not rank in search at all), so the probe was one
# wasted request per song with zero possible benefit. The choose_record
# preference stays dormant until lrclib serves the field in search.
# 2.5.0: quality_basis provenance (Faz 6 P1) — documents now say what
# quality_score MEASURED: "ctc-probs" (whole-audio ramp), "anchors"
# (windowed line-anchor agreement — word-level feel is NOT measured; the
# honest label behind "quality 1.0 but drifting words"), or "human"
# (lyricsfile fast path, fixed 1.0). The number itself is unchanged.
# 2.6.0: FX data foundation (Faz 6 P3) — additive fx/energy/sections
# blocks. fx = curated keyword/stem tags (word-level) + optional
# multilingual-e5-small line-theme tags (semantics extra, fx_embeddings);
# energy = 2 Hz track-normalized RMS envelope; sections = energy-derived
# "high" blocks (chorus proxy — allin1-style labels stay future/additive).
# Old clients ignore all three; effects arrive with overlay 0.4.0.
# 2.7.0: composite-title fallback (Faz 6 P7) — when the primary lrclib
# ladder comes up dry, "Channel | Artist - Song (Lyrics)" upload titles are
# conservatively parsed (exactly one dash after noise strip) and retried
# ONCE with the parsed artist/title; plausibility gates unchanged. Second
# miss re-raises the original honest error. "(Official Music Video)" class
# bracket groups now count as noise for title hygiene.
# 2.8.0: fx lexicon v1.1 (Faz 6 field round 1 — "a touch more plentiful"):
# ~70 new EN+TR keywords/stems across the same 20 categories (vowel-narrowing
# aware TR stems, min-4 discipline kept). Documents re-tag richer on
# reprocess; fx.lexicon says kashi-fx/1.1.0.
PIPELINE_VERSION = "2.11.0"
PIPELINE_MAJOR = 2
