"""The refresh preview (worker/preview.py): the document a replayed reprocess would store, built
from the stored evidence with nothing written and no audio read, compared and judged. The song,
its evidence and the stand-in aligner are test_worker_evidence's."""

import json
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
from kashi_server.pipeline.alignment import AlignResult
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
        return dict(RECORD, id=record_id) if record_id == RECORD["id"] else None

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
    _set_document(db_session, lambda d: d["alignment"].update(lyrics_source_id=78))
    row = _preview(db_session, lyrics="stored")
    assert get_record == [78]
    assert row["verdict"] == rules.FAILED and "the stored document's lyrics record" in row["detail"]


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
    assert json.loads(out.read_text().splitlines()[0])["verdict"] == rules.UNCHANGED
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
    assert row["verdict"] == rules.FULL_JOB and row["detail"].startswith("the replay failed")


def test_a_permanent_error_in_the_replay_needs_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")

    def invalid(doc):
        raise PipelineError("alignment_failed", "document failed schema validation")

    monkeypatch.setattr(wp, "validate_document", invalid)
    row = _preview(db_session)
    assert row["verdict"] == rules.FULL_JOB and row["detail"].startswith("alignment_failed")


# --- failures ------------------------------------------------------------------------------------


def test_weather_is_a_failure_not_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    audio.weather = True
    row = _preview(db_session)
    assert row["verdict"] == rules.FAILED and row["detail"].startswith("network")


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


def test_a_moved_line_is_reported_and_refreshed(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")

    def older(doc):
        doc["pipeline_version"] = "2.34.0"
        for word in doc["lines"][1]["words"]:
            word["start_ms"] -= 700
            word["end_ms"] -= 700
        doc["lines"][1]["start_ms"] -= 700

    old = _set_document(db_session, older)
    row = _preview(db_session)
    assert row["verdict"] == rules.REFRESH and row["detail"] == ""
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
def test_each_guard_keeps_counts_and_says_its_own(kind, caplog):
    """The extracted decision drives persist's own counters and log lines, one per guard."""
    from prometheus_client import REGISTRY
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
    assert summary["next_after"] == SONG and summary["terminated"] is False
    (out,) = tmp_path.glob("preview-*.jsonl")
    lines = out.read_text().splitlines()
    assert json.loads(lines[0])["source_id"] == SONG and "summary" in json.loads(lines[-1])
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
        {"source_id": "a", "verdict": rules.REFRESH, "detail": ""},
        {"source_id": "b", "verdict": rules.VETOED, "detail": "veto: W1, W3"},
        {"source_id": "c", "verdict": rules.GUARDED, "detail": "guard: stale: x"},
        {"source_id": "d", "verdict": rules.FULL_JOB, "detail": "no evidence stored"},
        {"source_id": "e", "verdict": rules.REFRESH, "detail": ""},
        {"source_id": "f", "verdict": rules.UNCHANGED, "detail": ""},
    ]
    summary = preview.summarize(rows)
    assert summary["refresh_ids"] == ["a", "e"] and summary["unchanged_ids"] == ["f"]
    assert summary["verdicts"] == {rules.REFRESH: 2, rules.UNCHANGED: 1, "keep": 2,
                                   rules.SHEET: 0, rules.FULL_JOB: 1, rules.FAILED: 0}
    assert summary["vetoes"] == {"W1": 1, "W3": 1}
    assert summary["keep"] == {"b": "veto: W1, W3", "c": "guard: stale: x"}
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
