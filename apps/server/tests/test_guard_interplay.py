"""The persist guard where the version order (2.33.0, review step B.4a) meets the
song-override rules (C.1): the cases neither branch could test on its own.

Combined rule (pipeline/document.py persist_processed_track), first match wins:
force_persist -> store; the stored document comes from a NEWER pipeline version
-> keep it, also against a run that applied the song's override (the version
order sits outside G1 and G2); G1 the stored document was built under the
override and this run did not apply it -> keep, whatever the version; G2 this
run applied the override and the stored document was not built under it ->
store without the regression comparison; then the regression rules. B1 records
the applied etag only when an applying run is stored.

A worker of another version is simulated as in test_persist_guard: the
module's PIPELINE_VERSION / PIPELINE_MAJOR are patched for that write.
"""

import logging
import uuid

import pytest
from sqlalchemy import select, text

from kashi_server import overrides
from kashi_server.db.models import Job, ProcessedTrack, SongOverride
from kashi_server.pipeline import document
from kashi_server.pipeline.document import regression, version_key
from kashi_server.version import PIPELINE_MAJOR, PIPELINE_VERSION

SOURCE = "guardMix001"
OVERRIDE = {"speed_factor": 1.1504, "lyrics_text": "caller line one\ncaller line two"}

_KEY = version_key(PIPELINE_VERSION)
assert _KEY is not None and _KEY[1] > 0, PIPELINE_VERSION
_M, _MINOR, _ = _KEY
OLDER_MINOR = f"{_M}.{_MINOR - 1}.0"
OLDER_MAJOR = f"{_M - 1}.99.0"
NEWER_MINOR = f"{_M}.{_MINOR + 1}.0"
NEWER_MAJOR = f"{_M + 1}.0.0"


def _as_version(monkeypatch, version: str) -> None:
    monkeypatch.setattr(document, "PIPELINE_VERSION", version)
    monkeypatch.setattr(document, "PIPELINE_MAJOR", int(version.split(".")[0]))


def _job(db_session, options: dict) -> Job:
    job = Job(
        source_type="youtube",
        source_id=SOURCE,
        pipeline_major=PIPELINE_MAJOR,
        hints={"title": "Song", "artist": "Artist", "duration_ms": 200_000},
        options=options,
        status="completed",  # several jobs of one song must not trip uq_jobs_active
    )
    job.id = uuid.uuid4()
    db_session.add(job)
    db_session.flush()
    return job


def _enqueued(db_session, request: dict | None = None) -> Job:
    """A job whose options went through the enqueue merge (the song's row, if any)."""
    return _job(db_session, overrides.apply(db_session, "youtube", SOURCE, request or {}))


def _doc(job, lines: int, quality: float, basis: str = "anchors") -> dict:
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
        windowed=basis != "ctc-probs",
        quality_basis=basis,
    )
    lyrics = LyricsText(line_texts=texts, full_text=" ".join(texts), source_id=7, had_synced=True)
    return document.build_document(
        job, lyrics, result, None, dict(DEFAULT_PALETTE), vocals_separated=True
    )


def _persist(db_session, job, lines, quality, basis="anchors") -> None:
    document.persist_processed_track(db_session, job, _doc(job, lines, quality, basis))


def _stored(db_session) -> ProcessedTrack:
    return db_session.scalars(
        select(ProcessedTrack).execution_options(populate_existing=True)
    ).one()


def _worded(db_session) -> int:
    """Judged on the STORED document (persist returns the stored etag on a refusal)."""
    return sum(1 for line in _stored(db_session).document["lines"] if line.get("words"))


def _applied_etag(db_session) -> str | None:
    return db_session.execute(text("SELECT applied_etag FROM song_overrides")).scalar_one()


def _set_override(db_session) -> None:
    db_session.add(SongOverride(source_type="youtube", source_id=SOURCE, **OVERRIDE))
    db_session.flush()


@pytest.fixture
def speaking_logger(monkeypatch):
    # alembic's fileConfig (test_alembic_downgrade) disables loggers that exist
    # by then; this one has to speak for the assertions below.
    monkeypatch.setattr(logging.getLogger(document.__name__), "disabled", False)


@pytest.fixture
def auto_doc(db_session):
    """A good automatic document stored by the current pipeline, then the
    song's override is set (nothing built under it yet)."""
    _persist(db_session, _job(db_session, {}), 40, 0.93)
    _set_override(db_session)
    assert _applied_etag(db_session) is None
    return _stored(db_session).etag


@pytest.fixture
def override_build(db_session, auto_doc):
    """The song's first build under its override, by the current pipeline."""
    _persist(db_session, _enqueued(db_session), 20, 0.6)
    stored = _stored(db_session)
    assert _applied_etag(db_session) == stored.etag and _worded(db_session) == 20
    return stored.etag


# --- (a) version order outside G2 ---------------------------------------------


