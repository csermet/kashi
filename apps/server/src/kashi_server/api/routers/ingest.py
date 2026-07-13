"""POST /v1/ingest — idempotent enqueue (contract: 202 with the job that
represents this source — fresh, already running, or already done — except a
422 up-front rejection for tracks the pipeline could never complete)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from kashi_server import queue
from kashi_server.api.deps import get_db, queue_full_response, rate_limited
from kashi_server.api.schemas import IngestRequest, IngestResponse
from kashi_server.config import settings
from kashi_server.db.models import ApiKey
from kashi_server.version import PIPELINE_MAJOR

router = APIRouter(prefix="/v1")


@router.post("/ingest", status_code=202, response_model=IngestResponse)
def ingest(
    body: IngestRequest,
    key: ApiKey = Depends(rate_limited("ingest")),
    db: Session = Depends(get_db),
):
    # A track over the pipeline cap can never complete — the download stage
    # enforces the same limit, but only after a job existed and lrclib was
    # queried with an implausible duration (field: a 61-minute mix earned a
    # 400 and burned its retries). Reject before a job exists; the admin
    # reprocess route deliberately bypasses this (operator-repaired hints).
    duration_ms = body.hints.duration_ms
    if duration_ms and duration_ms > settings.max_track_duration_s * 1000:
        raise HTTPException(
            status_code=422,
            detail=(
                f"track duration {duration_ms}ms exceeds the "
                f"{settings.max_track_duration_s}s processing cap"
            ),
        )
    try:
        job = queue.enqueue(
            db,
            source_type=body.source.type,
            source_id=body.source.id,
            pipeline_major=PIPELINE_MAJOR,
            hints=body.hints.model_dump(exclude_none=True),
            # exclude_none: absent nightcore options must not persist as nulls
            # in the job row (the worker checks key presence).
            options=body.options.model_dump(exclude_none=True),
            requested_by=key.id,
        )
    except queue.QueueFull:
        return queue_full_response()
    return IngestResponse(job_id=job.id, status=job.status)
