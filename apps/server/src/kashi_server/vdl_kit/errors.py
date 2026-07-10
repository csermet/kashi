"""Error taxonomy + yt-dlp message classification (ported from VDL, 2026-07-08).

The 12 VDL error types are a contract: the worker's retry policy, the API's
`error_type` field and the operator's dashboards all key on these strings.
"""

import math

# VDL's 12 error types, verbatim.
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
# deliberately NOT transient (VDL lesson: retrying a full disk just burns time).
TRANSIENT_ERROR_TYPES = (
    "rate_limited",
    "cookie_expired",
    "low_quality_audio",
    "network",
    "verify_failed",
)

RATE_LIMIT_ERROR_MARKER = "Rate-limited by YouTube"
COOKIE_EXPIRED_ERROR_MARKER = "Cookie expired or invalid"
LOW_QUALITY_AUDIO_MARKER = "Low-quality audio (Premium expected)"
CANCELLED_MARKER = "Cancelled by user"

_RATE_LIMIT_PATTERNS = (
    "this content isn't available, try again later",
    "this content isn't available",
    "rate-limit",
    "rate limit",
    "too many requests",
    "http error 429",
    "429:",
)
_COOKIE_EXPIRED_PATTERNS = (
    "sign in to confirm",
    "sign in to view",
    "please sign in",
    "use --cookies",
    "use --cookies-from-browser",
    "this video requires payment",
    "this video is available to this channel's members",
)
_DISK_FULL_PATTERNS = (
    "no space left on device",
    "disk full",
    "errno 28",
    "out of disk space",
    "ioerror: 28",
)

# Nominal (format-selection) thresholds; verify.py has separate MEASURED ones.
PREMIUM_AUDIO_THRESHOLD_KBPS = 200
QUALITY_GRACE_RATIO = 0.75


class PipelineError(Exception):
    """Carries a taxonomy `error_type` up to the worker's retry decision."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


class JobCanceled(Exception):
    """Raised at a checkpoint when the job was canceled or its lease was lost."""


def is_transient_error(error_type: str | None) -> bool:
    return error_type in TRANSIENT_ERROR_TYPES


def classify_error_message(msg: str) -> str:
    """Map a yt-dlp (or our own) error string onto the 12-type taxonomy."""
    text = msg.lower()

    if RATE_LIMIT_ERROR_MARKER.lower() in text:
        return "rate_limited"
    if COOKIE_EXPIRED_ERROR_MARKER.lower() in text:
        return "cookie_expired"
    if LOW_QUALITY_AUDIO_MARKER.lower() in text:
        return "low_quality_audio"

    if any(p in text for p in _RATE_LIMIT_PATTERNS):
        return "rate_limited"
    if any(p in text for p in _COOKIE_EXPIRED_PATTERNS):
        return "cookie_expired"
    if any(p in text for p in _DISK_FULL_PATTERNS):
        return "disk_full"

    if "age" in text and ("restrict" in text or "confirm your age" in text):
        return "age_restricted"
    if "private video" in text or "this video is private" in text:
        return "private"
    if "removed" in text or "deleted" in text or "terminated" in text:
        return "video_unavailable"
    if "region" in text or "geo" in text or ("country" in text and "not" in text):
        return "geo_blocked"
    if "copyright" in text or "blocked it on copyright" in text:
        return "copyright"
    if "unavailable" in text or "video not found" in text:
        return "video_unavailable"
    if any(p in text for p in ("network", "timed out", "timeout", "connection", "socket")):
        return "network"
    if "403" in text and "forbidden" in text:
        # KASHI ADDITION: a googlevideo 403 is a stale/failed signature or a
        # bot-check hiccup, not a permanent property of the video — retry.
        return "network"
    return "other"


def parse_ytdlp_error(exc: Exception) -> str:
    """User-facing message for a yt-dlp exception (markers stay machine-readable)."""
    try:
        from yt_dlp.utils import DownloadCancelled

        if isinstance(exc, DownloadCancelled):
            return CANCELLED_MARKER
    except ImportError:  # pragma: no cover - yt_dlp is a hard dependency
        pass

    message = str(exc)
    error_type = classify_error_message(message)
    if error_type == "rate_limited":
        return RATE_LIMIT_ERROR_MARKER
    if error_type == "cookie_expired":
        return COOKIE_EXPIRED_ERROR_MARKER
    return message


def classify_ytdlp_error(exc: Exception) -> str:
    return classify_error_message(str(exc))


def audio_codec_family(acodec: str | None) -> str:
    """'opus'/'libopus' -> opus; 'mp4a.*'/'aac*' -> aac; else ''."""
    if not acodec:
        return ""
    codec = acodec.lower()
    if codec.startswith(("opus", "libopus")):
        return "opus"
    if codec.startswith(("mp4a", "aac")):
        return "aac"
    return ""


def validate_audio_quality(info: dict) -> tuple[bool, float, float]:
    """(ok, downloaded_abr, max_available_abr) — codec-aware Premium gate.

    Compares only within the same codec family: Premium AAC existing while no
    Premium Opus does must not condemn a perfectly good Opus download.
    """
    downloaded_abr = float(info.get("abr") or 0.0)
    acodec = info.get("acodec")
    if not downloaded_abr and info.get("requested_formats"):
        for fmt in info["requested_formats"]:
            if fmt.get("vcodec") == "none":
                downloaded_abr = float(fmt.get("abr") or 0.0)
                acodec = fmt.get("acodec")
                break

    family = audio_codec_family(acodec)
    max_abr = 0.0
    for fmt in info.get("formats") or []:
        if fmt.get("vcodec") != "none" or fmt.get("acodec") in (None, "none"):
            continue
        if fmt.get("has_drm"):
            continue
        if family and audio_codec_family(fmt.get("acodec")) != family:
            continue
        max_abr = max(max_abr, float(fmt.get("abr") or 0.0))

    if not downloaded_abr:
        return True, downloaded_abr, max_abr  # no bitrate info — trust the download
    if max_abr <= PREMIUM_AUDIO_THRESHOLD_KBPS:
        return True, downloaded_abr, max_abr  # intrinsically low-quality source
    if downloaded_abr < max_abr * QUALITY_GRACE_RATIO and not math.isclose(
        downloaded_abr, max_abr * QUALITY_GRACE_RATIO
    ):
        return False, downloaded_abr, max_abr
    return True, downloaded_abr, max_abr
