"""Per-song overrides (review step C.1): precedence, the worker's lyrics side,
and the enqueue merge. The persist guard has its own file
(test_override_guard.py), the admin API too (test_api_overrides.py)."""

import pytest

from kashi_server import overrides
from kashi_server.pipeline.lrclib import (
    _shift_stamps,
    is_lrc,
    lyrics_from_lrc,
    lyrics_from_record,
)
from kashi_server.vdl_kit.errors import PipelineError

LRC = "[00:01.00] hello world\n[00:05.50] second line\n"


# --- precedence: merge(request, override) ----------------------------------


def test_no_override_fields_return_the_request_object_itself():
    request = {"separate": False, "lyrics_text": "x"}
    assert overrides.merge(request, {}) is request


def test_the_override_fills_what_the_request_leaves_out():
    merged = overrides.merge(
        {"separate": False}, {"lrclib_id": 77, "speed_factor": 1.1289, "offset_ms": -1500}
    )
    assert merged["lrclib_id"] == 77
    assert merged["speed_factor"] == 1.1289
    assert merged["sheet_offset_ms"] == -1500
    assert merged["separate"] is False
    marker = merged[overrides.MARKER]
    assert marker["applied"] == ["lrclib_id", "speed_factor", "offset_ms"]
    assert marker["displaced"] == []


def test_plain_text_rides_the_existing_caller_key_and_lrc_gets_its_own():
    plain = overrides.merge({}, {"lyrics_text": "hello\nworld"})
    assert plain["lyrics_text"] == "hello\nworld"
    assert overrides.LRC_TEXT_KEY not in plain
    lrc = overrides.merge({}, {"lyrics_text": LRC})
    assert lrc[overrides.LRC_TEXT_KEY] == LRC
    assert "lyrics_text" not in lrc  # a request's lyrics_text keeps its plain meaning


@pytest.mark.parametrize("request_key", ["lyrics_text", "original_title"])
def test_a_request_lyrics_choice_displaces_the_whole_override_lyrics_group(request_key):
    request = {request_key: "from the request"}
    merged = overrides.merge(request, {"lrclib_id": 77, "offset_ms": 900, "speed_factor": 1.2})
    assert merged[request_key] == "from the request"
    assert "lrclib_id" not in merged
    assert overrides.SHEET_OFFSET_KEY not in merged  # the offset belongs to the pinned sheet
    assert merged["speed_factor"] == 1.2  # the clock group is not the lyrics group
    marker = merged[overrides.MARKER]
    assert marker["applied"] == ["speed_factor"]
    assert marker["displaced"] == ["lrclib_id", "offset_ms"]


def test_a_request_lyrics_text_beats_the_override_text():
    merged = overrides.merge({"lyrics_text": "request text"}, {"lyrics_text": "override text"})
    assert merged["lyrics_text"] == "request text"
    assert merged[overrides.MARKER]["displaced"] == ["lyrics_text"]


def test_a_request_speed_factor_displaces_the_override_factor_only():
    merged = overrides.merge({"speed_factor": 1.3}, {"speed_factor": 1.1, "lyrics_text": "t"})
    assert merged["speed_factor"] == 1.3
    assert merged["lyrics_text"] == "t"
    assert merged[overrides.MARKER]["displaced"] == ["speed_factor"]


def test_request_keys_outside_both_groups_displace_nothing():
    merged = overrides.merge(
        {"separate": True, "force_persist": True}, {"lrclib_id": 5, "speed_factor": 1.1}
    )
    assert merged[overrides.MARKER]["displaced"] == []
    assert merged["force_persist"] is True and merged["separate"] is True


def test_the_request_dict_is_not_mutated():
    request = {"separate": False}
    overrides.merge(request, {"lrclib_id": 5})
    assert request == {"separate": False}


# --- fingerprint / applied --------------------------------------------------


def test_fingerprint_follows_content_not_order():
    a = overrides.fingerprint({"lrclib_id": 5, "speed_factor": 1.1})
    b = overrides.fingerprint({"speed_factor": 1.1, "lrclib_id": 5})
    assert a == b
    assert a != overrides.fingerprint({"lrclib_id": 5, "speed_factor": 1.1001})


