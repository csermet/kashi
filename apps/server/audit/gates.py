"""Faz 3.5 gates, committed before any run (``docs/research/yerel-model-denetim-2026-11.md``).

"Thresholds are written down and committed before the first run. A part that
does not beat the current ear on the labelled set does not enter." Every
number below is that note's, pinned by ``tests/test_audit_gates.py``; the
method parameters each step uses live with the method (``audit.anchors``).
Changing a gate after its step has run is a new pre-registration: say so in
the step's result line, and report the old gate's verdict next to the new one.
Where the note's wording needed an operational reading, the reading is written
here and marked READING.
"""

from __future__ import annotations

# --- Step 0: the evaluation, frozen -------------------------------------------
# Real errors in the 96 labelled moments, by class; the 5 `unsure` stay out
# until listened.
REAL_ERRORS = {"pile": 18, "late": 12, "early": 6, "missing": 6, "wrong_text": 4}
REAL_ERRORS_N = 46
VISIBLE_CLASSES = ("late", "early", "missing", "wrong_text")   # n = 28
VISIBLE_N = 28
# The owner's listening set: at least this many moments spread over the
# classes, plus the Enemy verse (the accuracy reference).
LISTENING_SET_MIN = 20
# Per-song summarizer input and the local server's context.
INPUT_MAX_TOKENS = 12_000
LOCAL_CONTEXT_TOKENS = 16_000

# --- Step 1: the anchor check (A1-A3) ------------------------------------------
# Pilot: a lyrics-source song is flagged with |section median| > 1 s and its
# offset within 1 s of the Faz 3 reading (sign included); no flag (READING: no
# shift and no wrong_sheet alarm) on the others, the hardstyle track included.
PILOT_SHIFT_MS = 1000
PILOT_OFFSET_TOL_MS = 1000
# Enemy is flagged by coverage only if the owner's listen confirms another mix.
WRONG_SHEET_UNMATCHED_SUNG = 0.25
WRONG_SHEET_UNMATCHED_LINES = 0.25
# Injected errors on the bench's JamendoLyrics copy (79 songs).
INJECT_GLOBAL_MS = (2000, 5000, 10_000, 20_000)
INJECT_VERSE_MS = (3000, 20_000)       # one verse shifted by a size in this range
INJECT_DRIFT_END_MS = (5000, 10_000)   # READING: linear drift, the last line moved this far
INJECT_GATED_MIN_MS = 5000             # detection is gated for shifts of at least this
INJECT_REPORTED_MS = 2000              # ... and reported for this one
INJECT_DETECTION_MIN = 0.90
# READING: "offset error <= 0.5 s when detected" as the MEDIAN absolute error
# over detected global and verse shifts (p90 reported); drift has no single
# offset and is detection-only.
INJECT_OFFSET_ERR_MS = 500
INJECT_FALSE_ALARM_MAX = 0.05          # READING: a shift or wrong_sheet alarm on an unmodified song

# --- Step 2: a second ear --------------------------------------------------------
# Per arm (Qwen3-ASR on the desktop; Whisper large-v3-turbo on cnr-intel):
SECOND_EAR_HEALTHY_MEDIAN_MS = 500     # |median line offset| on songs Faz 3 read healthy
SECOND_EAR_SHIFTED_MEDIAN_MS = 1000    # GOSSIP / Got 2 Luv U read beyond this, Faz 3 sign
SECOND_EAR_SILENT_REGION_MS = 2000     # CTC letter curve silent for longer than this ...
SECOND_EAR_SILENT_MAX = 0.05           # ... holds at most this share of transcript lines
# placed-line share at or above the current ear's: a relative gate (no number)

# --- Step 3: the summarizer A/B ----------------------------------------------------
SHUFFLED_RUNS = 3
M1_RECALL_MIN = 0.90                   # real errors (n = 46, <= 4 misses) ...
M1_PRECISION_MIN = 0.60                # ... at this precision, within finder + controls
M2_SPEARMAN_MIN = 0.5                  # model severity vs label severity, pooled
M2_PRECISION_AT_3_MIN = 0.7            # on songs with >= 3 real errors
M2_SONG_MIN_ERRORS = 3
M3_VISIBLE_RECALL_MIN = 0.7            # late + early + missing + wrong_text (n = 28)
M4_PROBE_MIN_CORRECT = 19              # numeric probe correct on >= 19 of 20 songs
M4_PROBE_SONGS = 20
M5_SCHEMA_VALID_MIN = 0.99             # schema-valid on the first try
M5_AGREEMENT_MIN = 0.90                # verdict agreement across shuffled runs

# --- Step 4: optional audio-LLM kill test ----------------------------------------
KILL_CLIPS = 20
KILL_CLIP_S = (4, 8)
KILL_REPEATED_MIN = 5                  # clips with the word repeated 2-4 times
KILL_REPEATS = (2, 4)
KILL_ABSENT_MIN = 5                    # clips without the word
KILL_COUNT_EXACT_MIN = 0.80
KILL_ONSET_TOL_MS = 250
KILL_ONSET_WITHIN_MIN = 0.80
KILL_MEDIAN_ONSET_STOP_MS = 500        # a median onset error above this ends the test


def verdict(passed: bool) -> str:
    """The word each step's result line uses."""
    return "passed" if passed else "failed"
