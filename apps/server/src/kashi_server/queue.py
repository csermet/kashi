"""Postgres-backed job queue (plan: FOR UPDATE SKIP LOCKED, no broker).

All functions take an open Session; TRANSACTION control belongs to the caller
(API request / worker loop). Times are computed in Python UTC so unit tests
can manipulate them; claim/lease math runs in SQL for atomicity.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kashi_server.config import settings
from kashi_server.db.models import LIVE_STATUSES, RUNNING_STATUSES, Job, ProcessedTrack
from kashi_server.vdl_kit.errors import is_transient_error

PERMANENT_FAIL_BLOCK_DAYS = 7


class QueueFull(Exception):
    """Queue depth cap reached — API maps this to 503 queue_full."""


def _now() -> datetime:
    return datetime.now(UTC)


def queue_depth(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(Job).where(Job.status.in_(LIVE_STATUSES))) or 0


def _find_reusable(
    s: Session, source_type: str, source_id: str, pipeline_major: int
) -> Job | None:
    """Idempotency lookups, in contract order (plan A1)."""
    live = s.scalars(
        select(Job)
        .where(
            Job.source_type == source_type,
            Job.source_id == source_id,
            Job.pipeline_major == pipeline_major,
            Job.status.in_(LIVE_STATUSES),
        )
        .limit(1)
    ).first()
    if live is not None:
        return live

    processed = s.scalars(
        select(ProcessedTrack)
        .where(
            ProcessedTrack.source_type == source_type,
            ProcessedTrack.source_id == source_id,
            ProcessedTrack.pipeline_major == pipeline_major,
        )
        .limit(1)
    ).first()
    if processed is not None and processed.job_id is not None:
        return s.get(Job, processed.job_id)

    latest = s.scalars(
        select(Job)
        .where(
            Job.source_type == source_type,
            Job.source_id == source_id,
            Job.pipeline_major == pipeline_major,
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()
    if (
        latest is not None
        and latest.status == "failed"
        and not is_transient_error(latest.error_type)
        and latest.finished_at is not None
        and latest.finished_at > _now() - timedelta(days=PERMANENT_FAIL_BLOCK_DAYS)
    ):
        # Permanent failure (e.g. lyrics_not_found) — block re-enqueue churn:
        # the client would otherwise re-enqueue the same track every listen.
        return latest
    return None


def enqueue(
    s: Session,
    *,
    source_type: str,
    source_id: str,
    pipeline_major: int,
    hints: dict[str, Any],
    options: dict[str, Any],
    requested_by: uuid.UUID | None,
) -> Job:
    existing = _find_reusable(s, source_type, source_id, pipeline_major)
    if existing is not None:
        return existing
    if queue_depth(s) >= settings.queue_depth_limit:
        raise QueueFull
    job = Job(
        source_type=source_type,
        source_id=source_id,
        pipeline_major=pipeline_major,
        hints=hints,
        options=options,
        requested_by=requested_by,
    )
    s.add(job)
    try:
        s.flush()
    except IntegrityError:
        # uq_jobs_active race: another request inserted first — return the winner.
        s.rollback()
        winner = _find_reusable(s, source_type, source_id, pipeline_major)
        if winner is None:  # pragma: no cover — winner just won the unique index
            raise
        return winner
    return job


# clock_timestamp(), not now(): now() is FROZEN at transaction start in PG, so a
# retry scheduled inside the same transaction would never look due (bit us in
# tests; also removes the whole frozen-clock surprise class from lease math).
_CLAIM_SQL = text(
    """
    UPDATE jobs SET status='downloading', attempts=attempts+1,
           started_at=COALESCE(started_at, clock_timestamp()),
           lease_expires_at=clock_timestamp() + make_interval(secs => :ttl),
           updated_at=clock_timestamp()
    WHERE id = (SELECT id FROM jobs
                WHERE status='queued' AND next_attempt_at <= clock_timestamp()
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
    RETURNING id
    """
)


def claim_next(s: Session) -> Job | None:
    row = s.execute(_CLAIM_SQL, {"ttl": settings.lease_ttl_s}).first()
    if row is None:
        return None
    job = s.get(Job, row.id)
    if job is not None:
        # The raw UPDATE bypassed the ORM — the identity map may hold a stale
        # copy of this row (e.g. still 'queued' when enqueued in this session).
        s.refresh(job)
    return job


def heartbeat(s: Session, job_id: uuid.UUID) -> None:
    s.execute(
        text(
            "UPDATE jobs SET lease_expires_at = clock_timestamp() + make_interval(secs => :ttl),"
            " updated_at = clock_timestamp() WHERE id = :id"
        ),
        {"ttl": settings.lease_ttl_s, "id": job_id},
    )


# State-transition helpers FLUSH their mutation (raw-SQL heartbeats already hit
# the DB immediately; mixing flushed and unflushed writes invites stale reads).
# COMMIT still belongs to the caller.


def set_status(s: Session, job: Job, status: str) -> None:
    job.status = status
    job.updated_at = _now()
    s.flush()
    heartbeat(s, job.id)


def mark_completed(s: Session, job: Job) -> None:
    job.status = "completed"
    job.finished_at = _now()
    job.updated_at = _now()
    s.flush()


def mark_failed(s: Session, job: Job, error_type: str, message: str) -> None:
    job.status = "failed"
    job.error_type = error_type
    job.error_message = message[:2000]
    job.finished_at = _now()
    job.updated_at = _now()
    s.flush()


def retry(s: Session, job: Job, delay_s: int) -> None:
    job.status = "queued"
    job.next_attempt_at = _now() + timedelta(seconds=delay_s)
    job.lease_expires_at = None
    job.updated_at = _now()
    s.flush()


def cancel(s: Session, job_id: uuid.UUID, requested_by: uuid.UUID | None, is_admin: bool) -> bool:
    """Cancel a QUEUED job (409 otherwise — running jobs only race-check via checkpoint)."""
    job = s.get(Job, job_id)
    if job is None or job.status != "queued":
        return False
    if not is_admin and requested_by is not None and job.requested_by != requested_by:
        return False
    job.status = "canceled"
    job.finished_at = _now()
    job.updated_at = _now()
    s.flush()
    return True


def reclaim_expired(s: Session) -> int:
    """Requeue (or fail at max_attempts) running jobs whose lease expired."""
    reclaimed = 0
    expired = s.scalars(
        select(Job)
        .where(Job.status.in_(RUNNING_STATUSES), Job.lease_expires_at < _now())
        .execution_options(populate_existing=True)  # bypass stale identity-map copies
    ).all()
    for job in expired:
        if job.attempts < job.max_attempts:
            retry(s, job, delay_s=0)
        else:
            mark_failed(s, job, "worker_lost", "lease expired at max attempts")
        reclaimed += 1
    return reclaimed
