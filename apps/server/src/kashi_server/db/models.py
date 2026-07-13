"""SQLAlchemy 2.0 models. Times are timestamptz; ids are server-side UUIDs
(gen_random_uuid() is built into PG13+ — no extension needed)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

LIVE_STATUSES = ("queued", "downloading", "separating", "aligning", "postprocessing")
RUNNING_STATUSES = ("downloading", "separating", "aligning", "postprocessing")
ALL_STATUSES = LIVE_STATUSES + ("completed", "failed", "canceled")

_LIVE_SQL = "','".join(LIVE_STATUSES)


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    key_hash: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    role: Mapped[str]
    disabled: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_used_at: Mapped[datetime | None]

    __table_args__ = (CheckConstraint("role IN ('admin','user')", name="ck_api_keys_role"),)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_type: Mapped[str]
    source_id: Mapped[str]
    pipeline_major: Mapped[int]
    status: Mapped[str] = mapped_column(server_default=text("'queued'"))
    hints: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    options: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(server_default=text("3"))
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    lease_expires_at: Mapped[datetime | None]
    error_type: Mapped[str | None]
    error_message: Mapped[str | None]
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint("source_type IN ('youtube','plex','upload')", name="ck_jobs_source_type"),
        CheckConstraint(f"status IN ('{"','".join(ALL_STATUSES)}')", name="ck_jobs_status"),
        # Idempotency backstop: at most ONE live job per (source, pipeline_major).
        Index(
            "uq_jobs_active",
            "source_type",
            "source_id",
            "pipeline_major",
            unique=True,
            postgresql_where=text(f"status IN ('{_LIVE_SQL}')"),
        ),
        Index(
            "ix_jobs_claim",
            "status",
            "next_attempt_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("ix_jobs_source", "source_type", "source_id", created_at.desc()),
    )


class ProcessedTrack(Base):
    __tablename__ = "processed_tracks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_type: Mapped[str]
    source_id: Mapped[str]
    schema_version: Mapped[int] = mapped_column(server_default=text("1"))
    pipeline_version: Mapped[str]
    pipeline_major: Mapped[int]
    sync: Mapped[str]
    quality_score: Mapped[float]
    title: Mapped[str | None]
    artist: Mapped[str | None]
    duration_ms: Mapped[int | None]
    document: Mapped[dict[str, Any]]
    etag: Mapped[str]
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "schema_version", name="uq_processed_source"),
    )


class UploadedAudio(Base):
    """Bring-your-own-audio staging (Faz 5 P4): the API pod receives the
    multipart upload, the worker pod fetches it from here — no shared
    volume, no object store, no new netpol. Rows are small-count and
    short-lived: the worker deletes them the moment their job goes
    terminal (the AUDIO DELETION GUARANTEE extends to the database), and
    a TTL sweep catches orphans whose job never ran."""

    __tablename__ = "uploaded_audio"

    # urlsafe-base64 sha256 of the content (43 chars, no padding) — doubles
    # as natural dedup and fits the SourceRef.id contract.
    id: Mapped[str] = mapped_column(primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int]
    mime: Mapped[str | None]
    duration_s: Mapped[float]
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    expires_at: Mapped[datetime]

    __table_args__ = (Index("ix_uploaded_audio_expires", "expires_at"),)


class LrclibPublish(Base):
    """Contribute-back ledger (Faz 5 P6): one row per (source, etag) —
    OUR dedup, since lrclib documents no server-side dedup (PoW is its only
    abuse control). A document edit (new etag) may be published again; the
    identical document never is."""

    __tablename__ = "lrclib_publishes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_type: Mapped[str]
    source_id: Mapped[str]
    etag: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'queued'"))
    error: Mapped[str | None]
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    finished_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','published','dry_run','failed')", name="ck_lrclib_publish_status"
        ),
        UniqueConstraint("source_type", "source_id", "etag", name="uq_lrclib_publish_doc"),
    )
