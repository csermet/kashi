"""The refresh preview (worker/preview.py): the document a replayed reprocess would store, built
from the stored evidence with nothing written and no audio read, compared and judged. The song,
its evidence and the stand-in aligner are test_worker_evidence's."""

import json
import os
import signal
import types
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy import text as sql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_worker_evidence import LINES, SECONDS, _job, _run
from test_worker_evidence import audio as evidence_audio  # noqa: F401  (registers the fixture)

from kashi_server import queue
from kashi_server.config import settings
from kashi_server.db.models import Job, ProcessedTrack, SongOverride
from kashi_server.pipeline import document, evidence
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.level import Envelope
from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.version import PIPELINE_VERSION
from kashi_server.worker import preview
from kashi_server.worker import preview_rules as rules
from kashi_server.worker import process as wp

SONG = "evidVid01"
RECORD = {"id": 77, "trackName": "T", "artistName": "A", "duration": SECONDS,
          "plainLyrics": "\n".join(LINES),
          "syncedLyrics": "[00:01.00] hello world\n[00:09.00] again here"}
TABLES = ("jobs", "processed_tracks", "track_evidence", "song_overrides", "uploaded_audio")
GONE = 78  # a record lrclib no longer serves
ALBUM = {"source": "album_art", "primary": "#102030", "secondary": "#203040",
         "background": "#000000", "text": "#ffffff", "accent": "#405060"}


def _snapshot(s):
    """Row count and content hash of every table a preview could touch."""
    s.rollback()
    out = {}
    for table in TABLES:
        out[table] = s.execute(sql(
            f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
            f"FROM {table} t")).one()
    s.rollback()
    return out


def _preview(db_session, lyrics="ladder", **kw):
    """The song previewed through a read-only session of its own (the test's session writes)."""
    from kashi_server.db.engine import SessionLocal

    db_session.rollback()
    with SessionLocal() as s:
        row = preview.preview_song(preview.read_only(s), SONG, lyrics=lyrics, **kw)
        assert not s.in_transaction()  # no snapshot held from one song to the next
        return row


def _set_document(db_session, change):
    db_session.rollback()
    row = db_session.scalars(select(ProcessedTrack)).one()
    doc = json.loads(json.dumps(row.document))
    change(doc)
    db_session.execute(sql("UPDATE processed_tracks SET document = CAST(:d AS jsonb),"
                           " pipeline_version = :v"),
                       {"d": json.dumps(doc), "v": doc["pipeline_version"]})
    db_session.commit()
    return doc


@pytest.fixture
def audio(evidence_audio):  # noqa: F811  (the imported fixture, by its name)
    """test_worker_evidence's song: every read of its audio, by kind."""
    return evidence_audio


@pytest.fixture
def get_record(monkeypatch):
    """lrclib's record by id (the stored-lyrics mode), counted."""
    calls = []

    def fetch(record_id, base_url):
        calls.append(record_id)
        return None if record_id == GONE else dict(RECORD, id=record_id)

    monkeypatch.setattr(wp, "fetch_record", fetch)
    return calls


# --- the document is the replay's ------------------------------------------------------------


def test_the_preview_builds_what_a_replayed_reprocess_stores(db_session, audio, monkeypatch,
                                                            tmp_path):
    recorded = _run(db_session, monkeypatch, "record")
    monkeypatch.setattr(settings, "evidence_mode", "off")  # the preview replays whatever the mode
    audio.reads.clear()
    row = _preview(db_session, documents=tmp_path)
    assert audio.total() == 0, audio.reads
    built = json.loads((tmp_path / f"{SONG}.json").read_text())
    built.pop("generated_at")
    replayed = _run(db_session, monkeypatch, "replay")
    assert audio.total() == 0
    assert built == replayed == recorded
    assert row["verdict"] == rules.UNCHANGED and row["lyrics"] == "ladder"
    assert row["served"]["emissions"] >= 3 and row["served"]["envelope"] >= 1
    assert row["pipeline"][0] == row["pipeline"][1]


def test_the_stored_record_is_asked_by_id_without_the_ladder(db_session, audio, monkeypatch,
                                                            get_record):
    recorded = _run(db_session, monkeypatch, "record")

    def ladder(hints, base_url):
        raise AssertionError("the ladder ran")

    monkeypatch.setattr(wp, "fetch_lyrics", ladder)
    row = _preview(db_session, lyrics="stored")
    assert get_record == [77]
    assert row["lyrics"] == "record 77" and row["verdict"] == rules.UNCHANGED
    assert row["sheet"] == [] and row["moved"]["over_100"] == 0
    assert recorded["alignment"]["lyrics_source_id"] == 77


def test_an_override_s_lyrics_stay_the_override_s(db_session, audio, monkeypatch, get_record):
    _run(db_session, monkeypatch, "record")
    db_session.add(SongOverride(source_type="youtube", source_id=SONG,
                                lyrics_text="\n".join(LINES)))
    db_session.commit()
    row = _preview(db_session, lyrics="stored")
    assert row["lyrics"] == "override" and get_record == []
    assert row["verdict"] == rules.SHEET and "lyrics source lrclib -> caller" in row["detail"]


def test_a_sheet_offset_without_the_override_s_lyrics_takes_the_ladder(db_session, audio,
                                                                       monkeypatch, get_record):
    _run(db_session, monkeypatch, "record")
    db_session.add(SongOverride(source_type="youtube", source_id=SONG, offset_ms=250))
    db_session.commit()
    row = _preview(db_session, lyrics="stored")
    assert row["lyrics"] == "ladder" and get_record == []


def test_a_record_the_stored_document_names_that_is_gone_fails(db_session, audio, monkeypatch,
                                                               get_record):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, lambda d: d["alignment"].update(lyrics_source_id=GONE))
    row = _preview(db_session, lyrics="stored")
    assert get_record == [GONE]
    assert (row["verdict"], row["detail"]) == (
        rules.FAILED, "the stored document's lyrics record is gone or unusable")


def test_another_error_in_stored_mode_is_not_blamed_on_the_record(db_session, audio, monkeypatch,
                                                                  get_record):
    _run(db_session, monkeypatch, "record")

    def other(doc):
        raise PipelineError("other", "something else went wrong")

    monkeypatch.setattr(wp, "validate_document", other)
    row = _preview(db_session, lyrics="stored")
    assert (row["verdict"], row["detail"]) == (rules.FULL_JOB, "pipeline error other")


# --- nothing is written -------------------------------------------------------------------------


