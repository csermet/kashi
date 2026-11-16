"""The 2026-11-10 repair survives the archive refresh once it is an override (C.1).

Built on the night verification's end-to-end proof of the bug
(kashi-gozden-gecirme/dogrulama/test_dg2_repair_overwrite.py): the real admin
endpoints and the real `process_job` (nightcore branch, `_align_stage`, line
QA, held-end restore, rescale, document build, persist guard, upsert) on
Postgres. Faked: download, decode/duration, separation, the aligner itself,
onsets, mix features, palette, lrclib (no network).

Shape of the Nightcall repair: options {speed_factor 1.1504, lyrics_text,
force_persist}; detection, left alone, reads r 1.3365 off the lrclib record.
The archive feeder then sends exactly {"source": {...}}. On main the stored
document becomes r 1.3365 / lrclib (the control below shows the same path
still does that for a song WITHOUT an override); with the repair stored as
an override it stays r 1.1504 / caller text.
"""

from pathlib import Path

import pytest
from helpers import TEST_ADMIN_KEY, auth
from sqlalchemy import select, text

from kashi_server import queue
from kashi_server.config import settings
from kashi_server.db.models import Job, ProcessedTrack
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.download import DownloadResult
from kashi_server.pipeline.palette import DEFAULT_PALETTE
from kashi_server.worker import process as wp

UPLOAD_S = 134.201
TRUE_R = 1.1504  # the repair's r (true original length / upload)
WRONG_R = 1.3365  # what detection picks from the lrclib record (2.31 wave: 1.33648)
N = 24
HINTS = {"title": "Nightcore - Nightcall (Lyrics)", "artist": "Chino", "duration_ms": 134201}
SOURCE = {"type": "youtube", "id": "ncRepair001"}
OVERRIDE_URL = f"/v1/admin/overrides/youtube/{SOURCE['id']}"
ADMIN = auth(TEST_ADMIN_KEY)


def _lines(n, tag):
    return [f"{tag} line {i} goes on and on" for i in range(n)]


CALLER_TEXT = "\n".join(_lines(N, "caller"))


def _record(n_lines):
    texts = _lines(n_lines, "record")
    synced = "\n".join(
        f"[{int((10 + 6 * i) // 60):02d}:{(10 + 6 * i) % 60:05.2f}] {t}"
        for i, t in enumerate(texts)
    )
    return {
        "id": 17581840,
        "trackName": "Nightcall",
        "artistName": "Kavinsky",
        "duration": round(UPLOAD_S * WRONG_R, 3),
        "syncedLyrics": synced,
        "plainLyrics": "\n".join(texts),
    }


def _fake_align(wav, line_texts, language, synced_starts_ms=None, **kw):
    windowed = synced_starts_ms is not None
    lines, words = [], []
    for i, line in enumerate(line_texts):
        start = (
            synced_starts_ms[i] if windowed and synced_starts_ms[i] is not None else 5000 + 5000 * i
        )
        tokens = line.split()
        step = 300
        aligned = [
            AlignedWord(start + k * step, start + (k + 1) * step - 20, t, 0.8)
            for k, t in enumerate(tokens)
        ]
        words.append(aligned)
        lines.append(LineTiming(start, aligned[-1].end_ms, line, 0.8))
    return AlignResult(
        sync="word", lines=lines, words_per_line=words, quality_score=0.8, windowed=windowed
    )


@pytest.fixture
def stages(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "retry_delays_s", [0, 0, 0])
    monkeypatch.setattr(settings, "separation_mode", "always")  # production
    monkeypatch.setattr(settings, "windowed_alignment", True)  # production
    assert settings.nightcore_detection is True  # code default = production

    def fake_download(source_id, dest_dir, **kw):
        path = Path(dest_dir) / "audio.webm"
        path.write_bytes(b"x" * 1024)
        return DownloadResult(path=path, abr=128.0, acodec="opus", duration_s=UPLOAD_S, info={})

    def fake_decode(src, dest, rate, *, tempo=1.0, start_s=None, length_s=None):
        Path(dest).write_text(repr(tempo))
        return Path(dest)

    def fake_wav_duration(p):
        return UPLOAD_S / float(Path(p).read_text())

    def fake_separate(audio, tmp, **kw):
        out = Path(tmp) / "vocals.wav"
        out.write_text("1.0")
        return out

    monkeypatch.setattr(wp, "download_audio", fake_download)
    monkeypatch.setattr(wp, "_decode", fake_decode)
    monkeypatch.setattr(wp, "_wav_duration_s", fake_wav_duration)
    monkeypatch.setattr(wp, "_separate_vocals", fake_separate)
    monkeypatch.setattr(wp, "align", _fake_align)
    monkeypatch.setattr(wp, "heard_tokens", lambda wav, language: [])
    monkeypatch.setattr(wp, "detect_onsets", lambda wav: None)
    monkeypatch.setattr(wp, "_mix_features", lambda mix: (None, None, None))
    monkeypatch.setattr(wp, "extract_palette", lambda url: dict(DEFAULT_PALETTE))

    def no_fetch(hints, base_url):
        raise AssertionError("plain fetch_lyrics should not run for a nightcore-marked title")

    searches: list[str] = []

    def search(query, *, base_url):
        searches.append(query)
        return [_record(N)]

    monkeypatch.setattr(wp, "fetch_lyrics", no_fetch)
    monkeypatch.setattr(wp, "search_candidates", search)
    monkeypatch.setattr(wp, "LRCLIB", wp.build_gateway())
    return searches


