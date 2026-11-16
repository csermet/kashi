"""Evidence back-fill (refresh without download, pipeline/evidence.py): give archive songs their
evidence WITHOUT queued jobs and without touching their stored documents.

Run by hand where fetching and separating is cheap (the GPU helper, from an image built at the
tag the cluster runs, so that its records carry the key the cluster's replay expects):

    python -m kashi_server.worker.backfill --limit 40 [--after SOURCE_ID]
    python -m kashi_server.worker.backfill --source-id ID [ID ...]

Each song runs the worker's own job code (`process._record` with `persist=False`) on an
in-memory job built as a reprocess builds one (the latest job's hints, the song's override merged
as at enqueue). Nothing enters the `jobs` table, so no worker can claim the work — neither one
that cannot record nor an older image that would run it as an ordinary reprocess and replace the
stored document — and no document is stored or replaced.

Songs are taken in source-id order after `--after` (wrapping round), so a song that fails or is
refused every time cannot hold the batch: the last line is a JSON summary whose `next_after` is
the cursor for the next run. Songs that can never be recorded (a nightcore clock) are not taken.

The download, separation and alignment are real. The audio lives in `<data_dir>/job-backfill-*`
for one song and is deleted when it ends, also on SIGTERM (turned into an exception, so the
cleanup runs); a run killed outright (SIGKILL, an out-of-memory kill) leaves it to the next run's
start-up sweep and to the worker's own sweep of `job-*`. One run at a time (a lock file).

Exit codes: 0 the batch ran (something stored, or nothing to do); 1 songs were tried and none was
stored; 2 the database schema is behind this code; 3 another back-fill holds the lock; 143 stopped
by SIGTERM.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import shutil
import signal
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kashi_server import overrides
from kashi_server.config import settings
from kashi_server.db.models import Job, ProcessedTrack, TrackEvidence
from kashi_server.pipeline import evidence
from kashi_server.vdl_kit.errors import PipelineError

logger = logging.getLogger(__name__)

#: Scratch directories of the back-fill: matched by the worker's own orphan sweep (`job-*`).
DIR_PREFIX = "job-backfill-"
LOCK_NAME = "backfill.lock"

EXIT_OK, EXIT_NONE_STORED, EXIT_SCHEMA, EXIT_LOCKED, EXIT_TERMINATED = 0, 1, 2, 3, 143


class Terminated(BaseException):  # noqa: N818 - like KeyboardInterrupt: not caught by `except Exception`
    """SIGTERM during a back-fill: unwinds the song (its `finally` deletes the audio)."""


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
    """An in-memory job with every field a row would get from its server defaults (the deferral
    path reads `deferrals`)."""
    from kashi_server.version import PIPELINE_MAJOR

    return Job(
        id=uuid.uuid4(), source_type=source_type, source_id=source_id,
        pipeline_major=PIPELINE_MAJOR, hints=hints,
        options=overrides.apply(s, source_type, source_id, {}),
        attempts=1, max_attempts=1, status="downloading", deferrals=0, deferred_since=None,
        error_type=None, error_message=None,
    )


def _never_recorded(document: dict | None, options: dict) -> str | None:
    """Why the song can never be recorded, or None (the worker refuses a nightcore clock)."""
    speed = ((document or {}).get("alignment") or {}).get("speed_factor")
    if options.get("speed_factor") is not None or (
        isinstance(speed, int | float) and not isinstance(speed, bool) and speed != 1
    ):
        return "a nightcore clock"
    return None


def candidates(s: Session, limit: int, after: str | None = None) -> list[str]:
    """Up to `limit` YouTube songs with a stored document whose evidence is missing or keyed
    differently from what this worker records, in source-id order after `after`, wrapping round
    to the start and `after` itself last; songs that can never be recorded are skipped."""
    from kashi_server.worker.process import _evidence_key

    rows = s.execute(
        select(ProcessedTrack.source_id, ProcessedTrack.document, TrackEvidence.key)
        .outerjoin(
            TrackEvidence,
            (TrackEvidence.source_type == ProcessedTrack.source_type)
            & (TrackEvidence.source_id == ProcessedTrack.source_id),
        )
        .where(ProcessedTrack.source_type == "youtube")
        .order_by(ProcessedTrack.source_id)
    ).all()
    if after is not None:
        rows = [r for r in rows if r.source_id > after] + [r for r in rows if r.source_id <= after]
    out: list[str] = []
    for source_id, document, key in rows:
        hints = _latest_hints(s, "youtube", source_id) or {}
        job = _job_for(s, "youtube", source_id, hints)
        if _never_recorded(document, job.options or {}) is not None:
            continue
        if key is not None and evidence.key_difference(key, _evidence_key(job)) is None:
            continue
        out.append(source_id)
        if len(out) >= limit:
            break
    return out


def sweep(data_dir: Path) -> int:
    """Delete every back-fill scratch directory (a run killed outright left it): called at start,
    under the lock, so no other back-fill is using one."""
    removed = 0
    for path in data_dir.glob(f"{DIR_PREFIX}*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    if removed:
        logger.warning("swept %d back-fill scratch dir(s) from %s", removed, data_dir)
    return removed


def backfill_song(s: Session, source_id: str, source_type: str = "youtube") -> str:
    """One song: 'stored', or why not."""
    from kashi_server.worker.process import _record

    if source_type != "youtube":
        return "skipped: evidence is recorded for YouTube songs only"
    hints = _latest_hints(s, source_type, source_id)
    if hints is None:
        return "skipped: no job history (no hints)"
    job = _job_for(s, source_type, source_id, hints)
    document = s.scalars(
        select(ProcessedTrack.document).where(
            ProcessedTrack.source_type == source_type, ProcessedTrack.source_id == source_id
        )
    ).first()
    never = _never_recorded(document, job.options or {})
    if never is not None:
        return f"skipped: never recorded ({never})"
    tmp = settings.data_dir / f"{DIR_PREFIX}{job.id}"
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


@contextlib.contextmanager
def _lock(data_dir: Path) -> Iterator[bool]:
    """True while this process holds the back-fill lock, False when another one does."""
    import fcntl

    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / LOCK_NAME, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def _sigterm_raises() -> Iterator[None]:
    def handler(signum: int, _frame: Any) -> None:
        raise Terminated(signum)

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence back-fill without queued jobs.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--after", default=None, help="the previous run's next_after")
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
        return EXIT_SCHEMA
    results: dict[str, str] = {}
    code = EXIT_OK
    with _lock(settings.data_dir) as held:
        if not held:
            print("back-fill refused: another back-fill is running", file=sys.stderr)
            return EXIT_LOCKED
        sweep(settings.data_dir)
        try:
            with _sigterm_raises(), SessionLocal() as s:
                ids = args.source_id or candidates(s, args.limit, args.after)
                for source_id in ids:
                    results[source_id] = backfill_song(s, source_id)
                    print(f"{source_id}: {results[source_id]}", flush=True)
        except Terminated:
            code = EXIT_TERMINATED
    stored = sum(1 for r in results.values() if r == "stored")
    if code == EXIT_OK and results and not stored:
        code = EXIT_NONE_STORED
    summary = {
        "tried": len(results), "stored": stored,
        "not_stored": sum(1 for r in results.values() if r.startswith(("not stored", "skipped"))),
        "failed": sum(1 for r in results.values() if r.startswith("failed")),
        "next_after": list(results)[-1] if results else args.after,
        "terminated": code == EXIT_TERMINATED,
    }
    print(f"back-fill summary {json.dumps(summary)}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
