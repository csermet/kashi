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
# 2.9.0: fx lexicon v1.2 (Faz 6.5 P4) — 4 new categories (drink/dream/space/
# storm) plus enrichment across the existing 20; variants_tr joins the
# exact-match space for irregular Turkish inflections. The embedding
# line-theme layer DEFAULTS OFF and EMBED_THRESHOLD rises to 0.90: a
# 200-line labelled archive sample showed it ~half wrong at every threshold
# (docs/research/embed-threshold-calibration-2026-07.md). Documents from
# here carry fx.lexicon kashi-fx/1.2.0 and, normally, no fx.lines.
# 2.10.0: structure sections v2 (Faz 6.5 P6) — librosa Laplacian
# segmentation over beat-synchronous chroma adds "chorus" spans beside the
# energy-derived "high" blocks. Config-gated (structure_sections, default
# off; the cluster runs it via env). allin1 was evaluated and declared
# unusable (docs/research/allin1-viability-2026-07.md).
# 2.11.0: reviewer follow-ups — segment boundaries rebuilt as
# [0, *beat_times, duration]; lexicon trap stems removed (yand-/titr-/text-).
# 2.12.0: structure honesty pass (Faz 6.5 closure verification). Boundaries
# now come from librosa.util.fix_frames — the same helper sync() uses — so a
# beat landing on frame 0 or the final frame no longer yields a label/bound
# mismatch that silently dropped the WHOLE structure pass (field: one canary
# track in ten). Chorus spans are also bounded: longer than 60 s is a
# structural block, not a chorus, and a winner still covering >55% of the
# track is the song's texture and yields nothing at all.
# 2.13.0: the document now says WHICH tagged words fire, not just which ones
# mean something (fx_select.py). Field verdict: an effect on every tagged word
# is exhausting — one library track carries 42 occurrences of the same
# category, and the archive averages 18.2 tagged words per document against a
# cap of 60 that was being hit. Selection ranks lines by the section holding
# them (chorus > loud), keeps all of a rare category but half of a dominant
# one (spread by even stride, never top-k: intensity is constant per category,
# so top-k is front-loading in disguise), allows a second effect only on a
# long line and never adjacent, caps the song by CADENCE rather than a flat
# count, guarantees a chorus that has something to say is never silent, and
# sweeps globally so two effects never land within 700 ms.
# The old 60-tag brake is GONE: it kept the strongest, which deleted weaker
# categories outright and reported a truncated count for the dominant one, so
# any ratio computed from it was computed against a lie. What remains is a
# 400-candidate memory bound in document order.
# `fx.select` is stamped on the block. It is load-bearing: without it a newer
# client cannot tell a chosen list from a legacy dense one and would fire
# every tag on a line.
# 2.14.0: a chorus is recognised by its LYRIC, not by an audio section.
# Field verdict on 2.13.0: repeats of the same line fired on different words
# and only some repeats fired at all (measured: one line repeated seven times
# chose word 7 three times, word 4 once, and four of seven stayed silent).
# Both followed from treating every repeat as an unrelated line. Lines that
# sing the same words now form a class, the in-line choice is made once and
# copied to every repeat (intersected with each repeat's own candidates, so a
# pattern word that is not a candidate there stays silent rather than being
# invented), and class words are exempt from density thinning.
# Also: a repeated word on one line ("music, music, music") is ONE gesture —
# counted once against quota, spacing and the cadence cap, but every member
# lights up; a glyph between members does not break it, a different sung word
# does. The line's opening word now takes a scoring penalty (not a ban) so
# effects stop gravitating to line starts. A quarter of the cadence is
# reserved for non-class lines, or a chorus that repeats twenty times would
# spend the whole budget and silence the verses.
# 2.14.1: the cap was undoing the consistency 2.14.0 had just bought. Field
# measurement on the first refreshed document: 52 candidates, cap 24, density
# thinning removed NOTHING because nearly every line belonged to some class, so
# the cap alone had to drop 28 gestures. It did that over the classes combined,
# which kept one chorus at six-of-six while cutting another to one-of-eleven —
# a chorus that fires every time beside a chorus that fires once, which is the
# inconsistency the class exists to remove. The loss is now shared between
# classes in proportion to their size. The pattern-thinning step had the same
# shape of bug and was worse: it treated every class's word indices as ONE
# shared pattern, so two choruses firing on words 4 and 3 had a union of {3,4}
# and dropping the later index silenced every repeat of the first outright.
# 2.14.2: the run gesture reached the field dead — every run in the first
# refreshed document had been ground down to a single word, so "aynı kelime her
# geçtiğinde efekt çıksın" was never actually shipping. One root cause with two
# faces: the selection counts GESTURES (a repeated word is one effect to the
# eye) while two later steps still counted WORDS.
#   · Pattern thinning removed one word index at a time. A five-word run is one
#     gesture, so each removal freed exactly zero budget while dismantling the
#     insistence — the loop ground the run to one word and only then moved on.
#     It now thins whole gestures, bucketed by tag exactly as `_gestures` counts.
#   · The chorus rescue then reinserted a run's FIRST member only and paid with
#     a single word. Both halves were wrong once runs survived: the repeat fired
#     a different pattern from its siblings (the inconsistency the class exists
#     to remove, reintroduced by the rescue), and evicting one word of a run
#     freed no gesture, so the song drifted past its cadence — measured at 26
#     gestures against a cap of 24. It now trades a whole gesture for a whole one.
#   · `MAX_RUN_WORDS` documented a per-gesture limit but was applied to the
#     line's total, so one long run could outrun the overlay's per-line belt and
#     be trimmed on the client instead — server planning one thing, screen
#     showing another. Enforced per gesture now, in the rescue too.
# Verified against the stored Rihanna document: every repeat class fires ONE
# pattern, the run survives whole as (4,5,6,7,8), 22 gestures under a cap of 24.
# 2.15.0: a repeat class fires all of its repeats or none of them.
# 2.14.x degraded a class the cadence could not afford by keeping the pattern
# and silencing some repeats, on the theory that quieter beats inconsistent.
# The field measured what that actually looks like: across 23 documents, 238
# repeat classes split 44 fully firing / 43 PARTIAL / 151 silent, and the
# verdict on the middle group was blunt — "the same line fires in one repeat and
# not in another is inconsistent" (Shape of You: "oh im in love with your body"
# 9 repeats, 3 firing; "come on be my baby" 11 repeats, 5 firing). A hook that
# stays quiet reads as a decision; a hook that flickers reads as a fault.
# The ladder is now: trim the singles to their reserve → thin every class's
# pattern uniformly (unchanged, and it already grinds each class to a one-gesture
# pattern before anything is dropped, so "make it fit before killing it" is a
# step that already existed) → pack WHOLE classes first-fit, dropping outright
# whatever does not fit. First-fit rather than first-fail-stop: one twenty-repeat
# chant must not starve the three smaller hooks that would have fitted after it.
# Classes are ordered by score, then by SIZE — score alone decides almost nothing
# (intensity is a per-category constant, so choruses routinely tie) and document
# order would let a twice-sung early phrase outrank the eleven-times-sung hook.
# Budget a killed class hands back is re-spent on the verses instead of
# evaporating; the non-class reserve is unchanged.
# The chorus rescue becomes class-atomic on BOTH sides, which also closed two
# latent defects that predate this change: its candidate pool is drawn from the
# full tagged list rather than the kept one, so it could resurrect a single
# repeat of a capped-away class (or place an OFF-pattern word on a repeat that
# was deliberately silent), and its eviction pool could pay for a rescue by
# silencing one repeat of a class that was otherwise firing throughout.
# Accepted costs, stated rather than discovered later: a low-scoring hook can be
# entirely silent in a dense song, and a short track that is nothing but a chant
# too long to seat carries no effects at all.
# 2.15.1: an impossible duration hint is discarded, not believed (Faz 8 P4).
# The 2.4.2 client-edit gate assumed "stale-hint jitter is seconds, a different
# edit is minutes". The archive says otherwise: all 76 client-edit failures ran
# 1.15x-22x the real audio (LMFAO "Hot Dog" claimed 3279s against 147s), and
# across 236 track_changed telemetry events the CURRENT extension never once
# reports above 900s — those hints are stale readings from the generation
# before the Faz 6.7 P0 position/duration guards. Because admin reprocess
# replays latest.hints verbatim, every retry re-earned the 7-day permanent-fail
# block and 29 songs stayed permanently unreachable. A hint above the
# pipeline's own track ceiling (max_track_duration_s — ingest will not even
# create a job for one, and download refuses audio that long) cannot describe
# any track, so it is now dropped with a warning + counter instead of being
# read as evidence of a different edit. The Sinsirella gate is untouched for
# every hint that could describe a real track.
# The same hint no longer rides the lrclib ladder as ?duration= (choose_record
# would reject the right record on a ±3s filter it can never satisfy), and
# track.duration_ms now comes from the MEASURED audio rather than the client's
# claim — 47 archived documents carry an impossible duration this way, which
# then propagates into canonical_group's 5s buckets and the processed_tracks
# column. Full audit: docs/research/hizalama-zinciri-durum-2026-08.md.
# 2.16.0: quality_score stops flattering itself (Faz 8 P-B2). The Faz 8 field
# audit found the number wrong in three separate ways, and 130 of 253 archived
# documents (51.4%) scored exactly 1.00 — a metric with half its population on
# the ceiling cannot rank anything, which is why "quality 1.0 but the words
# drift" kept being the honest field report.
#   · THE BASIS WAS A GUESS. document.py derived quality_basis from
#     `windowed`, but BOTH line-mode exits preserve that flag while returning
#     a prob-based score: _line_only_fallback (regroup token mismatch) and
#     _degrade_to_line (majority flagged). So documents with NO word timings
#     at all shipped stamped "anchors" — nine of the ten in the archive at
#     >= 0.94, five at exactly 1.00, BABYMETAL "BxMxC" with 32 of its 42 lines
#     flagged. The basis now travels on AlignResult, set where the number is
#     computed. A proxy that is right most of the time is the bug.
#   · THE SCORE ROSE WITH DAMAGE. _quality drew its ramp from surviving_probs
#     — the words QA had NOT deleted — so every dropped line pruned the pool
#     toward its most confident members. Tarkan "Op": 1.00 with 19 of 40 lines
#     flagged and an 11 s global offset. The ramp is now multiplied by the
#     fraction of referenced lines that survived intact, so damage can only
#     ever cost. Pinned as a property test, not one example.
#   · THE DEGRADE PATH NEVER RECOMPUTED. _degrade_to_line carried the pre-QA
#     number through untouched. It now reports line-anchor agreement under a
#     new basis, "line-anchors", which says in the name that no word evidence
#     exists.
# Two additive enum values in the schema ("probs+anchors", "line-anchors");
# the overlay never reads quality_basis, and old documents stay valid — this
# is a MINOR bump on purpose. Scores WILL move on reprocess, downward, and
# that is the point: the ones that fall are the ones that were damaged.
# NOT fixed here: the 0.02/0.15 prob ramp is still calibrated on FULL MIXES
# while every document is now aligned on separated vocals, which is the third
# saturation source. Recalibrating needs a measured benchmark run, not a
# guess at a constant. Audit: docs/research/hizalama-zinciri-durum-2026-08.md.
# 2.16.1: lrclib's clock is not assumed to be the audio's clock (Faz 8 P-B0).
# Caner's field report: on YouTube Music a song often exists as both a music
# entry and a VIDEO, and the video edit opens with an intro the song release
# does not have — "the lyrics start straight away and that is what makes it
# drift". The mechanism checks out. The intro also pushes the durations apart,
# which drops the lrclib anchors (ANCHOR_CLOCK_TOLERANCE_S) and leaves
# whole-audio alignment, whose measured mean offset is 2000 ms against 282 ms
# on the anchored path. If the document then degrades to line mode,
# _degrade_to_line wrote RAW lrclib starts — discarding the very shift the
# aligner had just measured and stored in qa.offset_ms. Three of the ten
# line-mode documents in the archive carry an |offset| above 3 s, the largest
# 16.9 s, every millisecond of it thrown away.
# The offset is now applied on that path, but only when it is a CLOCK
# DIFFERENCE rather than noise: a real one shifts every line by the same
# amount, so the deviations cluster tightly around their median, while an
# aligner that simply lost the song scatters them — and there lrclib's raw
# clock really is the better guess. Median absolute deviation is the test
# (OFFSET_TRUST_MAD_MS), median rather than mean so a couple of genuinely
# lost lines cannot veto a shift the rest of the document agrees on.
# Both halves are pinned, and the scatter case is the OLD behaviour unchanged.
# 2.17.0: the aligner becomes a seam instead of a constant (Faz 8 P-B1).
# Swapping it is now a certainty rather than a maybe: the MMS-300m checkpoint
# and facebook/mms-300m under it are BOTH CC-BY-NC-4.0, and the wrapper's own
# README says to use a different model commercially — so the shipped chain
# cannot go into a paid product no matter how good it is. A certainty does not
# belong in a constant buried in a module.
#   · `align_model` in settings picks the checkpoint; it defaults to today's,
#     so this release changes NO timings. The model cache is keyed by name
#     rather than a pair of module globals, which is what per-language routing
#     will need — the Japanese gap is closed by pointing one language at a
#     different checkpoint, not by rewriting the chain.
#   · AlignResult carries `model_name`, and `alignment.method` is BUILT from
#     it. It used to be a literal, so a swapped checkpoint would have entered
#     the archive still claiming mms-300m and a later comparison would have
#     had nothing to group by. Bare ids keep today's shape
#     (ctc-forced-aligner/mms-300m…); an org-qualified id keeps its org so two
#     forks of one name stay distinct.
# The human lyricsfile path is untouched: its method describes the LYRICS, not
# an aligner, and must not start advertising a model that never ran.
# 2.18.0: Japanese lyrics reach the aligner as kana morae (Faz 8 P-B3).
# Two failures stacked on every Japanese document, both measured on the
# archive: nine of the ten line-mode documents are non-Latin.
#   · regroup_words_into_lines needs sum(len(line.split())) == len(segments).
#     A script without word delimiters cannot satisfy that, so Japanese always
#     took the line-mode exit and shipped with no word timings at all.
#   · The deeper one: MMS romanizes through uroman, and uroman reads kanji as
#     CHINESE — 空 becomes "kong", not "sora". Even with the counts fixed, the
#     model was being shown text that does not sound like the audio. It is a
#     documented uroman limitation, not a bug.
# Both dissolve at the same point. Each morpheme is converted to its UniDic
# kana reading and the aligner is handed morae: uroman romanizes kana
# correctly, and one mora per token makes the identity hold by construction.
# `pron` wins over `kana` because it writes long vowels as ー, which is what
# is actually sung.
# What the SCREEN shows and what the ALIGNER hears are now separate things —
# PreparedLine carries both plus the ownership counts, and the mora spans fold
# back onto the surfaces that own them, so the document still displays 宇宙
# over the span of うちゅー. A surface takes the WEAKEST prob of its morae:
# averaging would let one confident kana hide a lost one.
# Non-Japanese lines are untouched — prepare_line returns None and the
# whitespace path runs exactly as before, which the tests pin explicitly.
# Dictionary segmentation rather than an LLM on purpose: deterministic (the
# document contract promises byte-identical output) and measurably more
# accurate. fugashi (MIT) + unidic-lite (BSD-selectable) are commercially
# clean, unlike pykakasi (GPL-3). The pattern is Nightingale's, read for the
# idea only — it is GPL-3 and no code was taken.
# NOT YET MEASURED on real audio: the acceptance case is the archive's
# BABYMETAL and ヤラララ documents leaving line mode.
#
# 2.18.1: 2.18.0 was built on two guesses and BOTH were wrong. Measured against
# the shipped aligner in the worker pod before it ever left main:
#   · The split granularity is decided by the `language` ARGUMENT, not by the
#     text. `language="jpn"` splits EVERYTHING per character — including a line
#     of pure English — while `language="eng"` splits on whitespace. So the
#     decision is per JOB, not per line; routing line by line would have
#     desynchronised exactly the mixed-script documents J-pop is full of.
#   · The unit the aligner emits is the CHARACTER, not the mora. 2.18.0 fed it
#     space-joined morae, which would have produced empty segments for the
#     separators and a count that could never match. Morae stay the right unit
#     for a human reading kana; they are not the unit of this contract.
# Also: blank segments are now dropped alongside stars. A Japanese job turns
# the space `" ".join(texts)` puts between lines into a segment of its own with
# an empty romanization; an English job never produces one, so the filter is a
# no-op there and load-bearing here.
# What 2.18.0 got right stands: uroman really does read 空 as "kong" (verified
# directly — ['k o n g', 'n i', 'g u a n g', 'r u'] for 空に光る), and the fix
# is still to hand the aligner the kana reading. Had 2.18.0 shipped it would
# have failed safe to line mode rather than misaligned, but it would have
# fixed nothing.
# 2.19.0: the anchor proposes, the audio disposes (Faz 8 B4 — the arbiter).
# A line drifting past DRIFT_THRESHOLD_MS lost its word timings outright. That
# rule had no second opinion in it: the lrclib anchor said "misplaced" and the
# words died for it, even when they were internally perfect and merely sat on
# a shifted clock.
# The archive says it is too eager. At document scale the cheapest threshold
# catching both genuinely bad songs destroys ELEVEN good ones. At line scale —
# 3383 lines against ground truth — the best signal flags the worst 5% and
# catches 35% of the truly bad: seven times chance, and nowhere near a
# separator. A signal that good is worth warning with and nowhere near good
# enough to delete with.
# So a flagged line now gets evidence, and DELETION CARRIES THE BURDEN OF
# PROOF:
#   · vocal onsets (onsets.py) — the only signal independent of the aligner,
#     because it comes from the audio rather than the model that produced the
#     timings. Measured Spearman +0.399 per line, the best of every free
#     candidate, and it survives its own density confounder.
#   · silence coverage — how much of the line's own span carries no word.
#     +0.345. A line smeared over an instrumental gap looks nothing like a
#     sung one.
# BOTH must corroborate the anchor before the words go. When they contradict
# it the line is block-shifted onto the anchor — line and words on one clock,
# the ad-lib path's precedent — and marked `uncertain` (additive schema field
# + a qa counter) so a client can de-emphasise what the server no longer
# destroys.
# Conservative by construction: fewer than three words means neither signal
# means anything and today's rule stands; onset detection failing (no librosa,
# unreadable audio) leaves coverage to rescue only the unambiguous case. No
# new behaviour anywhere there is no new evidence.
# The arbiter is pure and is handed onsets, never audio — detection lives at
# the I/O boundary in onsets.py, measured on the wav the WINNING alignment
# heard (the second pass swaps the mix for separated vocals, and onsets from
# the other one would be evidence about a different signal).
# Not in yet: cross-model disagreement. Qwen3-FA measured +0.483 song-level
# correlation against MMS where same-family models sit at +0.92..+0.945, so it
# is a real third signal — but its word-level value is unmeasured and nothing
# enters production unmeasured.
# 2.19.1: zero onset support condemns a line on its own (Faz 8 B4, corrected
# by its own first field run). 2.19.0 required BOTH signals to corroborate
# before deleting, and six reprocessed documents showed what that misses:
# every flagged line was rescued, including "To fight, to fight, to fight" at
# onset support 0.00 with coverage 1.00.
# The two signals are not symmetric. Coverage measures a line's SHAPE, onsets
# measure its PLACE — and a line dragged somewhere wrong keeps its shape
# perfectly, so coverage will always vouch for it. Measured on the 3206
# ground-truth lines: the 21 with zero onset support were **67% genuinely
# bad** (median PCO 0.25, median error 578 ms) against a 4% base rate, and the
# exact class 2.19.0 rescued — zero support with high coverage — was 65% bad.
# So exactly-zero support now deletes without a second vote. The rule is
# ZERO, not "low": one word landing on an onset means the line is somewhere
# real, and partial support goes back to needing corroboration. It touches
# 0.7% of lines, which is why the rescue win survives it.
# 2.20.0: the aligner is chosen PER LANGUAGE (Faz 8.1). 2.17.0 made the
# checkpoint a setting on the assumption that the licence-clean replacement
# would be one model. It is not: measured against the same benchmark, English
# is best served by jonatasgrosman/wav2vec2-xls-r-1b-english (Apache-2.0, PCO
# 0.8789 — it BEAT the CC-BY-NC default) and Turkish by mpoyraz's cv7
# (CC-BY-4.0, 0.930 against 0.938, a difference the 95% interval cannot
# distinguish from zero). One `align_model` cannot express that.
#   · `align_models` maps language -> checkpoint, empty by default. With no
#     entry the old two settings answer exactly as before, so every Faz 8
#     measurement stays valid and this release changes no timings.
#   · The checkpoint and its `romanize` flag are ONE object, never two
#     parallel tables. romanize is a property of the vocabulary, not a
#     preference: MMS learned romanized Latin, mpoyraz's model learned
#     ç ğ ı ö ş ü, and feeding either the other one's text form is measurably
#     wrong. A config shape that lets an operator set the model and forget the
#     flag would reintroduce the failure by hand.
#   · Language keys normalize ("en" -> "eng"), because the aligner is called
#     with ISO-639-3 and a two-letter key would silently never match. Two
#     spellings of one language are a startup error rather than a coin flip.
#   · An explicit model argument (the benchmark's --align-model) bypasses the
#     table entirely: a hand-picked checkpoint paired with some other model's
#     text form is the same mismatch in a different costume.
# Unlisted languages — Japanese among them, which still has no permissive
# candidate — keep the global default. Operational note: the worker caches
# weights per name, so a mixed-language queue holds every configured
# checkpoint in memory at once.
# 2.21.0: the aligner's systematic lateness becomes correctable (Faz 9 P1).
# Alignment error on SINGING is not centred on zero. Measured on JamendoLyrics
# English — full-precision human annotation, 5693 verified words, 20 songs —
# 76% of words are marked LATE with a median signed error of +80 ms, and ALL
# TWENTY songs carry it. That last part is what makes it a property rather than
# a few bad songs, and it is invisible in a pooled median.
# The mechanism was predicted in Faz 8 research before it was measured: a sung
# note's onset lands on the syllable's VOWEL while the written word starts on a
# consonant, so a CTC model hears the vowel and reports the start about one
# consonant late. Two independent confirmations that it is a property of
# SINGING and not of a checkpoint: MMS-300m (CC-BY-NC, kim-melband separator)
# and jonatasgrosman's XLS-R 1B (Apache, kim-base separator) — different
# models, different separators — produce 76.4%/+81 ms and 76.3%/+78 ms, and
# every one of 20 leave-one-song-out folds picks the same -80 ms correction.
#   · `align_offset_ms` shifts every word and line, and rides in `align_models`
#     per language beside the checkpoint it was measured for. DEFAULT 0: a
#     shift moves every timing the pipeline produces, so it ships per language
#     after being measured, never inherited. English is measured; Turkish is
#     NOT, because the Turkish eval set is only valid at 300 ms and cannot see
#     an 80 ms bias.
#   · Whole SPANS move, not just starts — the model did not mishear how long
#     the word was, and stretching every word by 80 ms would inflate exactly
#     the sustain the 2.3.0 end-trim exists to control. A span that would cross
#     zero is clamped there.
#   · Applied at the aligner's own exit, so anchors, the arbiter, line QA and
#     the benchmark all see the corrected clock rather than each judging a bias
#     the pipeline already knows about.
# Measured value of the correction on the shipped English chain, held out:
# PCO@0.1 0.4892 -> 0.5847 (+0.096), PCO@0.3 0.8931 -> 0.9101. The tool that
# fits it (`benchmarks/lateness.py`) averages SONGS rather than words, so its
# rows are comparable with the run's own aggregate, and cross-validates, so a
# per-song accident cannot be sold as a bias.
# 2.22.0: the lateness correction learns the word's first sound (Faz 9 P2),
# and in doing so REFUTES the mechanism 2.21.0 quoted for itself.
# Per-class medians on the shipped English chain, before any correction:
#   vowel-initial     +112 ms (n=1376)     plosive-initial   +62 ms (n=1813)
#   fricative-initial  +87 ms (n=828)      sonorant-initial  +59 ms (n=1676)
# Faz 8 predicted the opposite ordering: the note lands on the syllable's
# vowel, so a CONSONANT-initial word should be late by that consonant's
# duration and a vowel-initial word roughly unbiased. Vowel-initial words are
# instead the latest class by a wide margin. The correction survives because
# it was measured; its explanation does not, and pipeline/phonetics.py says so
# rather than quietly keeping the story that reads well.
# What the ordering does fit — offered as a hypothesis, not a finding — is
# LANDMARK SHARPNESS: a plosive burst is unmistakable, friction has a soft
# edge, and a word sung straight out of the previous word's voicing has no
# boundary in the signal at all, so the model drifts to the steady state.
#   · `offset_by_initial` refines `offset_ms` per class, per language. Empty
#     is 2.21.0 behaviour exactly. Turkish gets nothing: its eval set is only
#     valid at 300 ms and phonetics is a per-language fact, not a transferable
#     one.
#   · Per-word offsets can invert the word ORDER where two words sit closer
#     than their offsets differ, so the shifted stream is re-normalised the
#     way regroup normalises the raw one — starts never go backwards, a word
#     never runs past the next one's start, and lines follow their own words
#     instead of the base offset.
# Worth +0.010 PCO@0.1 held out (LOSO; 95% CI [+0.001, +0.018], winning in 14
# of 20 songs) on top of the constant's +0.0955. The number first recorded
# here (+0.013 "held out") was in fact the in-sample fit — corrected by the
# 2026-08-12 audit; the constant -80 itself IS fully held-out (all 20 folds
# chose it). Small, real, and the honest framing is that it is small: the
# remaining distance lives in the 100-300 ms band, not here.
# 2.23.0: the 2026-08-12 audit round — eight parallel reviews of everything
# Faz 8.1/9 shipped, and the fixes for what survived adversarial verification.
#   · langid: a DETECTED language outside the ten-code map now passes through
#     raw instead of becoming "eng". Since routing shipped, "eng" is no longer
#     a harmless hint: it selects the English-vocabulary checkpoint AND the
#     English lateness corrections, a regression from the MMS fallback such
#     songs should get. "eng" remains only for empty text / failed detection.
#   · The arbiter judges on the ACOUSTIC clock again: AlignedWord carries the
#     displacement the lateness correction actually applied (shift_ms), and
#     onset_support undoes it before comparing against onsets. The shift was
#     silently spending 60-110 ms of the 200 ms tolerance that was calibrated
#     on raw starts — found independently by two reviews. Restores the B4
#     rescue calibration exactly; no thresholds changed.
#   · AlignerChoice is strict: unknown fields are startup errors (pydantic's
#     default silently dropped "offsetms"/"romanize_" typos), offset values
#     are bounded to +-500 ms, and offset_by_initial keys must be the four
#     classes phonetics.py actually produces.
#   · phonetics folds combining marks before classifying, so â/î/û and é are
#     marked VOWELS rather than unknown script (dormant until a TR table).
#   · Record correction: the per-initial table's gain is +0.010 PCO@0.1 by
#     honest LOSO, not the +0.013 previously labelled "cross-validated" (that
#     was the in-sample fit). The constant -80 needs no correction: all 20
#     folds chose it.
# 2.23.1: the audit round's two deferred operational fixes.
#   · The model cache is BOUNDED: two checkpoints stay resident (the routed
#     EN+TR pair), a third arrival evicts the least recently used. Unbounded,
#     a mixed-language day held ~6.4 GB of weights under a measured 8.2 GB
#     separation peak against a 12 Gi limit. A wrong eviction costs a ~90 s
#     reload; no eviction costs an OOMKill mid-job.
#   · A network-shaped failure while loading a cold model (unlisted languages
#     are not warmed; the first such job downloads inside the job) is now
#     classified transient and retried. It used to land in "other" ->
#     permanent fail + a 7-day requeue block, for weather. A misspelled
#     checkpoint stays permanent.
# 2.24.0: the different-edit probe (Faz 9, the Safari case). /api/get returns
# ONE record and the ladder stopped there even when that record's duration
# said "wrong edit" — guaranteeing the downstream anchor gate would strip its
# stamps and line QA would fight its clock for the whole song. Measured live:
# Safari got a 187 s record for a 179 s track — 21 of 47 lines flagged, the
# document snapped -6 s, and the singer's own 179 s synced record sat in
# /api/search unread. When the get-rung record misses the audio duration by
# more than DIFFERENT_EDIT_TOLERANCE_S (pinned equal to the worker's
# ANCHOR_CLOCK_TOLERANCE_S by a contract test), ONE search request now looks
# for a duration-matched record, and only a SYNCED match replaces — anchors
# are the point, and trading a synced-but-wrong-edit record for plain text
# would lose the timing reference for nothing. Fires only on a mismatch, so
# the etiquette budget is untouched on the normal path.
# 2.24.1: a stamp past the record's OWN duration is not evidence. Safari's
# 179 s record timed its last line at 181.2 s, and that stamp anchored an
# alignment window past the end of the audio. The LINE stays — it is still
# sung — but it loses a time nothing can support, and a stampless line
# already has a defined meaning downstream (it inherits its neighbour's
# span). One second of slack absorbs honest rounding.
# 2.25.0: the sub-threshold drift nudge — the band nothing was watching.
# A line more than DRIFT_THRESHOLD_MS (2.5 s) from its anchor is flagged and
# judged; a line within it is passed through untouched. Field report
# 2026-08-12 landed squarely in between: "Oh I, oh I" arriving noticeably
# late, measured at +0.6..+0.9 s against its anchor across all six
# occurrences. A QUARTER of that document's lines sit 0.3-2.5 s from their
# anchors, and no layer looked at any of them — too close to be called
# misplaced, far enough for a listener to see.
# The anchor and the aligner disagree there, and neither can settle it: one
# is crowd-sourced, the other is the model under review. The audio can, since
# vocal onsets come from neither. So the line's words are scored where they
# are and where the anchor says they belong, and the line moves only when the
# anchor's position holds MEANINGFULLY more onsets (15 points; a one-word
# difference is noise and moving a visible line is not free). Ties, missing
# onsets, and lines under three words all leave the aligner's timing alone.
#   · Nothing is deleted and nothing is flagged: a nudged line was never in
#     doubt as CONTENT, only as position, so it carries no `uncertain` mark —
#     just a count in alignment.qa.nudged (schema additive).
#   · Block shift, line and words together, on the flagged-rescue precedent.
#   · The band's lower bound is a cheap skip, not the protection: below the
#     arbiter's 200 ms onset tolerance a shift cannot change support at all,
#     so the evidence gate is what holds. A test pins that relationship.
# 2.25.1: the ad-lib detector was missing two token shapes, and the repair
# built for exactly this case could not reach the lines that needed it most.
#   · "Oh I, oh I, oh I, oh I" failed the all-nonlexical test on its four
#     "I"s. A one-letter vowel word is lexical to a reader and pure
#     vocalization to a singer — no consonant, no landmark, nothing for CTC
#     to lock onto — so it now counts as ad-lib-compatible BESIDE a real
#     vocalization (never on its own, and only i/a: "Oh b" is not a hook).
#     Measured in the archive at +0.6..+0.9 s from its anchor across all six
#     occurrences, and reported by ear as arriving late.
#   · "aw" was simply absent from the table. On JamendoLyrics the line
#     "aw ah aw ah aw aw ah" sits FORTY SECONDS from its anchor — the worst
#     single placement in the set, and the ad-lib snap could not see it.
# Why the anchor is the right authority here, measured on the same set: for
# lines drifting into the snap band the anchor beats the aligner on 67% of
# ad-lib lines (median 378 -> 182 ms) and 80% of nonlexical ones overall
# (785 -> 216 ms), while on LEXICAL lines the aligner wins by the same margin
# (103 vs 287 ms). Ad-libs are where the human stamp is right and the model
# is lost; that asymmetry is why this path existed, and why two missing
# token shapes cost so much.
# 2.26.0: sustained hooks are HELD to the next line. With the ad-lib snap
# finally reaching them (2.25.1) the placement was right and the pace was
# not: "it goes by in 4 seconds when it should take 5". The numbers agree —
# Shape of You's hook covered 1.66 s of a 2.35 s phrase and then sat there.
# Measured across the archive (435 lines): an ad-lib line leaves a median
# 747 ms hole before the next line, 44% of its own span, where a LEXICAL line
# leaves 30 ms; 82% of ad-lib lines leave more than 300 ms against 31% of
# lexical ones. The aligner hears the part of a held note it can segment, not
# the note. So an ad-lib line now extends to a breath short of the next line
# before its words are redistributed across that span.
# Bounded at 1500 ms: a longer silence after a hook is an instrumental break,
# and stretching words over one is precisely the hanging word the sustain
# trim exists to prevent. The hold only ever LENGTHENS — a hole smaller than
# the breath margin is already closed, and a hold that shortens is not a hold.
# 2.27.0: a parenthetical response is moved onto the voice that ANSWERS.
# "I'm too hot (hot damn)" is two voices across a short breath, and the
# aligner staples the second to the tail of the first. Measured on 8
# human-annotated instances of that figure: the response's first word sat
# 550-850 ms EARLY in seven, and 800 ms LATE in the one where we had dropped
# it inside the silence itself. Loudness settles it — and only loudness does.
# Everything cheaper was measured and killed first: the beat grid is
# indistinguishable from random at beat/8th/16th resolution over 87k words;
# repeat-consistency CONFIRMS the wrong answer, because seven of the eight
# instances are wrong identically; onsets fire once every 344 ms, so every
# candidate position already sits on one.
# Result with the guards on: median |error| 750 -> 148 ms, 0/8 -> 4/8 within
# 200 ms, and NOTHING worsened. The guards are not decoration: without the
# next-line bound the same rule pushes three other parenthetical lines
# 391-748 ms INTO the line after them. It refuses rather than clamps —
# clamping scored identically on the one line that needed it (its room was
# already negative), so the simpler rule wins and never half-guesses.
# Ad-lib lines are skipped: rederive_adlib_words owns those spans.
PIPELINE_VERSION = "2.27.0"
PIPELINE_MAJOR = 2
