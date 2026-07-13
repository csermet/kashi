"""Audio acquisition dispatch (Faz 5 P4): one seam per source type.

`youtube` keeps the yt-dlp path (injected by the caller so the worker's
existing test seams stay put); `upload` fetches the staged bytes the API pod
stored in Postgres — no shared volume, no object store, no new netpol.
Deliberately a plain dispatch function, not an ABC: two variants do not
justify a hierarchy (a third would).
"""

import logging
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from kashi_server.config import settings
from kashi_server.db.models import Job, UploadedAudio
from kashi_server.pipeline.download import DownloadResult
from kashi_server.vdl_kit.errors import PipelineError

logger = logging.getLogger(__name__)


def fetch_audio(
    job: Job,
    dest_dir: Path,
    s: Session,
    *,
    youtube_fetch: Callable[..., DownloadResult],
) -> DownloadResult:
    """The worker's single audio entry point. `youtube_fetch` is injected by
    the caller (worker/process.py passes its module-level `download_audio`)
    so the long-standing monkeypatch seam survives the dispatch."""
    if job.source_type == "youtube":
        return youtube_fetch(job.source_id, dest_dir, max_duration_s=settings.max_track_duration_s)
    if job.source_type == "upload":
        return fetch_uploaded(s, job.source_id, dest_dir)
    raise PipelineError(  # 'plex' is reserved in the schema but unbuilt (backlog)
        "other", f"source type {job.source_type!r} has no audio fetcher"
    )


def fetch_uploaded(s: Session, source_id: str, dest_dir: Path) -> DownloadResult:
    """Staged upload → scratch file. A missing row is PERMANENT: uploads are
    deleted when their job goes terminal (deletion guarantee) or after the
    TTL — an honest error beats a retry loop against a row that cannot
    reappear. Re-upload + re-ingest is the documented recovery."""
    row = s.get(UploadedAudio, source_id)
    if row is None:
        raise PipelineError(
            "other",
            "uploaded audio is no longer staged (consumed or expired) — "
            "upload the file again, then re-ingest",
        )
    path = dest_dir / "upload-audio"
    path.write_bytes(row.content)
    logger.info("staged upload %s: %d bytes, %.1fs", source_id, row.size_bytes, row.duration_s)
    return DownloadResult(
        path=path,
        abr=0.0,  # unknown — the client brought the bytes
        acodec=row.mime or "unknown",
        duration_s=row.duration_s,
        info={"source": "upload", "size_bytes": row.size_bytes},
    )
