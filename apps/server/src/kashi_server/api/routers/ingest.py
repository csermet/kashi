"""POST /v1/ingest — idempotent enqueue (contract: always 202 with the job that
represents this source, whether fresh, already running, or already done)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from kashi_server import queue
from kashi_server.api.deps import get_db, queue_full_response, rate_limited
from kashi_server.api.schemas import IngestRequest, IngestResponse
from kashi_server.db.models import ApiKey
from kashi_server.version import PIPELINE_MAJOR

router = APIRouter(prefix="/v1")


@router.post("/ingest", status_code=202, response_model=IngestResponse)
def ingest(
    body: IngestRequest,
    key: ApiKey = Depends(rate_limited("ingest")),
    db: Session = Depends(get_db),
):
    try:
        job = queue.enqueue(
            db,
            source_type=body.source.type,
            source_id=body.source.id,
            pipeline_major=PIPELINE_MAJOR,
            hints=body.hints.model_dump(exclude_none=True),
            options=body.options.model_dump(),
            requested_by=key.id,
        )
    except queue.QueueFull:
        return queue_full_response()
    return IngestResponse(job_id=job.id, status=job.status)
