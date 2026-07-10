"""GET /v1/lyrics/{source_type}/{source_id} — the processed document, with
ETag/If-None-Match so the overlay's repeat fetches cost a 304."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from kashi_server.api.deps import get_db, rate_limited
from kashi_server.db.models import ProcessedTrack

router = APIRouter(prefix="/v1", dependencies=[Depends(rate_limited("lyrics_get"))])

_CACHE_CONTROL = "private, max-age=0, must-revalidate"


def _etag_matches(request: Request, etag: str) -> bool:
    raw = request.headers.get("if-none-match", "").strip()
    if not raw:
        return False
    if raw == "*":
        return True  # RFC 7232 §3.2: matches any existing representation
    # Strip weak prefixes and quotes; tolerate a comma-separated list.
    tags = {part.strip().removeprefix("W/").strip('"') for part in raw.split(",") if part.strip()}
    return etag in tags


@router.get("/lyrics/{source_type}/{source_id}")
def get_lyrics(
    source_type: str,
    source_id: str,
    request: Request,
    schema_version: int = 1,
    db: Session = Depends(get_db),
) -> Response:
    if schema_version != 1:
        raise HTTPException(status_code=400, detail="unsupported_schema_version")
    row = db.scalars(
        select(ProcessedTrack).where(
            ProcessedTrack.source_type == source_type,
            ProcessedTrack.source_id == source_id,
            ProcessedTrack.schema_version == schema_version,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    headers = {"ETag": f'"{row.etag}"', "Cache-Control": _CACHE_CONTROL}
    if _etag_matches(request, row.etag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=row.document, headers=headers)