def test_a_preview_cannot_write(db_session, audio, monkeypatch, tmp_path):
    _run(db_session, monkeypatch, "record")
    before = _snapshot(db_session)
    audio.reads.clear()

    def refuse(*_a, **_k):
        raise AssertionError("a preview wrote")

    # A session's flush that has something to write (`_flush`; `flush` itself runs on every
    # autoflush and returns when there is nothing), its commit, and every store entry point.
    for owner, name in ((Session, "_flush"), (Session, "commit"), (Session, "add"),
                        (Session, "delete"), (Session, "merge"),
                        (wp, "persist_processed_track"), (document, "persist_processed_track"),
                        (wp, "_store_evidence"), (evidence.DbStore, "save"),
                        (evidence.DirStore, "save"), (queue, "enqueue_reprocess"),
                        (queue, "enqueue"), (queue, "mark_failed"), (queue, "retry")):
        monkeypatch.setattr(owner, name, refuse)
    statements = []

    def only_reads(_conn, _cursor, statement, *_a):
        statements.append(statement)
        head = statement.lstrip().upper()
        assert head.startswith(("SELECT", "SHOW", "SET TRANSACTION READ ONLY")), statement[:60]

    from kashi_server.db.engine import engine

    event.listen(engine, "before_cursor_execute", only_reads)
    try:
        monkeypatch.setattr(settings, "lrclib_request_gap_s", 0.0)
        out = tmp_path / "rows.jsonl"
        assert preview.main(["--source-id", SONG, "--out", str(out), "--lyrics", "ladder"]) == 0
        row = _preview(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", only_reads)
    assert row["verdict"] == rules.UNCHANGED, row
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert "segment" in lines[0] and lines[1]["verdict"] == rules.UNCHANGED
    assert any(s.startswith("SET TRANSACTION READ ONLY") for s in statements)
    assert audio.total() == 0, audio.reads
    assert settings.lrclib_request_gap_s == 0.5  # batch work: the top of lrclib's asked gap
    assert _snapshot(db_session) == before


def test_the_read_session_refuses_writes(db_session):
    from kashi_server.db.engine import SessionLocal

    with SessionLocal() as s:
        preview.read_only(s)
        with pytest.raises(DBAPIError, match="read-only transaction"):
            s.execute(sql("DELETE FROM jobs"))
        s.rollback()
        with pytest.raises(DBAPIError, match="read-only transaction"):  # every transaction
            s.execute(sql("DELETE FROM jobs"))


def test_the_job_s_session_reaches_no_database():
    job = Job(id=uuid.uuid4(), source_type="youtube", source_id=SONG, pipeline_major=2, hints={},
              options={}, attempts=1, max_attempts=1, status="downloading", deferrals=0)
    s = preview.PreviewSession(job.id)
    assert queue.heartbeat(s, job.id) is False  # type: ignore[arg-type]
    assert queue.heartbeat(s, job.id, 1) is False  # type: ignore[arg-type]
    queue.set_status(s, job, "aligning")  # type: ignore[arg-type]
    queue.mark_completed(s, job)  # type: ignore[arg-type]
    s.commit()
    with pytest.raises(preview.PreviewWriteError):
        queue.heartbeat(s, uuid.uuid4())  # type: ignore[arg-type]  (another job's row)
    with pytest.raises(preview.PreviewWriteError):
        s.execute(sql("UPDATE jobs SET status = 'failed' WHERE id = :id"), {"id": job.id})
    with pytest.raises(preview.PreviewWriteError):
        s.add(job)
    with pytest.raises(preview.PreviewWriteError):  # persist's first read, under its lock
        document._lock_override(s, job)  # type: ignore[arg-type]


# --- what cannot be served is a full job, never a download ------------------------------------


def test_a_song_without_evidence_needs_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "off")
    audio.reads.clear()
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.FULL_JOB, "no evidence stored")
    assert audio.total() == 0


def test_a_key_that_differs_needs_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    monkeypatch.setattr(settings, "structure_sections", False)
    audio.reads.clear()
    row = _preview(db_session)
    assert row["verdict"] == rules.FULL_JOB and row["detail"].startswith("key differs at")
    assert audio.total() == 0


def test_a_window_cut_differently_needs_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    audio.slices = [(0, 16_000 * 9 + 1), (16_000 * 8, 16_000 * SECONDS)]
    audio.reads.clear()
    row = _preview(db_session)
    assert row["verdict"] == rules.FULL_JOB and row["detail"].startswith("no emissions for")
    assert audio.total() == 0


def test_a_nightcore_document_needs_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, lambda d: d["alignment"].update(speed_factor=1.25))
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.FULL_JOB, "a nightcore clock")


def test_a_player_length_far_from_the_record_needs_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    _job(db_session, hints={"duration_ms": SECONDS * 1000 + 3_001})  # the latest job's hints
    row = _preview(db_session)
    assert row["verdict"] == rules.FULL_JOB and "may have changed" in row["detail"]
    db_session.execute(sql("UPDATE jobs SET status = 'failed'"))  # no live job: a new one
    db_session.commit()
    _job(db_session, hints={"duration_ms": SECONDS * 1000 + 3_000})
    assert _preview(db_session)["verdict"] == rules.UNCHANGED


def test_the_by_ear_rung_needs_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")

    def miss(hints, base_url):
        raise PipelineError("lyrics_not_found", "no record")

    monkeypatch.setattr(wp, "fetch_lyrics", miss)
    monkeypatch.setattr(wp, "search_candidates", lambda query, base_url: [RECORD])
    audio.reads.clear()
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.FULL_JOB, "the by-ear rung")
    assert audio.total() == 0


def test_a_replay_fault_needs_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    audio.fail_align = True
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.FULL_JOB, "replay fault RuntimeError")


def test_a_permanent_error_in_the_replay_needs_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")

    def invalid(doc):
        raise PipelineError("alignment_failed", "document failed schema validation")

    monkeypatch.setattr(wp, "validate_document", invalid)
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.FULL_JOB, "pipeline error alignment_failed")


# --- failures ------------------------------------------------------------------------------------


def test_weather_is_a_failure_not_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    audio.weather = True
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.FAILED, "transient network")


def test_lrclib_unavailable_is_a_failure_and_nothing_is_deferred(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    before = _snapshot(db_session)

    def busy(hints, base_url):
        raise PipelineError("lrclib_unavailable", "503")

    monkeypatch.setattr(wp, "fetch_lyrics", busy)
    row = _preview(db_session)
    assert row["verdict"] == rules.FAILED and "would be deferred" in row["detail"]
    assert _snapshot(db_session) == before


def test_no_document_or_no_job_history_fails(db_session, audio, monkeypatch):
    row = preview.preview_song(db_session, "neverSeen01")
    assert (row["verdict"], row["detail"]) == (rules.FAILED, "no stored document")
    _run(db_session, monkeypatch, "record")
    db_session.execute(sql("UPDATE processed_tracks SET job_id = NULL"))
    db_session.execute(sql("DELETE FROM jobs"))
    db_session.commit()
    row = _preview(db_session)
    assert row["verdict"] == rules.FAILED and row["detail"].startswith("no job history")


# --- the comparison and the verdict, wired -------------------------------------------------------


def test_a_moved_line_is_reported_and_refreshed(db_session, audio, monkeypatch, ladder_calls):
    _run(db_session, monkeypatch, "record")
    ladder_calls.clear()

    def older(doc):
        doc["pipeline_version"] = "2.34.0"
        for word in doc["lines"][1]["words"]:
            word["start_ms"] -= 700
            word["end_ms"] -= 700
        doc["lines"][1]["start_ms"] -= 700

    old = _set_document(db_session, older)
    row = _preview(db_session)
    assert row["verdict"] == rules.REFRESH and row["detail"] == ""
    assert len(ladder_calls) == 1 and "ladder" not in row  # a ladder-mode refresh: one pass
    assert row["pipeline"] == ["2.34.0", PIPELINE_VERSION] and row["old_pipeline"] == "2.34.0"
    assert row["moved"]["over_500"] == 1 and row["moved"]["largest"]["d_ms"] == 700
    (moved,) = row["moved_lines"]
    assert moved["line"] == 1 and moved["old_ms"] == old["lines"][1]["start_ms"]
    assert "lead_old_ms" in moved and "lead_new_ms" in moved
    assert "lines" in row["differs"] and "pipeline_version" not in row["differs"]


def test_a_line_that_loses_its_words_is_vetoed(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    stand_in = wp.align

    def drop_second(*a, **kw):
        result = stand_in(*a, **kw)
        return AlignResult(sync="word", lines=result.lines,
                           words_per_line=[result.words_per_line[0], []],
                           quality_score=result.quality_score, windowed=True,
                           model_name=result.model_name)

    monkeypatch.setattr(wp, "align", drop_second)
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.VETOED, "veto: W3")
    assert row["w3"] == [1]


def test_the_persist_guard_is_asked_and_named(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, lambda d: d.update(pipeline_version="9.0.0"))
    row = _preview(db_session)
    assert row["verdict"] == rules.GUARDED and row["detail"].startswith("guard: stale: stored by")


def test_another_record_is_another_sheet_in_ladder_mode(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, lambda d: d["alignment"].update(lyrics_source_id=76))
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.SHEET, "lyrics record 76 -> 77")
    assert "moved" not in row


