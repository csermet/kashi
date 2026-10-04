"""Error taxonomy + yt-dlp message classification (ported from VDL, 2026-07-08).

The 12 VDL error types are a contract: the worker's retry policy, the API's
`error_type` field and the operator's dashboards all key on these strings.
"""

import math
from collections.abc import Callable

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


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(p in text for p in patterns)


# Ordered: the first rule that matches wins. Our own markers come first, then
# the yt-dlp phrasings, most specific before most generic.
_MESSAGE_RULES: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("rate_limited", lambda t: RATE_LIMIT_ERROR_MARKER.lower() in t),
    ("cookie_expired", lambda t: COOKIE_EXPIRED_ERROR_MARKER.lower() in t),
    ("low_quality_audio", lambda t: LOW_QUALITY_AUDIO_MARKER.lower() in t),
    ("rate_limited", lambda t: _has_any(t, _RATE_LIMIT_PATTERNS)),
    ("cookie_expired", lambda t: _has_any(t, _COOKIE_EXPIRED_PATTERNS)),
    ("disk_full", lambda t: _has_any(t, _DISK_FULL_PATTERNS)),
    ("age_restricted", lambda t: "age" in t and _has_any(t, ("restrict", "confirm your age"))),
    ("private", lambda t: _has_any(t, ("private video", "this video is private"))),
    ("video_unavailable", lambda t: _has_any(t, ("removed", "deleted", "terminated"))),
    ("geo_blocked", lambda t: _has_any(t, ("region", "geo")) or ("country" in t and "not" in t)),
    ("copyright", lambda t: _has_any(t, ("copyright", "blocked it on copyright"))),
    ("video_unavailable", lambda t: _has_any(t, ("unavailable", "video not found"))),
    ("network", lambda t: _has_any(t, ("network", "timed out", "timeout", "connection", "socket"))),
    # KASHI ADDITION: a googlevideo 403 is a stale/failed signature or a
    # bot-check hiccup, not a permanent property of the video — retry.
    ("network", lambda t: "403" in t and "forbidden" in t),
    # KASHI ADDITION (2026-10): this is what dead player clients look like
    # (only storyboards left, no audio format) — systemic, every video at
    # once, not a property of this one. As "other" it would be permanent
    # and block the song for 7 days after the fix ships.
    ("network", lambda t: "requested format is not available" in t),
)


def classify_error_message(msg: str) -> str:
    """Map a yt-dlp (or our own) error string onto the 12-type taxonomy."""
    text = msg.lower()
    return next((kind for kind, matches in _MESSAGE_RULES if matches(text)), "other")


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
    downloaded_abr, acodec = _downloaded_audio(info)
    max_abr = _best_available_abr(info.get("formats") or [], audio_codec_family(acodec))
    return _quality_ok(downloaded_abr, max_abr), downloaded_abr, max_abr


def _downloaded_audio(info: dict) -> tuple[float, str | None]:
    """Bitrate and codec of what was downloaded; a merged download carries
    them on its audio-only requested format instead of the top level."""
    downloaded_abr = float(info.get("abr") or 0.0)
    acodec = info.get("acodec")
    if not downloaded_abr and info.get("requested_formats"):
        for fmt in info["requested_formats"]:
            if fmt.get("vcodec") == "none":
                return float(fmt.get("abr") or 0.0), fmt.get("acodec")
    return downloaded_abr, acodec


def _best_available_abr(formats: list[dict], family: str) -> float:
    """Highest bitrate among DRM-free audio-only formats of the same family."""
    max_abr = 0.0
    for fmt in formats:
        if fmt.get("vcodec") != "none" or fmt.get("acodec") in (None, "none"):
            continue
        if fmt.get("has_drm"):
            continue
        if family and audio_codec_family(fmt.get("acodec")) != family:
            continue
        max_abr = max(max_abr, float(fmt.get("abr") or 0.0))
    return max_abr


def _quality_ok(downloaded_abr: float, max_abr: float) -> bool:
    if not downloaded_abr:
        return True  # no bitrate info — trust the download
    if max_abr <= PREMIUM_AUDIO_THRESHOLD_KBPS:
        return True  # intrinsically low-quality source
    floor = max_abr * QUALITY_GRACE_RATIO
    return not (downloaded_abr < floor and not math.isclose(downloaded_abr, floor))
