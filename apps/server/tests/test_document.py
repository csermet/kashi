"""Document builder output must pass the REAL schema (the persist hard gate)."""

import uuid

import pytest

from kashi_server.db.models import Job
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.beats import Beats
from kashi_server.pipeline.document import (
    build_document,
    canonical_group,
    compute_etag,
    validate_document,
)
from kashi_server.pipeline.lrclib import LyricsText
from kashi_server.pipeline.palette import DEFAULT_PALETTE
from kashi_server.vdl_kit.errors import PipelineError


def _job(**hints_extra):
    hints = {"title": "Song", "artist": "Artist", "duration_ms": 200_000, **hints_extra}
    job = Job(source_type="youtube", source_id="docTest0001", pipeline_major=1, hints=hints)
    job.id = uuid.uuid4()
    return job


def _lyrics():
    return LyricsText(
        line_texts=["hello world", "again"],
        full_text="hello world again",
        source_id=7,
        had_synced=True,
    )


def _word_result():
    lines = [
        LineTiming(1000, 2000, "hello world", 0.8),
        LineTiming(3000, 3500, "again", 0.6),
    ]
    words = [
        [AlignedWord(1000, 1400, "hello", 0.7), AlignedWord(1500, 2000, "world", 0.9)],
        [AlignedWord(3000, 3500, "again", 0.6)],
    ]
    return AlignResult(sync="word", lines=lines, words_per_line=words, quality_score=0.72)


def _beats():
    return Beats(bpm=120.0, confidence=0.9, times_ms=[0, 500, 1000, 1500], downbeat_indices=[0])


def test_word_document_validates():
    doc = build_document(
        _job(album="LP"),
        _lyrics(),
        _word_result(),
        _beats(),
        dict(DEFAULT_PALETTE),
        vocals_separated=False,
    )
    validate_document(doc)  # must not raise
    assert doc["sync"] == "word"
    assert doc["lines"][0]["words"][1]["text"] == "world"
    assert doc["track"]["album"] == "LP"
    assert doc["alignment"]["speed_factor"] == 1.0
    assert doc["track"]["canonical_group"] == "artist|song|200"


def test_line_document_has_no_words_keys():
    result = AlignResult(
        sync="line",
        lines=[LineTiming(0, 900, "hello world", 0.4)],
        words_per_line=[],
        quality_score=0.4,
    )
    doc = build_document(
        _job(), _lyrics(), result, None, dict(DEFAULT_PALETTE), vocals_separated=False
    )
    validate_document(doc)
    assert all("words" not in line for line in doc["lines"])
    assert "beats" not in doc


def test_duration_falls_back_to_downloaded_audio():
    job = _job()
    job.hints = {"title": "Song", "artist": "Artist"}  # no duration hint
    doc = build_document(
        job,
        _lyrics(),
        _word_result(),
        None,
        dict(DEFAULT_PALETTE),
        vocals_separated=False,
        fallback_duration_ms=201_500,
    )
    validate_document(doc)
    assert doc["track"]["duration_ms"] == 201_500


def test_invalid_document_is_rejected_by_the_gate():
    doc = build_document(
        _job(), _lyrics(), _word_result(), None, dict(DEFAULT_PALETTE), vocals_separated=False
    )
    doc["lines"][0]["start_ms"] = -5  # violate ms >= 0
    with pytest.raises(PipelineError) as exc:
        validate_document(doc)
    assert exc.value.error_type == "alignment_failed"
    assert "start_ms" in exc.value.message


def test_etag_is_canonical_and_stable():
    doc_a = {"b": 1, "a": {"y": [1, 2], "x": "ü"}}
    doc_b = {"a": {"x": "ü", "y": [1, 2]}, "b": 1}  # same content, different order
    assert compute_etag(doc_a) == compute_etag(doc_b)
    assert len(compute_etag(doc_a)) == 32
    assert compute_etag({"b": 2}) != compute_etag({"b": 1})


def test_canonical_group_normalizes():
    assert canonical_group("Rick Astley - Topic", "Never  Gonna", 213.0) == (
        "rick astley|never gonna|215"
    )


def test_persist_upserts_and_updates_etag(db_session):
    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack
    from kashi_server.pipeline.document import persist_processed_track

    job = _job()
    job.hints = dict(job.hints)
    db_session.add(job)
    db_session.flush()

    doc = build_document(
        job, _lyrics(), _word_result(), None, dict(DEFAULT_PALETTE), vocals_separated=False
    )
    etag_one = persist_processed_track(db_session, job, doc)

    doc["alignment"]["quality_score"] = 0.9  # reprocess with a better score
    etag_two = persist_processed_track(db_session, job, doc)  # upsert, not a dup row

    rows = db_session.scalars(select(ProcessedTrack)).all()
    assert len(rows) == 1
    assert rows[0].etag == etag_two != etag_one
    assert rows[0].quality_score == pytest.approx(0.9)
    assert rows[0].document["alignment"]["quality_score"] == 0.9