def test_rows_carry_no_lyric_text(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    text = json.dumps(_preview(db_session))
    assert not any(word in text for line in LINES for word in line.split())


# --- the persist guard's one copy ----------------------------------------------------------------


def test_the_guard_answer_is_the_one_persist_acts_on(db_session, audio, monkeypatch):
    recorded = _run(db_session, monkeypatch, "record")
    db_session.rollback()
    stored = db_session.execute(sql("SELECT document, etag, pipeline_version, pipeline_major "
                                    "FROM processed_tracks")).one()
    job = db_session.scalars(select(Job)).first()
    assert document.stored_document_kept(stored, None, job, recorded) is None
    one_line = dict(recorded, lines=recorded["lines"][:1])
    assert document.stored_document_kept(stored, None, job, one_line) is None  # 2 -> 1: half
    no_words = dict(recorded, lines=[{k: v for k, v in line.items() if k != "words"}
                                     for line in recorded["lines"]])
    assert document.stored_document_kept(stored, None, job, no_words) == (
        "regression", "worded lines 2 -> 0")
    forced = Job(options={"force_persist": True})
    assert document.stored_document_kept(stored, None, forced, no_words) is None


class _GuardSession:
    """persist_processed_track's two reads (the override row, the stored row) answered from memory;
    any further statement is recorded as a write."""

    def __init__(self, override, existing):
        self.answers = [override, existing]
        self.writes = []

    def execute(self, statement, params=None):
        if self.answers:
            row = self.answers.pop(0)
        else:
            self.writes.append(str(statement))
            row = None
        return types.SimpleNamespace(first=lambda: row)


@pytest.mark.parametrize("kind", ["stale", "override", "regression"])
def test_each_guard_keeps_counts_and_says_its_own(kind, caplog, monkeypatch):
    """The extracted decision drives persist's own counters and log lines, one per guard."""
    import logging

    from prometheus_client import REGISTRY

    # alembic's fileConfig (test_alembic_downgrade) disables loggers that exist by then.
    monkeypatch.setattr(logging.getLogger(document.__name__), "disabled", False)
    from test_persist_guard import _job as guard_job
    from test_persist_guard import _real_doc

    job = guard_job()
    good, worse = _real_doc(job, 40, 0.93), _real_doc(job, 4, 0.93)
    existing = types.SimpleNamespace(document=good, etag="e1", pipeline_version=PIPELINE_VERSION,
                                     pipeline_major=2)
    override = None
    if kind == "stale":
        existing.pipeline_version = "9.9.9"
    if kind == "override":
        override = types.SimpleNamespace(lrclib_id=5, lyrics_text=None, speed_factor=None,
                                         offset_ms=None, applied_etag="e1")
    names = {"stale": "kashi_persist_stale_writes_kept_total",
             "override": "kashi_persist_override_builds_kept_total",
             "regression": "kashi_persist_regressions_kept_total"}
    before = {k: REGISTRY.get_sample_value(n) or 0.0 for k, n in names.items()}
    session = _GuardSession(override, existing)
    with caplog.at_level("WARNING", logger="kashi_server.pipeline.document"):
        stored = document.persist_processed_track(session, job,  # type: ignore[arg-type]
                                                  worse if kind == "regression" else good)
    assert stored == "e1" and session.writes == []
    after = {k: REGISTRY.get_sample_value(n) or 0.0 for k, n in names.items()}
    assert {k: after[k] - before[k] for k in names} == {k: float(k == kind) for k in names}
    said = {"stale": "(a stale worker image?)", "override": "built under the song's override",
            "regression": "the new one regresses it"}
    assert said[kind] in caplog.text
    assert sum(text in caplog.text for text in said.values()) == 1
    kept = document.stored_document_kept(existing, override, job,
                                         worse if kind == "regression" else good)
    assert kept is not None and kept[0] == kind


# --- the command ---------------------------------------------------------------------------------


def test_the_command_previews_the_songs_with_evidence(db_session, audio, monkeypatch, tmp_path,
                                                      capsys):
    _run(db_session, monkeypatch, "record")
    monkeypatch.setattr(settings, "evidence_mode", "off")
    _run(db_session, monkeypatch, "off")  # its evidence stays
    db_session.execute(sql("INSERT INTO processed_tracks (source_type, source_id, "
                           "pipeline_version, pipeline_major, sync, quality_score, document, etag)"
                           " SELECT source_type, 'noEvidence1', pipeline_version, pipeline_major,"
                           " sync, quality_score, document, 'x' FROM processed_tracks"))
    db_session.commit()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "lrclib_request_gap_s", 0.0)
    assert preview.main(["--lyrics", "ladder"]) == 0
    printed = capsys.readouterr().out
    assert f"{SONG}: unchanged" in printed and "noEvidence1" not in printed.split("summary")[0]
    summary = json.loads(printed.rsplit("preview summary ", 1)[1])
    assert summary["verdicts"][rules.UNCHANGED] == 1 and summary["unchanged_ids"] == [SONG]
    assert summary["refresh_ids"] == [] and summary["without_evidence"] == 1
    assert summary["next_after"] == SONG and summary["this_run"]["exit_code"] == 0
    assert summary["segments"] == 1 and summary["this_run"]["peak_rss_mb"] > 0
    replay = summary["replay"]
    assert replay["evidence_mode"] == "replay" and replay["this_environment_evidence_mode"] == "off"
    assert replay["key"] == evidence.expected_key(
        settings, separate_first=settings.separation_mode == "always")
    assert "note: EVIDENCE_MODE here is 'off'" in printed
    (out,) = tmp_path.glob("preview-*.jsonl")
    lines = out.read_text().splitlines()
    assert "segment" in json.loads(lines[0]) and json.loads(lines[1])["source_id"] == SONG
    assert "summary" in json.loads(lines[-1])
    assert list(tmp_path.glob(f"{preview.DIR_PREFIX}*")) == []  # its scratch is gone
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(ProcessedTrack)) == 2


def test_candidates_order_cursor_and_limit(db_session, audio, monkeypatch):
    for song in ("songA0000001", "songB0000001", "songC0000001"):
        monkeypatch.setattr(settings, "evidence_mode", "record")
        job = _job(db_session, source_id=song)
        wp.process_job(db_session, job)
    db_session.rollback()
    assert preview.candidates(db_session) == ["songA0000001", "songB0000001", "songC0000001"]
    assert preview.candidates(db_session, after="songA0000001") == ["songB0000001",
                                                                   "songC0000001"]
    assert preview.candidates(db_session, limit=1, after="songA0000001") == ["songB0000001"]
    db_session.execute(sql("DELETE FROM track_evidence WHERE source_id = 'songB0000001'"))
    db_session.commit()
    assert preview.candidates(db_session) == ["songA0000001", "songC0000001"]
    assert preview.without_evidence(db_session) == 1


def test_the_summary_lists_what_to_refresh_and_why_not_the_rest():
    rows = [
        {"source_id": "a", "verdict": rules.REFRESH, "detail": "", "refresh_confirmed": True},
        {"source_id": "b", "verdict": rules.VETOED, "detail": "veto: W1, W3"},
        {"source_id": "c", "verdict": rules.GUARDED, "detail": "guard: stale: x"},
        {"source_id": "d", "verdict": rules.FULL_JOB, "detail": "no evidence stored"},
        {"source_id": "e", "verdict": rules.REFRESH, "detail": "", "refresh_confirmed": True},
        {"source_id": "f", "verdict": rules.UNCHANGED, "detail": ""},
    ]
    summary = preview.summarize(rows)
    assert summary["refresh_ids"] == ["a", "e"] and summary["unchanged_ids"] == ["f"]
    assert summary["verdicts"] == {rules.REFRESH: 2, rules.UNCHANGED: 1, rules.REVIEW: 0,
                                   "keep": 2, rules.SHEET: 0, rules.FULL_JOB: 1, rules.FAILED: 0}
    assert summary["vetoes"] == {"W1": 1, "W3": 1}
    assert summary["keep"] == {"b": {"detail": "veto: W1, W3", "vetoes": [], "reviews": []},
                               "c": {"detail": "guard: stale: x", "vetoes": [], "reviews": []}}
    assert summary["needs_full_job"] == {"d": "no evidence stored"}
    assert summary["next_after"] == "f"
    assert summary["same_sheet_totals"] == {"songs": 0, "old": {}, "new": {},
                                            "moved_lines": {"over_100": 0, "over_500": 0,
                                                            "over_1500": 0}}


