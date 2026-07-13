"""BYO-audio staging (Faz 5 P4): POST /v1/uploads + worker consumption.

Real ffprobe validates a real (tiny) wav — extensions and Content-Type lie,
streams do not. DB-backed tests; skipped without DATABASE_URL like the rest.
"""

import io
import struct
import wave

from helpers import auth as _auth
from sqlalchemy import select

from kashi_server.db.models import UploadedAudio


def _wav_bytes(seconds: float = 1.0, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buf.getvalue()


def _upload(client, key, data: bytes, name="clip.wav", mime="audio/wav"):
    return client.post(
        "/v1/uploads", files={"file": (name, data, mime)}, headers=_auth(key)
    )


def test_upload_stages_row_and_hands_back_a_source_ref(client, user_key, db_session):
    resp = _upload(client, user_key, _wav_bytes())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"]["type"] == "upload"
    assert len(body["source"]["id"]) == 43  # urlsafe sha256, no padding
    assert 900 <= body["duration_ms"] <= 1100
    row = db_session.get(UploadedAudio, body["source"]["id"])
    assert row is not None and row.duration_s > 0 and row.size_bytes == len(_wav_bytes())

    # Same bytes again: same id, expiry refreshed, still exactly one row.
    again = _upload(client, user_key, _wav_bytes())
    assert again.status_code == 201
    assert again.json()["source"]["id"] == body["source"]["id"]
    db_session.expire_all()
    rows = db_session.scalars(select(UploadedAudio)).all()
    assert len(rows) == 1


def test_upload_rejects_non_audio_and_empty_bodies(client, user_key):
    assert _upload(client, user_key, b"definitely not audio").status_code == 422
    assert _upload(client, user_key, b"").status_code == 422


def test_upload_rejects_over_cap_stream(client, user_key, monkeypatch):
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "upload_max_bytes", 1024)
    resp = _upload(client, user_key, _wav_bytes())  # ~16KB wav > 1KB cap
    assert resp.status_code == 413


def test_upload_rejects_audio_over_the_duration_cap(client, user_key, monkeypatch):
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "max_track_duration_s", 0)
    resp = _upload(client, user_key, _wav_bytes(seconds=1.0))
    assert resp.status_code == 422
    assert "processing cap" in resp.json()["detail"]


def test_worker_processes_an_upload_and_burns_the_row(
    client, user_key, db_session, monkeypatch, tmp_path
):
    from kashi_server import queue
    from kashi_server.pipeline.lrclib import LyricsText
    from kashi_server.worker import process as wp

    # Stage + ingest through the real API surface.
    upload_id = _upload(client, user_key, _wav_bytes()).json()["source"]["id"]
    resp = client.post(
        "/v1/ingest",
        json={
            "source": {"type": "upload", "id": upload_id},
            "hints": {"title": "Clip", "artist": "Uploader"},
        },
        headers=_auth(user_key),
    )
    assert resp.status_code == 202 and resp.json()["reused"] is False

    job = queue.claim_next(db_session)
    assert job is not None and job.source_type == "upload"

    # Stub the heavy stages; the AUDIO fetch itself runs for real from the DB.
    monkeypatch.setattr(wp, "_align_stage", lambda *a, **kw: (_fake_result(), False))
    monkeypatch.setattr(
        wp,
        "fetch_lyrics",
        lambda hints, base_url: LyricsText(["hello world"], "hello world", 5, True),
    )
    monkeypatch.setattr(wp, "extract_beats", lambda p: None)
    monkeypatch.setattr(
        wp,
        "extract_palette",
        lambda url: {"source": "default", "primary": "#e84545", "secondary": "#f5d76e",
                     "background": "#1a1a2e", "text": "#ffffff", "accent": "#903749"},
    )
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "separation_mode", "off")
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed", (job.error_type, job.error_message)
    # Deletion guarantee reaches the DB copy: the staged row is gone.
    db_session.expire_all()
    assert db_session.get(UploadedAudio, upload_id) is None


def _fake_result():
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming

    return AlignResult(
        sync="word",
        lines=[LineTiming(0, 1000, "hello world", 0.8)],
        words_per_line=[[AlignedWord(0, 400, "hello", 0.8), AlignedWord(500, 1000, "world", 0.8)]],
        quality_score=0.8,
    )


def test_missing_staged_row_fails_permanent(db_session, monkeypatch, tmp_path):
    from kashi_server import queue
    from kashi_server.config import settings
    from kashi_server.version import PIPELINE_MAJOR
    from kashi_server.worker import process as wp

    monkeypatch.setattr(settings, "retry_delays_s", [0])
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    job, _ = queue.enqueue(
        db_session,
        source_type="upload",
        source_id="A" * 43,
        pipeline_major=PIPELINE_MAJOR,
        hints={"title": "t", "artist": "a"},
        options={},
        requested_by=None,
    )
    db_session.commit()
    claimed = queue.claim_next(db_session)
    assert claimed is not None
    wp.process_job(db_session, claimed)
    db_session.refresh(claimed)
    assert claimed.status == "failed"
    assert claimed.error_type == "other"  # permanent — the row cannot reappear


def test_purge_expired_uploads_sweeps_only_stale_rows(db_session):
    from datetime import UTC, datetime, timedelta

    from kashi_server import queue

    fresh = UploadedAudio(
        id="F" * 43, content=b"x", size_bytes=1, mime=None, duration_s=1.0,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    stale = UploadedAudio(
        id="S" * 43, content=b"x", size_bytes=1, mime=None, duration_s=1.0,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add_all([fresh, stale])
    db_session.flush()
    assert queue.purge_expired_uploads(db_session) == 1
    db_session.expire_all()
    assert db_session.get(UploadedAudio, "F" * 43) is not None
    assert db_session.get(UploadedAudio, "S" * 43) is None


def test_unauthenticated_big_body_dies_at_the_small_cap(client):
    # No Authorization header -> the /v1/uploads override must NOT apply;
    # a 65KB anonymous body dies in the middleware, not after full parsing.
    resp = client.post(
        "/v1/uploads",
        content=b"x" * (128 * 1024),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert resp.status_code == 413


def test_ttl_sweep_spares_rows_referenced_by_live_jobs(db_session):
    from datetime import UTC, datetime, timedelta

    from kashi_server import queue
    from kashi_server.version import PIPELINE_MAJOR

    expired_but_live = UploadedAudio(
        id="L" * 43, content=b"x", size_bytes=1, mime=None, duration_s=1.0,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(expired_but_live)
    queue.enqueue(
        db_session,
        source_type="upload",
        source_id="L" * 43,
        pipeline_major=PIPELINE_MAJOR,
        hints={"title": "t", "artist": "a"},
        options={},
        requested_by=None,
    )
    db_session.flush()
    assert queue.purge_expired_uploads(db_session) == 0
    db_session.expire_all()
    assert db_session.get(UploadedAudio, "L" * 43) is not None
