"""What field diagnostics are allowed to carry (Faz 6.7 P1).

The privacy promise for telemetry is "nothing beyond what a lyrics lookup
already sends". A promise in a document decays; this module makes it a
property of the server. Every event is filtered against the field list for
its kind before it reaches the database, so a client — including a future
version of our own overlay — physically cannot store a field nobody agreed
to. Unknown kinds and unknown keys are dropped and counted, never rejected:
diagnostics must not fail a client that is merely newer than its server.

Kinds mirror the sketch in docs/research/telemetry-6.7-sketch.md.
"""

import math
import re
from typing import Any

from kashi_server.auth import KEY_PREFIX

# One entry per event kind. The value is the complete set of payload keys the
# server will persist for it — adding a field means changing this line, which
# is exactly the review checkpoint the privacy contract needs.
TELEMETRY_FIELDS: dict[str, frozenset[str]] = {
    # Sent once when the overlay starts, so a report can be read against the
    # machine that produced it.
    "session_start": frozenset(
        {
            "app_version",
            "extension_version",
            "os",
            "os_version",
            "arch",
            "electron",
            "chromium",
            "display_count",
            "display_size",
            "effect_level",
            "theme_scope",
            "fill_style",
            "timing_offset_ms",
            "server_host",
        }
    ),
    # Track identity is already sent to this same server for every lookup.
    "track_changed": frozenset(
        {"video_id", "title", "artist", "duration_ms", "id_source"}
    ),
    # What the user actually got: which source answered and how good it was.
    "lyrics_outcome": frozenset(
        {
            "source",
            "quality",
            "pipeline_version",
            "sync",
            "speed_factor",
            "line_count",
            "upgraded",
            "attempt",
            # Whether the document came from a cache the client fell back to
            # because the live request failed. Without it a stale hit is
            # indistinguishable from a fresh one in the field data — the two
            # look identical at the call site.
            "stale",
        }
    ),
    # The event the timing work exists for: how far a position report was off
    # and what the client did about it.
    "position_anomaly": frozenset(
        {
            "reason",
            "position_ms",
            "duration_ms",
            "delta_ms",
            "action",
            "source",
            # Which video it happened on. Identity is already sent to this same
            # server for every lookup (see track_changed), and without it an
            # anomaly cannot be tied to the audio that produced it: the
            # 2026-08-13 carry-over reads exactly like an ordinary deep seek
            # until you can see that two neighbouring events name two different
            # videos with one duration between them.
            "video_id",
            # What the duration was corrected FROM. The client has sent this
            # since overlay 0.26.4 and the server has been dropping it, which
            # left the correction event unable to answer the one question it
            # exists for — whether the number it replaced was the previous
            # track's.
            "previous_duration_ms",
        }
    ),
    "error": frozenset({"scope", "code", "message"}),
    "watchdog": frozenset({"reason", "elapsed_ms", "cleared"}),
}

KNOWN_KINDS = frozenset(TELEMETRY_FIELDS)

# A single value is a diagnostic, not a document. Anything longer is either a
# mistake or an attempt to smuggle a payload through a field that passed the
# name check.
MAX_VALUE_CHARS = 500

# Millisecond fields are the ones analysis actually computes on, so they are
# the ones a unit slip would corrupt silently. They are coerced to a bounded
# integer or dropped — a missing number beats a wrong one.
_MS_FIELD_LIMIT = 10**12  # ~31 years; anything past this is not a timestamp

# A free-text field is the one place a caller can accidentally hand us a
# secret: an error string built from a failed request carries the bearer token
# or a credentialed URL. Names alone cannot catch that, so values are scrubbed.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"{re.escape(KEY_PREFIX)}[0-9a-fA-F]{{8,}}"),
    re.compile(r"[Bb]earer\s+\S+"),
    re.compile(r"://[^/@\s]+:[^/@\s]+@"),  # user:pass in a URL
)
REDACTED = "[redacted]"


def redact_secrets(text: str) -> str:
    """Strip API keys, bearer headers and URL credentials out of free text."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def _clean_value(name: str, value: Any) -> Any | None:
    """Coerce one contracted value, or None to drop it."""
    # Nested structures have no contracted meaning here and are the easy way
    # to hide unbounded data behind an allowed key name.
    if isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return value  # bool before int: it is a subclass
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            # NaN/Infinity survive JSON parsing but PostgreSQL rejects them in
            # jsonb, which would fail the whole INSERT for one bad number.
            return None
        if name.endswith("_ms"):
            if abs(value) > _MS_FIELD_LIMIT:
                return None
            return int(value)
        return value
    if isinstance(value, str):
        return redact_secrets(value)[:MAX_VALUE_CHARS]
    if value is None:
        return None
    return None


def sanitize_payload(kind: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int] | None:
    """Filter one event's payload down to its contracted fields.

    Returns None for an unknown kind (the whole event is dropped), otherwise
    the cleaned payload and how many values were removed. That count is
    reported back to the client: a version whose field NAMES drifted would
    otherwise keep receiving a cheerful 202 while storing empty rows, and
    nobody would notice for a retention period.
    """
    allowed = TELEMETRY_FIELDS.get(kind)
    if allowed is None:
        return None
    clean: dict[str, Any] = {}
    filtered = 0
    for name, value in payload.items():
        cleaned = _clean_value(name, value) if name in allowed else None
        if cleaned is None:
            filtered += 1
            continue
        clean[name] = cleaned
    return clean, filtered