def test_the_summary_adds_up_the_songs_judged_on_the_same_sheet():
    old = {"lines": 3, "dwell_under_120": 2}
    rows = [
        {"source_id": "a", "verdict": rules.REFRESH, "old": old, "new": {"lines": 3,
         "dwell_under_120": 1}, "moved": {"over_100": 2, "over_500": 1, "over_1500": 0}},
        {"source_id": "b", "verdict": rules.SHEET, "old": old, "new": {"lines": 4,
         "dwell_under_120": 9}},
        {"source_id": "c", "verdict": rules.VETOED, "old": old, "new": {"lines": 3,
         "dwell_under_120": 5}, "moved": {"over_100": 1, "over_500": 1, "over_1500": 1}},
    ]
    totals = preview.summarize(rows)["same_sheet_totals"]
    assert totals == {"songs": 2, "old": {"lines": 6, "dwell_under_120": 4},
                      "new": {"lines": 6, "dwell_under_120": 6},
                      "moved_lines": {"over_100": 3, "over_500": 2, "over_1500": 1}}


# --- round 2: the ladder confirms a stored-mode refresh ------------------------------------------


def _older(doc, line=1, by=700):
    """The stored document as an older build left it: one line `by` ms earlier."""
    doc["pipeline_version"] = "2.34.0"
    for word in doc["lines"][line].get("words") or []:
        word["start_ms"] -= by
        word["end_ms"] -= by
    doc["lines"][line]["start_ms"] -= by


@pytest.fixture
def ladder_calls(monkeypatch):
    """Every ladder lookup, counted; the record it finds is test_worker_evidence's (77)."""
    calls = []
    found = wp.fetch_lyrics

    def fetch(hints, base_url):
        calls.append(hints.get("title"))
        return found(hints, base_url=base_url)  # the worker's own call shape

    monkeypatch.setattr(wp, "fetch_lyrics", fetch)
    return calls


def test_a_stored_mode_refresh_is_confirmed_by_the_ladder(db_session, audio, monkeypatch,
                                                         get_record, ladder_calls):
    _run(db_session, monkeypatch, "record")
    ladder_calls.clear()
    _set_document(db_session, _older)
    row = _preview(db_session, lyrics="stored")
    assert get_record == [77] and len(ladder_calls) == 1
    assert row["verdict"] == rules.REFRESH and row["lyrics"] == "record 77"
    assert row["ladder"] == {"verdict": rules.REFRESH, "detail": "", "lyrics_record": 77,
                             "confirmed": True}


def test_only_a_refresh_asks_the_ladder(db_session, audio, monkeypatch, get_record, ladder_calls):
    _run(db_session, monkeypatch, "record")
    ladder_calls.clear()
    row = _preview(db_session, lyrics="stored")
    assert row["verdict"] == rules.UNCHANGED and ladder_calls == [] and "ladder" not in row


def test_the_ladder_choosing_another_record_is_a_changed_sheet(db_session, audio, monkeypatch,
                                                               get_record, ladder_calls):
    _run(db_session, monkeypatch, "record")

    def older_on_76(doc):
        _older(doc)
        doc["alignment"]["lyrics_source_id"] = 76

    _set_document(db_session, older_on_76)
    row = _preview(db_session, lyrics="stored")
    assert get_record == [76]
    assert (row["verdict"], row["detail"]) == (rules.SHEET, "ladder: lyrics record 76 -> 77")
    assert row["ladder"]["confirmed"] is False and row["ladder"]["lyrics_record"] == 77


def test_a_ladder_that_needs_the_by_ear_rung_is_a_full_job(db_session, audio, monkeypatch,
                                                           get_record):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, _older)

    def miss(hints, base_url):
        raise PipelineError("lyrics_not_found", "no record")

    monkeypatch.setattr(wp, "fetch_lyrics", miss)
    monkeypatch.setattr(wp, "search_candidates", lambda query, base_url: [RECORD])
    row = _preview(db_session, lyrics="stored")
    assert (row["verdict"], row["detail"]) == (rules.FULL_JOB, "ladder: the by-ear rung")


def test_ladder_weather_fails_the_confirmation(db_session, audio, monkeypatch, get_record):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, _older)

    def busy(hints, base_url):
        raise PipelineError("lrclib_unavailable", "503")

    monkeypatch.setattr(wp, "fetch_lyrics", busy)
    row = _preview(db_session, lyrics="stored")
    assert row["verdict"] == rules.FAILED and row["detail"].startswith("ladder: lrclib unavailable")


def test_a_ladder_building_another_document_on_the_same_record_is_review(db_session, audio,
                                                                        monkeypatch, get_record):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, _older)
    palettes = iter([dict(ALBUM), dict(ALBUM, primary="#ffffff")])  # the two passes differ
    monkeypatch.setattr(wp, "extract_palette", lambda url: next(palettes))
    row = _preview(db_session, lyrics="stored")
    assert row["verdict"] == rules.REVIEW and row["ladder"]["confirmed"] is False
    assert row["detail"] == "the ladder builds another document than the stored record does"


# --- round 2: W1, W2, W4 and review end to end ---------------------------------------------------


def _rebuild_with(monkeypatch, change):
    """The stand-in aligner's result, changed by `change(words_per_line)` (in a replay only)."""
    stand_in = wp.align

    def changed(*a, **kw):
        from kashi_server.pipeline import evidence as ev

        result = stand_in(*a, **kw)
        if not ev.replaying():
            return result
        words = change([list(chunk) for chunk in result.words_per_line])
        lines = [LineTiming(chunk[0].start_ms, chunk[-1].end_ms, line.text, line.score)
                 for chunk, line in zip(words, result.lines, strict=True)]
        return AlignResult(sync="word", lines=lines, words_per_line=words,
                           quality_score=result.quality_score, windowed=True,
                           model_name=result.model_name)

    monkeypatch.setattr(wp, "align", changed)


def test_w1_end_to_end(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")

    def pile(words):
        first = words[0][0]
        words[0] = [AlignedWord(first.start_ms, first.start_ms + 200, "w", 0.8)] + [
            AlignedWord(first.start_ms + 200 + 10 * k, first.start_ms + 210 + 10 * k, "w", 0.8)
            for k in range(5)]  # the first word keeps its dwell: W1 alone
        return words

    _rebuild_with(monkeypatch, pile)
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.VETOED, "veto: W1")
    assert row["w1"]["words"] == 5 and row["w1"]["line"] == 0


def test_w4_end_to_end(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, lambda d: _older(d, by=300))

    def rush(words):
        a, b = words[1][0], words[1][1]
        words[1] = [a, AlignedWord(a.start_ms + 50, b.end_ms, b.text, b.prob)]
        return words

    _rebuild_with(monkeypatch, rush)
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.VETOED, "veto: W4")
    assert row["w4"] == {"old": 0, "new": 1, "new_from_worded": 1, "new_from_wordless": 0,
                         "lines": [{"line": 1, "dwell_old_ms": 300, "dwell_new_ms": 50}]}
    assert (row["old"]["first_dwell_under_120"], row["new"]["first_dwell_under_120"]) == (0, 1)


def test_w5_end_to_end(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")

    def early(words):
        last, first = words[0][-1], words[1][0]
        words[1][0] = AlignedWord(last.start_ms - 1, first.end_ms, first.text, first.prob)
        return words

    _rebuild_with(monkeypatch, early)
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.VETOED, "veto: W5")
    assert row["w5"] == {"old": 0, "new": 1}