def _run_next(db_session) -> Job:
    db_session.expire_all()
    job = queue.claim_next(db_session)
    db_session.commit()
    assert job is not None
    wp.process_job(db_session, job)
    db_session.refresh(job)
    return job


def _stored(db_session) -> dict:
    db_session.expire_all()
    return db_session.scalars(select(ProcessedTrack)).one().document["alignment"]


def _repair_2026_11_10(client, db_session):
    """The hand repair as it was sent: request options + force_persist."""
    body = {
        "source": SOURCE,
        "hints": HINTS,
        "options": {"speed_factor": TRUE_R, "lyrics_text": CALLER_TEXT, "force_persist": True},
    }
    assert client.post("/v1/admin/reprocess", json=body, headers=ADMIN).status_code == 202
    assert _run_next(db_session).status == "completed"
    stored = _stored(db_session)
    assert stored["speed_factor"] == TRUE_R and stored["lyrics_source"] == "caller"


def _store_override(client):
    """What scripts/import_overrides.py sends for this payload entry."""
    resp = client.put(
        OVERRIDE_URL, json={"speed_factor": TRUE_R, "lyrics_text": CALLER_TEXT}, headers=ADMIN
    )
    assert resp.status_code == 201, resp.text


def _feeder_refresh(client, db_session) -> Job:
    body = {"source": SOURCE}  # scratch/toplu-2.31/toplu.sh enqueue(): exactly this
    assert client.post("/v1/admin/reprocess", json=body, headers=ADMIN).status_code == 202
    return _run_next(db_session)


def test_control_without_an_override_the_feeder_still_reverts_the_repair(
    client, db_session, stages
):
    """Today's behaviour, unchanged for a song with no override row: the
    scenario has teeth (this is the bug the override exists for)."""
    _repair_2026_11_10(client, db_session)
    job = _feeder_refresh(client, db_session)
    assert job.status == "completed"
    assert job.options == {"separate": False}
    stored = _stored(db_session)
    assert stored["lyrics_source"] == "lrclib"
    assert abs(stored["speed_factor"] - WRONG_R) < 0.01


def test_with_the_override_stored_the_feeder_refresh_keeps_r_and_the_caller_text(
    client, db_session, stages
):
    _repair_2026_11_10(client, db_session)
    _store_override(client)
    searches_before = len(stages)
    for _ in range(2):  # the next refresh, and the one after it
        job = _feeder_refresh(client, db_session)
        assert job.status == "completed", job.error_message
        assert job.options["speed_factor"] == TRUE_R
        assert job.options["lyrics_text"] == CALLER_TEXT
        assert job.options["song_override"]["displaced"] == []
        stored = _stored(db_session)
        assert stored["speed_factor"] == TRUE_R
        assert stored["lyrics_source"] == "caller"
        assert "lyrics_source_id" not in stored
    assert len(stages) == searches_before  # detection never ran again
    etag = db_session.execute(text("SELECT etag FROM processed_tracks")).scalar_one()
    assert client.get(OVERRIDE_URL, headers=ADMIN).json()["document_under_override"] is True
    assert db_session.execute(text("SELECT applied_etag FROM song_overrides")).scalar_one() == etag


def test_a_refresh_queued_before_the_import_cannot_undo_the_override_build(
    client, db_session, stages
):
    """The archive wave enqueued the song before the owner ran the importer:
    its job carries no override and runs after the override build."""
    _repair_2026_11_10(client, db_session)
    # The wave's job, enqueued first (no override yet) ...
    wave = client.post("/v1/admin/reprocess", json={"source": SOURCE}, headers=ADMIN)
    assert wave.status_code == 202
    early = db_session.scalars(select(Job).order_by(Job.created_at.desc())).first()
    assert early is not None and early.options == {"separate": False}
    db_session.execute(text("UPDATE jobs SET status = 'canceled' WHERE id = :id"), {"id": early.id})
    db_session.commit()
    # ... the import and its immediate rebuild ...
    _store_override(client)
    assert _feeder_refresh(client, db_session).status == "completed"
    # ... then the early job runs after all (re-queued as it would have been).
    db_session.execute(
        text("UPDATE jobs SET status = 'queued', next_attempt_at = now() WHERE id = :id"),
        {"id": early.id},
    )
    db_session.commit()
    job = _run_next(db_session)
    assert job.id == early.id and job.status == "completed"
    stored = _stored(db_session)
    assert stored["speed_factor"] == TRUE_R and stored["lyrics_source"] == "caller"


def test_after_a_delete_the_feeder_returns_the_song_to_detection(client, db_session, stages):
    """DELETE hands the song back: the next refresh is judged by the
    ordinary rules again (documented: a reprocess, not the delete, rebuilds)."""
    _repair_2026_11_10(client, db_session)
    _store_override(client)
    _feeder_refresh(client, db_session)
    assert client.delete(OVERRIDE_URL, headers=ADMIN).status_code == 204
    _feeder_refresh(client, db_session)
    stored = _stored(db_session)
    assert stored["lyrics_source"] == "lrclib"
    assert abs(stored["speed_factor"] - WRONG_R) < 0.01
