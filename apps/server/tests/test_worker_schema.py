"""A worker waits for the API's migration before its ready line and its first
claim (release 0.37.0 review).

Only the API deployment runs `alembic upgrade head`; the cluster worker and
the hand-started GPU helper roll on their own. A 0.37.0 worker that claimed
before the migration would fail every finished job as `other` (its persist
path reads `song_overrides`): a lost run and, for a new song, the 7-day
ingest block.
"""

import logging
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from kashi_server.db.schema import MIGRATIONS_DIR, schema_gap
from kashi_server.worker import main

HEAD = "f3a8c2d61b07"  # this release's single head
BEHIND = "b36dfc2aae66"  # 0.36.0's head


# --- schema_gap against the real database (rolled back after each test) -------


def test_the_migrations_ship_where_the_check_looks():
    assert MIGRATIONS_DIR == Path(__file__).resolve().parents[1] / "alembic"
    assert MIGRATIONS_DIR.is_dir()


def test_a_database_at_the_head_is_ready(db_session):
    assert schema_gap(db_session.connection()) is None


def test_a_database_at_an_older_revision_of_this_chain_is_behind(db_session):
    db_session.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": BEHIND})
    gap = schema_gap(db_session.connection())
    assert gap == f"expected Alembic revision {HEAD}, found {BEHIND}"


def test_a_database_never_migrated_is_behind(db_session):
    db_session.execute(text("DELETE FROM alembic_version"))
    assert schema_gap(db_session.connection()) == f"expected Alembic revision {HEAD}, found none"
    db_session.execute(text("ALTER TABLE alembic_version RENAME TO alembic_version_gone"))
    assert schema_gap(db_session.connection()) == (
        f"expected Alembic revision {HEAD}, found no alembic_version table"
    )


def test_a_revision_this_code_does_not_know_is_a_newer_image_not_a_gap(db_session, caplog):
    """A rollout with the API first: an older worker must not stall on it."""
    db_session.execute(text("UPDATE alembic_version SET version_num = 'a0b1c2d3e4f5'"))
    with caplog.at_level("WARNING", logger="kashi_server.db.schema"):
        assert schema_gap(db_session.connection()) is None


def test_no_migrations_directory_cannot_tell_and_does_not_refuse(db_session, tmp_path):
    assert schema_gap(db_session.connection(), tmp_path / "missing") is None


# --- wait_for_schema: the loop ------------------------------------------------


class Checks:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.slept: list[float] = []

    def check(self):
        answer = self.answers.pop(0)
        if answer == "db_down":
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        return answer

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def test_waits_with_the_database_backoff_until_the_schema_is_ready():
    checks = Checks(f"expected {HEAD}, found {BEHIND}", "db_down", None)
    assert main.wait_for_schema(checks.check, lambda: False, checks.sleep) is True
    assert sum(checks.slept) == 1.0 + 2.0 and max(checks.slept) <= 1.0
    assert checks.answers == []


def test_a_stop_while_waiting_returns_false():
    stopping = {"flag": False}

    def sleep(seconds: float) -> None:
        stopping["flag"] = True

    assert main.wait_for_schema(lambda: "behind", lambda: stopping["flag"], sleep) is False


# --- run_forever: no ready line and no claim while the schema is behind -------


@pytest.fixture
def start_up(monkeypatch, caplog):
    """run_forever with its model, port and loop pieces stubbed; returns what it did."""
    did: dict = {"serve": 0, "pauses": 0, "handlers": {}}
    monkeypatch.setattr(logging.getLogger(main.__name__), "disabled", False)
    monkeypatch.setattr(main, "_warm_up", lambda: None)
    monkeypatch.setattr(main, "sweep_orphans", lambda data_dir: 0)
    monkeypatch.setattr(main, "start_http_server", lambda port: None)
    monkeypatch.setattr(main, "_environment_defaults", lambda: None)
    monkeypatch.setattr(
        main.signal, "signal", lambda signum, handler: did["handlers"].__setitem__(signum, handler)
    )

    def serve(iteration, should_stop):
        did["serve"] += 1

    monkeypatch.setattr(main, "serve", serve)
    caplog.set_level("INFO", logger=main.__name__)
    return did


def _ready_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if " ready: pipeline " in r.getMessage()]


def test_schema_behind_no_ready_line_and_no_claim(monkeypatch, caplog, start_up):
    monkeypatch.setattr(
        main, "_schema_gap", lambda: f"expected Alembic revision {HEAD}, found {BEHIND}"
    )

    def pause(seconds: float) -> None:
        start_up["pauses"] += 1
        if start_up["pauses"] == 3:  # SIGTERM while waiting for the migration
            start_up["handlers"][main.signal.SIGTERM](main.signal.SIGTERM, None)

    monkeypatch.setattr(main, "_pause", pause)
    main.run_forever()
    assert start_up["serve"] == 0  # the claim loop never started
    assert _ready_lines(caplog) == []
    waits = [r.getMessage() for r in caplog.records if r.getMessage().startswith("not claiming")]
    assert waits and f"expected Alembic revision {HEAD}, found {BEHIND}" in waits[0]
    assert "worker stopped" in caplog.text


def test_schema_at_head_starts_as_before(monkeypatch, caplog, start_up):
    monkeypatch.setattr(main, "_schema_gap", lambda: None)
    monkeypatch.setattr(main, "_pause", lambda seconds: pytest.fail("no wait at the head"))
    main.run_forever()
    assert start_up["serve"] == 1
    assert len(_ready_lines(caplog)) == 1
    assert "not claiming" not in caplog.text


def test_the_ready_line_follows_the_migration(monkeypatch, caplog, start_up):
    answers = [f"expected Alembic revision {HEAD}, found {BEHIND}", None]
    monkeypatch.setattr(main, "_schema_gap", lambda: answers.pop(0))
    monkeypatch.setattr(main, "_pause", lambda seconds: None)
    main.run_forever()
    messages = [r.getMessage() for r in caplog.records]
    wait = next(i for i, m in enumerate(messages) if m.startswith("not claiming"))
    ready = next(i for i, m in enumerate(messages) if " ready: pipeline " in m)
    assert wait < ready and start_up["serve"] == 1
