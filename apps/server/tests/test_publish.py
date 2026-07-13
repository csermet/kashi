"""lrclib contribute-back (Faz 5 P6): gate, YAML round-trip, PoW, API, worker.

The one mistake this feature must never make is publishing wrong/synthetic
data to a free community service — most tests here are rejection paths.
"""

import hashlib

import httpx
import pytest
from helpers import auth as _auth
from sqlalchemy import select

from kashi_server.db.models import LrclibPublish
from kashi_server.pipeline.lyricsfile import alignresult_from_lyricsfile
from kashi_server.pipeline.publish import (
    _nonce_ok,
    generate_lyricsfile,
    publish_document,
    publish_gate,
    solve_challenge,
)
from kashi_server.vdl_kit.errors import PipelineError


def _doc(**over):
    doc = {
        "schema_version": 1,
        "sync": "word",
        "track": {
            "source": {"type": "youtube", "id": "pubTest0001"},
            "title": "Song",
            "artist": "Artist",
            "duration_ms": 200_000,
        },
        "alignment": {
            "method": "ctc-forced-aligner/mms-300m+line-windowed",
            "lyrics_source": "lrclib",
            "quality_score": 0.9,
            "speed_factor": 1.0,
            "qa": {
                "flagged": 0,
                "density_dropped": 0,
                "adlib_shifted": 0,
                "adlib_rederived": 1,
                "offset_ms": 0,
                "trimmed_ends": 2,
            },
        },
        "lines": [
            {
                "start_ms": 1000,
                "end_ms": 3000,
                "text": "Hello wide world",
                "words": [
                    {"start_ms": 1000, "end_ms": 1400, "text": "Hello"},
                    {"start_ms": 1500, "end_ms": 2100, "text": "wide"},
                    {"start_ms": 2100, "end_ms": 3000, "text": "world"},
                ],
            },
            {
                "start_ms": 4000,
                "end_ms": 6000,
                "text": "Ooh ooh",
                "adlib": True,
                "words_derived": True,
                "words": [
                    {"start_ms": 4000, "end_ms": 5000, "text": "Ooh"},
                    {"start_ms": 5000, "end_ms": 6000, "text": "ooh"},
                ],
            },
        ],
    }
    doc.update(over)
    return doc


def test_gate_accepts_the_clean_document():
    assert publish_gate(_doc()) == []


def test_gate_rejects_every_forbidden_provenance():
    def reasons(**over):
        return publish_gate(_doc(**over))

    assert reasons(sync="line")  # not word sync
    caller = _doc()
    caller["alignment"]["lyrics_source"] = "caller"  # copyright question
    assert publish_gate(caller)
    lf = _doc()
    lf["alignment"]["lyrics_source"] = "lyricsfile"  # human data: never re-publish
    assert publish_gate(lf)
    nightcore = _doc()
    nightcore["alignment"]["speed_factor"] = 1.25  # wrong clock for the record
    assert publish_gate(nightcore)
    lowq = _doc()
    lowq["alignment"]["quality_score"] = 0.4
    assert publish_gate(lowq)
    flagged = _doc()
    flagged["alignment"]["qa"]["flagged"] = 2  # aligner lost lock
    assert publish_gate(flagged)
    old = _doc()
    del old["alignment"]["qa"]  # pre-2.3.0 doc without provenance
    assert publish_gate(old)


def test_generate_lyricsfile_round_trips_and_hides_derived_words():
    yaml_text = generate_lyricsfile(_doc())
    result = alignresult_from_lyricsfile(yaml_text, duration_s=200.0)
    assert result is not None
    # Measured words round-trip byte-exact on the time axis…
    words = result.words_per_line[0]
    assert [(w.start_ms, w.end_ms, w.text) for w in words] == [
        (1000, 1400, "Hello"),
        (1500, 2100, "wide"),
        (2100, 3000, "world"),
    ]
    # …while rederived (synthetic) spans are published as a wordless line.
    assert result.words_per_line[1] == []
    assert "Ooh ooh" in yaml_text and "words_derived" not in yaml_text
    # Trailing-space rule: every word but the line's last carries one.
    assert '"Hello "' in yaml_text or "Hello ''" in yaml_text or "'Hello '" in yaml_text


def test_pow_solves_an_easy_target_and_token_verifies():
    target_hex = "f" * 64  # every hash passes: nonce 0
    assert solve_challenge("prefix", target_hex) == "0"
    # A harder (but quick) target: leading zero nibble.
    target_hex = "0f" + "f" * 62
    nonce = solve_challenge("kashi-test", target_hex, max_attempts=200_000)
    digest = hashlib.sha256(f"kashi-test{nonce}".encode()).digest()
    assert _nonce_ok(digest, bytes.fromhex(target_hex))


def test_pow_gives_up_honestly():
    with pytest.raises(PipelineError):
        solve_challenge("p", "00" * 32, max_attempts=50)


