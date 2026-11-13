"""The persist guard under song overrides (C.1), against a real database.

Rule (pipeline/document.py persist_processed_track; notes c1-duzeltme):
G1 a document built under the song's override (its etag is the override's
   applied_etag) is kept against a run that did not apply that override whole;
G2 a run that applied it replaces a document NOT built under it without the
   major/regression checks;
B1 such a persist records its etag as applied_etag;
otherwise today's rules, and with no override row exactly today's rules.
"""

import uuid

import pytest
from sqlalchemy import select, text

from kashi_server import overrides
from kashi_server.db.models import Job, ProcessedTrack, SongOverride
from kashi_server.pipeline.document import persist_processed_track, regression
from kashi_server.version import PIPELINE_MAJOR

SOURCE = "guardOvr001"
OVERRIDE = {"speed_factor": 1.1289, "lyrics_text": "hello\nworld"}


def _job(db_session, options=None) -> Job:
    job = Job(
        source_type="youtube",
        source_id=SOURCE,
        pipeline_major=PIPELINE_MAJOR,
        hints={"title": "Song", "artist": "Artist", "duration_ms": 200_000},
        options=options if options is not None else {},
        # Not live: several jobs of one song in a test must not trip uq_jobs_active.
        status="completed",
    )
    job.id = uuid.uuid4()
    db_session.add(job)
    db_session.flush()
    return job


def _doc(job, lines: int, quality: float, basis: str = "anchors"):
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
    from kashi_server.pipeline.document import build_document
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
        windowed=basis != "ctc-probs",
        quality_basis=basis,
    )
    lyrics = LyricsText(line_texts=texts, full_text=" ".join(texts), source_id=7, had_synced=True)
    return build_document(job, lyrics, result, None, dict(DEFAULT_PALETTE), vocals_separated=True)


def _set_override(db_session, fields=None, **extra) -> SongOverride:
    db_session.execute(text("DELETE FROM song_overrides"))
    fields = OVERRIDE if fields is None else fields
    row = SongOverride(source_type="youtube", source_id=SOURCE, **fields, **extra)
    db_session.add(row)
    db_session.flush()
    return row


def _override_job(db_session, request=None, fields=None):
    """A job whose options came through the enqueue merge."""
    return _job(db_session, overrides.merge(request or {}, OVERRIDE if fields is None else fields))


def _stored(db_session) -> ProcessedTrack:
    return db_session.scalars(
        select(ProcessedTrack).execution_options(populate_existing=True)
    ).one()


def _applied_etag(db_session) -> str | None:
    return db_session.execute(text("SELECT applied_etag FROM song_overrides")).scalar_one()


@pytest.fixture
def auto_stored(db_session):
    """A good automatic document, persisted before any override existed."""
    job = _job(db_session)
    return persist_processed_track(db_session, job, _doc(job, 40, 0.93))


# --- G2 + B1: the first build under an override ------------------------------


def test_an_override_build_replaces_the_automatic_document_whatever_the_scores(
    db_session, auto_stored
):
    """Same basis, quality 0.93 -> 0.40 and 40 -> 12 worded lines: today's
    rules would keep the automatic document (the 2026-11-10 repairs needed
    force_persist for exactly this). The override is the owner's correction."""
    _set_override(db_session)
    job = _override_job(db_session)
    new = _doc(job, 12, 0.40)
    assert regression(_stored(db_session).document, new) is not None  # today: refused
    etag = persist_processed_track(db_session, job, new)
    assert etag != auto_stored
    assert _stored(db_session).etag == etag
    assert _applied_etag(db_session) == etag  # B1


def test_an_override_build_replaces_a_document_from_a_newer_or_other_basis(db_session, auto_stored):
    _set_override(db_session)
    job = _override_job(db_session)
    etag = persist_processed_track(db_session, job, _doc(job, 30, 0.2, basis="ctc-probs"))
    assert _stored(db_session).etag == etag


# --- G1: an override build is kept against runs that did not apply it ---------


@pytest.fixture
def override_stored(db_session, auto_stored):
    _set_override(db_session)
    job = _override_job(db_session)
    etag = persist_processed_track(db_session, job, _doc(job, 20, 0.6))
    assert _applied_etag(db_session) == etag
    return etag


def test_an_automatic_run_cannot_replace_an_override_build(db_session, override_stored):
    """The archive refresh of a job queued before the override, or a stale
    image: no marker. Even a 'better' document is refused."""
    job = _job(db_session, {"separate": False})
    kept = persist_processed_track(db_session, job, _doc(job, 45, 0.99))
    assert kept == override_stored
    assert _stored(db_session).etag == override_stored


def test_an_older_major_override_build_is_still_protected(db_session, override_stored):
    db_session.execute(text("UPDATE processed_tracks SET pipeline_major = pipeline_major - 1"))
    job = _job(db_session)
    assert persist_processed_track(db_session, job, _doc(job, 45, 0.99)) == override_stored


def test_a_run_whose_request_displaced_part_of_the_override_cannot_replace_it(
    db_session, override_stored
):
    job = _override_job(db_session, request={"speed_factor": 1.3})
    assert overrides.MARKER in job.options  # it applied the lyrics, not the clock
    assert persist_processed_track(db_session, job, _doc(job, 45, 0.99)) == override_stored


