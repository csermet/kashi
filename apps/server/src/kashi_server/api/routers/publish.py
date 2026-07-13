"""POST /v1/publish-requests — operator-approved lrclib contribute-back
(Faz 5 P6). Default-OFF twice over: this endpoint 409s until
lrclib_publish_enabled, and the worker still only DRY-RUNS (logs the YAML)
until lrclib_publish_dry_run is also flipped. Every request is a human
decision ("Report good sync" in the overlay tray) — nothing auto-publishes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from kashi_server.api.deps import get_db, rate_limited
from kashi_server.api.schemas import PublishRequestIn, PublishRequestOut
from kashi_server.config import settings
from kashi_server.db.models import ApiKey, LrclibPublish, ProcessedTrack
from kashi_server.pipeline.publish import publish_gate

router = APIRouter(prefix="/v1")


@router.post("/publish-requests", status_code=202, response_model=PublishRequestOut)
def request_publish(
    body: PublishRequestIn,
    key: ApiKey = Depends(rate_limited("ingest")),
    db: Session = Depends(get_db),
):
    if not settings.lrclib_publish_enabled:
        raise HTTPException(
            status_code=409, detail="lrclib publishing is disabled on this server"
        )
    row = db.scalars(
        select(ProcessedTrack).where(
            ProcessedTrack.source_type == body.source.type,
            ProcessedTrack.source_id == body.source.id,
            ProcessedTrack.schema_version == 1,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="no processed document for this source")
    reasons = publish_gate(row.document)
    if reasons:
        raise HTTPException(status_code=422, detail="not publishable: " + "; ".join(reasons))

    # (source, etag) is OUR dedup — the identical document is queued at most
    # once, ever; a reprocessed document (new etag) may be requested again.
    db.execute(
        pg_insert(LrclibPublish)
        .values(
            source_type=body.source.type,
            source_id=body.source.id,
            etag=row.etag,
            requested_by=key.id,
        )
        .on_conflict_do_nothing(constraint="uq_lrclib_publish_doc")
    )
    existing = db.scalars(
        select(LrclibPublish).where(
            LrclibPublish.source_type == body.source.type,
            LrclibPublish.source_id == body.source.id,
            LrclibPublish.etag == row.etag,
        )
    ).one()
    if existing.status == "failed":
        # An EXPLICIT human re-request revives a failed attempt (a network
        # blip must not dead-end a gate-clean document forever); published/
        # dry_run rows stay terminal — the dedup ledger's whole point.
        existing.status = "queued"
        existing.error = None
        existing.finished_at = None
    return PublishRequestOut(id=existing.id, status=existing.status)
