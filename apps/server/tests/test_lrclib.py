"""Server-side lyrics fetch: exact -> search, timestamp stripping, etiquette."""

import httpx
import pytest

from kashi_server.pipeline.lrclib import (
    USER_AGENT,
    fetch_lyrics,
    has_usable_lyrics,
    normalize_artist,
    plausible_match,
    title_covers,
)
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
    # get hit lacks word-level lyricsfile data -> exactly ONE upgrade probe
    # (2.4.3); the dict-shaped search response upgrades nothing.
    assert calls == ["/api/get", "/api/search"]
    assert lyrics.line_texts == ["Meet me at the hotel room", "Forget about it"]
    assert lyrics.full_text == "Meet me at the hotel room Forget about it"
    assert lyrics.had_synced and lyrics.source_id == 42


def test_synced_starts_are_kept_parallel_to_lines():
    def handler(request):
        synced = "[00:12.34] First\n[00:15.00]\n[01:02.500] Second\nBare line"
        return httpx.Response(200, json={"id": 1, "syncedLyrics": synced})

    lyrics = _fetch(handler)
    assert lyrics.line_texts == ["First", "Second", "Bare line"]
    # 2-digit fraction = centiseconds, 3-digit (below) = milliseconds; a
    # stampless line keeps its slot as None so the lists stay parallel.
    assert lyrics.synced_starts_ms == [12_340, 62_500, None]
    assert len(lyrics.synced_starts_ms) == len(lyrics.line_texts)


def test_multi_stamp_lrc_line_uses_first_stamp_and_clean_text():
    def handler(request):
        synced = "[00:10.00][00:40.000] Repeated hook"
        return httpx.Response(200, json={"id": 1, "syncedLyrics": synced})

    lyrics = _fetch(handler)
    assert lyrics.line_texts == ["Repeated hook"]  # no stamp leaks into the text
    assert lyrics.synced_starts_ms == [10_000]


def test_plain_lyrics_have_no_synced_starts():
    def handler(request):
        return httpx.Response(200, json={"id": 1, "plainLyrics": "one\ntwo"})

    lyrics = _fetch(handler)
    assert lyrics.line_texts == ["one", "two"]
    assert lyrics.synced_starts_ms is None and not lyrics.had_synced


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
    searches = []

    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(404)
        searches.append(dict(request.url.params))
        return httpx.Response(200, json=[{"id": 1, "plainLyrics": "x", "duration": 999}])

    with pytest.raises(PipelineError) as exc:
        _fetch(handler)
    assert exc.value.error_type == "lyrics_not_found"
    # Both search rungs ran: structured first, then the free-text q= fallback
    # (whose only candidate is rejected — no shared title/artist tokens AND
    # out of duration tolerance).
    assert [("q" in params) for params in searches] == [False, True]


WET_HINTS = {"title": "Wet", "artist": "Snoop Dogg", "duration_ms": 195_000}
WET_REMIX = {
    "id": 1867697,
    "trackName": "Wet (Snoop Dogg vs. David Guetta) [Remix]",
    "artistName": "Snoop Dogg & David Guetta",
    "plainLyrics": "remix lines",
    "duration": 195,
}


def test_freetext_fallback_finds_remix_record():
    calls = []

    def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/api/get":
            return httpx.Response(404)
        if "q" in request.url.params:
            return httpx.Response(200, json=[WET_REMIX])
        return httpx.Response(200, json=[])  # structured search misses the remix

    lyrics = _fetch(handler, hints=WET_HINTS)
    assert lyrics.source_id == 1867697
    assert calls[-1][1] == {"q": "Snoop Dogg Wet"}
    assert [path for path, _ in calls] == ["/api/get", "/api/search", "/api/search"]


def test_freetext_fires_when_structured_candidates_are_out_of_tolerance():
    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(404)
        if "q" in request.url.params:
            return httpx.Response(200, json=[WET_REMIX])
        # Structured search finds only a same-title record far off in duration.
        return httpx.Response(200, json=[{"id": 3, "plainLyrics": "x", "duration": 400}])

    assert _fetch(handler, hints=WET_HINTS).source_id == 1867697


def test_structured_hit_never_reaches_freetext():
    searches = []

    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(404)
        searches.append(dict(request.url.params))
        return httpx.Response(200, json=[{"id": 5, "plainLyrics": "ok", "duration": 234}])

    assert _fetch(handler).source_id == 5
    assert len(searches) == 1 and "q" not in searches[0]