def test_w2_end_to_end(db_session, audio, monkeypatch):
    """The voice returns at 11 000 ms on the recorded envelope; the stored document starts line 1
    there, the rebuild ~3 s earlier."""
    frames = 16_000 * SECONDS // 320
    levels = bytes([100] * 550 + [0] * 10 + [100] * (frames - 560))
    monkeypatch.setattr(wp, "level_envelope", lambda path: Envelope(
        levels, crossings=bytes(frames), loudest=(10**9, 320)))
    recorded = _run(db_session, monkeypatch, "record")
    by = recorded["lines"][1]["start_ms"] - 11_000
    _set_document(db_session, lambda d: _older(d, by=by))
    row = _preview(db_session)
    assert row["voice"]["returns"] == 1 and row["voice"]["placed_old"] == 1
    assert abs(by) > 1500
    assert row["w2"]["voice_ms"] == 11_000 and row["w2"]["away_ms"] == abs(by)
    assert (row["verdict"], row["detail"]) == (rules.VETOED, "veto: W2")


def test_a_move_w2_cannot_judge_is_review(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, lambda d: _older(d, line=0, by=-1600))  # line 0 is never placed
    row = _preview(db_session)
    assert row["w2_unjudged"] == [0]
    assert (row["verdict"], row["detail"]) == (
        rules.REVIEW, "line(s) 0 moved > 1500 ms where W2 cannot judge")


def test_a_palette_falling_back_is_carried_as_persist_carries_it(db_session, audio, monkeypatch,
                                                                 tmp_path):
    calls = []

    def album(url):
        calls.append(url)
        return dict(ALBUM)

    monkeypatch.setattr(wp, "extract_palette", album)
    _run(db_session, monkeypatch, "record")

    def fails(url):
        calls.append(url)
        return dict(document_default_palette())

    monkeypatch.setattr(wp, "extract_palette", fails)
    calls.clear()
    row = _preview(db_session, documents=tmp_path)
    assert len(calls) == 1  # fetched live, not from evidence
    assert (row["verdict"], row["detail"]) == (rules.UNCHANGED, "")
    assert row["differs"] == [] and row["palette_carried"] is True
    assert json.loads((tmp_path / f"{SONG}.json").read_text())["palette"] == ALBUM
    stored = _run(db_session, monkeypatch, "replay")  # what a reprocess stores: the same
    assert stored["palette"] == ALBUM
    monkeypatch.setattr(wp, "extract_palette", album)
    assert _preview(db_session)["palette_carried"] is False


def document_default_palette():
    from kashi_server.pipeline.palette import DEFAULT_PALETTE

    return DEFAULT_PALETTE


# --- round 2: the command ------------------------------------------------------------------------


def _main(monkeypatch, tmp_path, *args):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "lrclib_request_gap_s", 0.0)
    return preview.main(["--lyrics", "ladder", *args])


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_out_is_never_overwritten_and_append_sums_up_the_whole_file(db_session, audio,
                                                                     monkeypatch, tmp_path):
    _run(db_session, monkeypatch, "record")
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out)) == 0
    first = out.read_text()
    assert _main(monkeypatch, tmp_path, "--out", str(out)) == preview.EXIT_OUT_EXISTS
    assert out.read_text() == first
    _set_document(db_session, _older)
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--append") == 0
    lines = _lines(out)
    assert [("segment" in x, "source_id" in x, "summary" in x) for x in lines] == [
        (True, False, False), (False, True, False), (False, False, True),
        (True, False, False), (False, True, False), (False, False, True)]
    summary = lines[-1]["summary"]
    assert summary["segments"] == 2 and summary["songs"] == 1  # the song's latest row
    assert summary["refresh_ids"] == [SONG] and summary["this_run"]["songs"] == 1


def test_live_jobs_refuse_the_run_or_stop_it(db_session, audio, monkeypatch, tmp_path):
    _run(db_session, monkeypatch, "record")
    _job(db_session)  # claimed: running
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out)) == preview.EXIT_LIVE_JOBS
    assert not out.exists()
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--beside-live-jobs") == 0
    answers = iter([0, 1])
    monkeypatch.setattr(preview, "live_jobs", lambda s: next(answers))
    out2 = tmp_path / "rows2.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out2)) == preview.EXIT_LIVE_JOBS
    summary = _lines(out2)[-1]["summary"]
    assert summary["songs"] == 0 and summary["this_run"]["exit_code"] == preview.EXIT_LIVE_JOBS


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_stop_signal_ends_the_run_with_its_summary(db_session, audio, monkeypatch, tmp_path,
                                                     sig):
    _run(db_session, monkeypatch, "record")
    audio.on_replay = lambda: os.kill(os.getpid(), sig)
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out)) == 128 + sig
    summary = _lines(out)[-1]["summary"]
    assert summary["songs"] == 0 and summary["this_run"]["exit_code"] == 128 + sig
    assert list(tmp_path.glob(f"{preview.DIR_PREFIX}*")) == []  # the song's scratch is gone
    assert signal.getsignal(sig) is not None  # the handler before the run is back


def test_the_database_going_away_ends_the_run_with_its_summary(db_session, audio, monkeypatch,
                                                               tmp_path):
    from sqlalchemy.exc import OperationalError

    _run(db_session, monkeypatch, "record")

    def gone(*_a, **_k):
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))

    monkeypatch.setattr(preview, "preview_song", gone)
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out)) == preview.EXIT_DATABASE
    assert _lines(out)[-1]["summary"]["this_run"]["exit_code"] == preview.EXIT_DATABASE


def test_after_and_limit_through_the_command(db_session, audio, monkeypatch, tmp_path):
    for song in ("songA0000001", "songB0000001", "songC0000001"):
        monkeypatch.setattr(settings, "evidence_mode", "record")
        job = _job(db_session, source_id=song)
        wp.process_job(db_session, job)
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--after", "songA0000001",
                 "--limit", "1") == 0
    assert [x["source_id"] for x in _lines(out) if "source_id" in x] == ["songB0000001"]


def test_source_id_needs_at_least_one_id(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as stop:
        preview.main(["--source-id"])
    assert stop.value.code == 2


# --- round 2: the report carries no free text ----------------------------------------------------


@pytest.mark.parametrize("raised,detail", [
    (PipelineError("alignment_failed", "no lyrics for SECRETARTIST - hello world"),
     "pipeline error alignment_failed"),
    (PipelineError("network", "SECRETARTIST hello world"), "transient network"),
    (RuntimeError("SECRETARTIST hello world"), "replay fault RuntimeError"),
])
def test_failure_rows_carry_no_title_or_lyrics(db_session, audio, monkeypatch, raised, detail):
    _run(db_session, monkeypatch, "record")

    def fail(doc):
        raise raised

    monkeypatch.setattr(wp, "validate_document", fail)
    row = _preview(db_session)
    assert row["detail"] == detail
    text = json.dumps(row)
    assert "SECRETARTIST" not in text and "hello" not in text


def test_evidence_replaced_while_read_fails_instead_of_asking_a_full_job(db_session, audio,
                                                                         monkeypatch):
    _run(db_session, monkeypatch, "record")
    load = evidence.DbStore.load

    def replaced(self, source_type, source_id):
        record = load(self, source_type, source_id)
        assert record is not None

        def changed():
            raise evidence.EvidenceChanged("the stored record changed while it was read")

        record.load_blob = changed
        return record

    monkeypatch.setattr(evidence.DbStore, "load", replaced)
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (
        rules.FAILED, "evidence replaced while it was read: preview the song again")


def test_a_record_replaced_between_its_meta_and_its_arrays_is_evidence_changed(db_session, audio,
                                                                              monkeypatch):
    """The store's own check (another writer between the two reads) raises the subclass the
    preview reports as `failed`; a job still runs in full on it (an EvidenceMissing)."""
    _run(db_session, monkeypatch, "record")
    db_session.rollback()
    record = evidence.DbStore(db_session).load("youtube", SONG)
    assert record is not None
    db_session.execute(sql("UPDATE track_evidence SET updated_at = clock_timestamp() + "
                           "interval '1 second'"))
    db_session.commit()
    with pytest.raises(evidence.EvidenceChanged):
        record.blob  # noqa: B018 - reading it is the test
    assert issubclass(evidence.EvidenceChanged, evidence.EvidenceMissing)


# --- round 3 ------------------------------------------------------------------------------------


def test_a_refresh_without_its_confirmation_is_never_an_id_to_refresh():
    rows = [
        {"source_id": "a", "verdict": rules.REFRESH, "detail": "", "refresh_confirmed": True},
        {"source_id": "b", "verdict": rules.REFRESH, "detail": ""},  # an earlier format's row
        {"source_id": "c", "verdict": rules.REFRESH, "detail": "", "refresh_confirmed": False},
    ]
    summary = preview.summarize(rows)
    assert summary["refresh_ids"] == ["a"]
    assert summary["review"] == {"b": "refresh not confirmed by the ladder",
                                 "c": "refresh not confirmed by the ladder"}


def test_a_stored_record_refresh_without_its_ladder_pass_is_unconfirmed(db_session, audio,
                                                                        monkeypatch, get_record):
    from kashi_server.db.engine import SessionLocal

    _run(db_session, monkeypatch, "record")
    _set_document(db_session, _older)
    with SessionLocal() as s:
        row = preview.preview_song(preview.read_only(s), SONG, lyrics="stored", confirm=False)
    assert row["verdict"] == rules.REFRESH and row["refresh_confirmed"] is False
    assert preview.summarize([row])["refresh_ids"] == []


def test_every_refresh_row_says_whether_its_reprocess_s_lyrics_confirmed_it(db_session, audio,
                                                                           monkeypatch,
                                                                           get_record):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, _older)
    ladder_row = _preview(db_session)
    assert ladder_row["verdict"] == rules.REFRESH and ladder_row["refresh_confirmed"] is True
    stored_row = _preview(db_session, lyrics="stored")
    assert stored_row["refresh_confirmed"] is True and stored_row["ladder"]["confirmed"] is True