def test_a_run_of_older_override_content_cannot_replace_the_current_build(
    db_session, override_stored
):
    job = _override_job(db_session, fields={"speed_factor": 1.5})
    assert persist_processed_track(db_session, job, _doc(job, 45, 0.99)) == override_stored


def test_force_persist_still_wins_and_ends_the_build_under_status(db_session, override_stored):
    job = _job(db_session, {"force_persist": True})
    etag = persist_processed_track(db_session, job, _doc(job, 5, 0.1))
    assert _stored(db_session).etag == etag
    assert _applied_etag(db_session) == override_stored  # stale now: not "built under"
    # ...so the next override run is a first build again (G2), scores aside.
    job2 = _override_job(db_session)
    etag2 = persist_processed_track(db_session, job2, _doc(job2, 3, 0.05))
    assert _stored(db_session).etag == etag2


# --- a refresh under the same override compares like with like ---------------


def test_a_same_override_refresh_is_judged_by_the_usual_rules(db_session, override_stored):
    job = _override_job(db_session)
    kept = persist_processed_track(db_session, job, _doc(job, 2, 0.6))  # 20 -> 2 worded lines
    assert kept == override_stored
    assert _applied_etag(db_session) == override_stored
    better = _override_job(db_session)
    etag = persist_processed_track(db_session, better, _doc(better, 21, 0.62))
    assert _stored(db_session).etag == etag
    assert _applied_etag(db_session) == etag


# --- stale-image overwrite heals; changed or deleted overrides -------------------


def test_a_foreign_overwrite_ends_the_build_under_status(db_session, override_stored):
    """An old worker image upserts without the guard: the etag changes, so the
    next run under the override is a first build again."""
    db_session.execute(text("UPDATE processed_tracks SET etag = 'written-by-an-old-image'"))
    job = _override_job(db_session)
    etag = persist_processed_track(db_session, job, _doc(job, 3, 0.05))
    assert _stored(db_session).etag == etag


def test_after_a_delete_the_usual_rules_apply_again(db_session, override_stored):
    db_session.execute(text("DELETE FROM song_overrides"))
    job = _job(db_session)
    etag = persist_processed_track(db_session, job, _doc(job, 21, 0.62))
    assert _stored(db_session).etag == etag
    job2 = _job(db_session)
    kept = persist_processed_track(db_session, job2, _doc(job2, 2, 0.62))
    assert kept == etag  # and the worded-lines rule is back


def test_a_plain_only_row_acts_on_nothing(db_session, auto_stored):
    _set_override(db_session, fields={}, plain_only=True)
    job = _job(db_session, overrides.apply(db_session, "youtube", SOURCE, {}))
    assert overrides.MARKER not in job.options
    kept = persist_processed_track(db_session, job, _doc(job, 2, 0.9))
    assert kept == auto_stored  # the usual worded-lines rule, no first-build pass
    assert _applied_etag(db_session) is None


def test_an_override_row_for_another_song_changes_nothing(db_session, auto_stored):
    db_session.add(SongOverride(source_type="youtube", source_id="someOther01", speed_factor=1.2))
    db_session.flush()
    job = _job(db_session)
    assert persist_processed_track(db_session, job, _doc(job, 2, 0.9)) == auto_stored


# --- no override row: exactly today's decisions --------------------------------


@pytest.mark.parametrize(
    ("stored", "new", "force", "older_major"),
    [
        ((40, 0.93, "anchors"), (20, 0.93, "anchors"), False, False),  # exactly half: allowed
        ((40, 0.93, "anchors"), (19, 0.93, "anchors"), False, False),  # under half: refused
        ((40, 0.93, "anchors"), (40, 0.68, "anchors"), False, False),  # drop = 0.25: refused
        ((40, 0.93, "anchors"), (40, 0.6801, "anchors"), False, False),  # drop < 0.25
        ((40, 0.93, "ctc-probs"), (40, 0.10, "anchors"), False, False),  # other basis
        ((40, 0.93, "anchors"), (1, 0.0, "anchors"), True, False),  # force_persist
        ((40, 0.93, "anchors"), (1, 0.0, "anchors"), False, True),  # older major
    ],
)
def test_without_an_override_the_decisions_are_todays(db_session, stored, new, force, older_major):
    first = _job(db_session)
    stored_etag = persist_processed_track(db_session, first, _doc(first, *stored))
    if older_major:
        db_session.execute(text("UPDATE processed_tracks SET pipeline_major = pipeline_major - 1"))
    job = _job(db_session, {"force_persist": True} if force else {})
    new_doc = _doc(job, *new)
    today_refuses = (
        not force and not older_major and regression(_stored(db_session).document, new_doc)
    )
    etag = persist_processed_track(db_session, job, new_doc)
    if today_refuses:
        assert etag == stored_etag
    else:
        assert etag != stored_etag
    assert _stored(db_session).etag == etag
    assert db_session.execute(text("SELECT count(*) FROM song_overrides")).scalar_one() == 0
