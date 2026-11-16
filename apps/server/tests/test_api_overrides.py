"""/v1/admin/overrides (C.1) and the enqueue merge that makes an override reach
every job: ingest, admin reprocess (the archive feeder sends no options)."""

import uuid

import pytest
from helpers import TEST_ADMIN_KEY
from helpers import auth as _auth

from kashi_server import overrides, queue
from kashi_server.db.models import Job

VID = "ovrApiVid01"
URL = f"/v1/admin/overrides/youtube/{VID}"
ADMIN = _auth(TEST_ADMIN_KEY)
LRC = "[00:01.00] hello world\n[00:05.00] again\n"


def _job(db_session, job_id) -> Job:
    job = db_session.get(Job, uuid.UUID(job_id))
    assert job is not None
    db_session.refresh(job)
    return job


# --- auth -------------------------------------------------------------------


def test_admin_only(client, user_key):
    for method in ("get", "delete"):
        assert getattr(client, method)(URL, headers=_auth(user_key)).status_code == 403
    assert client.put(URL, json={"speed_factor": 1.1}, headers=_auth(user_key)).status_code == 403
    assert client.get("/v1/admin/overrides", headers=_auth(user_key)).status_code == 403
    assert client.get(URL).status_code == 401


# --- CRUD ---------------------------------------------------------------------


def test_put_creates_then_replaces_get_reads_delete_removes(client):
    created = client.put(URL, json={"speed_factor": 1.1289, "note": "nightcore"}, headers=ADMIN)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == {"type": "youtube", "id": VID}
    assert body["speed_factor"] == 1.1289
    assert body["lrclib_id"] is None and body["lyrics_text"] is None
    assert body["plain_only"] is False
    assert body["document_under_override"] is False

    replaced = client.put(URL, json={"lrclib_id": 17581840, "offset_ms": -20800}, headers=ADMIN)
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["lrclib_id"] == 17581840 and body["offset_ms"] == -20800
    assert body["speed_factor"] is None  # PUT replaces the whole override
    assert body["note"] is None

    got = client.get(URL, headers=ADMIN)
    assert got.status_code == 200 and got.json()["lrclib_id"] == 17581840

    assert client.delete(URL, headers=ADMIN).status_code == 204
    assert client.get(URL, headers=ADMIN).status_code == 404
    assert client.delete(URL, headers=ADMIN).status_code == 404


def test_list_leaves_the_text_out(client):
    client.put(URL, json={"lyrics_text": LRC}, headers=ADMIN)
    client.put("/v1/admin/overrides/youtube/ovrApiVid02", json={"plain_only": True}, headers=ADMIN)
    listed = client.get("/v1/admin/overrides", headers=ADMIN)
    assert listed.status_code == 200
    by_id = {entry["source"]["id"]: entry for entry in listed.json()}
    assert set(by_id) == {VID, "ovrApiVid02"}
    assert "lyrics_text" not in by_id[VID]
    assert by_id[VID]["lyrics_text_chars"] == len(LRC)
    assert by_id[VID]["lyrics_text_lrc"] is True
    assert by_id["ovrApiVid02"]["lyrics_text_chars"] is None
    assert by_id["ovrApiVid02"]["plain_only"] is True


