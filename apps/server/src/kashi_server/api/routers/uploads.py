"""POST /v1/uploads — bring-your-own-audio staging (Faz 5 P4).

The API pod streams the multipart body to /tmp (emptyDir; the rootfs is
read-only), ffprobes it there (type + duration validation — extensions and
Content-Type lie, streams do not), then stages the bytes in Postgres for the
worker pod. The response hands back a ready-made `source` ref: the client
follows up with a normal POST /v1/ingest.
"""

import asyncio
import base64
import hashlib
import json
import logging
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from kashi_server.api.deps import get_db, rate_limited
from kashi_server.api.schemas import SourceRef, UploadResponse
from kashi_server.config import settings
from kashi_server.db.models import ApiKey, UploadedAudio

router = APIRouter(prefix="/v1")
logger = logging.getLogger(__name__)

_CHUNK_BYTES = 1024 * 1024


def _ffprobe_duration_s(path: Path) -> float | None:
    """Duration of the file's audio, or None when it has no audio stream
    (the only trustworthy type check — never the filename or Content-Type)."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    try:
        probe = json.loads(out.stdout.decode() or "{}")
    except ValueError:
        return None
    if not any(s.get("codec_type") == "audio" for s in probe.get("streams") or []):
        return None
    raw_duration = (probe.get("format") or {}).get("duration")
    try:
        duration = float(raw_duration) if raw_duration is not None else 0.0
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


@router.post("/uploads", status_code=201, response_model=UploadResponse)
async def upload_audio(
    file: UploadFile,
    key: ApiKey = Depends(rate_limited("uploads")),
    db: Session = Depends(get_db),
):
    hasher = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(suffix="-kashi-upload") as spool:
        while chunk := await file.read(_CHUNK_BYTES):
            size += len(chunk)
            if size > settings.upload_max_bytes:
                # Belt to the middleware's Content-Length suspenders (chunked
                # encodings carry no declared length).
                raise HTTPException(
                    status_code=413,
                    detail=f"upload exceeds the {settings.upload_max_bytes}-byte cap",
                )
            hasher.update(chunk)
            spool.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="empty upload")
        spool.flush()
        # Thread offload: a 30s ffprobe or a 64MB read must not pin the
        # event loop (health probes share it — liveness-kill risk).
        duration_s = await asyncio.to_thread(_ffprobe_duration_s, Path(spool.name))
        if duration_s is None:
            raise HTTPException(
                status_code=422, detail="not decodable audio (no audio stream found)"
            )
        if duration_s > settings.max_track_duration_s:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"audio is {duration_s:.0f}s — over the "
                    f"{settings.max_track_duration_s}s processing cap"
                ),
            )
        spool.seek(0)
        content = await asyncio.to_thread(spool.read)

    # urlsafe sha256, no padding (43 chars): fits SourceRef's alphabet and
    # dedupes identical re-uploads into one row (expiry refreshed below).
    upload_id = base64.urlsafe_b64encode(hasher.digest()).rstrip(b"=").decode()
    expires_at = datetime.now(UTC) + timedelta(hours=settings.upload_ttl_hours)
    db.execute(
        pg_insert(UploadedAudio)
        .values(
            id=upload_id,
            content=content,
            size_bytes=size,
            mime=file.content_type,
            duration_s=duration_s,
            uploaded_by=key.id,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"expires_at": expires_at, "uploaded_by": key.id},
        )
    )
    logger.info("staged upload %s (%d bytes, %.1fs) for %s", upload_id, size, duration_s, key.name)
    return UploadResponse(
        source=SourceRef(type="upload", id=upload_id),
        duration_ms=round(duration_s * 1000),
        expires_at=expires_at,
    )
