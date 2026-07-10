"""GET /v1/lyrics: 404 / 200+ETag / 304 / 400 unsupported schema_version."""

from helpers import auth as _auth


def _insert_processed(db_session, source_id="vidLyrics01", etag="e" * 32):
    from kashi_server.db.models import ProcessedTrack

    doc = {"schema_version": 1, "sync": "line", "lines": []}
    db_session.add(
        ProcessedTrack(
            source_type="youtube",
            source_id=source_id,
            pipeline_version="1.0.0",
            pipeline_major=1,
            sync="line",
            quality_score=1.0,
            document=doc,
            etag=etag,
        )
    )
    db_session.commit()
    return doc


def test_lyrics_404_when_absent(client, user_key):
    resp = client.get("/v1/lyrics/youtube/nope", headers=_auth(user_key))
    assert resp.status_code == 404


def test_lyrics_200_with_etag_then_304(client, user_key, db_session):
    doc = _insert_processed(db_session)
    resp = client.get("/v1/lyrics/youtube/vidLyrics01", headers=_auth(user_key))
    assert resp.status_code == 200
    assert resp.json() == doc
    assert resp.headers["ETag"] == '"' + "e" * 32 + '"'
    assert "must-revalidate" in resp.headers["Cache-Control"]

    revalidated = client.get(
        "/v1/lyrics/youtube/vidLyrics01",
        headers=_auth(user_key) | {"If-None-Match": resp.headers["ETag"]},
    )
    assert revalidated.status_code == 304
    assert revalidated.content == b""


def test_lyrics_304_tolerates_weak_and_list_etags(client, user_key, db_session):
    _insert_processed(db_session)
    resp = client.get(
        "/v1/lyrics/youtube/vidLyrics01",
        headers=_auth(user_key) | {"If-None-Match": f'W/"other", "{"e" * 32}"'},
    )
    assert resp.status_code == 304


def test_lyrics_stale_etag_gets_fresh_body(client, user_key, db_session):
    _insert_processed(db_session)
    resp = client.get(
        "/v1/lyrics/youtube/vidLyrics01",
        headers=_auth(user_key) | {"If-None-Match": '"stale"'},
    )
    assert resp.status_code == 200


def test_lyrics_unknown_schema_version(client, user_key, db_session):
    _insert_processed(db_session)
    resp = client.get(
        "/v1/lyrics/youtube/vidLyrics01?schema_version=2", headers=_auth(user_key)
    )
    assert resp.status_code == 400
