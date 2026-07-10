"""Auth behavior over the wire: 401/403 paths, disabled keys, bootstrap admin."""

from helpers import TEST_ADMIN_KEY
from helpers import auth as _auth


def test_missing_and_malformed_auth_rejected(client):
    assert client.get("/v1/jobs").status_code == 401
    assert client.get("/v1/jobs", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/jobs", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/v1/jobs", headers=_auth("ksh_" + "0" * 32)).status_code == 401  # unknown


def test_user_key_cannot_reach_admin_endpoints(client, user_key):
    resp = client.get("/v1/admin/keys", headers=_auth(user_key))
    assert resp.status_code == 403


def test_disabled_key_stops_working(client, user_key):
    keys = client.get("/v1/admin/keys", headers=_auth(TEST_ADMIN_KEY)).json()
    target = next(k for k in keys if k["name"] == "test-user")
    assert (
        client.delete(f"/v1/admin/keys/{target['id']}", headers=_auth(TEST_ADMIN_KEY)).status_code
        == 204
    )
    assert client.get("/v1/jobs", headers=_auth(user_key)).status_code == 401


def test_admin_key_lifecycle(client):
    created = client.post(
        "/v1/admin/keys",
        json={"name": "n1", "role": "user"},
        headers=_auth(TEST_ADMIN_KEY),
    )
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("ksh_") and body["role"] == "user"
    listed = client.get("/v1/admin/keys", headers=_auth(TEST_ADMIN_KEY)).json()
    assert all("key" not in item for item in listed)  # plaintext never listed
    assert {item["name"] for item in listed} >= {"bootstrap-admin", "n1"}


def test_probes_are_unauthenticated(client):
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/ready").status_code == 200
