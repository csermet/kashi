"""A reprocess must not replace a good document with a clearly worse one (Faz 9).

Field, 2026-10-16: lrclib's own record for "Never Gonna Give You Up" had
degraded to a single line ("probe"). The archive reprocess aligned it, and the
unconditional upsert replaced a 58-line document scored 0.93 with it.
"""

import uuid

import pytest

from kashi_server.db.models import Job
from kashi_server.pipeline.document import (
    QUALITY_DROP_LIMIT,
    regression,
    stale_writer,
    version_key,
)


def _doc(worded_lines: int, quality: float, basis: str = "anchors", plain_lines: int = 0) -> dict:
    lines = [{"text": "la la", "words": [{"text": "la"}, {"text": "la"}]}] * worded_lines
    lines = lines + [{"text": "plain"}] * plain_lines
    return {"lines": lines, "alignment": {"quality_score": quality, "quality_basis": basis}}


def test_the_field_case_is_refused():
    assert regression(_doc(58, 0.931), _doc(1, 0.0, "ctc-probs")) == "worded lines 58 -> 1"


def test_losing_exactly_half_the_worded_lines_is_allowed_more_is_not():
    assert regression(_doc(10, 0.9), _doc(5, 0.9)) is None
    assert regression(_doc(10, 0.9), _doc(4, 0.9)) == "worded lines 10 -> 4"


def test_a_quality_drop_at_the_limit_is_refused_below_it_allowed():
    assert regression(_doc(20, 0.9), _doc(20, round(0.9 - QUALITY_DROP_LIMIT, 4))) is not None
    assert regression(_doc(20, 0.9), _doc(20, 0.9 - QUALITY_DROP_LIMIT + 0.0001)) is None


def test_scores_from_different_formulas_are_not_compared():
    # INJI, 2026-10-16: 1.00 on CTC probabilities -> 0.76 on anchors, a fair reprocess.
    assert regression(_doc(35, 1.0, "ctc-probs"), _doc(38, 0.1, "anchors")) is None


def test_an_old_document_without_a_basis_is_judged_on_lines_only():
    stored = _doc(20, 0.9)
    del stored["alignment"]["quality_basis"]
    assert regression(stored, _doc(20, 0.1)) is None
    assert regression(stored, _doc(2, 0.9)) is not None


def test_a_line_synced_document_has_no_worded_lines_to_lose():
    assert regression(_doc(0, 0.5, plain_lines=30), _doc(0, 0.5, plain_lines=3)) is None


def test_nothing_stored_means_nothing_to_regress():
    assert regression(None, _doc(1, 0.0)) is None


# --- persist, against a real database ---------------------------------------


def _job(options=None):
    job = Job(
        source_type="youtube",
        source_id="guardTest01",
        pipeline_major=2,
        hints={"title": "Song", "artist": "Artist", "duration_ms": 200_000},
        options=options or {},
    )
    job.id = uuid.uuid4()
    return job


def _real_doc(job, lines: int, quality: float):
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
    from kashi_server.pipeline.document import build_document
    from kashi_server.pipeline.lrclib import LyricsText
    from kashi_server.pipeline.palette import DEFAULT_PALETTE

    texts = [f"line {i} words" for i in range(lines)]
    timings = [LineTiming(1000 * i, 1000 * i + 800, t, 0.8) for i, t in enumerate(texts)]
    words = [
        [AlignedWord(1000 * i, 1000 * i + 300, "line", 0.8)]
        + [AlignedWord(1000 * i + 300, 1000 * i + 500, str(i), 0.8)]
        + [AlignedWord(1000 * i + 500, 1000 * i + 800, "words", 0.8)]
        for i in range(lines)
    ]
    result = AlignResult(
        sync="word",
        lines=timings,
        words_per_line=words,
        quality_score=quality,
        windowed=True,
        quality_basis="anchors",
    )
    lyrics = LyricsText(line_texts=texts, full_text=" ".join(texts), source_id=7, had_synced=True)
    return build_document(job, lyrics, result, None, dict(DEFAULT_PALETTE), vocals_separated=True)


