"""Regression tests for the A2 review findings."""

from helpers import TEST_ADMIN_KEY
from helpers import auth as _auth

from kashi_server.api.middleware import MAX_BODY_BYTES


def test_oversized_body_rejected_before_auth(client):
    """Unauthenticated POST with a huge body must die at the middleware."""
    payload = "x" * (MAX_BODY_BYTES + 1024)
    resp = client.post("/v1/ingest", content=payload, headers={"content-type": "application/json"})
    assert resp.status_code == 413
    assert resp.json() == {"error": "payload_too_large"}


def test_normal_body_still_accepted(client, user_key):
    body = {
        "source": {"type": "youtube", "id": "sizeOk00001"},
        "hints": {"title": "T", "artist": "A"},
    }
    assert client.post("/v1/ingest", json=body, headers=_auth(user_key)).status_code == 202


def test_source_id_rejects_path_separators(client, user_key):
    for bad in ["folder/track", "a?b", "a#b", "a b"]:
        body = {"source": {"type": "upload", "id": bad}, "hints": {"title": "T", "artist": "A"}}
        assert client.post("/v1/ingest", json=body, headers=_auth(user_key)).status_code == 422


def test_second_key_can_poll_the_deduped_job(client, user_key):
    """Idempotent ingest hands out the FIRST key's job id — it must be pollable."""
    other = client.post(
        "/v1/admin/keys", json={"name": "second", "role": "user"}, headers=_auth(TEST_ADMIN_KEY)
    ).json()["key"]
    body = {
        "source": {"type": "youtube", "id": "dedupPoll01"},
        "hints": {"title": "T", "artist": "A"},
    }
    first = client.post("/v1/ingest", json=body, headers=_auth(user_key)).json()["job_id"]
    second = client.post("/v1/ingest", json=body, headers=_auth(other)).json()["job_id"]
    assert first == second
    assert client.get(f"/v1/jobs/{second}", headers=_auth(other)).status_code == 200
    # Cancel stays owner-scoped.
    assert client.delete(f"/v1/jobs/{second}", headers=_auth(other)).status_code == 404
    assert client.delete(f"/v1/jobs/{first}", headers=_auth(user_key)).status_code == 204


def test_last_used_at_survives_a_404_response(client, user_key):
    assert client.get("/v1/lyrics/youtube/absent", headers=_auth(user_key)).status_code == 404
    keys = client.get("/v1/admin/keys", headers=_auth(TEST_ADMIN_KEY)).json()
    row = next(k for k in keys if k["name"] == "test-user")
    assert row["last_used_at"] is not None


def test_bootstrap_restores_disabled_admin_key(client, db_session):
    """A soft-disabled bootstrap key must not lock the operator out forever."""
    from sqlalchemy import select

    from kashi_server.api.app import _bootstrap_admin_key
    from kashi_server.db.models import ApiKey

    keys = client.get("/v1/admin/keys", headers=_auth(TEST_ADMIN_KEY)).json()
    boot = next(k for k in keys if k["name"] == "bootstrap-admin")
    assert (
        client.delete(f"/v1/admin/keys/{boot['id']}", headers=_auth(TEST_ADMIN_KEY)).status_code
        == 204
    )
    assert client.get("/v1/admin/keys", headers=_auth(TEST_ADMIN_KEY)).status_code == 401

    _bootstrap_admin_key()  # what a restart does
    assert client.get("/v1/admin/keys", headers=_auth(TEST_ADMIN_KEY)).status_code == 200
    restored = db_session.scalars(select(ApiKey).where(ApiKey.name == "bootstrap-admin")).first()
    assert restored is not None and restored.disabled is False


def test_bootstrap_rotation_disables_the_previous_key(client, monkeypatch):
    from kashi_server.api.app import _bootstrap_admin_key
    from kashi_server.config import settings

    new_key = "ksh_" + "cd" * 16
    monkeypatch.setattr(settings, "admin_api_key", new_key)
    _bootstrap_admin_key()

    assert client.get("/v1/admin/keys", headers=_auth(new_key)).status_code == 200
    assert client.get("/v1/admin/keys", headers=_auth(TEST_ADMIN_KEY)).status_code == 401


def test_if_none_match_star_returns_304(client, user_key, db_session):
    from kashi_server.db.models import ProcessedTrack

    db_session.add(
        ProcessedTrack(
            source_type="youtube",
            source_id="starEtag001",
            pipeline_version="1.0.0",
            pipeline_major=1,
            sync="line",
            quality_score=1.0,
            document={"schema_version": 1},
            etag="f" * 32,
        )
    )
    db_session.commit()
    resp = client.get(
        "/v1/lyrics/youtube/starEtag001", headers=_auth(user_key) | {"If-None-Match": "*"}
    )
    assert resp.status_code == 304