# --- validation ------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},  # nothing to override
        {"note": "only a note"},
        {"lyrics_text": ""},
        {"lyrics_text": "   \n  "},
        {"lyrics_text": "x" * 20_001},  # the reprocess bound
        {"lyrics_text": "words", "lrclib_id": 5},  # one lyrics source
        {"offset_ms": 1500},  # an offset needs its sheet
        {"offset_ms": 1500, "lyrics_text": "plain words"},
        {"offset_ms": 1500, "speed_factor": 1.2},
        {"lrclib_id": 5, "offset_ms": 300_001},
        {"lrclib_id": 5, "offset_ms": -300_001},
        {"speed_factor": 1.0},  # 1.0 < r <= 2.0, as for the request option
        {"speed_factor": 2.0001},
        {"speed_factor": 0.9},
        {"lrclib_id": 0},
        {"lyrics_text": "[00:01.00]\n[00:02.00]"},  # LRC with no lyric line
        {"speed_factor": 1.2, "speed": 1.2},  # unknown key: a typo must not pass
    ],
)
def test_put_refuses_incoherent_or_out_of_bounds_bodies(client, body):
    resp = client.put(URL, json=body, headers=ADMIN)
    assert resp.status_code == 422, (body, resp.text)
    assert client.get(URL, headers=ADMIN).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"speed_factor": 2.0},
        {"speed_factor": 1.0001},
        {"lyrics_text": "x" * 20_000},
        {"lrclib_id": 5, "offset_ms": 300_000},
        {"lrclib_id": 5, "offset_ms": -300_000},
        {"lyrics_text": LRC, "offset_ms": -500},  # LRC text carries its own sheet
        {"plain_only": True},
        {"lrclib_id": 5, "offset_ms": 0},  # zero offset = none
    ],
)
def test_put_accepts_the_bounds_themselves(client, body):
    resp = client.put(URL, json=body, headers=ADMIN)
    assert resp.status_code == 201, (body, resp.text)
    if body.get("offset_ms") == 0:
        assert resp.json()["offset_ms"] is None


@pytest.mark.parametrize("path", ["youtube/bad%2Fid", "youtube/has.dot", "vimeo/abc"])
def test_put_refuses_paths_no_song_can_have(client, path):
    resp = client.put(f"/v1/admin/overrides/{path}", json={"speed_factor": 1.2}, headers=ADMIN)
    assert resp.status_code in (404, 422)


# --- the merge reaches every job --------------------------------------------------

_HINTS = {"title": "Nightcore - Song", "artist": "Chan"}


def test_admin_reprocess_without_options_applies_the_override(client, db_session):
    """The archive feeder: POST /v1/admin/reprocess with no options at all."""
    client.put(URL, json={"speed_factor": 1.1504, "lyrics_text": "hello\nworld"}, headers=ADMIN)
    resp = client.post(
        "/v1/admin/reprocess",
        json={"source": {"type": "youtube", "id": VID}, "hints": _HINTS},
        headers=ADMIN,
    )
    assert resp.status_code == 202, resp.text
    job = _job(db_session, resp.json()["job_id"])
    assert job.options["speed_factor"] == 1.1504
    assert job.options["lyrics_text"] == "hello\nworld"
    assert job.options["separate"] is False
    assert job.options[overrides.MARKER]["displaced"] == []


def test_explicit_reprocess_options_win_for_that_run(client, db_session):
    client.put(URL, json={"speed_factor": 1.1504, "lrclib_id": 99}, headers=ADMIN)
    resp = client.post(
        "/v1/admin/reprocess",
        json={
            "source": {"type": "youtube", "id": VID},
            "hints": _HINTS,
            "options": {"speed_factor": 1.25, "original_title": "Song"},
        },
        headers=ADMIN,
    )
    job = _job(db_session, resp.json()["job_id"])
    assert job.options["speed_factor"] == 1.25
    assert job.options["original_title"] == "Song"
    assert "lrclib_id" not in job.options
    assert job.options[overrides.MARKER]["displaced"] == ["lrclib_id", "speed_factor"]


def test_ingest_of_a_new_song_applies_the_override(client, user_key, db_session):
    client.put(URL, json={"lrclib_id": 4242}, headers=ADMIN)
    resp = client.post(
        "/v1/ingest",
        json={"source": {"type": "youtube", "id": VID}, "hints": _HINTS},
        headers=_auth(user_key),
    )
    assert resp.status_code == 202
    job = _job(db_session, resp.json()["job_id"])
    assert job.options["lrclib_id"] == 4242


def test_a_job_already_live_keeps_the_options_it_was_enqueued_with(client, user_key, db_session):
    live = client.post(
        "/v1/ingest",
        json={"source": {"type": "youtube", "id": VID}, "hints": _HINTS},
        headers=_auth(user_key),
    )
    client.put(URL, json={"lrclib_id": 4242}, headers=ADMIN)
    again = client.post(
        "/v1/admin/reprocess", json={"source": {"type": "youtube", "id": VID}}, headers=ADMIN
    )
    assert again.json()["job_id"] == live.json()["job_id"]  # the live job wins, as before
    assert "lrclib_id" not in _job(db_session, live.json()["job_id"]).options


