"""Every migration's `downgrade()` has to actually run, not just exist.

Until Faz 7 the downgrade bodies were only ever executed by hand during a phase
acceptance, so a typo in one of them (a wrong index name, a table dropped in the
wrong order) would have sat undetected until the day it was needed most. This
walks the whole chain down to base and back up.

The round trip leaves the schema at head, so ordering against the other
DB-backed tests does not matter; their fixtures truncate anyway.
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (needs Postgres)"
)

# The tables the current head is expected to own; the round trip has to end with
# all of them back. Kept explicit so a dropped-and-never-recreated table fails
# loudly instead of silently shrinking the schema.
HEAD_TABLES = {
    "api_keys",
    "jobs",
    "processed_tracks",
    "uploaded_audio",
    "lrclib_publishes",
    "telemetry",
}


def _alembic_config() -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    # Absolute, so the test does not depend on pytest's working directory.
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    return config


def _table_names() -> set[str]:
    from kashi_server.db.engine import engine

    # Fresh connection: the migrations run on their own engine, so a pooled
    # connection here could otherwise answer from a stale catalog snapshot.
    engine.dispose()
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def test_downgrade_to_base_and_back_up():
    """upgrade head -> downgrade base -> upgrade head, on a real Postgres."""
    from kashi_server.db.engine import engine

    config = _alembic_config()

    # Whatever state the DB is in, start from head.
    engine.dispose()
    command.upgrade(config, "head")
    assert HEAD_TABLES <= _table_names()

    # Every downgrade() body in the chain runs here.
    engine.dispose()
    command.downgrade(config, "base")
    after_downgrade = _table_names()
    leftovers = HEAD_TABLES & after_downgrade
    assert not leftovers, f"downgrade left tables behind: {sorted(leftovers)}"

    # And the whole chain has to still apply cleanly on the emptied schema.
    engine.dispose()
    command.upgrade(config, "head")
    assert HEAD_TABLES <= _table_names()
