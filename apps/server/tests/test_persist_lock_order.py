"""Two writers of one song that has an override: the second decides on what
the first committed (release 0.37.0 review).

persist_processed_track locks the song's override row FOR UPDATE. Until the
review it read the stored document first and waited for that lock second, so
a writer that had waited judged a snapshot from before the other writer's
commit: an automatic run read the old automatic document, then found the
override's applied_etag pointing at the new build, saw "not built under" and
replaced the owner's correction. Now the lock comes first. Two real sessions
on Postgres, interleaved: writer one holds the lock (uncommitted override
build), writer two is seen waiting on it, then writer one commits.

A song WITHOUT an override row locks nothing, as before C.1: its writers can
still cross (documented, not tested here).
"""

import threading
import time
import uuid

from sqlalchemy import select, text

from kashi_server import overrides
from kashi_server.db.models import Job, ProcessedTrack, SongOverride
from kashi_server.pipeline import document
from kashi_server.version import PIPELINE_MAJOR

SOURCE = "lockOrder01"
OVERRIDE = {"speed_factor": 1.1289, "lyrics_text": "caller one\ncaller two"}


def _job(s, options: dict) -> Job:
    job = Job(
        source_type="youtube",
        source_id=SOURCE,
        pipeline_major=PIPELINE_MAJOR,
        hints={"title": "Song", "artist": "Artist", "duration_ms": 200_000},
        options=options,
        status="completed",  # several jobs of one song must not trip uq_jobs_active
    )
    job.id = uuid.uuid4()
    s.add(job)
    s.flush()
    return job


def _doc(job, lines: int, quality: float) -> dict:
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
    from kashi_server.pipeline.lrclib import LyricsText
    from kashi_server.pipeline.palette import DEFAULT_PALETTE

    texts = [f"line {i} words" for i in range(lines)]
    result = AlignResult(
        sync="word",
        lines=[LineTiming(1000 * i, 1000 * i + 800, t, 0.8) for i, t in enumerate(texts)],
        words_per_line=[
            [AlignedWord(1000 * i, 1000 * i + 400, "line", 0.8)]
            + [AlignedWord(1000 * i + 400, 1000 * i + 800, "words", 0.8)]
            for i in range(lines)
        ],
        quality_score=quality,
        windowed=True,
        quality_basis="anchors",
    )
    lyrics = LyricsText(line_texts=texts, full_text=" ".join(texts), source_id=7, had_synced=True)
    return document.build_document(
        job, lyrics, result, None, dict(DEFAULT_PALETTE), vocals_separated=True
    )


def _someone_waits_on_a_lock(conn) -> bool:
    return conn.execute(text("SELECT count(*) FROM pg_locks WHERE NOT granted")).scalar_one() > 0


def test_a_writer_that_waited_for_the_override_lock_judges_what_the_first_committed(db_session):
    from kashi_server.db.engine import SessionLocal, engine

    # Committed setup (the two writers are other sessions): an automatic
    # document, then the song's override; writer one's job applied it,
    # writer two's was queued before it existed.
    auto = _job(db_session, {})
    document.persist_processed_track(db_session, auto, _doc(auto, 40, 0.93))
    db_session.add(SongOverride(source_type="youtube", source_id=SOURCE, **OVERRIDE))
    db_session.flush()
    one_job = _job(db_session, overrides.apply(db_session, "youtube", SOURCE, {}))
    two_job = _job(db_session, {"separate": False})
    db_session.commit()

    one, two = SessionLocal(), SessionLocal()
    outcome: dict = {}

    def writer_two() -> None:
        try:
            outcome["etag"] = document.persist_processed_track(
                two, two_job, _doc(two_job, 45, 0.99)
            )
            two.commit()
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            outcome["error"] = exc
            two.rollback()

    try:
        built = document.persist_processed_track(one, one_job, _doc(one_job, 20, 0.6))  # G2
        thread = threading.Thread(target=writer_two)
        thread.start()
        deadline = time.monotonic() + 10
        with engine.connect() as watch:
            while not _someone_waits_on_a_lock(watch):
                assert thread.is_alive(), "writer two finished without waiting for writer one"
                assert time.monotonic() < deadline, "writer two never waited"
                time.sleep(0.02)
        one.commit()
        thread.join(10)
        assert not thread.is_alive()
        assert "error" not in outcome, outcome.get("error")
        assert outcome["etag"] == built  # G1 refused writer two
        stored = db_session.scalars(
            select(ProcessedTrack).execution_options(populate_existing=True)
        ).one()
        assert stored.etag == built
        assert sum(1 for line in stored.document["lines"] if line.get("words")) == 20
        applied = db_session.execute(text("SELECT applied_etag FROM song_overrides")).scalar_one()
        assert applied == built
    finally:
        one.close()
        two.close()
        db_session.rollback()
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE jobs, processed_tracks, song_overrides CASCADE"))