def test_append_refuses_a_file_another_judge_wrote(db_session, audio, monkeypatch, tmp_path):
    _run(db_session, monkeypatch, "record")
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out)) == 0
    segment = _lines(out)[0]["segment"]
    judge = segment["judge"]
    assert judge["preview_format"] == preview.PREVIEW_FORMAT and judge["lyrics"] == "ladder"
    assert judge["pipeline"] == PIPELINE_VERSION
    assert judge["evidence"] == evidence.expected_key(
        settings, separate_first=settings.separation_mode == "always")
    first = out.read_text()
    monkeypatch.setattr(settings, "lrclib_request_gap_s", 0.0)
    assert preview.main(["--lyrics", "stored", "--out", str(out), "--append"]) == (
        preview.EXIT_OUT_EXISTS)  # another lyrics mode
    monkeypatch.setattr(preview, "PREVIEW_FORMAT", preview.PREVIEW_FORMAT + 1)
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--append") == preview.EXIT_OUT_EXISTS
    assert out.read_text() == first


def test_append_refuses_a_file_without_judged_segments(db_session, audio, monkeypatch, tmp_path):
    """A file the first preview format wrote: rows, no segment lines."""
    _run(db_session, monkeypatch, "record")
    out = tmp_path / "rows.jsonl"
    out.write_text(json.dumps({"source_id": SONG, "verdict": rules.REFRESH, "detail": ""}) + "\n")
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--append") == preview.EXIT_OUT_EXISTS


def test_a_truncated_last_line_is_skipped_and_counted(db_session, audio, monkeypatch, tmp_path,
                                                      capsys):
    _run(db_session, monkeypatch, "record")
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out)) == 0
    with open(out, "a") as handle:
        handle.write('{"source_id": "cut-off-mid-wri')  # a run killed mid-line
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--append") == 0
    summary = _lines_tolerant(out)[-1]["summary"]
    assert summary["unreadable_lines"] == 1 and summary["songs"] == 1
    assert "1 unreadable line(s)" in capsys.readouterr().out


def _lines_tolerant(path):
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def test_a_source_id_run_keeps_the_cursor_s_frontier(db_session, audio, monkeypatch, tmp_path):
    for song in ("songA0000001", "songB0000001", "songC0000001"):
        monkeypatch.setattr(settings, "evidence_mode", "record")
        job = _job(db_session, source_id=song)
        wp.process_job(db_session, job)
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--limit", "2") == 0
    assert _lines(out)[-1]["summary"]["next_after"] == "songB0000001"
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--append", "--source-id",
                 "songC0000001") == 0  # a song past the frontier, given by hand
    summary = _lines(out)[-1]["summary"]
    assert summary["next_after"] == "songB0000001"  # not the given song
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--append", "--source-id",
                 "songA0000001") == 0  # read back, the given segment still moves no cursor
    summary = _lines(out)[-1]["summary"]
    assert summary["next_after"] == "songB0000001"
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--append", "--after",
                 summary["next_after"]) == 0
    assert _lines(out)[-1]["summary"]["next_after"] == "songC0000001"


def test_no_row_carries_an_overrun_any_more(db_session, audio, monkeypatch, get_record):
    """Since 2026-11-19 the job's Viterbi is its own and defined on every input; the preview has no
    overrun region to report (PREVIEW_FORMAT 5)."""
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, _older)
    row = _preview(db_session, lyrics="stored", confirm_all=True)
    assert row["verdict"] == rules.REFRESH
    assert "aligner_overruns" not in row and "aligner_overruns" not in row["ladder"]
    assert preview.PREVIEW_FORMAT == 6 and not hasattr(preview, "LADDER_OVERRUN")


def test_an_aligner_refusal_is_a_failure_not_a_full_job(db_session, audio, monkeypatch):
    from kashi_server.pipeline import alignment

    _run(db_session, monkeypatch, "record")

    def refused(*a, **kw):
        raise alignment.AlignerRefusal("window 3 (samples 1-2), segment pass: ... (T=1, L=2, R=0)")

    monkeypatch.setattr(wp, "align", refused)
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.FAILED, "alignment_failed")


# --- round 4: every row says whether the ladder builds its document -------------------------------


def test_without_confirm_all_a_row_that_is_not_a_refresh_says_not_run(db_session, audio,
                                                                       monkeypatch, get_record,
                                                                       ladder_calls):
    _run(db_session, monkeypatch, "record")
    ladder_calls.clear()
    row = _preview(db_session, lyrics="stored")
    assert row["verdict"] == rules.UNCHANGED and ladder_calls == []
    assert row["ladder_same"] == preview.LADDER_NOT_RUN == "not run"


def test_confirm_all_asks_the_ladder_for_a_row_that_is_not_a_refresh(db_session, audio,
                                                                      monkeypatch, get_record,
                                                                      ladder_calls):
    _run(db_session, monkeypatch, "record")
    ladder_calls.clear()
    row = _preview(db_session, lyrics="stored", confirm_all=True)
    assert get_record == [77] and len(ladder_calls) == 1
    assert row["verdict"] == rules.UNCHANGED  # the verdict stays
    assert row["ladder_same"] is True
    assert row["ladder"] == {"verdict": rules.UNCHANGED, "detail": "", "lyrics_record": 77}


def test_confirm_all_on_a_kept_song_whose_ladder_picks_another_record(db_session, audio,
                                                                      monkeypatch, get_record,
                                                                      ladder_calls):
    """2026-11-19: a `keep` row, judged on the stored record, was reprocessed; the ladder picked
    another record and the worker stored a sheet nobody had previewed."""
    _run(db_session, monkeypatch, "record")

    def on_76_rushed(doc):
        _older(doc, by=300)
        doc["alignment"]["lyrics_source_id"] = 76

    _set_document(db_session, on_76_rushed)

    def rush(words):
        a, b = words[1][0], words[1][1]
        words[1] = [a, AlignedWord(a.start_ms + 50, b.end_ms, b.text, b.prob)]
        return words

    _rebuild_with(monkeypatch, rush)
    ladder_calls.clear()
    plain = _preview(db_session, lyrics="stored")
    assert (plain["verdict"], plain["detail"]) == (rules.VETOED, "veto: W4")
    assert plain["ladder_same"] == "not run" and ladder_calls == []
    row = _preview(db_session, lyrics="stored", confirm_all=True)
    assert (row["verdict"], row["detail"]) == (rules.VETOED, "veto: W4")  # the verdict stays
    assert row["ladder_same"] is False and len(ladder_calls) == 1
    assert row["ladder"] == {"verdict": rules.SHEET, "detail": "lyrics record 76 -> 77",
                             "lyrics_record": 77}