def _stored(db_session):
    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    rows = db_session.scalars(select(ProcessedTrack)).all()
    assert len(rows) == 1
    return rows[0]


@pytest.fixture
def stored_good(db_session):
    from kashi_server.pipeline.document import persist_processed_track

    job = _job()
    db_session.add(job)
    db_session.flush()
    etag = persist_processed_track(db_session, job, _real_doc(job, 40, 0.93))
    return job, etag


def test_persist_keeps_the_stored_document_against_a_regression(db_session, stored_good):
    from kashi_server.pipeline.document import persist_processed_track

    job, good_etag = stored_good
    returned = persist_processed_track(db_session, job, _real_doc(job, 1, 0.0))
    assert returned == good_etag
    stored = _stored(db_session)
    assert stored.etag == good_etag
    assert len(stored.document["lines"]) == 40


def test_persist_replaces_with_a_better_or_equal_document(db_session, stored_good):
    from kashi_server.pipeline.document import persist_processed_track

    job, good_etag = stored_good
    better = persist_processed_track(db_session, job, _real_doc(job, 42, 0.95))
    assert better != good_etag
    assert _stored(db_session).etag == better


def test_force_persist_accepts_a_regression_on_purpose(db_session, stored_good):
    from kashi_server.pipeline.document import persist_processed_track

    job, good_etag = stored_good
    job.options = {"force_persist": True}
    db_session.flush()
    etag = persist_processed_track(db_session, job, _real_doc(job, 1, 0.0))
    assert etag != good_etag
    assert len(_stored(db_session).document["lines"]) == 1


def test_a_document_from_an_older_major_is_always_replaced(db_session, stored_good):
    from sqlalchemy import text

    from kashi_server.pipeline.document import persist_processed_track

    job, good_etag = stored_good
    db_session.execute(text("UPDATE processed_tracks SET pipeline_major = pipeline_major - 1"))
    etag = persist_processed_track(db_session, job, _real_doc(job, 1, 0.0))
    assert etag != good_etag
    assert _stored(db_session).etag == etag


def test_force_persist_is_an_admin_option_only():
    from kashi_server.api.schemas import IngestOptions, ReprocessOptions

    assert "force_persist" not in IngestOptions.model_fields
    assert ReprocessOptions(force_persist=True).force_persist is True


# --- 2.33.0: the full version orders a persist, not only the major -------------
#
# Review 2026-11-13, reproduced on a throwaway database the same night: a worker
# running an older 2.x image (the GPU helper is rebuilt by hand each release)
# replaced a newer 2.x document whenever the quality rules let it, because only
# the MAJOR was compared.


def test_versions_order_as_numbers_not_strings():
    assert version_key("2.10.0") == (2, 10, 0)
    assert version_key("2.10.0") > version_key("2.9.1")  # as strings, "2.9.1" sorts after
    assert version_key("2.32.1") > version_key("2.32.0")


@pytest.mark.parametrize("text", ["", None, "2.32", "2.32.0.1", "2.32.0-rc1", "v2.32.0", "2.x.0"])
def test_a_string_that_is_not_a_version_reads_as_none(text):
    assert version_key(text) is None


@pytest.mark.parametrize(
    ("stored", "current", "refused"),
    [
        ("2.32.0", "2.31.0", True),  # the field case: a stale helper image
        ("2.32.1", "2.32.0", True),  # a patch is newer too
        ("2.10.0", "2.9.9", True),  # the string-compare trap
        ("3.0.0", "2.33.0", True),  # a newer major is newer
        ("2.32.0", "2.32.0", False),  # same pipeline: the regression rules decide
        ("2.31.0", "2.32.0", False),  # the normal refresh
        ("1.9.0", "2.33.0", False),
        ("garbage", "2.33.0", False),  # unreadable: never freeze a document on it
    ],
)
def test_only_an_older_worker_is_refused(stored, current, refused):
    reason = stale_writer(stored, current)
    assert (reason is not None) == refused
    if refused:
        assert reason == f"stored by pipeline {stored}, this worker runs {current}"


