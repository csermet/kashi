import os

import pytest
from fastapi.testclient import TestClient

from kashi_server.api.app import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (needs Postgres)"
)
def test_ready_with_database() -> None:
    assert client.get("/v1/ready").status_code == 200


def test_ready_reports_503_when_database_is_down(monkeypatch) -> None:
    """Never assert 503 by relying on 'nothing listens on the default port' —
    a developer running the compose Postgres there would fail the suite."""
    from kashi_server.db import engine as engine_module

    def boom():
        raise OSError("connection refused")

    monkeypatch.setattr(engine_module.engine, "connect", boom)
    assert client.get("/v1/ready").status_code == 503