def test_confirm_all_a_ladder_that_builds_no_document_is_not_the_same(db_session, audio,
                                                                      monkeypatch, get_record):
    _run(db_session, monkeypatch, "record")

    def busy(hints, base_url):
        raise PipelineError("lrclib_unavailable", "503")

    monkeypatch.setattr(wp, "fetch_lyrics", busy)
    row = _preview(db_session, lyrics="stored", confirm_all=True)
    assert row["verdict"] == rules.UNCHANGED and row["ladder_same"] is False
    assert row["ladder"] == {
        "verdict": rules.FAILED, "detail": "lrclib unavailable: a reprocess now would be deferred",
        "lyrics_record": None}


def test_a_refresh_s_confirmation_says_ladder_same(db_session, audio, monkeypatch, get_record,
                                                    ladder_calls):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, _older)
    ladder_calls.clear()
    row = _preview(db_session, lyrics="stored", confirm_all=True)
    assert row["verdict"] == rules.REFRESH and row["ladder_same"] is True
    assert row["ladder"]["confirmed"] is True and len(ladder_calls) == 1  # one pass, not two


def test_a_ladder_or_override_build_is_the_reprocess_s_own(db_session, audio, monkeypatch,
                                                           get_record, ladder_calls):
    _run(db_session, monkeypatch, "record")
    ladder_calls.clear()
    row = _preview(db_session, lyrics="ladder", confirm_all=True)
    assert row["ladder_same"] is True and len(ladder_calls) == 1  # its own pass, no second one
    db_session.add(SongOverride(source_type="youtube", source_id=SONG,
                                lyrics_text="\n".join(LINES)))
    db_session.commit()
    row = _preview(db_session, lyrics="stored", confirm_all=True)
    assert row["lyrics"] == "override" and row["ladder_same"] is True and get_record == []


def test_no_document_is_no_document_even_with_confirm_all(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "off")
    row = _preview(db_session, lyrics="stored", confirm_all=True)
    assert row["verdict"] == rules.FULL_JOB and row["ladder_same"] == "no document"
    assert preview.LADDER_NO_DOCUMENT == "no document"


def test_a_fault_in_the_extra_ladder_pass_keeps_the_verdict(db_session, audio, monkeypatch,
                                                             get_record):
    _run(db_session, monkeypatch, "record")
    first = preview._preview
    calls = []

    def second_faults(s, source_id, mode, documents):
        calls.append(mode)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return first(s, source_id, mode, documents)

    monkeypatch.setattr(preview, "_preview", second_faults)
    row = _preview(db_session, lyrics="stored", confirm_all=True)
    assert calls == ["stored", "ladder"]
    assert (row["verdict"], row["detail"]) == (rules.UNCHANGED, "")  # the verdict stays
    assert row["ladder_same"] == "not run" and row["ladder"] == {"fault": "RuntimeError"}


def test_a_fault_in_a_refresh_s_confirmation_still_fails_it(db_session, audio, monkeypatch,
                                                            get_record):
    """A refresh needs its confirmation: without it the song is not refreshed."""
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, _older)
    first = preview._preview
    calls = []

    def second_faults(s, source_id, mode, documents):
        calls.append(mode)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return first(s, source_id, mode, documents)

    monkeypatch.setattr(preview, "_preview", second_faults)
    row = _preview(db_session, lyrics="stored", confirm_all=True)
    assert row["verdict"] == rules.FAILED and row["ladder_same"] == "not run"


def test_the_summary_says_ladder_same_per_song():
    rows = [
        {"source_id": "a", "verdict": rules.REFRESH, "refresh_confirmed": True,
         "ladder_same": True},
        {"source_id": "b", "verdict": rules.VETOED, "detail": "veto: W4", "ladder_same": False},
        {"source_id": "c", "verdict": rules.VETOED, "detail": "veto: W4", "ladder_same": True},
        {"source_id": "d", "verdict": rules.FULL_JOB, "detail": "x",
         "ladder_same": "no document"},
        {"source_id": "e", "verdict": rules.UNCHANGED},  # an earlier format's row
    ]
    summary = preview.summarize(rows)
    assert summary["ladder_same"] == {"a": True, "b": False, "c": True, "d": "no document",
                                      "e": "not run"}
    assert summary["ladder_same_counts"] == {"true": 2, "false": 1, "not run": 1,
                                             "no document": 1}


def test_the_command_carries_confirm_all(db_session, audio, monkeypatch, tmp_path, capsys,
                                         get_record, ladder_calls):
    _run(db_session, monkeypatch, "record")
    ladder_calls.clear()
    out = tmp_path / "rows.jsonl"
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "lrclib_request_gap_s", 0.0)
    assert preview.main(["--out", str(out), "--confirm-all"]) == 0
    lines = _lines(out)
    assert lines[0]["segment"]["confirm_all"] is True and lines[1]["ladder_same"] is True
    assert lines[-1]["summary"]["ladder_same"] == {SONG: True}
    assert f"{SONG}: unchanged [ladder_same true]" in capsys.readouterr().out
    assert len(ladder_calls) == 1
    assert lines[-1]["summary"]["confirm_all"] == "all"
    assert preview.main(["--out", str(out), "--append"]) == 0  # without: not run
    assert _lines(out)[-1]["summary"]["ladder_same"] == {SONG: "not run"}
    assert _lines(out)[-1]["summary"]["confirm_all"] == "mixed"
    assert len(ladder_calls) == 1


# --- round 5: hidden reasons, the override, the palette's escape hatches ----------------------


def test_confirm_all_of_the_segments():
    assert preview.confirm_all_of([True, True]) == "all"
    assert preview.confirm_all_of([False]) == "none"
    assert preview.confirm_all_of([True, False]) == "mixed"




def test_a_veto_does_not_hide_the_review_reasons(db_session, audio, monkeypatch):
    """2026-11-19: six songs kept by W4 alone were refreshed by hand; under the full order they
    were `review` (moves W2 cannot judge)."""
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, lambda d: _older(d, line=0, by=-1600))  # line 0 is never placed

    def rush(words):
        a, b = words[1][0], words[1][1]
        words[1] = [a, AlignedWord(a.start_ms + 50, b.end_ms, b.text, b.prob)]
        return words

    _rebuild_with(monkeypatch, rush)
    row = _preview(db_session)
    assert (row["verdict"], row["detail"]) == (rules.VETOED, "veto: W4")
    assert row["vetoes"] == ["W4"]
    assert row["reviews"] == ["line(s) 0 moved > 1500 ms where W2 cannot judge"]
    summary = preview.summarize([row])
    assert summary["keep"][SONG] == {"detail": "veto: W4", "vetoes": ["W4"],
                                     "reviews": ["line(s) 0 moved > 1500 ms where W2 cannot judge"]}
    assert summary["vetoes"] == {"W4": 1}  # the detail's vetoes, counted as before