@pytest.mark.parametrize("older", [OLDER_MINOR, OLDER_MAJOR])
@pytest.mark.parametrize("built_under", [False, True], ids=["first-build", "rebuild"])
def test_an_older_worker_that_applied_the_override_cannot_replace_a_newer_document(
    db_session, monkeypatch, caplog, speaking_logger, auto_doc, older, built_under
):
    """first-build: G2 alone would let this run in (it applied the override and
    the stored document was not built under it). rebuild: the regression rules
    alone would (a better document). The version order refuses both, and B1
    records nothing."""
    if built_under:
        _persist(db_session, _enqueued(db_session), 20, 0.6)
    before = _stored(db_session)
    applied_before = _applied_etag(db_session)
    _as_version(monkeypatch, older)
    job = _enqueued(db_session)
    assert overrides.applied_fingerprint(job.options) is not None  # it applied it whole
    with caplog.at_level("WARNING", logger=document.__name__):
        _persist(db_session, job, 45, 0.99)
    after = _stored(db_session)
    assert (after.etag, after.pipeline_version) == (before.etag, PIPELINE_VERSION)
    assert _applied_etag(db_session) == applied_before  # no B1 on a refusal
    assert f"stored by pipeline {PIPELINE_VERSION}, this worker runs {older}" in caplog.text


# --- (b) G1 at the current and at a newer version ------------------------------


@pytest.mark.parametrize(
    "writer", [None, NEWER_MINOR, NEWER_MAJOR], ids=["current", "newer-minor", "newer-major"]
)
def test_a_run_without_the_override_cannot_replace_an_override_build_even_when_better(
    db_session, monkeypatch, caplog, speaking_logger, override_build, writer
):
    """45 lines at 0.99 over 20 at 0.6: the regression rules (and, for a newer
    writer, the version order) would let it in. G1 keeps the override build."""
    if writer is not None:
        _as_version(monkeypatch, writer)
    job = _job(db_session, {"separate": False})  # e.g. queued before the override existed
    with caplog.at_level("WARNING", logger=document.__name__):
        _persist(db_session, job, 45, 0.99)
    assert _stored(db_session).etag == override_build and _worded(db_session) == 20
    assert _applied_etag(db_session) == override_build
    assert "built under the song's override" in caplog.text


# --- (c) G2 whatever the quality basis ------------------------------------------


@pytest.mark.parametrize(
    ("lines", "quality", "basis"),
    [
        (40, 0.40, "anchors"),  # same basis, quality drop 0.53
        (12, 0.90, "anchors"),  # same basis, worded lines 40 -> 12
        (12, 0.05, "ctc-probs"),  # other basis (never compared), worded lines 40 -> 12
    ],
)
@pytest.mark.parametrize("stored_by", [None, OLDER_MINOR], ids=["stored-current", "stored-older"])
def test_a_current_run_with_the_override_replaces_a_document_not_built_under_it(
    db_session, monkeypatch, lines, quality, basis, stored_by
):
    if stored_by is not None:
        _as_version(monkeypatch, stored_by)
    _persist(db_session, _job(db_session, {}), 40, 0.93)
    _as_version(monkeypatch, PIPELINE_VERSION)
    _set_override(db_session)
    job = _enqueued(db_session)
    new = _doc(job, lines, quality, basis)
    assert regression(_stored(db_session).document, new) is not None  # the usual rules refuse
    etag = document.persist_processed_track(db_session, job, new)
    stored = _stored(db_session)
    assert (stored.etag, stored.pipeline_version) == (etag, PIPELINE_VERSION)
    assert _worded(db_session) == lines
    assert _applied_etag(db_session) == etag  # B1


# --- (d) force_persist wins in all three ----------------------------------------


@pytest.mark.parametrize("older", [OLDER_MINOR, OLDER_MAJOR])
def test_force_persist_lets_an_older_override_run_replace_a_newer_document(
    db_session, monkeypatch, auto_doc, older
):
    _as_version(monkeypatch, older)
    job = _enqueued(db_session, {"force_persist": True})
    assert overrides.applied_fingerprint(job.options) is not None
    _persist(db_session, job, 3, 0.05)
    stored = _stored(db_session)
    assert stored.pipeline_version == older and _worded(db_session) == 3
    assert _applied_etag(db_session) == stored.etag  # B1: it applied the override


@pytest.mark.parametrize("writer", [None, NEWER_MINOR], ids=["current", "newer-minor"])
def test_force_persist_lets_a_run_without_the_override_replace_an_override_build(
    db_session, monkeypatch, override_build, writer
):
    if writer is not None:
        _as_version(monkeypatch, writer)
    job = _job(db_session, {"force_persist": True})
    _persist(db_session, job, 3, 0.05)
    assert _stored(db_session).etag != override_build and _worded(db_session) == 3
    assert _applied_etag(db_session) == override_build  # no longer built under: G1 is off


def test_force_persist_and_g2_agree_on_a_first_override_build(db_session, auto_doc):
    """Both rules store it; each alone is enough (proved by mutation: G2 removed,
    then force removed from the regression block, then both)."""
    job = _enqueued(db_session, {"force_persist": True})
    new = _doc(job, 12, 0.05, "ctc-probs")
    assert regression(_stored(db_session).document, new) is not None
    etag = document.persist_processed_track(db_session, job, new)
    assert _stored(db_session).etag == etag and _worded(db_session) == 12
    assert _applied_etag(db_session) == etag
