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

from typing import Any

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
        }
    ),
    # The event the timing work exists for: how far a position report was off
    # and what the client did about it.
    "position_anomaly": frozenset(
        {"reason", "position_ms", "duration_ms", "delta_ms", "action", "source"}
    ),
    "error": frozenset({"scope", "code", "message"}),
    "watchdog": frozenset({"reason", "elapsed_ms", "cleared"}),
}

KNOWN_KINDS = frozenset(TELEMETRY_FIELDS)

# A single value is a diagnostic, not a document. Anything longer is either a
# mistake or an attempt to smuggle a payload through a field that passed the
# name check.
MAX_VALUE_CHARS = 500


def sanitize_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Filter one event's payload down to its contracted fields.

    Returns None for an unknown kind (the whole event is dropped). Unknown
    keys are silently removed; over-long strings are truncated rather than
    dropped, since a clipped message still names the failure.
    """
    allowed = TELEMETRY_FIELDS.get(kind)
    if allowed is None:
        return None
    clean: dict[str, Any] = {}
    for name, value in payload.items():
        if name not in allowed:
            continue
        if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
            value = value[:MAX_VALUE_CHARS]
        # Nested structures have no contracted meaning here and are the easy
        # way to hide unbounded data behind an allowed key name.
        if isinstance(value, (dict, list)):
            continue
        clean[name] = value
    return clean
