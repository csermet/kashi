"""POST /v1/admin/reprocess — force a fresh pipeline run for one source.

Exists because ingest is deliberately idempotent: once a document is stored,
enqueue always returns the old completed job (queue._find_reusable), so a
pipeline improvement (e.g. line QA) can never reach an already-processed
track through the public path. Completion overwrites the stored document in
place, so clients keep getting lyrics throughout.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from kashi_server import queue
from kashi_server.api.deps import get_db, queue_full_response, require_key
from kashi_server.api.schemas import IngestResponse, ReprocessRequest
from kashi_server.db.models import ApiKey, Job
from kashi_server.version import PIPELINE_MAJOR

router = APIRouter(prefix="/v1/admin")


@router.post("/reprocess", status_code=202, response_model=IngestResponse)
def reprocess(
    body: ReprocessRequest,
    key: ApiKey = Depends(require_key("admin")),
    db: Session = Depends(get_db),
):
    if body.hints is not None:
        hints = body.hints.model_dump(exclude_none=True)
    else:
        latest = db.scalars(
            select(Job)
            .where(Job.source_type == body.source.type, Job.source_id == body.source.id)
            .order_by(Job.created_at.desc())
            .limit(1)
        ).first()
        if latest is None:
            raise HTTPException(
                status_code=404, detail="source has no job history; pass hints explicitly"
            )
        hints = latest.hints or {}
    try:
        job = queue.enqueue_reprocess(
            db,
            source_type=body.source.type,
            source_id=body.source.id,
            pipeline_major=PIPELINE_MAJOR,
            hints=hints,
            options={},
            requested_by=key.id,
        )
    except queue.QueueFull:
        return queue_full_response()
    return IngestResponse(job_id=job.id, status=job.status)
