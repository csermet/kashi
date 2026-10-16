#!/usr/bin/env python3
"""Trim an lrclib database dump down to what a lyrics mirror serves.

lrclib publishes its whole SQLite database (https://lrclib.net/db-dumps). The
2026-10-12 dump unpacks to ~262 GB, most of it lyrics history and per-row
word-timing YAML (`lyricsfile`). A Kashi mirror answers one question — "plain
synced lyrics for this track" — and the stock lrclib server only ever reads
the row `tracks.last_lyrics_id` points at. So the trimmed copy keeps:

  - every track whose LATEST lyrics row has synced lyrics (record ids unchanged,
    so a mirror answer and an lrclib.net answer name the same record),
  - exactly that one lyrics row per track, with `lyricsfile` dropped,
  - the full schema, triggers and `PRAGMA user_version`, so the unmodified
    lrclib server starts on it (it refuses a database whose version it does not
    know: `DatabaseTooFarAhead`).

Usage (stdlib only; read the source from wherever it was unpacked):

    python3 -I trim.py SOURCE.sqlite3 DEST.sqlite3

The source is opened read-only and immutable (no -wal/-shm files appear next
to it). Both scans are sequential on purpose: the source is large and usually
sits on a spinning disk, where per-row seeks would take days.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

# Columns zeroed in the copy: the word-timing YAML is the bulk of the dump and
# no Kashi client reads it from a mirror (the server pipeline asks lrclib.net
# for it first).
DROPPED_LYRICS_COLUMNS = {
    "lyricsfile": "NULL",
    "has_lyricsfile": "0",
    "has_word_sync": "0",
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def columns(con: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA {schema}.table_info({table})")]


def schema_objects(con: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """(tables + virtual tables, indexes + triggers) from the source, in order.

    FTS shadow tables and SQLite's own bookkeeping are recreated implicitly.
    """
    tables: list[str] = []
    later: list[str] = []
    rows = con.execute(
        "SELECT type, name, sql FROM src.sqlite_master WHERE sql IS NOT NULL ORDER BY rowid"
    ).fetchall()
    shadow_prefixes = tuple(
        f"{name}_" for type_, name, sql in rows if type_ == "table" and "VIRTUAL TABLE" in sql.upper()
    )
    for type_, name, sql in rows:
        if name.startswith("sqlite_") or name.startswith(shadow_prefixes):
            continue
        (tables if type_ == "table" else later).append(sql)
    return tables, later


def trim(source: Path, dest: Path) -> None:
    if dest.exists():
        raise SystemExit(f"{dest} exists — refusing to overwrite")
    con = sqlite3.connect(dest, isolation_level=None)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA temp_store=FILE")
    con.execute("PRAGMA cache_size=-1048576")  # 1 GiB
    con.execute(f"ATTACH DATABASE 'file:{source}?mode=ro&immutable=1' AS src")

    user_version = con.execute("PRAGMA src.user_version").fetchone()[0]
    log(f"source user_version={user_version}")

    tables, later = schema_objects(con)
    for sql in tables:
        con.execute(sql)

    con.execute("BEGIN")
    log("collecting the latest lyrics id of every track (tracks scan)")
    con.execute("CREATE TEMP TABLE keep (id INTEGER PRIMARY KEY)")
    con.execute(
        "INSERT OR IGNORE INTO temp.keep SELECT last_lyrics_id FROM src.tracks "
        "WHERE last_lyrics_id IS NOT NULL"
    )
    log(f"  {con.execute('SELECT count(*) FROM temp.keep').fetchone()[0]:,} candidates")

    lyrics_cols = columns(con, "src", "lyrics")
    select = ", ".join(DROPPED_LYRICS_COLUMNS.get(c, f"l.{c}") for c in lyrics_cols)
    log("copying synced latest lyrics rows (sequential lyrics scan — the long step)")
    # Unary + keeps the planner off idx_lyrics_has_synced_lyrics: an index walk
    # would turn a sequential read into one seek per row.
    con.execute(
        f"INSERT INTO main.lyrics ({', '.join(lyrics_cols)}) "
        f"SELECT {select} FROM src.lyrics AS l "
        f"WHERE +l.has_synced_lyrics = 1 AND l.id IN (SELECT id FROM temp.keep)"
    )
    kept_lyrics = con.execute("SELECT count(*) FROM main.lyrics").fetchone()[0]
    log(f"  {kept_lyrics:,} lyrics rows kept")

    track_cols = ", ".join(columns(con, "src", "tracks"))
    log("copying their tracks")
    con.execute(
        f"INSERT INTO main.tracks ({track_cols}) SELECT {track_cols} FROM src.tracks "
        "WHERE last_lyrics_id IN (SELECT id FROM main.lyrics)"
    )
    kept_tracks = con.execute("SELECT count(*) FROM main.tracks").fetchone()[0]
    log(f"  {kept_tracks:,} tracks kept")

    # AUTOINCREMENT counters, so ids the server might mint never collide. The
    # inserts above already wrote their own (lower) counters: replace them.
    con.execute("DELETE FROM main.sqlite_sequence WHERE name IN ('tracks', 'lyrics')")
    con.execute(
        "INSERT INTO main.sqlite_sequence (name, seq) "
        "SELECT name, seq FROM src.sqlite_sequence WHERE name IN ('tracks', 'lyrics')"
    )
    con.execute("COMMIT")

    log("indexes and triggers (after the data: one sort per index, not one update per row)")
    for sql in later:
        con.execute(sql)
    log("rebuilding the full-text table the triggers maintain")
    con.execute("INSERT INTO tracks_fts (tracks_fts) VALUES ('rebuild')")
    con.execute(f"PRAGMA main.user_version = {int(user_version)}")
    con.execute("DETACH DATABASE src")
    con.execute("ANALYZE")
    log("vacuum")
    con.execute("VACUUM")
    con.close()
    size_gb = dest.stat().st_size / 1e9
    log(f"done: {kept_tracks:,} tracks, {kept_lyrics:,} lyrics, {size_gb:.1f} GB, user_version={user_version}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    trim(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())


if __name__ == "__main__":
    main()
