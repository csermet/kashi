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
