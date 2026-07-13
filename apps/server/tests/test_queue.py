"""Queue contract tests (plan A1). All cases run against real Postgres —
SKIP LOCKED and partial unique indexes cannot be faked on SQLite."""

import os
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (needs Postgres)"
)


def _enqueue(s, **overrides):
    from kashi_server import queue

    kwargs = {
        "source_type": "youtube",
        "source_id": "vid00000001",
        "pipeline_major": 1,
        "hints": {"title": "T", "artist": "A"},
        "options": {},
        "requested_by": None,
    }
    kwargs.update(overrides)
    job, _reused = queue.enqueue(s, **kwargs)
    return job


def test_enqueue_is_idempotent_for_active_job(db_session):
    a = _enqueue(db_session)
    b = _enqueue(db_session)
    assert a.id == b.id
    db_session.commit()


def test_enqueue_returns_job_of_processed_track(db_session):
    from kashi_server import queue
    from kashi_server.db.models import ProcessedTrack

    job = _enqueue(db_session)
    queue.mark_completed(db_session, job)
    db_session.add(
        ProcessedTrack(
            source_type="youtube",
            source_id=job.source_id,
            pipeline_version="1.0.0",
            pipeline_major=1,
            sync="word",
            quality_score=0.9,
            document={"schema_version": 1},
            etag="e" * 32,
            job_id=job.id,
        )
    )
    db_session.flush()
    again = _enqueue(db_session)
    assert again.id == job.id and again.status == "completed"


def test_permanent_failure_blocks_reenqueue_for_7_days(db_session):
    from kashi_server import queue

    job = _enqueue(db_session)
    queue.claim_next(db_session)
    db_session.refresh(job)
    queue.mark_failed(db_session, job, "lyrics_not_found", "no lyrics")
    db_session.flush()

    blocked = _enqueue(db_session)
    assert blocked.id == job.id  # returns the failed job, no new row

    job.finished_at = datetime.now(UTC) - timedelta(days=8)
    db_session.flush()
    fresh = _enqueue(db_session)
    assert fresh.id != job.id  # block expired -> new job


def test_transient_exhausted_failure_allows_new_job(db_session):
    from kashi_server import queue

    job = _enqueue(db_session)
    queue.claim_next(db_session)
    db_session.refresh(job)
    queue.mark_failed(db_session, job, "network", "flaky")
    db_session.flush()
    fresh = _enqueue(db_session)
    assert fresh.id != job.id


def test_queue_depth_limit(db_session, monkeypatch):
    from kashi_server import queue
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "queue_depth_limit", 1)
    _enqueue(db_session)
    with pytest.raises(queue.QueueFull):
        _enqueue(db_session, source_id="vid00000002")


def test_claim_marks_downloading_and_increments_attempts(db_session):
    from kashi_server import queue

    job = _enqueue(db_session)
    claimed = queue.claim_next(db_session)
    assert claimed is not None and claimed.id == job.id
    assert claimed.status == "downloading"
    assert claimed.attempts == 1
    assert claimed.lease_expires_at is not None
    assert queue.claim_next(db_session) is None  # nothing else queued


def test_claim_skip_locked_under_concurrency(db_session):
    """Two uncommitted sessions must claim DIFFERENT jobs (SKIP LOCKED)."""
    from kashi_server import queue
    from kashi_server.db.engine import SessionLocal

    _enqueue(db_session, source_id="vidA0000001")
    _enqueue(db_session, source_id="vidB0000001")
    db_session.commit()

    s1, s2 = SessionLocal(), SessionLocal()
    try:
        j1 = queue.claim_next(s1)  # transaction stays open
        j2 = queue.claim_next(s2)
        assert j1 is not None and j2 is not None
        assert j1.id != j2.id
    finally:
        s1.rollback()
        s2.rollback()
        s1.close()
        s2.close()


def test_retry_delays_next_claim(db_session):
    from kashi_server import queue

    _enqueue(db_session)
    job = queue.claim_next(db_session)
    queue.retry(db_session, job, delay_s=3600)
    db_session.flush()
    assert queue.claim_next(db_session) is None  # not due yet

    job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()
    again = queue.claim_next(db_session)
    assert again is not None and again.attempts == 2


def test_reclaim_expired_requeues_then_fails_at_max(db_session):
    from kashi_server import queue

    _enqueue(db_session)
    job = queue.claim_next(db_session)
    job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()
    assert queue.reclaim_expired(db_session) == 1
    db_session.refresh(job)
    assert job.status == "queued"

    job.attempts = job.max_attempts
    db_session.flush()  # raw-SQL claim reads the DB — pending ORM state must land first
    queue.claim_next(db_session)  # attempts -> max+1
    job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()
    queue.reclaim_expired(db_session)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "worker_lost"


def test_cancel_only_queued(db_session):
    from kashi_server import queue

    job = _enqueue(db_session)
    assert queue.cancel(db_session, job.id, None, is_admin=True) is True
    db_session.refresh(job)
    assert job.status == "canceled"

    job2 = _enqueue(db_session, source_id="vid00000003")
    queue.claim_next(db_session)
    db_session.refresh(job2)
    assert queue.cancel(db_session, job2.id, None, is_admin=True) is False
