"""Is the database at the schema this code was built for? (release 0.37.0)

Only the API deployment migrates (`alembic upgrade head` in its
initContainer); the cluster worker and the hand-started GPU helper roll on
their own. A worker whose persist path reads a table the database does not
have yet (0.37.0 reads `song_overrides` on every persist) fails each finished
job as `other`: a lost run and, for a new song, the 7-day ingest block. So a
worker checks once at start, before its ready line and its first claim, and
waits for the migration (worker/main.py).
"""

import logging
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

#: apps/server/alembic in a checkout, /app/alembic in the image (Dockerfile).
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "alembic"


def schema_gap(conn: Connection, migrations: Path = MIGRATIONS_DIR) -> str | None:
    """Why this code must not use the database yet, or None.

    It refuses only when the database is certainly BEHIND this code: no
    `alembic_version` table or no row in it (never migrated), or a revision of
    this code's own chain other than its head (the chain is linear, so a known
    revision that is not the head is an older one). Two cases are let through
    on purpose, so the check cannot raise a false alarm: a revision this code
    does not know (a NEWER image migrated the database: a rollout in progress
    with the API first; refusing it would stall an older worker at every
    future rollout), and a code tree without its migrations directory (it
    cannot tell). Both are logged."""
    if not migrations.is_dir():
        logger.warning("schema check skipped: no migrations directory at %s", migrations)
        return None
    script = ScriptDirectory(str(migrations))
    heads = set(script.get_heads())
    known = {revision.revision for revision in script.walk_revisions()}
    expected = ", ".join(sorted(heads))
    if conn.execute(text("SELECT to_regclass('alembic_version')")).scalar() is None:
        return f"expected Alembic revision {expected}, found no alembic_version table"
    found = sorted(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if not found:
        return f"expected Alembic revision {expected}, found none"
    if any(revision in known and revision not in heads for revision in found):
        return f"expected Alembic revision {expected}, found {', '.join(found)}"
    unknown = [revision for revision in found if revision not in known]
    if unknown:
        logger.warning(
            "database at Alembic revision %s, which this code (head %s) does not know: "
            "taken as migrated by a newer image",
            ", ".join(unknown),
            expected,
        )
    return None
