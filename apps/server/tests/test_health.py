from fastapi.testclient import TestClient

from kashi_server.api.app import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready() -> None:
    resp = client.get("/v1/ready")
    assert resp.status_code == 200