def test_freetext_plausibility_guard_rejects_unrelated_record():
    stranger = {
        "id": 66,
        "trackName": "Completely Different Song",
        "artistName": "Someone Else",
        "plainLyrics": "wrong words",
        "duration": 195,  # matching duration alone must not be enough
    }

    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(404)
        if "q" in request.url.params:
            return httpx.Response(200, json=[stranger])
        return httpx.Response(200, json=[])

    with pytest.raises(PipelineError) as exc:
        _fetch(handler, hints=WET_HINTS)
    assert exc.value.error_type == "lyrics_not_found"


def test_title_covers_requires_all_significant_tokens():
    # Field failure 2026-07-13: "Come On Now" matched "Come On Eileen" via
    # mere overlap (come/on); containment demands "now" too.
    assert not title_covers("Come On Eileen", "Come On Now")
    assert title_covers("We Don't Sleep at Night", "We Don't Sleep At Night")
    assert title_covers("Come On Now (Remastered)", "Come On Now")


def test_tokens_fold_turkish_dotted_i_and_accents():
    # "İstanbul" vs "Istanbul" must share tokens (retro finding).
    assert title_covers("Istanbul Hatirasi", "İstanbul Hatırası".replace("ı", "i"))
    assert plausible_match(
        {"trackName": "İstanbul", "artistName": "Sezen"}, "Istanbul", "SEZEN"
    )


def test_stopword_only_overlap_is_not_plausible():
    stranger = {"trackName": "The Version", "artistName": "Official Audio"}
    assert not plausible_match(stranger, "The Song", "Real Artist")


def test_has_usable_lyrics_uses_the_real_parse():
    assert not has_usable_lyrics({"id": 1, "syncedLyrics": "\n\n"})  # truthy junk
    assert has_usable_lyrics({"id": 1, "plainLyrics": "hello"})
    assert not has_usable_lyrics({"id": 1, "instrumental": True, "plainLyrics": "x"})


def test_freetext_duration_less_last_chance_rescues_offset_records():
    """Mor/Gasolina field failure: every rung filtered by ±3 s missed the only
    real record whose duration differs from the video's; the q= rung now takes
    the plausible pick without the duration constraint as a last chance."""

    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(404)
        if "q" in request.url.params:
            return httpx.Response(
                200,
                json=[{
                    "id": 77,
                    "trackName": "Mor",
                    "artistName": "Hande Yener",
                    "plainLyrics": "mor",
                    "duration": 176,  # 13 s off the video — out of tolerance
                }],
            )
        return httpx.Response(200, json=[])

    hints = {"title": "Mor", "artist": "Hande Yener", "duration_ms": 189_000}
    assert _fetch(handler, hints=hints).source_id == 77


def test_freetext_without_duration_takes_first_plausible():
    def handler(request):
        if request.url.path == "/api/get":
            return httpx.Response(404)
        if "q" in request.url.params:
            return httpx.Response(
                200,
                json=[
                    {"id": 9, "trackName": "Wet", "artistName": "Snoop Dogg", "plainLyrics": "a"},
                    {"id": 10, "trackName": "Wet", "artistName": "Snoop Dogg", "plainLyrics": "b"},
                ],
            )
        return httpx.Response(200, json=[])

    hints = {"title": "Wet", "artist": "Snoop Dogg"}  # no duration hint
    assert _fetch(handler, hints=hints).source_id == 9


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


# --- Faz 5 P2: 4xx classification + multi-artist split retry ---


def test_split_artists_variants():
    from kashi_server.pipeline.lrclib import split_artists

    assert split_artists("blueberry ve PiNKII") == ["blueberry", "PiNKII"]
    assert split_artists("Pitbull  ve J Balvin") == ["Pitbull", "J Balvin"]  # double space
    assert split_artists("Shawn Mendes & Camila Cabello") == ["Shawn Mendes", "Camila Cabello"]
    assert split_artists("TWXNY, Sxilwix ve Airfox") == ["TWXNY", "Sxilwix", "Airfox"]
    assert split_artists("TheFatRat feat. Maisy Kay") == ["TheFatRat", "Maisy Kay"]
    assert split_artists("KIDA x Dler") == ["KIDA", "Dler"]
    assert split_artists("Lil Nas X") == []  # trailing X is a name, not a separator
    assert split_artists("Pitbull") == []


