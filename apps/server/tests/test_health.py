import os

import pytest
from fastapi.testclient import TestClient

from kashi_server.api.app import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_names_the_pipeline_version() -> None:
    """The cosmetic server version alone could not tell which pipeline a
    deployment runs; checking a rollout needed a worker log line (2.33.0)."""
    from kashi_server import __version__
    from kashi_server.version import PIPELINE_VERSION

    assert client.get("/v1/health").json() == {
        "status": "ok", "version": __version__, "pipeline_version": PIPELINE_VERSION,
    }


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
