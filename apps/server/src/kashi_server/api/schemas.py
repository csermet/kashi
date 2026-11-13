"""Request/response models for the v1 API (contract: kashi-faz3-plan.md A2)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from kashi_server.pipeline.lrclib import is_lrc, lyrics_from_lrc
from kashi_server.vdl_kit.errors import PipelineError


class SourceRef(BaseModel):
    type: Literal["youtube", "plex", "upload"]
    # Path-safe by construction: source ids land in /v1/lyrics/{type}/{id}, and
    # a '/' there would produce a row no URL can ever address (Starlette routes
    # on the decoded path, so even %2F does not help). YouTube ids, Plex rating
    # keys and upload hashes all fit this alphabet.
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class IngestHints(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    artist: str = Field(min_length=1, max_length=500)
    album: str | None = Field(default=None, max_length=500)
    duration_ms: int | None = Field(default=None, ge=0)
    artwork_url: str | None = Field(default=None, max_length=2000)


# One definition each, shared by the request options and the song override
# (C.1), so the two bounds cannot drift apart.
#: Explicit nightcore factor: 1.0 < r <= 2.0 (detection's own band is narrower).
SPEED_FACTOR_GT = 1.0
SPEED_FACTOR_LE = 2.0
#: Caller lyrics text, in characters.
LYRICS_TEXT_MAX_CHARS = 20_000


class IngestOptions(BaseModel):
    separate: bool = False
    # Nightcore (Faz 4): an explicit factor beats title auto-detection; the
    # original song's title/lyrics are the escape hatch when the reupload's
    # metadata is mangled (channel-name "artists" etc.).
    speed_factor: float | None = Field(default=None, gt=SPEED_FACTOR_GT, le=SPEED_FACTOR_LE)
    lyrics_text: str | None = Field(default=None, min_length=1, max_length=LYRICS_TEXT_MAX_CHARS)
    original_title: str | None = Field(default=None, min_length=1, max_length=500)


class ReprocessOptions(IngestOptions):
    # Admin only: store the new document even when it regresses the stored one
    # (pipeline/document.py `regression`). Not on IngestOptions, so a client
    # ingest can never push a worse document over a better one. None, not
    # False, so the stored job options stay exactly what they were unless asked.
    force_persist: bool | None = None


class IngestRequest(BaseModel):
    source: SourceRef
    hints: IngestHints
    options: IngestOptions = IngestOptions()


class IngestResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    # True when an EXISTING job answered (live, done, or the 7-day permanent-
    # fail block) — reused+failed is that block speaking, not a fresh attempt.
    reused: bool = False


class UploadResponse(BaseModel):
    source: SourceRef  # feed straight into POST /v1/ingest
    duration_ms: int
    expires_at: datetime


class PublishRequestIn(BaseModel):
    source: SourceRef


class PublishRequestOut(BaseModel):
    id: uuid.UUID
    status: str  # queued | published | dry_run | failed


class ReprocessRequest(BaseModel):
    source: SourceRef
    # Optional: when omitted, the latest job's hints for this source are reused.
    hints: IngestHints | None = None
    # The ingest escape hatches work here too: reprocess IS the manual-retry
    # tool, and user ingest reuses completed/failed jobs — without options
    # there was no API path to retry a wrong-song track with original_title.
    options: ReprocessOptions = ReprocessOptions()


class JobOut(BaseModel):
    id: uuid.UUID
    status: str
    progress_stage: str
    error_type: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    result_url: str | None


class KeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "user"] = "user"


class KeyOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    disabled: bool
    created_at: datetime
    last_used_at: datetime | None


class KeyCreatedOut(KeyOut):
    key: str  # plaintext — returned exactly once, at creation


class TelemetryEventIn(BaseModel):
    """One diagnostic event. `kind` is a free string on purpose: an unknown
    kind is dropped and counted by the handler rather than failing the batch,
    so an overlay newer than its server keeps reporting everything else."""

    # Aware only: the column is timestamptz, so a naive value would be read
    # in the server's session timezone and silently shift the whole timeline.
    ts: AwareDatetime
    kind: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatchIn(BaseModel):
    session_id: uuid.UUID
    # The 64 KB body cap already bounds this; the count keeps one batch from
    # becoming thousands of tiny rows in a single transaction.
    events: list[TelemetryEventIn] = Field(min_length=1, max_length=100)


class TelemetryAck(BaseModel):
    stored: int
    dropped: int
    # Values removed by the field contract. A client watching this number
    # learns its field names drifted before the rows go quiet.
    filtered: int = 0


#: Whole-sheet clock offset bound for a song override: five minutes. The
#: measured whole-sheet errors are 15-21 s (2026-11 review, fresh archive
#: songs) and a lyricless video intro runs a minute or two; past this the
#: record is not this edit, and pinning another record is the repair.
OFFSET_LIMIT_MS = 300_000
#: lrclib ids are integers; 2^53 - 1 keeps them exact in every JSON client.
LRCLIB_ID_MAX = 9_007_199_254_740_991


class SongOverrideIn(BaseModel):
    """PUT body: the whole override (create or replace). Field meanings and
    the precedence rules: `kashi_server.overrides`. Unknown keys are refused
    - a typo here would otherwise store an override without the repair."""

    model_config = ConfigDict(extra="forbid")

    lrclib_id: int | None = Field(default=None, ge=1, le=LRCLIB_ID_MAX)
    lyrics_text: str | None = Field(default=None, min_length=1, max_length=LYRICS_TEXT_MAX_CHARS)
    speed_factor: float | None = Field(default=None, gt=SPEED_FACTOR_GT, le=SPEED_FACTOR_LE)
    # + = the singing comes later than the sheet's stamps say. 0 = none.
    offset_ms: int | None = Field(default=None, ge=-OFFSET_LIMIT_MS, le=OFFSET_LIMIT_MS)
    # Stored only: the pipeline cannot show text without lit words yet.
    plain_only: bool = False
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def _coherent(self) -> "SongOverrideIn":
        if self.offset_ms == 0:
            self.offset_ms = None
        text = self.lyrics_text
        if text is not None and not text.strip():
            raise ValueError("lyrics_text is blank")
        if text is not None and self.lrclib_id is not None:
            raise ValueError("lyrics_text and lrclib_id are exclusive: one lyrics source per song")
        lrc = text is not None and is_lrc(text)
        if lrc:
            try:
                lyrics_from_lrc(text or "")
            except PipelineError as exc:
                raise ValueError(f"lyrics_text: {exc.message}") from None
        if self.offset_ms is not None and self.lrclib_id is None and not lrc:
            raise ValueError(
                "offset_ms corrects a sheet's stamps: it needs a pinned lrclib_id or "
                "lyrics_text with LRC stamps (on the automatic pick it would silently "
                "go wrong the day the lookup picks another record)"
            )
        if (
            self.lrclib_id is None
            and text is None
            and self.speed_factor is None
            and self.offset_ms is None
            and not self.plain_only
        ):
            raise ValueError("nothing to override")
        return self


class SongOverrideOut(BaseModel):
    source: SourceRef
    lrclib_id: int | None
    lyrics_text: str | None
    speed_factor: float | None
    offset_ms: int | None
    plain_only: bool
    note: str | None
    # True when the stored document is the one a run that applied this
    # override's current content persisted (song_overrides.applied_etag).
    document_under_override: bool
    created_at: datetime
    updated_at: datetime


class SongOverrideSummary(BaseModel):
    """List entry: the lyrics text itself is left out (up to 20 000 chars)."""

    source: SourceRef
    lrclib_id: int | None
    lyrics_text_chars: int | None
    lyrics_text_lrc: bool
    speed_factor: float | None
    offset_ms: int | None
    plain_only: bool
    note: str | None
    document_under_override: bool
    updated_at: datetime