def test_publish_document_sends_token_and_body():
    seen: dict = {}

    def handler(request):
        if request.url.path == "/api/request-challenge":
            return httpx.Response(200, json={"prefix": "abc", "target": "f" * 64})
        seen["token"] = request.headers.get("X-Publish-Token")
        import json as _json

        seen["body"] = _json.loads(request.content.decode())
        return httpx.Response(201, json={})

    with httpx.Client(
        base_url="https://lrclib.test", transport=httpx.MockTransport(handler)
    ) as client:
        publish_document(_doc(), base_url="https://lrclib.test", client=client)
    assert seen["token"] == "abc:0"
    assert seen["body"]["trackName"] == "Song" and seen["body"]["duration"] == 200
    assert seen["body"]["lyricsfile"].startswith("version:")
    assert "[00:01.00] Hello wide world" in seen["body"]["syncedLyrics"]


# --- API + worker (DB-backed) ---


def _persist_doc(db_session, doc):
    import json as _json
    import uuid as _uuid

    from kashi_server.db.models import Job, ProcessedTrack
    from kashi_server.pipeline.document import compute_etag

    job = Job(
        source_type="youtube",
        source_id=doc["track"]["source"]["id"],
        pipeline_major=2,
        hints={},
        options={},
    )
    job.id = _uuid.uuid4()
    db_session.add(job)
    etag = compute_etag(doc)
    db_session.add(
        ProcessedTrack(
            source_type="youtube",
            source_id=doc["track"]["source"]["id"],
            schema_version=1,
            pipeline_version="2.4.0",
            pipeline_major=2,
            sync=doc["sync"],
            quality_score=doc["alignment"]["quality_score"],
            title=doc["track"]["title"],
            artist=doc["track"]["artist"],
            duration_ms=doc["track"]["duration_ms"],
            document=_json.loads(_json.dumps(doc)),
            etag=etag,
            job_id=job.id,
        )
    )
    db_session.flush()
    return etag


def test_publish_request_endpoint_gates_and_dedupes(client, user_key, db_session, monkeypatch):
    from kashi_server.config import settings

    body = {"source": {"type": "youtube", "id": "pubTest0001"}}

    def post():
        return client.post("/v1/publish-requests", json=body, headers=_auth(user_key))

    assert post().status_code == 409  # hard-off by default

    monkeypatch.setattr(settings, "lrclib_publish_enabled", True)
    assert post().status_code == 404  # no document yet

    _persist_doc(db_session, _doc())
    db_session.commit()
    first = client.post("/v1/publish-requests", json=body, headers=_auth(user_key))
    assert first.status_code == 202 and first.json()["status"] == "queued"
    again = client.post("/v1/publish-requests", json=body, headers=_auth(user_key))
    assert again.json()["id"] == first.json()["id"]  # (source, etag) dedup

    # A nightcore document is rejected at the door.
    bad = _doc()
    bad["track"]["source"]["id"] = "pubTestNC01"
    bad["alignment"]["speed_factor"] = 1.3
    _persist_doc(db_session, bad)
    db_session.commit()
    resp = client.post(
        "/v1/publish-requests",
        json={"source": {"type": "youtube", "id": "pubTestNC01"}},
        headers=_auth(user_key),
    )
    assert resp.status_code == 422 and "nightcore" in resp.json()["detail"]


def test_worker_dry_run_marks_and_logs_without_publishing(db_session, monkeypatch, caplog):
    from kashi_server.config import settings
    from kashi_server.worker.publisher import process_one_publish

    monkeypatch.setattr(settings, "lrclib_publish_enabled", True)
    assert settings.lrclib_publish_dry_run is True  # the default must be safe

    doc = _doc()
    doc["track"]["source"]["id"] = "pubTestDry1"
    etag = _persist_doc(db_session, doc)
    db_session.add(LrclibPublish(source_type="youtube", source_id="pubTestDry1", etag=etag))
    db_session.flush()

    def explode(*a, **kw):
        raise AssertionError("dry run must not touch lrclib")

    monkeypatch.setattr("kashi_server.worker.publisher.publish_document", explode)
    assert process_one_publish(db_session) is True
    row = db_session.scalars(select(LrclibPublish)).one()
    assert row.status == "dry_run" and row.finished_at is not None
    assert process_one_publish(db_session) is False  # queue drained


def test_worker_fails_when_document_moved_on(db_session, monkeypatch):
    from kashi_server.config import settings
    from kashi_server.worker.publisher import process_one_publish

    monkeypatch.setattr(settings, "lrclib_publish_enabled", True)
    doc = _doc()
    doc["track"]["source"]["id"] = "pubTestGone"
    _persist_doc(db_session, doc)
    db_session.add(
        LrclibPublish(source_type="youtube", source_id="pubTestGone", etag="stale-etag")
    )
    db_session.flush()
    assert process_one_publish(db_session) is True
    row = db_session.scalars(select(LrclibPublish)).one()
    assert row.status == "failed" and "changed or vanished" in (row.error or "")
