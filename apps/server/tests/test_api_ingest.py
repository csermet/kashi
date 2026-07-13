"""POST /v1/ingest contract: 202 + idempotency, queue_full 503, 429 bucket."""

from helpers import auth as _auth

_BODY = {
    "source": {"type": "youtube", "id": "dQw4w9WgXcQ"},
    "hints": {"title": "Never Gonna Give You Up", "artist": "Rick Astley"},
}


def test_ingest_returns_202_and_is_idempotent(client, user_key):
    first = client.post("/v1/ingest", json=_BODY, headers=_auth(user_key))
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "queued"
    second = client.post("/v1/ingest", json=_BODY, headers=_auth(user_key))
    assert second.status_code == 202
    assert second.json()["job_id"] == first.json()["job_id"]


def test_ingest_validation(client, user_key):
    bad = {"source": {"type": "youtube", "id": ""}, "hints": {"title": "t", "artist": "a"}}
    assert client.post("/v1/ingest", json=bad, headers=_auth(user_key)).status_code == 422
    no_artist = {"source": {"type": "youtube", "id": "x1"}, "hints": {"title": "t"}}
    assert client.post("/v1/ingest", json=no_artist, headers=_auth(user_key)).status_code == 422


def test_ingest_nightcore_options_validate_and_persist_without_nulls(
    client, user_key, db_session
):
    from sqlalchemy import select

    from kashi_server.db.models import Job

    # speed_factor must be a real speed-UP: 1.0 and out-of-band values reject.
    for bad_factor in (1.0, 0.8, 2.5):
        body = {**_BODY, "options": {"speed_factor": bad_factor}}
        assert client.post("/v1/ingest", json=body, headers=_auth(user_key)).status_code == 422

    body = {
        "source": {"type": "youtube", "id": "nightcoreAA"},
        "hints": {"title": "Nightcore - Song", "artist": "Chan", "duration_ms": 200_000},
        "options": {"speed_factor": 1.2, "original_title": "Song"},
    }
    resp = client.post("/v1/ingest", json=body, headers=_auth(user_key))
    assert resp.status_code == 202, resp.text
    job = db_session.scalars(select(Job).where(Job.source_id == "nightcoreAA")).one()
    # exclude_none: absent options (lyrics_text) never persist as nulls.
    assert job.options == {"separate": False, "speed_factor": 1.2, "original_title": "Song"}


def test_ingest_queue_full(client, user_key, monkeypatch):
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "queue_depth_limit", 0)
    resp = client.post("/v1/ingest", json=_BODY, headers=_auth(user_key))
    assert resp.status_code == 503
    assert resp.json() == {"error": "queue_full"}


def test_ingest_rate_limited(client, user_key, monkeypatch):
    from kashi_server import ratelimit

    monkeypatch.setitem(ratelimit.RATE_LIMITS, "ingest", (1.0, 0.001))
    ok = client.post("/v1/ingest", json=_BODY, headers=_auth(user_key))
    assert ok.status_code == 202
    limited = client.post("/v1/ingest", json=_BODY, headers=_auth(user_key))
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


def test_ingest_rejects_tracks_over_the_duration_cap(client, user_key, db_session):
    from sqlalchemy import select

    from kashi_server.db.models import Job

    # Field case: a 61-minute mix could never complete but still created a
    # job, queried lrclib with duration=3679 (a 400) and burned its retries.
    body = {
        "source": {"type": "youtube", "id": "longmix0001"},
        "hints": {"title": "Mega Mix", "artist": "DJ Long", "duration_ms": 3_679_000},
    }
    resp = client.post("/v1/ingest", json=body, headers=_auth(user_key))
    assert resp.status_code == 422
    assert "processing cap" in resp.json()["detail"]
    assert (
        db_session.execute(select(Job).where(Job.source_id == "longmix0001")).scalar_one_or_none()
        is None
    )  # no job row was created


def test_ingest_duration_at_the_cap_still_queues(client, user_key):
    body = {
        "source": {"type": "youtube", "id": "capedge0001"},
        "hints": {"title": "Edge", "artist": "Cap", "duration_ms": 1_200_000},
    }
    assert client.post("/v1/ingest", json=body, headers=_auth(user_key)).status_code == 202


def test_reused_flag_distinguishes_fresh_from_existing(client, user_key):
    body = {
        "source": {"type": "youtube", "id": "reusedFlag1"},
        "hints": {"title": "Reused", "artist": "Flag"},
    }
    first = client.post("/v1/ingest", json=body, headers=_auth(user_key))
    assert first.status_code == 202 and first.json()["reused"] is False
    second = client.post("/v1/ingest", json=body, headers=_auth(user_key))
    assert second.status_code == 202 and second.json()["reused"] is True
    assert second.json()["job_id"] == first.json()["job_id"]
