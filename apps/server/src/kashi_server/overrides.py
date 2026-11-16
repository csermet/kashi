"""Per-song overrides (review step C.1): a correction entered once and remembered.

A repair used to live only in one job's `options` (caller lyrics text, an
explicit nightcore speed factor), and the next reprocess, which takes options
from its request only, built the song again without it. A `song_overrides` row
now carries the repair for the song, and:

- `apply` merges it into the options of EVERY job created for the song (both
  enqueue paths in `queue`), so the worker reads it exactly like request
  options and the job row records what the run used;
- `override_lyrics` is the worker's side for the keys only an override writes
  (a pinned lrclib record, caller text with LRC stamps, a sheet offset);
- `applied_fingerprint` / `fingerprint` let the persist guard tell a run that
  applied the song's CURRENT override from one that did not.

Precedence: options given explicitly in the request win over the override for
that one run, per group (see `merge`); the override wins over automatic lookup
and detection. Docs: song_overrides notes and the module docstring of
`pipeline.document.persist_processed_track` for the guard rule.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kashi_server.db.models import SongOverride
from kashi_server.pipeline.lrclib import LyricsText, is_lrc, lyrics_from_lrc, lyrics_from_record
from kashi_server.vdl_kit.errors import PipelineError

#: Job-options key of the marker an override run carries:
#: {"fingerprint": str, "applied": [field...], "displaced": [field...]}.
MARKER = "song_override"

#: Override fields the worker acts on, in a fixed order. `plain_only` is stored
#: but not acted on (the document schema cannot express "text without times"),
#: so it is neither merged nor fingerprinted; `note` is commentary.
ACTED_FIELDS = ("lrclib_id", "lyrics_text", "speed_factor", "offset_ms")

#: Request option keys that decide which lyrics a run aligns / which clock.
REQUEST_LYRICS_KEYS = ("lyrics_text", "original_title")
REQUEST_CLOCK_KEYS = ("speed_factor",)
#: The override fields of the clock group; every other acted-on field belongs
#: to the lyrics group (an offset belongs to the override's own sheet).
OVERRIDE_CLOCK_FIELDS = frozenset({"speed_factor"})

#: Job-options keys only an override writes (request models drop unknown keys).
PINNED_RECORD_KEY = "lrclib_id"
LRC_TEXT_KEY = "lyrics_lrc"
SHEET_OFFSET_KEY = "sheet_offset_ms"


def acted_fields(row: Any) -> dict[str, Any]:
    """The row's acted-on fields that are set (a zero offset is no offset).
    `row`: a SongOverride, or a raw-SQL row with the same column names."""
    fields: dict[str, Any] = {}
    if row.lrclib_id is not None:
        fields["lrclib_id"] = int(row.lrclib_id)
    if row.lyrics_text is not None:
        fields["lyrics_text"] = row.lyrics_text
    if row.speed_factor is not None:
        fields["speed_factor"] = float(row.speed_factor)
    if row.offset_ms:
        fields["offset_ms"] = int(row.offset_ms)
    return fields


def fingerprint(fields: dict[str, Any]) -> str:
    """Stable identity of an override's acted-on content."""
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def merge(request: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """The options a run starts with: the request's, plus the override's.

    Per group, the request wins: a request `lyrics_text` or `original_title`
    displaces the override's whole lyrics group (pinned record, text, offset);
    a request `speed_factor` displaces the override's factor. Displaced fields
    are listed in the marker, and a run with any displaced field does not
    count as having applied the override (persist guard).

    No acted-on fields -> the request dict itself, untouched (no marker)."""
    if not fields:
        return request
    lyrics_taken = any(key in request for key in REQUEST_LYRICS_KEYS)
    clock_taken = any(key in request for key in REQUEST_CLOCK_KEYS)
    merged = dict(request)
    applied: list[str] = []
    displaced: list[str] = []
    for name in ACTED_FIELDS:
        if name not in fields:
            continue
        taken = clock_taken if name in OVERRIDE_CLOCK_FIELDS else lyrics_taken
        if taken:
            displaced.append(name)
            continue
        applied.append(name)
        value = fields[name]
        if name == "lyrics_text":
            # Plain text rides the existing caller path byte for byte; text
            # with stamps gets its own key, so a REQUEST's lyrics_text keeps
            # today's plain meaning.
            merged[LRC_TEXT_KEY if is_lrc(value) else "lyrics_text"] = value
        elif name == "offset_ms":
            merged[SHEET_OFFSET_KEY] = value
        else:
            merged[name] = value
    merged[MARKER] = {
        "fingerprint": fingerprint(fields),
        "applied": applied,
        "displaced": displaced,
    }
    return merged


def find(
    s: Session, source_type: str, source_id: str, *, for_update: bool = False
) -> SongOverride | None:
    stmt = select(SongOverride).where(
        SongOverride.source_type == source_type, SongOverride.source_id == source_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    return s.scalars(stmt).first()


def apply(s: Session, source_type: str, source_id: str, options: dict[str, Any]) -> dict[str, Any]:
    """`options` merged with the song's override; `options` itself when the
    song has none (the job row then holds exactly what it held before)."""
    row = find(s, source_type, source_id)
    if row is None:
        return options
    return merge(options, acted_fields(row))


def applied_fingerprint(options: dict[str, Any] | None) -> str | None:
    """The fingerprint of the override a job applied WHOLE, or None (no
    override, or a request displaced part of it)."""
    marker = (options or {}).get(MARKER)
    if not isinstance(marker, dict) or marker.get("displaced"):
        return None
    value = marker.get("fingerprint")
    return value if isinstance(value, str) else None


def carries(options: dict[str, Any] | None, row: Any) -> bool:
    """True when a job with these options applies the row's CURRENT acted-on
    content whole (the persist guard's "applied"; the admin reprocess reports
    it). `row`: a SongOverride or a raw-SQL row with the same column names."""
    fields = acted_fields(row)
    return bool(fields) and applied_fingerprint(options) == fingerprint(fields)


def _sheet_offset(options: dict[str, Any]) -> int:
    value = options.get(SHEET_OFFSET_KEY)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def override_lyrics(
    options: dict[str, Any], get_record: Callable[[int], dict | None]
) -> LyricsText | None:
    """Lyrics an override names (caller LRC, or a pinned lrclib record), with
    its sheet offset applied; None when the options name none.

    A pin that cannot serve lyrics fails the job as `other`, naming the pin:
    never `lyrics_not_found`, which would hand the job to the by-ear rung, and
    never a fallback to the ladder - either would rebuild the very document
    the pin exists to replace. Transient errors pass through (retry/defer)."""
    lrc = options.get(LRC_TEXT_KEY)
    if isinstance(lrc, str) and lrc.strip():
        try:
            return lyrics_from_lrc(lrc, offset_ms=_sheet_offset(options))
        except PipelineError as exc:
            raise PipelineError("other", f"song override text: {exc.message}") from None
    record_id = options.get(PINNED_RECORD_KEY)
    if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id <= 0:
        return None
    try:
        record = get_record(record_id)
    except PipelineError as exc:
        if exc.error_type != "lyrics_not_found":
            raise
        record = None
    if record is None:
        raise PipelineError("other", f"song override: pinned lrclib record {record_id} not found")
    try:
        lyrics = lyrics_from_record(record, offset_ms=_sheet_offset(options))
    except PipelineError:
        raise PipelineError(
            "other", f"song override: pinned lrclib record {record_id} carries no usable lyrics"
        ) from None
    return replace(lyrics, pinned=True)
