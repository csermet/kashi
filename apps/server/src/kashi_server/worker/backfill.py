"""Evidence back-fill (refresh without download, pipeline/evidence.py): give archive songs their
evidence WITHOUT queued jobs and without touching their stored documents.

Run by hand where fetching and separating is cheap (the GPU helper's image and env file):

    python -m kashi_server.worker.backfill --limit 40              # songs lacking evidence
    python -m kashi_server.worker.backfill --source-id ID [ID ...]  # these songs

Each song runs the worker's own job code (`process._record` with `persist=False`) on an
in-memory job built as a reprocess builds one (the latest job's hints, the song's override merged
as at enqueue). Nothing enters the `jobs` table, so no worker can claim the work — neither one
that cannot record nor an older image that would run it as an ordinary reprocess and replace the
stored document (review finding 1, 2026-11-16) — and no document is stored or replaced. The
download, separation and alignment are real; the audio is deleted afterwards, as in a job. A song
that fails is reported and skipped; running again picks it up.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kashi_server import overrides
from kashi_server.config import settings
from kashi_server.db.models import Job, ProcessedTrack, TrackEvidence
from kashi_server.pipeline import evidence
from kashi_server.vdl_kit.errors import PipelineError

logger = logging.getLogger(__name__)


class _JoblessSession:
    """The real session for everything the job writes (the evidence row; queue SQL aimed at a
    job id no row has touches nothing), with the ORM refresh of the in-memory job made a no-op:
    the job is not a row."""

    def __init__(self, real: Session) -> None:
        self._real = real

    def expire(self, *_a: Any, **_k: Any) -> None: ...

    def refresh(self, *_a: Any, **_k: Any) -> None: ...

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _latest_hints(s: Session, source_type: str, source_id: str) -> dict | None:
    job = s.scalars(
        select(Job)
        .where(Job.source_type == source_type, Job.source_id == source_id)
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()
    return None if job is None else dict(job.hints or {})


def _job_for(s: Session, source_type: str, source_id: str, hints: dict) -> Job:
    from kashi_server.version import PIPELINE_MAJOR

    return Job(
        id=uuid.uuid4(), source_type=source_type, source_id=source_id,
        pipeline_major=PIPELINE_MAJOR, hints=hints,
        options=overrides.apply(s, source_type, source_id, {}),
        attempts=1, max_attempts=1, status="downloading",
    )


def candidates(s: Session, limit: int) -> list[str]:
    """YouTube songs with a stored document whose evidence is missing or keyed differently from
    what this worker records, the longest-unrefreshed documents first."""
    from kashi_server.worker.process import _evidence_key

    rows = s.execute(
        select(ProcessedTrack.source_id, TrackEvidence.key)
        .outerjoin(
            TrackEvidence,
            (TrackEvidence.source_type == ProcessedTrack.source_type)
            & (TrackEvidence.source_id == ProcessedTrack.source_id),
        )
        .where(ProcessedTrack.source_type == "youtube")
        .order_by(ProcessedTrack.updated_at)
    ).all()
    out: list[str] = []
    for source_id, key in rows:
        if key is not None:
            hints = _latest_hints(s, "youtube", source_id) or {}
            expected = _evidence_key(_job_for(s, "youtube", source_id, hints))
            if evidence.key_difference(key, expected) is None:
                continue
        out.append(source_id)
        if len(out) >= limit:
            break
    return out


def backfill_song(s: Session, source_id: str, source_type: str = "youtube") -> str:
    """One song: 'stored', or why not."""
    from kashi_server.worker.process import _record

    if source_type != "youtube":
        return "skipped: evidence is recorded for YouTube songs only"
    hints = _latest_hints(s, source_type, source_id)
    if hints is None:
        return "skipped: no job history (no hints)"
    job = _job_for(s, source_type, source_id, hints)
    tmp = settings.data_dir / f"backfill-{job.id}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        stored = _record(_JoblessSession(s), job, tmp, persist=False)  # type: ignore[arg-type]
    except PipelineError as exc:
        s.rollback()
        return f"failed: {exc.error_type}: {exc.message[:200]}"
    except Exception as exc:  # noqa: BLE001 - one song's fault must not stop the run
        s.rollback()
        logger.warning("back-fill %s crashed", source_id, exc_info=True)
        return f"failed: {type(exc).__name__}: {str(exc)[:200]}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # the audio deletion guarantee, as in a job
    if job.status != "completed":
        return f"not stored: the job ended {job.status} ({job.error_type or 'no error'})"
    return "stored" if stored else "not stored (see the log line above)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence back-fill without queued jobs.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--source-id", nargs="*", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from kashi_server.db.engine import SessionLocal, engine
    from kashi_server.db.schema import schema_gap

    with engine.connect() as conn:
        gap = schema_gap(conn)
    if gap is not None:
        print(f"back-fill refused: database schema behind this code: {gap}", file=sys.stderr)
        return 2
    with SessionLocal() as s:
        ids = args.source_id if args.source_id else candidates(s, args.limit)
        results = {}
        for source_id in ids:
            results[source_id] = backfill_song(s, source_id)
            print(f"{source_id}: {results[source_id]}", flush=True)
    stored = sum(1 for r in results.values() if r == "stored")
    print(f"back-fill: {stored} of {len(results)} stored", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
