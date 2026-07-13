"""Request/response models for the v1 API (contract: kashi-faz3-plan.md A2)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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


class IngestOptions(BaseModel):
    separate: bool = False
    # Nightcore (Faz 4): an explicit factor beats title auto-detection; the
    # original song's title/lyrics are the escape hatch when the reupload's
    # metadata is mangled (channel-name "artists" etc.).
    speed_factor: float | None = Field(default=None, gt=1.0, le=2.0)
    lyrics_text: str | None = Field(default=None, min_length=1, max_length=20_000)
    original_title: str | None = Field(default=None, min_length=1, max_length=500)


class IngestRequest(BaseModel):
    source: SourceRef
    hints: IngestHints
    options: IngestOptions = IngestOptions()


class IngestResponse(BaseModel):
    job_id: uuid.UUID
    status: str


class ReprocessRequest(BaseModel):
    source: SourceRef
    # Optional: when omitted, the latest job's hints for this source are reused.
    hints: IngestHints | None = None
    # The ingest escape hatches work here too: reprocess IS the manual-retry
    # tool, and user ingest reuses completed/failed jobs — without options
    # there was no API path to retry a wrong-song track with original_title.
    options: IngestOptions = IngestOptions()


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
