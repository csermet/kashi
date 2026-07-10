"""Shared fixtures. DB-backed tests need DATABASE_URL (a running Postgres with
migrations applied) and auto-skip without it — CI provides a service container,
local runs can skip or point at the compose/dev instance.
"""

import os

import pytest
from helpers import TEST_ADMIN_KEY
from helpers import auth as _auth


@pytest.fixture()
def db_session():
    from sqlalchemy import text

    from kashi_server.db.engine import SessionLocal, engine

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE api_keys, jobs, processed_tracks CASCADE"))
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client(db_session, monkeypatch):
    """TestClient with a fresh app; lifespan bootstraps TEST_ADMIN_KEY."""
    from fastapi.testclient import TestClient

    from kashi_server.api.app import create_app
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def user_key(client) -> str:
    """A plain-role API key created through the admin endpoint."""
    resp = client.post(
        "/v1/admin/keys", json={"name": "test-user", "role": "user"}, headers=_auth(TEST_ADMIN_KEY)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


def pytest_collection_modifyitems(config, items):
    if os.environ.get("DATABASE_URL"):
        return
    skip_db = pytest.mark.skip(reason="DATABASE_URL not set (needs Postgres)")
    for item in items:
        if "db_session" in getattr(item, "fixturenames", ()):
            item.add_marker(skip_db)
