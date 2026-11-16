"""Listening does not refresh a stored document: characterisation of today's
behaviour (adversarial verification 2026-11-13, claim 3; its throwaway test
dogrulama/test_dg3_listen_refresh.py, adopted unchanged in substance).

GET /v1/lyrics serves whatever is stored, whatever its pipeline version; the
overlay enqueues (POST /v1/ingest) only after a 404. An ingest reuses the
stored job while the stored MAJOR equals the server's and enqueues a fresh job
only for an older major. So an older minor is refreshed only by an admin
reprocess (the archive feeder). If refresh-on-listen is ever built, these
tests are meant to change with it.
"""

from helpers import auth
from sqlalchemy import select, text

from kashi_server import queue
from kashi_server.db.models import Job
from kashi_server.pipeline.alignment import AlignResult, LineTiming
from kashi_server.pipeline.document import build_document, persist_processed_track
from kashi_server.pipeline.lrclib import LyricsText
from kashi_server.pipeline.palette import DEFAULT_PALETTE
from kashi_server.version import PIPELINE_MAJOR

SRC = {"type": "youtube", "id": "listenOld01"}
HINTS = {"title": "T", "artist": "A", "duration_ms": 200_000}


def _store(db_session, version: str, major: int) -> None:
    """A completed job and its document, then relabelled as made by `version`."""
    job, _ = queue.enqueue(
        db_session,
        source_type="youtube",
        source_id=SRC["id"],
        pipeline_major=PIPELINE_MAJOR,
        hints=HINTS,
        options={},
        requested_by=None,
    )
    db_session.flush()
    result = AlignResult(
        sync="line", lines=[LineTiming(0, 900, "la", 0.9)], words_per_line=[], quality_score=0.9
    )
    doc = build_document(
        job,
        LyricsText(["la"], "la", 1, False),
        result,
        None,
        dict(DEFAULT_PALETTE),
        vocals_separated=False,
        fallback_duration_ms=200_000,
    )
    persist_processed_track(db_session, job, doc)
    queue.mark_completed(db_session, job)
    db_session.execute(
        text(
            "UPDATE processed_tracks SET pipeline_version = :v, pipeline_major = :m, "
            "document = jsonb_set(document, '{pipeline_version}', CAST(:vj AS jsonb))"
        ),
        {"v": version, "m": major, "vj": f'"{version}"'},
    )
    db_session.execute(text("UPDATE jobs SET pipeline_major = :m"), {"m": major})
    db_session.commit()


def _jobs(db_session) -> int:
    db_session.expire_all()
    return len(db_session.scalars(select(Job)).all())


def test_an_older_minor_is_served_and_an_ingest_reuses_its_job(client, db_session, user_key):
    old = f"{PIPELINE_MAJOR}.0.0"
    _store(db_session, old, PIPELINE_MAJOR)
    got = client.get(f"/v1/lyrics/youtube/{SRC['id']}", headers=auth(user_key))
    assert got.status_code == 200 and got.json()["pipeline_version"] == old
    before = _jobs(db_session)
    ingest = client.post("/v1/ingest", json={"source": SRC, "hints": HINTS}, headers=auth(user_key))
    assert ingest.json()["reused"] is True
    assert _jobs(db_session) == before  # nothing is refreshed by listening


def test_an_older_major_is_served_and_only_an_ingest_enqueues_a_refresh(
    client, db_session, user_key
):
    old = f"{PIPELINE_MAJOR - 1}.9.0"
    _store(db_session, old, PIPELINE_MAJOR - 1)
    got = client.get(f"/v1/lyrics/youtube/{SRC['id']}", headers=auth(user_key))
    # Served: the overlay sees a hit and never enqueues on its own.
    assert got.status_code == 200 and got.json()["pipeline_version"] == old
    before = _jobs(db_session)
    ingest = client.post("/v1/ingest", json={"source": SRC, "hints": HINTS}, headers=auth(user_key))
    assert ingest.json()["reused"] is False
    assert _jobs(db_session) == before + 1
