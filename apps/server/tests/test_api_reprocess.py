"""POST /v1/admin/reprocess: admin-only forced fresh run for one source."""

import pytest
from helpers import TEST_ADMIN_KEY
from helpers import auth as _auth

from kashi_server import queue
from kashi_server.version import PIPELINE_MAJOR

_INGEST = {
    "source": {"type": "youtube", "id": "reproVid001"},
    "hints": {"title": "T", "artist": "A"},
}
_REPROCESS = {"source": {"type": "youtube", "id": "reproVid001"}}


def _complete_with_document(db_session, job_id) -> None:
    """Drive the queued job to completed WITH a stored document, so the public
    ingest path would reuse it forever."""
    import uuid

    from kashi_server.db.models import Job
    from kashi_server.pipeline.alignment import AlignResult, LineTiming
    from kashi_server.pipeline.document import build_document, persist_processed_track
    from kashi_server.pipeline.lrclib import LyricsText
    from kashi_server.pipeline.palette import DEFAULT_PALETTE

    job = db_session.get(Job, uuid.UUID(job_id))
    result = AlignResult(
        sync="line",
        lines=[LineTiming(0, 900, "la", 0.9)],
        words_per_line=[],
        quality_score=0.9,
    )
    lyrics = LyricsText(["la"], "la", 1, False)
    doc = build_document(
        job,
        lyrics,
        result,
        None,
        dict(DEFAULT_PALETTE),
        vocals_separated=False,
        fallback_duration_ms=200_000,
    )
    persist_processed_track(db_session, job, doc)
    queue.mark_completed(db_session, job)
    db_session.commit()


def test_requires_admin_role(client, user_key):
    resp = client.post("/v1/admin/reprocess", json=_REPROCESS, headers=_auth(user_key))
    assert resp.status_code == 403


def test_unknown_source_without_hints_is_404(client):
    resp = client.post("/v1/admin/reprocess", json=_REPROCESS, headers=_auth(TEST_ADMIN_KEY))
    assert resp.status_code == 404


def test_unknown_source_with_explicit_hints_enqueues(client):
    body = {**_REPROCESS, "hints": {"title": "T", "artist": "A"}}
    resp = client.post("/v1/admin/reprocess", json=body, headers=_auth(TEST_ADMIN_KEY))
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"


def test_completed_track_gets_a_fresh_job_with_inherited_hints(client, user_key, db_session):
    first = client.post("/v1/ingest", json=_INGEST, headers=_auth(user_key))
    assert first.status_code == 202
    _complete_with_document(db_session, first.json()["job_id"])

    # Public ingest keeps returning the old job (idempotency contract)...
    again = client.post("/v1/ingest", json=_INGEST, headers=_auth(user_key))
    assert again.json()["job_id"] == first.json()["job_id"]

    # ...but admin reprocess forces a FRESH one, inheriting the stored hints.
    forced = client.post("/v1/admin/reprocess", json=_REPROCESS, headers=_auth(TEST_ADMIN_KEY))
    assert forced.status_code == 202, forced.text
    assert forced.json()["job_id"] != first.json()["job_id"]
    assert forced.json()["status"] == "queued"

    import uuid

    from kashi_server.db.models import Job

    fresh = db_session.get(Job, uuid.UUID(forced.json()["job_id"]))
    assert fresh.hints["title"] == "T" and fresh.hints["artist"] == "A"


def test_reprocess_carries_the_ingest_escape_hatches(client, db_session):
    """Reprocess IS the manual-retry tool (ingest reuses failed jobs), so the
    options escape hatches must ride along — 2.2.4 field need: retrying a
    wrong-song nightcore with original_title."""
    body = {
        **_REPROCESS,
        "hints": {"title": "Nightcore - Song (Lyrics)", "artist": "Chan"},
        "options": {"original_title": "Song"},
    }
    resp = client.post("/v1/admin/reprocess", json=body, headers=_auth(TEST_ADMIN_KEY))
    assert resp.status_code == 202, resp.text

    import uuid

    from kashi_server.db.models import Job

    job = db_session.get(Job, uuid.UUID(resp.json()["job_id"]))
    assert job.options == {"original_title": "Song", "separate": False}


def test_live_job_is_returned_not_duplicated(client, user_key):
    live = client.post("/v1/ingest", json=_INGEST, headers=_auth(user_key))
    resp = client.post("/v1/admin/reprocess", json=_REPROCESS, headers=_auth(TEST_ADMIN_KEY))
    assert resp.status_code == 202
    assert resp.json()["job_id"] == live.json()["job_id"]  # uq_jobs_active respected


def test_queue_full_maps_to_503(client, user_key, db_session, monkeypatch):
    first = client.post("/v1/ingest", json=_INGEST, headers=_auth(user_key))
    _complete_with_document(db_session, first.json()["job_id"])

    from kashi_server.config import settings

    monkeypatch.setattr(settings, "queue_depth_limit", 0)
    resp = client.post("/v1/admin/reprocess", json=_REPROCESS, headers=_auth(TEST_ADMIN_KEY))
    assert resp.status_code == 503
    assert resp.json() == {"error": "queue_full"}


def test_enqueue_reprocess_race_returns_live_winner(db_session, monkeypatch):
    """IntegrityError path: a competitor inserts between lookup and flush."""
    winner = queue.enqueue(
        db_session,
        source_type="youtube",
        source_id="raceVid0001",
        pipeline_major=PIPELINE_MAJOR,
        hints={"title": "T", "artist": "A"},
        options={},
        requested_by=None,
    )
    db_session.commit()

    real_find_live = queue._find_live
    calls = {"n": 0}

    def hide_live_once(s, source_type, source_id, pipeline_major):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # simulate the pre-insert lookup missing the winner
        return real_find_live(s, source_type, source_id, pipeline_major)

    monkeypatch.setattr(queue, "_find_live", hide_live_once)
    job = queue.enqueue_reprocess(
        db_session,
        source_type="youtube",
        source_id="raceVid0001",
        pipeline_major=PIPELINE_MAJOR,
        hints={"title": "T", "artist": "A"},
        options={},
        requested_by=None,
    )
    assert job.id == winner.id


@pytest.mark.parametrize("payload", [{}, {"source": {"type": "youtube", "id": "bad/slash"}}])
def test_validation_rejects_malformed_bodies(client, payload):
    resp = client.post("/v1/admin/reprocess", json=payload, headers=_auth(TEST_ADMIN_KEY))
    assert resp.status_code == 422
