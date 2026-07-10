"""process_job with mocked stages: status flow, retries, cancellation, and the
audio-deletion guarantee on every exit path."""

from pathlib import Path

import pytest

from kashi_server import queue
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.download import DownloadResult
from kashi_server.pipeline.lrclib import LyricsText
from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.worker import process as wp
from kashi_server.worker.main import sweep_orphans


@pytest.fixture()
def job(db_session):
    queue.enqueue(
        db_session,
        source_type="youtube",
        source_id="workerVid01",
        pipeline_major=1,
        hints={"title": "T", "artist": "A", "duration_ms": 200_000},
        options={},
        requested_by=None,
    )
    db_session.commit()
    claimed = queue.claim_next(db_session)
    db_session.commit()
    assert claimed is not None
    return claimed


@pytest.fixture()
def scratch(tmp_path, monkeypatch):
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "retry_delays_s", [0, 0, 0])
    return tmp_path


def _happy_stages(monkeypatch, scratch):
    def fake_download(source_id, dest_dir, **kw):
        path = Path(dest_dir) / "audio.webm"
        path.write_bytes(b"x" * 1024)
        return DownloadResult(path=path, abr=128.0, acodec="opus", duration_s=200.0, info={})

    def fake_align_stage(s, j, tmp, audio, lyrics):
        result = AlignResult(
            sync="word",
            lines=[LineTiming(0, 1000, "hello world", 0.8)],
            words_per_line=[
                [AlignedWord(0, 400, "hello", 0.8), AlignedWord(500, 1000, "world", 0.8)]
            ],
            quality_score=0.8,
        )
        return result, False

    monkeypatch.setattr(wp, "download_audio", fake_download)
    monkeypatch.setattr(wp, "_align_stage", fake_align_stage)
    monkeypatch.setattr(
        wp,
        "fetch_lyrics",
        lambda hints, base_url: LyricsText(["hello world"], "hello world", 5, True),
    )
    monkeypatch.setattr(wp, "extract_beats", lambda p: None)
    monkeypatch.setattr(
        wp,
        "extract_palette",
        lambda url: {
            "source": "default",
            "primary": "#e84545",
            "secondary": "#f5d76e",
            "background": "#1a1a2e",
            "text": "#ffffff",
            "accent": "#903749",
        },
    )


def test_happy_path_completes_and_cleans_tmp(db_session, job, scratch, monkeypatch):
    _happy_stages(monkeypatch, scratch)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert list(scratch.glob("job-*")) == []  # audio gone

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    row = db_session.scalars(select(ProcessedTrack)).one()
    assert row.source_id == "workerVid01" and row.sync == "word"
    assert row.document["track"]["duration_ms"] == 200_000


def test_transient_failure_retries_then_cleans(db_session, job, scratch, monkeypatch):
    def flaky_download(source_id, dest_dir, **kw):
        raise PipelineError("network", "connection reset")

    monkeypatch.setattr(wp, "download_audio", flaky_download)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "queued"  # retry scheduled
    assert job.attempts == 1
    assert list(scratch.glob("job-*")) == []


def test_permanent_failure_fails_once(db_session, job, scratch, monkeypatch):
    monkeypatch.setattr(
        wp,
        "download_audio",
        lambda *a, **k: (_ for _ in ()).throw(PipelineError("copyright", "blocked")),
    )
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "copyright"
    assert list(scratch.glob("job-*")) == []


def test_transient_exhaustion_becomes_failed(db_session, job, scratch, monkeypatch):
    monkeypatch.setattr(
        wp,
        "download_audio",
        lambda *a, **k: (_ for _ in ()).throw(PipelineError("network", "reset")),
    )
    job.attempts = job.max_attempts  # already at the cap
    db_session.flush()
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "network"


def test_unexpected_crash_maps_to_other_and_cleans(db_session, job, scratch, monkeypatch):
    monkeypatch.setattr(
        wp, "download_audio", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    job.attempts = job.max_attempts
    db_session.flush()
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "other"
    assert list(scratch.glob("job-*")) == []


def test_cancel_between_stages_aborts_quietly(db_session, job, scratch, monkeypatch):
    def cancel_then_download(source_id, dest_dir, **kw):
        # An admin cancels while the download runs; the NEXT checkpoint aborts.
        from sqlalchemy import text

        db_session.execute(text("UPDATE jobs SET status='canceled' WHERE id=:id"), {"id": job.id})
        db_session.commit()
        path = Path(dest_dir) / "audio.webm"
        path.write_bytes(b"x")
        return DownloadResult(path=path, abr=128.0, acodec="opus", duration_s=200.0, info={})

    monkeypatch.setattr(wp, "download_audio", cancel_then_download)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "canceled"
    assert list(scratch.glob("job-*")) == []  # tmp cleaned even on abort


def test_sweep_orphans_removes_stale_dirs_only(tmp_path):
    import os

    old = tmp_path / "job-old"
    old.mkdir()
    (old / "audio.webm").write_bytes(b"x")
    os.utime(old, (1, 1))  # ancient mtime
    fresh = tmp_path / "job-fresh"
    fresh.mkdir()

    assert sweep_orphans(tmp_path) == 1
    assert not old.exists() and fresh.exists()
