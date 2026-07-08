"""Shared fixtures. DB-backed tests need DATABASE_URL (a running Postgres with
migrations applied) and auto-skip without it — CI provides a service container,
local runs can skip or point at the compose/dev instance.
"""

import os

import pytest

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (needs Postgres)"
)


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
