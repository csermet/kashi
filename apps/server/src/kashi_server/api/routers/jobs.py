"""Job status endpoints. Non-admin callers only see their own jobs; a foreign
job id answers 404 (existence is not leaked)."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from kashi_server import queue
from kashi_server.api.deps import get_db, require_key
from kashi_server.api.schemas import JobOut
from kashi_server.db.models import ALL_STATUSES, ApiKey, Job

router = APIRouter(prefix="/v1")


def _to_out(job: Job) -> JobOut:
    result_url = (
        f"/v1/lyrics/{quote(job.source_type, safe='')}/{quote(job.source_id, safe='')}"
        if job.status == "completed"
        else None
    )
    return JobOut(
        id=job.id,
        status=job.status,
        progress_stage=job.status,
        error_type=job.error_type,
        error_message=job.error_message,
        created_at=job.created_at,
        finished_at=job.finished_at,
        result_url=result_url,
    )


def _readable(job: Job | None) -> Job:
    """Status of any job is readable by any authenticated key.

    Ingest is idempotent: a second key asking for the same track gets back the
    FIRST key's job id, so owner-scoped reads would 404 exactly the id we just
    handed out — and a client that cannot poll re-enqueues forever (review
    finding). Job rows carry no secrets, only the track hints the caller sent.
    """
    if job is None:
        raise HTTPException(status_code=404, detail="not_found")
    return job


def _owned(job: Job | None, key: ApiKey) -> Job:
    """Mutations (cancel) stay scoped to the requester (or an admin)."""
    if job is None or (key.role != "admin" and job.requested_by != key.id):
        raise HTTPException(status_code=404, detail="not_found")
    return job


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID,
    key: ApiKey = Depends(require_key("user")),
    db: Session = Depends(get_db),
):
    return _to_out(_readable(db.get(Job, job_id)))


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    status: str | None = None,
    limit: int = 20,
    key: ApiKey = Depends(require_key("user")),
    db: Session = Depends(get_db),
):
    if status is not None and status not in ALL_STATUSES:
        raise HTTPException(status_code=400, detail="unknown_status")
    stmt = select(Job).order_by(Job.created_at.desc()).limit(max(1, min(limit, 100)))
    if key.role != "admin":
        stmt = stmt.where(Job.requested_by == key.id)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    return [_to_out(j) for j in db.scalars(stmt).all()]


@router.delete("/jobs/{job_id}", status_code=204)
def cancel_job(
    job_id: uuid.UUID,
    key: ApiKey = Depends(require_key("user")),
    db: Session = Depends(get_db),
) -> None:
    job = _owned(db.get(Job, job_id), key)
    if job.status != "queued":
        # Running jobs are only race-checked at worker checkpoints; terminal
        # jobs have nothing to cancel.
        raise HTTPException(status_code=409, detail="not_cancelable")
    queue.cancel(db, job.id, requested_by=key.id, is_admin=key.role == "admin")