def test_a_guard_does_not_hide_the_vetoes(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")

    def stale_and_rushed(doc):
        doc["pipeline_version"] = "9.0.0"

    _set_document(db_session, stale_and_rushed)

    def rush(words):
        a, b = words[1][0], words[1][1]
        words[1] = [a, AlignedWord(a.start_ms + 50, b.end_ms, b.text, b.prob)]
        return words

    _rebuild_with(monkeypatch, rush)
    row = _preview(db_session)
    assert row["verdict"] == rules.GUARDED and row["detail"].startswith("guard: stale")
    assert row["vetoes"] == ["W4"] and row["reviews"] == []
    assert preview.summarize([row])["keep"][SONG]["vetoes"] == ["W4"]


def test_the_row_names_the_override_that_built_it(db_session, audio, monkeypatch, get_record):
    from kashi_server import overrides

    _run(db_session, monkeypatch, "record")
    assert _preview(db_session, lyrics="stored")["override"] is None
    override = SongOverride(source_type="youtube", source_id=SONG, lyrics_text="\n".join(LINES))
    db_session.add(override)
    db_session.commit()
    row = _preview(db_session, lyrics="stored")
    assert row["override"] == overrides.fingerprint(overrides.acted_fields(override))


def test_the_preview_keeps_the_built_palette_when_the_stored_one_fails_the_schema(
        db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, lambda d: d.update(
        palette={"source": "album_art", "primary": "not a colour"}))
    monkeypatch.setattr(wp, "extract_palette", lambda url: dict(document_default_palette()))
    row = _preview(db_session)
    assert row["palette_carried"] is False and "palette" in row["differs"]


# --- --set: a pipeline setting overridden for the build only (2026-11-19) -------------------------

CANDIDATE = ["letterless_lines_wordless=true", "word_text_from_sheet=true"]
CANDIDATE_SET = {"letterless_lines_wordless": True, "word_text_from_sheet": True}


def _set_args(*pairs):
    return [arg for pair in pairs for arg in ("--set", pair)]


def test_set_takes_a_pipeline_setting_once_and_only_a_value_it_does_not_run():
    assert preview.parse_overrides(CANDIDATE) == CANDIDATE_SET
    assert preview.parse_overrides([]) == {}
    for bad in (
        ["evidence_mode=replay"],  # operational: not what a document depends on
        ["database_url=x"],
        ["nope=1"],
        ["letterless_lines_wordless"],
        [CANDIDATE[0], CANDIDATE[0]],
        ["letterless_lines_wordless=false"],  # what this process already runs
        ["letterless_lines_wordless=maybe"],
    ):
        with pytest.raises(preview.OverrideError):
            preview.parse_overrides(bad)
    with pytest.raises(SystemExit):
        preview.main(["--set", "evidence_mode=replay"])


def test_overridden_settings_come_back_also_when_the_run_fails():
    with pytest.raises(RuntimeError):
        with preview.overridden(CANDIDATE_SET):
            assert settings.letterless_lines_wordless is True
            raise RuntimeError("stop")
    assert settings.letterless_lines_wordless is False
    assert settings.word_text_from_sheet is False


def test_under_overrides_the_summary_names_no_id_to_refresh():
    rows = [{"source_id": "a", "verdict": rules.REFRESH, "detail": "", "refresh_confirmed": True}]
    plain = preview.summarize(rows)
    assert plain["refresh_ids"] == ["a"] and plain["pipeline_overrides"] is None
    overridden = preview.summarize(rows, CANDIDATE_SET)
    assert overridden["refresh_ids"] == [] and overridden["verdicts"][rules.REFRESH] == 1
    assert overridden["pipeline_overrides"] == preview.overrides_label(CANDIDATE_SET)
    assert "refresh_withheld" in overridden


def test_an_override_reaches_the_build_and_nothing_else(db_session, audio, monkeypatch, tmp_path):
    _run(db_session, monkeypatch, "record")
    _set_document(db_session, _older)  # the rebuild is a refresh
    seen = []
    real = wp._run_pipeline

    def spy(*a, **k):
        seen.append((settings.letterless_lines_wordless, settings.word_text_from_sheet))
        return real(*a, **k)

    monkeypatch.setattr(wp, "_run_pipeline", spy)
    judged = []
    real_compare = rules.compare

    def compare(*a, **k):  # the rules judge as the build ran
        judged.append(k.get("letterless_wordless"))
        return real_compare(*a, **k)

    monkeypatch.setattr(rules, "compare", compare)
    before = _snapshot(db_session)
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out), *_set_args(*CANDIDATE)) == 0
    assert seen and set(seen) == {(True, True)}
    assert judged and set(judged) == {True}
    # put back for whatever this process runs next; nothing stored, no evidence touched
    assert (settings.letterless_lines_wordless, settings.word_text_from_sheet) == (False, False)
    assert _snapshot(db_session) == before
    label = preview.overrides_label(CANDIDATE_SET)
    assert label is not None and len(label["fingerprint"]) == 16
    lines = _lines(out)
    assert lines[0]["segment"]["judge"]["pipeline_overrides"] == CANDIDATE_SET
    assert lines[1]["verdict"] == rules.REFRESH and lines[1]["pipeline_overrides"] == label
    summary = lines[-1]["summary"]
    assert summary["pipeline_overrides"] == label and summary["refresh_ids"] == []
    assert summary["verdicts"][rules.REFRESH] == 1
    # what the environment itself overrides is named too (a label; conftest's env sets some)
    from kashi_server.config import pipeline_overrides

    assert summary["env_pipeline_overrides"] == pipeline_overrides(settings)
    assert lines[0]["segment"]["env_pipeline_overrides"] == pipeline_overrides(settings)
    # the same song without an override is an id to refresh, its rows unlabelled
    plain = tmp_path / "plain.jsonl"
    judged.clear()
    assert _main(monkeypatch, tmp_path, "--out", str(plain)) == 0
    assert judged and set(judged) == {False}  # judged exactly as 0.43.0 judges
    assert "pipeline_overrides" not in _lines(plain)[1]
    assert _lines(plain)[-1]["summary"]["refresh_ids"] == [SONG]


def test_append_never_mixes_two_sets_of_overrides(db_session, audio, monkeypatch, tmp_path):
    _run(db_session, monkeypatch, "record")
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out), *_set_args(CANDIDATE[0])) == 0
    first = out.read_text()
    for other in ([], _set_args(CANDIDATE[1]), _set_args(*CANDIDATE)):
        assert _main(monkeypatch, tmp_path, "--out", str(out), "--append", *other) == (
            preview.EXIT_OUT_EXISTS
        )
        assert out.read_text() == first
    assert _main(
        monkeypatch, tmp_path, "--out", str(out), "--append", *_set_args(CANDIDATE[0])
    ) == 0


def test_documents_built_under_set_go_to_their_fingerprint_s_directory(
    db_session, audio, monkeypatch, tmp_path
):
    _run(db_session, monkeypatch, "record")
    docs = tmp_path / "docs"
    out = tmp_path / "rows.jsonl"
    assert _main(monkeypatch, tmp_path, "--out", str(out), "--documents", str(docs),
                 *_set_args(*CANDIDATE)) == 0
    label = preview.overrides_label(CANDIDATE_SET)
    assert label is not None
    target = docs / f"set-{label['fingerprint']}"
    assert sorted(p.name for p in docs.iterdir()) == [target.name]  # nothing beside it
    assert (target / f"{SONG}.json").exists()
    named = json.loads((target / "OVERRIDES.json").read_text())
    assert named["set"] == CANDIDATE_SET and named["fingerprint"] == label["fingerprint"]
    # without --set: the directory itself, as before
    plain = tmp_path / "plain-docs"
    assert _main(monkeypatch, tmp_path, "--out", str(tmp_path / "plain.jsonl"),
                 "--documents", str(plain)) == 0
    assert sorted(p.name for p in plain.iterdir()) == [f"{SONG}.json"]


def test_set_is_validated_by_the_settings_model_and_takes_its_value():
    """align_models' validator keys the table the way the aligner is called ("en" -> "eng"); a
    bare type check would let "en" through, and the override would silently never apply."""
    got = preview.parse_overrides(['align_models={"en": {"checkpoint": "x/y"}}'])
    assert list(got["align_models"]) == ["eng"]
    assert got["align_models"]["eng"]["checkpoint"] == "x/y"
    before = dict(settings.align_models)
    with preview.overridden(got):
        assert list(settings.align_models) == ["eng"]
        assert settings.align_models["eng"].checkpoint == "x/y"
    assert settings.align_models == before
    with pytest.raises(preview.OverrideError, match="refused by the settings model"):
        preview.parse_overrides(
            ['align_models={"en": {"checkpoint": "x/y"}, "eng": {"checkpoint": "x/z"}}']
        )
