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
    # Faz 4 additive fields land here: speed_factor, lyrics_text, original_title.


class IngestRequest(BaseModel):
    source: SourceRef
    hints: IngestHints
    options: IngestOptions = IngestOptions()


class IngestResponse(BaseModel):
    job_id: uuid.UUID
    status: str


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
