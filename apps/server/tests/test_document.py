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
    # Lexical lines never carry the adlib flag (omitted, not false).
    assert all("adlib" not in line for line in doc["lines"])


def test_adlib_line_carries_the_flag_in_word_and_line_docs():
    """Faz 4: the client styles nonlexical hooks differently; the flag comes
    from the text (single predicate with line QA), so line-mode docs get it
    too."""
    lines = [
        LineTiming(1000, 2000, "hello world", 0.8),
        LineTiming(3000, 5000, "Oh-ooh, whoa-oh", 0.6),
    ]
    words = [
        [AlignedWord(1000, 1400, "hello", 0.7), AlignedWord(1500, 2000, "world", 0.9)],
        [AlignedWord(3000, 4000, "Oh-ooh,", 0.6), AlignedWord(4000, 5000, "whoa-oh", 0.6)],
    ]
    word_doc = build_document(
        _job(),
        _lyrics(),
        AlignResult(sync="word", lines=lines, words_per_line=words, quality_score=0.72),
        None,
        dict(DEFAULT_PALETTE),
        vocals_separated=False,
    )
    validate_document(word_doc)
    assert "adlib" not in word_doc["lines"][0]
    assert word_doc["lines"][1]["adlib"] is True

    line_doc = build_document(
        _job(),
        _lyrics(),
        AlignResult(sync="line", lines=lines, words_per_line=[], quality_score=0.4),
        None,
        dict(DEFAULT_PALETTE),
        vocals_separated=False,
    )
    validate_document(line_doc)
    assert line_doc["lines"][1]["adlib"] is True


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


def test_mixed_word_document_omits_words_for_qa_dropped_lines():
    lines = [
        LineTiming(1000, 2000, "hello world", 0.8),
        LineTiming(3000, 3500, "again", 0.0),
    ]
    words = [
        [AlignedWord(1000, 1400, "hello", 0.7), AlignedWord(1500, 2000, "world", 0.9)],
        [],  # line QA dropped this line's words
    ]
    result = AlignResult(sync="word", lines=lines, words_per_line=words, quality_score=0.7)
    doc = build_document(
        _job(), _lyrics(), result, None, dict(DEFAULT_PALETTE), vocals_separated=False
    )
    validate_document(doc)  # mixed documents are contract-valid
    assert "words" in doc["lines"][0]
    assert "words" not in doc["lines"][1]  # omitted, never an empty array


def test_word_document_without_any_words_is_rejected():
    doc = build_document(
        _job(), _lyrics(), _word_result(), None, dict(DEFAULT_PALETTE), vocals_separated=False
    )
    for line in doc["lines"]:
        line.pop("words", None)
    with pytest.raises(PipelineError) as exc:
        validate_document(doc)
    assert "no word timings" in exc.value.message


def test_line_document_carrying_words_is_rejected():
    doc = build_document(
        _job(), _lyrics(), _word_result(), None, dict(DEFAULT_PALETTE), vocals_separated=False
    )
    doc["sync"] = "line"  # words stayed — structural invariant must fire
    with pytest.raises(PipelineError) as exc:
        validate_document(doc)
    assert "sync=line" in exc.value.message


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


def test_quality_basis_names_what_the_number_measured():
    """Faz 6 P1: the number is unchanged; the document now says what it
    measured. anchors = line-anchor agreement (word feel NOT measured) —
    the honest label behind the "quality 1.0 but drifting words" class."""
    from dataclasses import replace

    common = dict(beats=_beats(), palette=dict(DEFAULT_PALETTE), vocals_separated=False)

    plain = build_document(_job(), _lyrics(), _word_result(), **common)
    assert plain["alignment"]["quality_basis"] == "ctc-probs"

    windowed = build_document(_job(), _lyrics(), replace(_word_result(), windowed=True), **common)
    assert windowed["alignment"]["quality_basis"] == "anchors"
    assert windowed["alignment"]["method"].endswith("+line-windowed")

    human_lyrics = replace(_lyrics(), source="lyricsfile")
    human = build_document(_job(), human_lyrics, _word_result(), **common)
    assert human["alignment"]["quality_basis"] == "human"
    assert human["alignment"]["method"] == "lrclib-lyricsfile/1.0"

    for doc in (plain, windowed, human):
        validate_document(doc)  # additive field passes the hard schema gate


def test_fx_energy_sections_serialize_additively():
    from kashi_server.pipeline.energy import Energy, Section
    from kashi_server.pipeline.semantics import FxTags, LineTag, WordTag

    common = dict(beats=_beats(), palette=dict(DEFAULT_PALETTE), vocals_separated=False)
    doc = build_document(
        _job(),
        _lyrics(),
        _word_result(),
        **common,
        fx=FxTags(
            lexicon_version="kashi-fx/1.0.0",
            engine="keywords",
            words=[WordTag(0, 1, "love", 0.6)],
            lines=[LineTag(1, "night")],
        ),
        energy=Energy(rate_hz=2, values=[10, 50, 90]),
        sections=[Section("high", 3000, 12000)],
    )
    assert doc["fx"]["lexicon"] == "kashi-fx/1.0.0"
    assert doc["fx"]["words"] == [{"line": 0, "word": 1, "tag": "love", "intensity": 0.6}]
    assert doc["fx"]["lines"] == [{"line": 1, "tag": "night"}]
    assert doc["energy"] == {"rate_hz": 2, "values": [10, 50, 90]}
    assert doc["sections"] == [{"type": "high", "start_ms": 3000, "end_ms": 12000}]
    validate_document(doc)  # hard schema gate accepts the additive blocks

    empty = build_document(
        _job(),
        _lyrics(),
        _word_result(),
        **common,
        fx=FxTags("kashi-fx/1.0.0", "keywords", [], []),
        energy=None,
        sections=[],
    )
    for key in ("fx", "energy", "sections"):
        assert key not in empty  # empty enrichment = absent, not null/[]
    validate_document(empty)


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


def test_qa_provenance_block_and_words_derived_flag():
    from kashi_server.pipeline.line_qa import LineQAOutcome

    result = _word_result()
    qa = LineQAOutcome(
        result=result,
        flagged=[1],
        offset_ms=-120,
        degraded_to_line=False,
        density_dropped=[],
        adlib_shifted=[],
        adlib_rederived=[0],
        trimmed_ends=3,
    )
    doc = build_document(
        _job(), _lyrics(), result, _beats(), DEFAULT_PALETTE, vocals_separated=False, qa=qa
    )
    validate_document(doc)
    assert doc["alignment"]["qa"] == {
        "flagged": 1,
        "density_dropped": 0,
        "adlib_shifted": 0,
        "adlib_rederived": 1,
        "offset_ms": -120,
        "trimmed_ends": 3,
    }
    assert doc["lines"][0]["words_derived"] is True  # rederived AND word-carrying
    assert "words_derived" not in doc["lines"][1]


def test_document_without_qa_omits_the_block_entirely():
    doc = build_document(
        _job(), _lyrics(), _word_result(), _beats(), DEFAULT_PALETTE, vocals_separated=False
    )
    validate_document(doc)
    assert "qa" not in doc["alignment"]
    assert all("words_derived" not in line for line in doc["lines"])
