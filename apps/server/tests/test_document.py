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


def test_measured_audio_duration_beats_the_client_hint():
    # Faz 8 P4: the hint used to win, which imported client noise into
    # track.duration_ms (47 archived documents carry an impossible value —
    # LMFAO "Hot Dog" stored 3279s for a 147s song). The worker ffprobes what
    # it actually aligned; that is the number the document must carry.
    job = _job()
    job.hints = {"title": "Song", "artist": "Artist", "duration_ms": 3_279_000}
    doc = build_document(
        job,
        _lyrics(),
        _word_result(),
        None,
        dict(DEFAULT_PALETTE),
        vocals_separated=False,
        fallback_duration_ms=147_000,
    )
    validate_document(doc)
    assert doc["track"]["duration_ms"] == 147_000
    # canonical_group buckets the duration in 5s steps — it must follow the
    # measured value too, or discovery groups on a number no audio has.
    assert doc["track"]["canonical_group"].endswith("|145")


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
    """Faz 6 P1: the document says what its number measured. Faz 8 P-B2: it
    READS that from the producer instead of inferring it from `windowed` —
    the inference was wrong for every line-mode document, which kept a
    prob-based score under an "anchors" label."""
    from dataclasses import replace

    common = dict(beats=_beats(), palette=dict(DEFAULT_PALETTE), vocals_separated=False)

    plain = build_document(_job(), _lyrics(), _word_result(), **common)
    assert plain["alignment"]["quality_basis"] == "ctc-probs"

    anchored = build_document(
        _job(), _lyrics(), replace(_word_result(), windowed=True, quality_basis="anchors"), **common
    )
    assert anchored["alignment"]["quality_basis"] == "anchors"
    # `method` still keys off `windowed` — that is a fact about how alignment
    # RAN, which is a different question from which formula scored it.
    assert anchored["alignment"]["method"].endswith("+line-windowed")

    # The defect this replaced: windowed alignment that fell back to a
    # prob-based score must NOT be stamped "anchors" any more.
    fallback = build_document(
        _job(),
        _lyrics(),
        replace(_word_result(), windowed=True, quality_basis="ctc-probs"),
        **common,
    )
    assert fallback["alignment"]["quality_basis"] == "ctc-probs"
    assert fallback["alignment"]["method"].endswith("+line-windowed")

    human_lyrics = replace(_lyrics(), source="lyricsfile")
    human = build_document(_job(), human_lyrics, _word_result(), **common)
    assert human["alignment"]["quality_basis"] == "human"
    assert human["alignment"]["method"] == "lrclib-lyricsfile/1.0"

    for doc in (plain, anchored, fallback, human):
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
        # Faz 8 B4: how many flagged lines the audio vouched for. Zero here —
        # this fixture predates the arbiter and none were rescued.
        "uncertain": 0,
        # Faz 9: lines that drifted BELOW the flag threshold and were moved
        # onto their anchor because the audio backed that position.
        "nudged": 0,
    }
    assert doc["lines"][0]["words_derived"] is True  # rederived AND word-carrying
    assert "words_derived" not in doc["lines"][1]


def test_rescued_lines_carry_the_uncertain_flag():
    """Faz 8 B4: a line the drift threshold flagged but the audio vouched for
    keeps its words and says so, instead of shipping as a silent hole."""
    from kashi_server.pipeline.line_qa import LineQAOutcome

    result = _word_result()
    qa = LineQAOutcome(
        result=result,
        flagged=[1],
        offset_ms=0,
        degraded_to_line=False,
        uncertain=[1],
    )
    doc = build_document(
        _job(), _lyrics(), result, _beats(), DEFAULT_PALETTE, vocals_separated=False, qa=qa
    )
    validate_document(doc)  # additive field passes the hard schema gate
    assert doc["alignment"]["qa"]["uncertain"] == 1
    assert doc["lines"][1]["uncertain"] is True
    assert doc["lines"][1]["words"], "the words survived — that is the point"
    assert "uncertain" not in doc["lines"][0]  # omitted when false, never written


def test_document_without_qa_omits_the_block_entirely():
    doc = build_document(
        _job(), _lyrics(), _word_result(), _beats(), DEFAULT_PALETTE, vocals_separated=False
    )
    validate_document(doc)
    assert "qa" not in doc["alignment"]
    assert all("words_derived" not in line for line in doc["lines"])


def test_fx_select_marker_rides_the_document():
    """The one bit that tells a client "the server already chose".

    Without it a newer overlay cannot distinguish a selected list from a
    legacy dense one, and would fire every tag on a line — on the existing
    archive that is two or three effects where there is one today.
    """
    from kashi_server.pipeline.semantics import FxTags, WordTag

    common = dict(beats=_beats(), palette=dict(DEFAULT_PALETTE), vocals_separated=False)
    selected = build_document(
        _job(),
        _lyrics(),
        _word_result(),
        **common,
        fx=FxTags(
            lexicon_version="kashi-fx/1.0.0",
            engine="keywords",
            words=[WordTag(0, 1, "love", 0.6)],
            lines=[],
            select="density/1.0",
        ),
        energy=None,
        sections=[],
    )
    assert selected["fx"]["select"] == "density/1.0"
    validate_document(selected)

    # An unselected block must NOT claim to be selected — that is what keeps
    # the client's legacy path reachable.
    legacy = build_document(
        _job(),
        _lyrics(),
        _word_result(),
        **common,
        fx=FxTags("kashi-fx/1.0.0", "keywords", [WordTag(0, 1, "love", 0.6)], []),
        energy=None,
        sections=[],
    )
    assert "select" not in legacy["fx"]
    validate_document(legacy)


def test_alignment_method_reports_the_model_that_actually_ran():
    """Faz 8 P-B1: the model used to be a literal in the document builder, so
    a swapped checkpoint would have gone into the archive still claiming
    mms-300m. A swap is now a certainty — the default weights are CC-BY-NC-4.0
    and cannot ship in a paid product — so the archive has to be able to say
    which documents came from which aligner."""
    from dataclasses import replace

    from kashi_server.pipeline.document import alignment_method

    default = _word_result()
    assert alignment_method(default) == "ctc-forced-aligner/mms-300m"
    assert (
        alignment_method(replace(default, windowed=True))
        == "ctc-forced-aligner/mms-300m+line-windowed"
    )
    # A different checkpoint must be visible, not silently absorbed.
    swapped = replace(default, model_name="Qwen/Qwen3-ForcedAligner-0.6B")
    assert alignment_method(swapped) == "ctc-forced-aligner/Qwen3-ForcedAligner-0.6B"
    assert alignment_method(swapped) != alignment_method(default)
    # A fine-tune of the same family keeps its own identity.
    ja = replace(default, model_name="NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn")
    assert ja.model_name not in alignment_method(default)
    assert alignment_method(ja) != alignment_method(default)


def test_swapped_aligner_reaches_the_document():
    from dataclasses import replace

    common = dict(beats=_beats(), palette=dict(DEFAULT_PALETTE), vocals_separated=False)
    swapped = replace(_word_result(), model_name="Qwen/Qwen3-ForcedAligner-0.6B")
    doc = build_document(_job(), _lyrics(), swapped, **common)
    validate_document(doc)
    assert "Qwen3-ForcedAligner-0.6B" in doc["alignment"]["method"]
    # The human path is about the LYRICS, not the aligner — it must not start
    # advertising a model that never ran.
    human = build_document(
        _job(), replace(_lyrics(), source="lyricsfile"), swapped, **common
    )
    assert human["alignment"]["method"] == "lrclib-lyricsfile/1.0"