def _persist_as(monkeypatch, db_session, job, version: str, lines: int, quality: float) -> str:
    from kashi_server.pipeline import document

    monkeypatch.setattr(document, "PIPELINE_VERSION", version)
    monkeypatch.setattr(document, "PIPELINE_MAJOR", int(version.split(".")[0]))
    return document.persist_processed_track(db_session, job, _real_doc(job, lines, quality))


@pytest.fixture
def stored_2320(db_session, monkeypatch):
    job = _job()
    db_session.add(job)
    db_session.flush()
    etag = _persist_as(monkeypatch, db_session, job, "2.32.0", 40, 0.93)
    assert _stored(db_session).pipeline_version == "2.32.0"
    return job, etag


def test_a_stale_worker_keeps_its_hands_off_a_newer_document(db_session, monkeypatch, stored_2320):
    """Equal quality, same major: the major-only guard let this through."""
    job, etag = stored_2320
    assert _persist_as(monkeypatch, db_session, job, "2.31.0", 40, 0.93) == etag
    db_session.expire_all()
    stored = _stored(db_session)
    assert (stored.pipeline_version, stored.etag) == ("2.32.0", etag)
    assert stored.document["pipeline_version"] == "2.32.0"


def test_a_stale_patch_release_is_refused_too(db_session, monkeypatch, stored_2320):
    job, etag = stored_2320
    assert _persist_as(monkeypatch, db_session, job, "2.31.9", 41, 0.95) == etag


def test_force_persist_lets_an_older_pipeline_write_on_purpose(db_session, monkeypatch,
                                                               stored_2320):
    job, etag = stored_2320
    job.options = {"force_persist": True}
    db_session.flush()
    new_etag = _persist_as(monkeypatch, db_session, job, "2.31.0", 40, 0.93)
    db_session.expire_all()
    assert new_etag != etag and _stored(db_session).pipeline_version == "2.31.0"


@pytest.mark.parametrize(
    ("version", "lines", "quality", "basis"),
    [
        # The 2026-11-13 adversarial verification's overwrites (dogrulama/
        # test_dg1_stale_worker.py), each of which the major-only guard let in.
        ("2.29.0", 40, 0.93, None),  # equal quality
        ("2.29.0", 21, 0.6801, None),  # a drop just under the limit, 21 of 40 lines
        ("2.29.0", 40, 0.05, "ctc-probs"),  # any drop across quality bases
        ("1.9.0", 40, 0.93, None),  # an older MAJOR
    ],
)
def test_the_verifications_stale_overwrites_are_refused_and_logged(
        db_session, monkeypatch, caplog, version, lines, quality, basis):
    import logging

    from kashi_server.pipeline import document

    # alembic's fileConfig (test_alembic_downgrade) disables loggers that exist
    # by then; this one has to speak for the assertion below.
    monkeypatch.setattr(logging.getLogger(document.__name__), "disabled", False)
    job = _job()
    db_session.add(job)
    db_session.flush()
    stored_etag = _persist_as(monkeypatch, db_session, job, "2.31.0", 40, 0.93)
    monkeypatch.setattr(document, "PIPELINE_VERSION", version)
    monkeypatch.setattr(document, "PIPELINE_MAJOR", int(version.split(".")[0]))
    doc = _real_doc(job, lines, quality)
    if basis is not None:
        doc["alignment"]["quality_basis"] = basis
    with caplog.at_level("WARNING", logger="kashi_server.pipeline.document"):
        assert document.persist_processed_track(db_session, job, doc) == stored_etag
    db_session.expire_all()
    assert _stored(db_session).pipeline_version == "2.31.0"
    assert f"stored by pipeline 2.31.0, this worker runs {version}" in caplog.text


def test_a_newer_worker_replaces_and_an_equal_one_still_meets_the_quality_rules(
        db_session, monkeypatch, stored_2320):
    job, etag = stored_2320
    assert _persist_as(monkeypatch, db_session, job, "2.32.0", 1, 0.0) == etag  # regression
    newer = _persist_as(monkeypatch, db_session, job, "2.33.0", 40, 0.93)
    db_session.expire_all()
    assert newer != etag and _stored(db_session).pipeline_version == "2.33.0"