def test_lrclib_400_is_permanent_not_retried_as_network():
    # Field case: duration=3679 (61-min mix) -> 400; three identical retries
    # burned the attempt budget on an answer that could never change.
    def handler(request):
        return httpx.Response(400, text="Bad Request")

    with pytest.raises(PipelineError) as err:
        _fetch(handler)
    assert err.value.error_type == "lyrics_not_found"


def test_lrclib_429_maps_to_rate_limited():
    def handler(request):
        return httpx.Response(429, text="Too Many Requests")

    with pytest.raises(PipelineError) as err:
        _fetch(handler)
    assert err.value.error_type == "rate_limited"


def test_lrclib_5xx_stays_transient_network():
    def handler(request):
        return httpx.Response(503, text="upstream sad")

    with pytest.raises(PipelineError) as err:
        _fetch(handler)
    assert err.value.error_type == "network"


def test_multi_artist_hint_retries_with_the_primary_artist():
    calls = []

    def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        params = dict(request.url.params)
        if request.url.path == "/api/get":
            return httpx.Response(404)
        # Structured + free-text searches with the joined artist find nothing;
        # the primary-artist structured search hits.
        if params.get("artist_name") == "blueberry ve PiNKII":
            return httpx.Response(200, json=[])
        if params.get("q") == "blueberry ve PiNKII Drift Barbie":
            return httpx.Response(200, json=[])
        if params.get("artist_name") == "blueberry":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 18141280,
                        "trackName": "Drift Barbie",
                        "artistName": "blueberry, PiNKII",
                        "duration": 180,
                        "syncedLyrics": "[00:01.00] Drift\n[00:02.00] Barbie",
                    }
                ],
            )
        return httpx.Response(200, json=[])

    hints = {"title": "Drift Barbie", "artist": "blueberry ve PiNKII", "duration_ms": 180_000}
    lyrics = _fetch(handler, hints)
    assert lyrics.source_id == 18141280 and lyrics.had_synced
    # Request budget: get, search(full), q(full), search(primary) — 4 calls.
    assert len(calls) == 4


def test_split_retry_plausibility_accepts_any_credited_part():
    # The record credits only the FEATURED artist; the primary-artist q= rung
    # must still accept it (any-part plausibility), not just primary matches.
    def handler(request):
        params = dict(request.url.params)
        if request.url.path == "/api/get":
            return httpx.Response(404)
        if params.get("q") == "TheFatRat The Storm":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 7,
                        "trackName": "The Storm",
                        "artistName": "Maisy Kay",
                        "duration": 222,
                        "syncedLyrics": "[00:01.00] Storm line",
                    }
                ],
            )
        return httpx.Response(200, json=[])

    hints = {"title": "The Storm", "artist": "TheFatRat ve Maisy Kay", "duration_ms": 222_000}
    lyrics = _fetch(handler, hints)
    assert lyrics.source_id == 7


def test_single_artist_never_pays_the_split_rungs():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/get":
            return httpx.Response(404)
        return httpx.Response(200, json=[])

    with pytest.raises(PipelineError) as err:
        _fetch(handler)
    assert err.value.error_type == "lyrics_not_found"
    assert len(calls) == 3  # get, search, q — no extra requests for "Pitbull"


def test_search_primary_rung_rejects_implausible_records():
    # The primary-artist structured search returns a DIFFERENT song by the
    # same primary token; the plausibility gate must reject it and fall
    # through to the q= rung (which finds nothing -> lyrics_not_found).
    calls = []

    def handler(request):
        calls.append(request.url.path)
        params = dict(request.url.params)
        if request.url.path == "/api/get":
            return httpx.Response(404)
        if params.get("artist_name") == "Tyler":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 99,
                        "trackName": "Completely Different Song",
                        "artistName": "Tyler",
                        "duration": 180,
                        "syncedLyrics": "[00:01.00] wrong words",
                    }
                ],
            )
        return httpx.Response(200, json=[])

    hints = {"title": "Some Ballad", "artist": "Tyler, The Creator", "duration_ms": 180_000}
    with pytest.raises(PipelineError) as err:
        _fetch(handler, hints)
    assert err.value.error_type == "lyrics_not_found"
    assert len(calls) == 5  # full budget spent, wrong record never accepted


def test_lrclib_403_stays_transient():
    # Reads need no auth: a 403 is edge/WAF weather, not a permanent verdict —
    # it must not stamp tracks lyrics_not_found behind the 7-day block.
    def handler(request):
        return httpx.Response(403, text="blocked")

    with pytest.raises(PipelineError) as err:
        _fetch(handler)
    assert err.value.error_type == "network"


