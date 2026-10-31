"""Shared fixtures. DB-backed tests need DATABASE_URL (a running Postgres with
migrations applied) and auto-skip without it — CI provides a service container,
local runs can skip or point at the compose/dev instance.
"""

import os

import pytest
from helpers import TEST_ADMIN_KEY
from helpers import auth as _auth


@pytest.fixture
def db_session():
    from sqlalchemy import text

    from kashi_server.db.engine import SessionLocal, engine

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE api_keys, jobs, processed_tracks, telemetry CASCADE"))
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _hermetic_pipeline_defaults(monkeypatch):
    """Unit tests exercise MECHANICS, not the shipped defaults: 2.0.0 turned
    separation + windowing on by default, which would drag audio-separator
    imports and window planning into every worker test. Tests that target
    those paths opt in explicitly; the defaults themselves are pinned by
    test_config_defaults.

    lrclib.net is unreachable by default (a closed local port): a test that
    leans on a live answer fails every time instead of whenever lrclib.net
    changes its mind (2026-10-31: misses turned from 404 into 503 and seven
    tests deferred instead of failing; see lrclib_has_no_record)."""
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "lrclib_base_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "separation_mode", "off")
    monkeypatch.setattr(settings, "windowed_alignment", False)
    monkeypatch.setattr(settings, "fx_embeddings", False)  # keyword layer stays on


_SHIPPED_RECORD_GATE: dict[str, float] = {}


@pytest.fixture(autouse=True)
def _toy_records_pass_the_record_gate(monkeypatch):
    """Unit tests feed one- and two-line toy lrclib records, which the
    degenerate-record gate (LyricFix P4) rightly rejects as too thin to be a
    song. Mechanics tests run with the gate open; tests of the gate itself
    take `shipped_record_gate` to get the real floors back."""
    from kashi_server.pipeline import lrclib

    for name in ("MIN_SYNCED_LINES", "MIN_WORDS_PER_MINUTE"):
        _SHIPPED_RECORD_GATE.setdefault(name, getattr(lrclib, name))
        monkeypatch.setattr(lrclib, name, 0)


@pytest.fixture
def shipped_record_gate(monkeypatch):
    from kashi_server.pipeline import lrclib

    for name, value in _SHIPPED_RECORD_GATE.items():
        monkeypatch.setattr(lrclib, name, value)


@pytest.fixture(autouse=True)
def _fresh_lrclib_gateway(monkeypatch, _hermetic_pipeline_defaults):
    """The worker's lrclib breaker is process-wide state (LyricFix P3): one
    test tripping it must not send the next test's lookups to the mirror.
    Only rebuilt when the worker module is already loaded — importing it here
    would drag the pipeline into every API test. Built AFTER the hermetic
    defaults (explicit dependency), or it keeps the live lrclib.net URL."""
    import sys

    process = sys.modules.get("kashi_server.worker.process")
    if process is not None:
        monkeypatch.setattr(process, "LRCLIB", process.build_gateway())


@pytest.fixture
def client(db_session, monkeypatch):
    """TestClient with a fresh app; lifespan bootstraps TEST_ADMIN_KEY."""
    from fastapi.testclient import TestClient

    from kashi_server.api.app import create_app
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "admin_api_key", TEST_ADMIN_KEY)
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
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


@pytest.fixture
def lrclib_has_no_record(monkeypatch):
    """The lyrics ladder MISSES, offline. Failure-path worker tests need the
    plain-job lookup to come back empty so the job reaches the stage under
    test. They used to get that miss from the real lrclib.net, which answered
    their toy hints with 404 - until 2026-10-31, when it began answering misses
    with 503 ServerOverloaded, the precheck deferred every such job, and CI on
    main went red on a docs-only commit. No test may depend on lrclib.net."""
    from kashi_server.vdl_kit.errors import PipelineError
    from kashi_server.worker import process as wp

    def no_record(hints, base_url):
        raise PipelineError("lyrics_not_found", "no record (offline test)")

    monkeypatch.setattr(wp, "fetch_lyrics", no_record)
    monkeypatch.setattr(wp, "search_candidates", lambda query, base_url: [])
