"""/v1/admin/overrides — per-song corrections that survive reprocess (C.1).

PUT creates or replaces the whole override of one song, GET reads it, DELETE
removes it, GET on the collection lists them (without the lyrics text). The
row takes effect for jobs ENQUEUED after it is written (`queue` merges it into
their options); a job already queued keeps the options it was created with.
To rebuild a song under its override now: PUT, then `POST /v1/admin/reprocess`
with no options. After a DELETE the stored document stays as it is until the
next reprocess (or force_persist) replaces it under the ordinary rules.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kashi_server import overrides
from kashi_server.api.deps import get_db, require_key
from kashi_server.api.schemas import (
    SongOverrideIn,
    SongOverrideOut,
    SongOverrideSummary,
    SourceRef,
)
from kashi_server.db.models import ProcessedTrack, SongOverride
from kashi_server.pipeline.lrclib import is_lrc

router = APIRouter(prefix="/v1/admin", dependencies=[Depends(require_key("admin"))])

SourceType = Literal["youtube", "plex", "upload"]
# Same alphabet and bounds as SourceRef.id (path-safe by construction).
SourceId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "No override for this source"}}


def _stored_etag(db: Session, source_type: str, source_id: str) -> str | None:
    return db.scalars(
        select(ProcessedTrack.etag).where(
            ProcessedTrack.source_type == source_type,
            ProcessedTrack.source_id == source_id,
            ProcessedTrack.schema_version == 1,
        )
    ).first()


def _under(row: SongOverride, stored_etag: str | None) -> bool:
    return row.applied_etag is not None and row.applied_etag == stored_etag


def _out(db: Session, row: SongOverride) -> SongOverrideOut:
    return SongOverrideOut(
        source=SourceRef.model_validate({"type": row.source_type, "id": row.source_id}),
        lrclib_id=row.lrclib_id,
        lyrics_text=row.lyrics_text,
        speed_factor=row.speed_factor,
        offset_ms=row.offset_ms,
        plain_only=row.plain_only,
        note=row.note,
        document_under_override=_under(row, _stored_etag(db, row.source_type, row.source_id)),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.put(
    "/overrides/{source_type}/{source_id}",
    response_model=SongOverrideOut,
    responses={
        201: {"description": "Created"},
        409: {"description": "A concurrent write created it first - retry"},
    },
)
def put_override(
    source_type: SourceType,
    source_id: SourceId,
    body: SongOverrideIn,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> SongOverrideOut:
    # Locked: a worker persisting under this override holds the same row lock
    # (pipeline/document.py), so its bookkeeping and this change serialise.
    row = overrides.find(db, source_type, source_id, for_update=True)
    created = row is None
    if row is None:
        row = SongOverride(source_type=source_type, source_id=source_id)
        db.add(row)
    before = None if created else overrides.acted_fields(row)
    row.lrclib_id = body.lrclib_id
    row.lyrics_text = body.lyrics_text
    row.speed_factor = body.speed_factor
    row.offset_ms = body.offset_ms
    row.plain_only = body.plain_only
    row.note = body.note
    if before != overrides.acted_fields(row):
        # B2: the stored document was built under content that is gone.
        # A note or plain_only edit changes nothing a run does: kept.
        row.applied_etag = None
    if not created:
        row.updated_at = func.clock_timestamp()
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="concurrent_write") from None
    db.refresh(row)
    response.status_code = 201 if created else 200
    return _out(db, row)


@router.get(
    "/overrides/{source_type}/{source_id}", response_model=SongOverrideOut, responses=_NOT_FOUND
)
def get_override(
    source_type: SourceType,
    source_id: SourceId,
    db: Annotated[Session, Depends(get_db)],
) -> SongOverrideOut:
    row = overrides.find(db, source_type, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return _out(db, row)


@router.delete("/overrides/{source_type}/{source_id}", status_code=204, responses=_NOT_FOUND)
def delete_override(
    source_type: SourceType,
    source_id: SourceId,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    row = overrides.find(db, source_type, source_id, for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    db.delete(row)


@router.get("/overrides", response_model=list[SongOverrideSummary])
def list_overrides(db: Annotated[Session, Depends(get_db)]) -> list[SongOverrideSummary]:
    rows = db.execute(
        select(SongOverride, ProcessedTrack.etag)
        .outerjoin(
            ProcessedTrack,
            and_(
                ProcessedTrack.source_type == SongOverride.source_type,
                ProcessedTrack.source_id == SongOverride.source_id,
                ProcessedTrack.schema_version == 1,
            ),
        )
        .order_by(SongOverride.updated_at.desc(), SongOverride.source_id)
    ).all()
    return [
        SongOverrideSummary(
            source=SourceRef.model_validate({"type": row.source_type, "id": row.source_id}),
            lrclib_id=row.lrclib_id,
            lyrics_text_chars=len(row.lyrics_text) if row.lyrics_text is not None else None,
            lyrics_text_lrc=row.lyrics_text is not None and is_lrc(row.lyrics_text),
            speed_factor=row.speed_factor,
            offset_ms=row.offset_ms,
            plain_only=row.plain_only,
            note=row.note,
            document_under_override=_under(row, etag),
            updated_at=row.updated_at,
        )
        for row, etag in rows
    ]
