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