def test_choose_record_prefers_parsed_synced_within_the_duration_band():
    from kashi_server.pipeline.lrclib import choose_record

    plain_close = {"id": 1, "plainLyrics": "close", "duration": 234}
    synced_far = {
        "id": 2,
        "syncedLyrics": "[00:01.00] far but synced",
        "duration": 236,  # inside the ±3 s band, farther than the plain one
    }
    junk_synced = {"id": 3, "syncedLyrics": "\n\n", "plainLyrics": "junk", "duration": 234}
    picked = choose_record([plain_close, synced_far, junk_synced], duration_s=234)
    assert picked is not None and picked["id"] == 2  # parsed-synced outranks distance
    # Junk syncedLyrics must not be treated as synced: with the real synced
    # record gone, the closest PLAIN record wins over truthy junk.
    picked = choose_record([plain_close, junk_synced], duration_s=234)
    assert picked is not None and picked["id"] == 1

    # Out-of-band records never qualify, synced or not.
    assert choose_record([{"id": 4, "syncedLyrics": "[00:01.00] x", "duration": 400}],
                         duration_s=234) is None
    # duration_s=None: synced-first, original order breaks ties.
    picked = choose_record([plain_close, synced_far], duration_s=None)
    assert picked is not None and picked["id"] == 2


def test_choose_record_lyricsfile_probe_never_outranks_synced():
    from kashi_server.pipeline.lrclib import choose_record

    # A PLAIN record with a words-bearing lyricsfile must not shadow a synced
    # record: if the lyricsfile later fails the real parse, the CTC fallback
    # would have lost its QA reference (reviewer catch).
    plain_with_lf = {
        "id": 1,
        "plainLyrics": "hello",
        "duration": 234,
        "lyricsfile": (
            "version: '1.0'\nlines:\n  - text: hi\n    start_ms: 1\n"
            "    words:\n      - {text: hi, start_ms: 1}\n"
        ),
    }
    synced_plain_lf = {"id": 2, "syncedLyrics": "[00:01.00] hello", "duration": 235}
    picked = choose_record([plain_with_lf, synced_plain_lf], duration_s=234)
    assert picked is not None and picked["id"] == 2
    # WITHIN the synced class the lyricsfile probe wins.
    synced_with_lf = dict(plain_with_lf, id=3, syncedLyrics="[00:01.00] hello")
    picked = choose_record([synced_plain_lf, synced_with_lf], duration_s=234)
    assert picked is not None and picked["id"] == 3


def test_get_hit_without_lyricsfile_upgrades_to_a_word_sync_sibling():
    # /api/get returns one record; the primary rung would otherwise never see
    # a sibling carrying HUMAN word sync (closure-e2e field finding).
    calls = []
    lf = "version: '1.0'\nlines:\n  - text: hi\n    words:\n      - {text: hi, start_ms: 1}\n"

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/get":
            return httpx.Response(
                200, json={"id": 1, "syncedLyrics": "[00:01.00] hi", "duration": 234}
            )
        return httpx.Response(
            200,
            json=[
                {"id": 2, "syncedLyrics": "[00:01.00] hi", "duration": 234, "lyricsfile": lf}
            ],
        )

    lyrics = _fetch(handler)
    assert lyrics.source_id == 2 and lyrics.lyricsfile_raw is not None
    assert calls == ["/api/get", "/api/search"]  # exactly one extra request


def test_get_hit_with_lyricsfile_pays_no_extra_request():
    lf = "version: '1.0'\nlines:\n  - text: hi\n    words:\n      - {text: hi, start_ms: 1}\n"
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"id": 7, "syncedLyrics": "[00:01.00] hi", "duration": 234, "lyricsfile": lf},
        )

    lyrics = _fetch(handler)
    assert lyrics.source_id == 7 and lyrics.lyricsfile_raw is not None
    assert calls == ["/api/get"]


def test_upgrade_probe_keeps_the_get_hit_when_no_sibling_has_words():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/get":
            return httpx.Response(
                200, json={"id": 1, "syncedLyrics": "[00:01.00] hi", "duration": 234}
            )
        return httpx.Response(
            200, json=[{"id": 2, "syncedLyrics": "[00:01.00] hi", "duration": 235}]
        )

    lyrics = _fetch(handler)
    assert lyrics.source_id == 1  # get hit kept; probe was just a probe