REQUEST_OPTIONS = [
    {},
    {"separate": False},
    {"separate": True},
    {"lyrics_text": "hello\nworld"},
    {"speed_factor": 1.25, "lyrics_text": "x"},
    {"original_title": "Song"},
    {"force_persist": True, "separate": False},
]


@pytest.mark.parametrize("request_options", REQUEST_OPTIONS)
@pytest.mark.parametrize("path", ["enqueue", "enqueue_reprocess"])
def test_without_a_row_the_job_holds_exactly_the_request_options(db_session, request_options, path):
    """No-change proof, enqueue side: an empty table (and a row for another
    song) leaves the job's options identical to what main stores."""
    from kashi_server.db.models import SongOverride

    db_session.add(SongOverride(source_type="youtube", source_id="anotherSong", lrclib_id=1))
    db_session.flush()
    sent = dict(request_options)
    kwargs = dict(
        source_type="youtube",
        source_id="noRowSong01",
        pipeline_major=2,
        hints={"title": "T", "artist": "A"},
        options=sent,
        requested_by=None,
    )
    if path == "enqueue":
        job, _ = queue.enqueue(db_session, **kwargs)
    else:
        job = queue.enqueue_reprocess(db_session, **kwargs)
    db_session.commit()
    db_session.expire_all()
    stored = db_session.get(Job, job.id)
    assert stored is not None
    assert stored.options == request_options
    assert sent == request_options  # the caller's dict is untouched


def test_b2_a_content_change_clears_the_built_under_mark_a_note_change_keeps_it(client, db_session):
    from sqlalchemy import text

    client.put(URL, json={"speed_factor": 1.2, "note": "a"}, headers=ADMIN)
    db_session.execute(text("UPDATE song_overrides SET applied_etag = 'e1'"))
    db_session.commit()

    def mark():
        db_session.expire_all()
        return db_session.execute(text("SELECT applied_etag FROM song_overrides")).scalar_one()

    client.put(URL, json={"speed_factor": 1.2, "note": "b", "plain_only": True}, headers=ADMIN)
    assert mark() == "e1"
    client.put(URL, json={"speed_factor": 1.21, "note": "b"}, headers=ADMIN)
    assert mark() is None


def test_document_under_override_reports_the_stored_etag(client, db_session):
    from sqlalchemy import text

    from kashi_server.pipeline.alignment import AlignResult, LineTiming
    from kashi_server.pipeline.document import build_document, persist_processed_track
    from kashi_server.pipeline.lrclib import LyricsText
    from kashi_server.pipeline.palette import DEFAULT_PALETTE

    client.put(URL, json={"speed_factor": 1.2}, headers=ADMIN)
    fields = {"speed_factor": 1.2}
    job = Job(
        source_type="youtube",
        source_id=VID,
        pipeline_major=2,
        hints={"title": "T", "artist": "A"},
        options=overrides.merge({}, fields),
    )
    db_session.add(job)
    db_session.flush()
    doc = build_document(
        job,
        LyricsText(["la"], "la", 1, False),
        AlignResult(
            sync="line", lines=[LineTiming(0, 900, "la", 0.9)], words_per_line=[], quality_score=0.9
        ),
        None,
        dict(DEFAULT_PALETTE),
        vocals_separated=False,
        speed_factor=1.2,
        fallback_duration_ms=200_000,
    )
    etag = persist_processed_track(db_session, job, doc)
    db_session.commit()
    assert db_session.execute(text("SELECT applied_etag FROM song_overrides")).scalar_one() == etag
    assert client.get(URL, headers=ADMIN).json()["document_under_override"] is True
    listed = client.get("/v1/admin/overrides", headers=ADMIN).json()
    assert listed[0]["document_under_override"] is True
