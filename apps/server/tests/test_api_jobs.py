"""Jobs endpoints: ownership scoping, listing, cancel semantics."""

from helpers import TEST_ADMIN_KEY
from helpers import auth as _auth


def _ingest(client, key, source_id):
    body = {
        "source": {"type": "youtube", "id": source_id},
        "hints": {"title": "T", "artist": "A"},
    }
    resp = client.post("/v1/ingest", json=body, headers=_auth(key))
    assert resp.status_code == 202
    return resp.json()["job_id"]


def test_get_own_job(client, user_key):
    job_id = _ingest(client, user_key, "vidJobs0001")
    resp = client.get(f"/v1/jobs/{job_id}", headers=_auth(user_key))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued" and body["result_url"] is None


def test_foreign_job_status_is_readable_but_not_cancelable(client, user_key):
    """Status reads are open to any key (idempotent ingest hands out foreign
    job ids); mutations stay owner-scoped."""
    other = client.post(
        "/v1/admin/keys", json={"name": "other", "role": "user"}, headers=_auth(TEST_ADMIN_KEY)
    ).json()["key"]
    job_id = _ingest(client, other, "vidJobs0002")
    assert client.get(f"/v1/jobs/{job_id}", headers=_auth(user_key)).status_code == 200
    assert client.get(f"/v1/jobs/{job_id}", headers=_auth(TEST_ADMIN_KEY)).status_code == 200
    assert client.delete(f"/v1/jobs/{job_id}", headers=_auth(user_key)).status_code == 404
    assert client.get("/v1/jobs", headers=_auth(user_key)).json() == []  # list stays scoped
    assert (
        client.get(
            f"/v1/jobs/{'0' * 8}-0000-0000-0000-{'0' * 12}", headers=_auth(user_key)
        ).status_code
        == 404
    )


def test_list_jobs_scoping_and_filter(client, user_key):
    _ingest(client, user_key, "vidJobs0003")
    _ingest(client, user_key, "vidJobs0004")
    listed = client.get("/v1/jobs", headers=_auth(user_key)).json()
    assert len(listed) == 2
    queued = client.get("/v1/jobs?status=queued&limit=1", headers=_auth(user_key)).json()
    assert len(queued) == 1
    assert client.get("/v1/jobs?status=bogus", headers=_auth(user_key)).status_code == 400


def test_cancel_queued_then_409_on_terminal(client, user_key):
    job_id = _ingest(client, user_key, "vidJobs0005")
    assert client.delete(f"/v1/jobs/{job_id}", headers=_auth(user_key)).status_code == 204
    assert client.get(f"/v1/jobs/{job_id}", headers=_auth(user_key)).json()["status"] == "canceled"
    assert client.delete(f"/v1/jobs/{job_id}", headers=_auth(user_key)).status_code == 409


def test_completed_job_exposes_result_url(client, user_key, db_session):
    import uuid

    from kashi_server.db.models import Job

    job_id = _ingest(client, user_key, "vidJobs0006")
    job = db_session.get(Job, uuid.UUID(job_id))
    job.status = "completed"
    db_session.commit()
    body = client.get(f"/v1/jobs/{job_id}", headers=_auth(user_key)).json()
    assert body["result_url"] == "/v1/lyrics/youtube/vidJobs0006"
