"""Error taxonomy (VDL kit). A1 ships the constants the queue needs;
the yt-dlp message classifiers land with the download stage (A3).
"""

# VDL's 12 error types, verbatim (gitlab root/vdl worker.py, verified 2026-07-08).
TRACK_ERROR_TYPES = (
    "rate_limited",
    "cookie_expired",
    "low_quality_audio",
    "video_unavailable",
    "geo_blocked",
    "copyright",
    "age_restricted",
    "private",
    "network",
    "disk_full",
    "verify_failed",
    "other",
)

# Kashi additions (not part of the VDL taxonomy).
KASHI_EXTRA_ERROR_TYPES = ("lyrics_not_found", "worker_lost", "alignment_failed")

# Transient types auto-retry (max_attempts, increasing delay); disk_full is
# deliberately NOT transient (VDL lesson).
TRANSIENT_ERROR_TYPES = (
    "rate_limited",
    "cookie_expired",
    "low_quality_audio",
    "network",
    "verify_failed",
)


def is_transient_error(error_type: str | None) -> bool:
    return error_type in TRANSIENT_ERROR_TYPES
