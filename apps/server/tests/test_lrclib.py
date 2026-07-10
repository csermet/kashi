"""Server-side lyrics fetch: exact -> search, timestamp stripping, etiquette."""

import httpx
import pytest

from kashi_server.pipeline.lrclib import USER_AGENT, fetch_lyrics, normalize_artist
from kashi_server.vdl_kit.errors import PipelineError

HINTS = {"title": "Hotel Room Service", "artist": "Pitbull", "duration_ms": 234_000}


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://lrclib.test",
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": USER_AGENT},
    )


def _fetch(handler, hints=None):
    with _client(handler) as client:
        return fetch_lyrics(hints or HINTS, base_url="https://lrclib.test", client=client)


def test_normalize_artist_strips_topic_suffix():
    assert normalize_artist("Pitbull - Topic") == "Pitbull"
    assert normalize_artist("Pitbull -   topic") == "Pitbull"
    assert normalize_artist("Topic") == "Topic"  # the band, not the suffix


def test_exact_hit_strips_timestamps():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        synced = "[00:12.34] Meet me at the hotel room\n[00:15.00]\n[00:16.20] Forget about it"
        return httpx.Response(200, json={"id": 42, "syncedLyrics": synced})

    lyrics = _fetch(handler)
    assert calls == ["/api/get"]  # search never touched
    assert lyrics.line_texts == ["Meet me at the hotel room", "Forget about it"]
    assert lyrics.full_text == "Meet me at the hotel room Forget about it"
    assert lyrics.had_synced and lyrics.source_id == 42


def test_user_agent_identifies_the_project():
    assert USER_AGENT.startswith("kashi-server/")
    assert "github.com/csermet/kashi" in USER_AGENT


def test_exact_404_falls_through_to_search_with_duration_tolerance():
    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(404)
        return httpx.Response(
            200,
            json=[
                {"id": 1, "plainLyrics": "far off", "duration": 400},  # outside ±3 s
                {"id": 2, "plainLyrics": "close enough", "duration": 235},
            ],
        )

    lyrics = _fetch(handler)
    assert lyrics.source_id == 2 and lyrics.line_texts == ["close enough"]
    assert not lyrics.had_synced


def test_exact_hit_without_lyrics_still_searches():
    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(200, json={"id": 7, "syncedLyrics": None, "plainLyrics": None})
        return httpx.Response(200, json=[{"id": 9, "plainLyrics": "found", "duration": 234}])

    assert _fetch(handler).source_id == 9


def test_instrumental_is_not_lyrics():
    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(200, json={"id": 1, "instrumental": True, "plainLyrics": "x"})
        return httpx.Response(200, json=[])

    with pytest.raises(PipelineError) as exc:
        _fetch(handler)
    assert exc.value.error_type == "lyrics_not_found"


def test_no_candidate_within_tolerance_is_a_permanent_miss():
    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(404)
        return httpx.Response(200, json=[{"id": 1, "plainLyrics": "x", "duration": 999}])

    with pytest.raises(PipelineError) as exc:
        _fetch(handler)
    assert exc.value.error_type == "lyrics_not_found"


def test_network_failure_is_transient_not_a_miss():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(PipelineError) as exc:
        _fetch(handler)
    assert exc.value.error_type == "network"  # retried, unlike lyrics_not_found


def test_missing_hints_fail_before_any_request():
    def handler(request):  # pragma: no cover - must never run
        raise AssertionError("should not hit the network")

    with pytest.raises(PipelineError) as exc:
        _fetch(handler, hints={"title": "", "artist": "x"})
    assert exc.value.error_type == "lyrics_not_found"


def test_topic_suffix_is_stripped_before_querying():
    seen = {}

    def handler(request):
        seen["artist"] = request.url.params.get("artist_name")
        return httpx.Response(200, json={"id": 1, "plainLyrics": "ok"})

    _fetch(handler, hints={"title": "T", "artist": "Rick Astley - Topic"})
    assert seen["artist"] == "Rick Astley"