def test_only_a_whole_application_counts_as_applied():
    whole = overrides.merge({}, {"lrclib_id": 5})
    assert overrides.applied_fingerprint(whole) == overrides.fingerprint({"lrclib_id": 5})
    partial = overrides.merge({"original_title": "x"}, {"lrclib_id": 5, "speed_factor": 1.1})
    assert overrides.applied_fingerprint(partial) is None
    assert overrides.applied_fingerprint({}) is None
    assert overrides.applied_fingerprint(None) is None


class _Row:
    def __init__(self, **kw):
        self.lrclib_id = kw.get("lrclib_id")
        self.lyrics_text = kw.get("lyrics_text")
        self.speed_factor = kw.get("speed_factor")
        self.offset_ms = kw.get("offset_ms")


def test_acted_fields_skip_unset_and_zero_offset():
    assert overrides.acted_fields(_Row()) == {}
    assert overrides.acted_fields(_Row(offset_ms=0, speed_factor=1.5)) == {"speed_factor": 1.5}


# --- the worker's lyrics side ----------------------------------------------


_RECORD = {
    "id": 4242,
    "trackName": "Song",
    "artistName": "Artist",
    "duration": 100.0,
    "syncedLyrics": "[00:10.00] first\n[00:50.00] middle\n[01:45.00] last",
    "plainLyrics": "first\nmiddle\nlast",
    "lyricsfile": "lines:\n  - words: x",
}


def _never(record_id):
    raise AssertionError("lrclib asked")


def test_no_override_keys_ask_nothing():
    assert overrides.override_lyrics({"lyrics_text": "x", "separate": True}, _never) is None


def test_a_pinned_record_is_fetched_by_id_and_marked_pinned():
    asked = []
    lyrics = overrides.override_lyrics(
        {"lrclib_id": 4242}, lambda rid: asked.append(rid) or _RECORD
    )
    assert asked == [4242]
    assert lyrics is not None
    assert lyrics.pinned is True
    assert lyrics.source == "lrclib" and lyrics.source_id == 4242
    # 105 s is past the record's 100 s (+1 s slack): blanked, as on the ladder.
    assert lyrics.synced_starts_ms == [10_000, 50_000, None]
    assert lyrics.lyricsfile_raw is not None  # no offset: the record as served


def test_the_sheet_offset_moves_stamps_before_the_overrun_check():
    """A sheet 20 s late has its last stamp past the record's end (100 s + 1 s
    slack): blanked if checked first, valid once moved back."""
    lyrics = overrides.override_lyrics(
        {"lrclib_id": 4242, overrides.SHEET_OFFSET_KEY: -20_000}, lambda rid: _RECORD
    )
    assert lyrics is not None
    assert lyrics.synced_starts_ms == [None, 30_000, 85_000]  # 10 s - 20 s < 0 -> blank
    assert lyrics.lyricsfile_raw is None  # word times on the uncorrected clock are dropped


def test_a_positive_offset_past_the_record_end_is_blanked():
    lyrics = overrides.override_lyrics(
        {"lrclib_id": 4242, overrides.SHEET_OFFSET_KEY: 51_000}, lambda rid: _RECORD
    )
    assert lyrics is not None
    assert lyrics.synced_starts_ms == [61_000, 101_000, None]  # 101 s is the slack's edge


def test_the_unshifted_record_is_byte_identical_to_the_ladder_path():
    assert overrides.override_lyrics({"lrclib_id": 4242}, lambda rid: _RECORD) == (
        _with_pin(lyrics_from_record(_RECORD))
    )


def _with_pin(lyrics):
    from dataclasses import replace

    return replace(lyrics, pinned=True)


@pytest.mark.parametrize(
    "answer",
    [
        None,  # 404: the record is gone
        PipelineError("lyrics_not_found", "lrclib rejected the request (400)"),
        {"id": 4242, "instrumental": True},  # unusable
        {"id": 4242, "syncedLyrics": "", "plainLyrics": ""},
    ],
)
def test_a_pin_that_cannot_serve_fails_as_other_never_lyrics_not_found(answer):
    """lyrics_not_found would hand the job to the by-ear rung and rebuild the
    document the pin exists to replace."""

    def get(rid):
        if isinstance(answer, Exception):
            raise answer
        return answer

    with pytest.raises(PipelineError) as err:
        overrides.override_lyrics({"lrclib_id": 4242}, get)
    assert err.value.error_type == "other"
    assert "4242" in err.value.message


@pytest.mark.parametrize("error_type", ["network", "rate_limited", "lrclib_unavailable"])
def test_lrclib_weather_on_a_pin_stays_transient(error_type):
    def get(rid):
        raise PipelineError(error_type, "busy")

    with pytest.raises(PipelineError) as err:
        overrides.override_lyrics({"lrclib_id": 4242}, get)
    assert err.value.error_type == error_type


def test_caller_lrc_keeps_its_stamps_and_caller_provenance():
    lyrics = overrides.override_lyrics({overrides.LRC_TEXT_KEY: LRC}, _never)
    assert lyrics is not None
    assert lyrics.source == "caller" and lyrics.source_id == 0
    assert lyrics.line_texts == ["hello world", "second line"]
    assert lyrics.synced_starts_ms == [1000, 5500]
    assert lyrics.pinned is False


def test_caller_lrc_takes_the_offset_too():
    lyrics = overrides.override_lyrics(
        {overrides.LRC_TEXT_KEY: LRC, overrides.SHEET_OFFSET_KEY: -2000}, _never
    )
    assert lyrics is not None
    assert lyrics.synced_starts_ms == [None, 3500]


def test_lrc_detection():
    assert is_lrc(LRC)
    assert is_lrc("plain first line\n[01:02.03] then a stamp")
    assert not is_lrc("hello\nworld [not a stamp]")
    with pytest.raises(PipelineError):
        lyrics_from_lrc("[00:01.00]\n[00:02.00]   \n")


def test_shift_stamps_zero_is_the_same_list():
    starts = [1, None, 3]
    assert _shift_stamps(starts, 0) is starts
    assert _shift_stamps(starts, -2) == [None, None, 1]


# --- the gateway's record-by-id door ----------------------------------------


def _gateway(get, *, mirror="http://mirror"):
    from kashi_server.pipeline.lrclib_gateway import LrclibBreaker, LrclibGateway

    return LrclibGateway(
        fetch=lambda hints, url: (_ for _ in ()).throw(AssertionError("fetch")),
        search=lambda query, url: [],
        get=get,
        primary_url="http://primary",
        mirror_url=mirror,
        breaker=LrclibBreaker(),
    )


def test_gateway_primary_404_is_none_and_the_mirror_is_not_asked():
    asked = []
    gw = _gateway(lambda rid, url: asked.append(url) or None)
    assert gw.get_record(5) is None
    assert asked == ["http://primary"]


def test_gateway_busy_primary_falls_back_to_the_mirror():
    def get(rid, url):
        if url == "http://primary":
            raise PipelineError("network", "503")
        return {"id": rid}

    assert _gateway(get).get_record(5) == {"id": 5}


def test_gateway_mirror_miss_while_busy_defers():
    def get(rid, url):
        if url == "http://primary":
            raise PipelineError("network", "503")
        return None

    with pytest.raises(PipelineError) as err:
        _gateway(get).get_record(5)
    assert err.value.error_type == "lrclib_unavailable"


def test_fetch_record_asks_the_by_id_route(monkeypatch):
    import httpx

    from kashi_server.pipeline import lrclib

    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/404"):
            return httpx.Response(404)
        return httpx.Response(200, json={"id": 7, "plainLyrics": "x"})

    client = httpx.Client(base_url="http://lrclib.test", transport=httpx.MockTransport(handler))
    assert lrclib.fetch_record(7, base_url="http://lrclib.test", client=client) == {
        "id": 7,
        "plainLyrics": "x",
    }
    assert lrclib.fetch_record(404, base_url="http://lrclib.test", client=client) is None
    assert seen == ["/api/get/7", "/api/get/404"]


def test_the_worker_gateway_can_fetch_records(monkeypatch):
    from kashi_server.worker import process as wp

    monkeypatch.setattr(wp, "fetch_record", lambda rid, *, base_url: {"id": rid, "u": base_url})
    assert wp.build_gateway().get_record(9)["id"] == 9
