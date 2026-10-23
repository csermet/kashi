"""LyricFix P3: the worker's lrclib gateway (primary -> mirror -> defer) and
its breaker. Real lrclib.py code runs underneath, against one
httpx.MockTransport per target, so the error mapping is the shipped one."""

import httpx
import pytest
from prometheus_client import REGISTRY

from kashi_server.pipeline import lrclib
from kashi_server.pipeline.lrclib_gateway import (
    BREAKER_BASE_COOLDOWN_S,
    BREAKER_MAX_BACKOFF_S,
    LrclibBreaker,
    LrclibGateway,
)
from kashi_server.vdl_kit.errors import PipelineError, is_transient_error

PRIMARY = "https://lrclib.test"
MIRROR = "http://mirror.test"
HINTS = {"title": "Got 2 Luv U", "artist": "Sean Paul", "duration_ms": 205_000}
RECORD = {
    "id": 42,
    "trackName": "Got 2 Luv U",
    "artistName": "Sean Paul",
    "duration": 205,
    "syncedLyrics": "[00:01.00] one\n[00:02.00] two",
}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Target:
    """One fake lrclib server: answers with `status` (and Retry-After), or
    with the record for /api/get when status is 200."""

    def __init__(self, status: int = 200, retry_after: str | None = None) -> None:
        self.status = status
        self.retry_after = retry_after
        self.paths: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        headers = {"Retry-After": self.retry_after} if self.retry_after else {}
        if self.status == 404:
            if request.url.path == "/api/get":
                return httpx.Response(404)
            return httpx.Response(200, json=[])
        if self.status != 200:
            return httpx.Response(self.status, headers=headers, text="busy")
        if request.url.path == "/api/get":
            return httpx.Response(200, json=RECORD)
        return httpx.Response(200, json=[RECORD])


def _gateway(primary: Target, mirror: Target | None, clock: Clock | None = None) -> LrclibGateway:
    targets = {PRIMARY: primary, MIRROR: mirror}

    def client(url: str) -> httpx.Client:
        target = targets[url]
        assert target is not None, f"{url} must not be asked"
        return httpx.Client(base_url=url, transport=httpx.MockTransport(target.handler))

    return LrclibGateway(
        fetch=lambda hints, url: lrclib.fetch_lyrics(hints, base_url=url, client=client(url)),
        search=lambda q, url: lrclib.search_candidates(q, base_url=url, client=client(url)),
        primary_url=PRIMARY,
        mirror_url=MIRROR if mirror is not None else None,
        breaker=LrclibBreaker(now=clock or Clock()),
    )


# --- breaker -----------------------------------------------------------------


def test_the_breaker_opens_at_the_second_failure_not_the_first():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    assert breaker.allows_primary()  # one 503 is lrclib's "retry in a second"
    breaker.record_failure()
    assert not breaker.allows_primary()
    assert breaker.opens == 1


def test_a_success_between_failures_resets_the_count():
    breaker = LrclibBreaker(now=Clock())
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.allows_primary()


def test_cooldown_doubles_per_open_and_caps_at_fifteen_minutes():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    breaker.record_failure()  # open #1: 60 s
    clock.t += BREAKER_BASE_COOLDOWN_S - 0.001
    assert not breaker.allows_primary()
    clock.t += 0.001
    assert breaker.allows_primary()  # half-open: the next request is the probe
    breaker.record_failure()  # the probe failed: open #2 at once, 120 s
    assert breaker.opens == 2
    clock.t += 2 * BREAKER_BASE_COOLDOWN_S - 0.001
    assert not breaker.allows_primary()
    clock.t += 0.001
    assert breaker.allows_primary()
    for _ in range(10):  # 240, 480, 960 -> capped
        breaker.record_failure()
        clock.t += BREAKER_MAX_BACKOFF_S
    breaker.record_failure()
    clock.t += BREAKER_MAX_BACKOFF_S - 0.001
    assert not breaker.allows_primary()
    clock.t += 0.001
    assert breaker.allows_primary()


def test_cooldown_honours_a_longer_retry_after():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    breaker.record_failure(retry_after_s=1800)  # lrclib asked for 30 min
    clock.t += BREAKER_MAX_BACKOFF_S + 1  # past the backoff cap
    assert not breaker.allows_primary()
    clock.t += 1800 - BREAKER_MAX_BACKOFF_S
    assert breaker.allows_primary()


def test_a_retry_after_below_the_threshold_pauses_without_opening():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure(retry_after_s=300)
    assert not breaker.allows_primary()
    assert breaker.opens == 0
    clock.t += 300
    assert breaker.allows_primary()


def test_a_half_open_success_closes_and_resets_the_backoff():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    breaker.record_failure()
    clock.t += BREAKER_BASE_COOLDOWN_S
    breaker.record_success()
    assert breaker.opens == 0
    breaker.record_failure()
    assert breaker.allows_primary()  # closed again: needs two failures anew
    breaker.record_failure()
    clock.t += BREAKER_BASE_COOLDOWN_S  # back to the first step, not 120 s
    assert breaker.allows_primary()


def test_the_open_gauge_follows_the_breaker():
    def gauge() -> float | None:
        return REGISTRY.get_sample_value("kashi_lrclib_breaker_open", {"target": "primary"})

    breaker = LrclibBreaker(now=Clock())
    assert gauge() == 0
    breaker.record_failure()
    breaker.record_failure()
    assert gauge() == 1
    breaker.record_success()
    assert gauge() == 0


# --- gateway -----------------------------------------------------------------


def test_a_primary_hit_never_asks_the_mirror():
    primary, mirror = Target(200), Target(200)
    lyrics = _gateway(primary, mirror).fetch_lyrics(HINTS)
    assert lyrics.source_id == 42
    assert mirror.paths == []


def test_a_clean_primary_404_is_lyrics_not_found_and_never_asks_the_mirror():
    primary, mirror = Target(404), Target(200)
    gateway = _gateway(primary, mirror)
    with pytest.raises(PipelineError) as err:
        gateway.fetch_lyrics(HINTS)
    assert err.value.error_type == "lyrics_not_found"
    assert mirror.paths == []


@pytest.mark.parametrize("status", [429, 503, 403])
def test_a_busy_primary_falls_through_to_the_mirror(status):
    primary, mirror = Target(status), Target(200)
    gateway = _gateway(primary, mirror)
    lyrics = gateway.fetch_lyrics(HINTS)
    assert lyrics.source_id == 42
    assert primary.paths == ["/api/get"]  # stopped at the first busy answer
    assert mirror.paths == ["/api/get"]


def test_a_mirror_miss_during_an_outage_is_unavailable_never_not_found():
    primary, mirror = Target(503), Target(404)
    gateway = _gateway(primary, mirror)
    with pytest.raises(PipelineError) as err:
        gateway.fetch_lyrics(HINTS)
    assert err.value.error_type == "lrclib_unavailable"
    assert is_transient_error(err.value.error_type)  # never the 7-day block


def test_a_failing_mirror_is_unavailable_too():
    primary, mirror = Target(503), Target(500)
    gateway = _gateway(primary, mirror)
    with pytest.raises(PipelineError) as err:
        gateway.fetch_lyrics(HINTS)
    assert err.value.error_type == "lrclib_unavailable"


def test_no_mirror_configured_means_unavailable():
    gateway = _gateway(Target(429, retry_after="120"), None)
    with pytest.raises(PipelineError) as err:
        gateway.fetch_lyrics(HINTS)
    assert err.value.error_type == "lrclib_unavailable"
    assert err.value.retry_after_s == 120


def test_an_open_breaker_goes_straight_to_the_mirror():
    primary, mirror = Target(503), Target(200)
    gateway = _gateway(primary, mirror)
    gateway.fetch_lyrics(HINTS)
    gateway.fetch_lyrics(HINTS)  # second failure: open
    assert not gateway.breaker.allows_primary()
    primary.paths.clear()
    lyrics = gateway.fetch_lyrics(HINTS)
    assert lyrics.source_id == 42
    assert primary.paths == []


def test_a_primary_retry_after_reaches_the_breaker():
    clock = Clock()
    primary, mirror = Target(429, retry_after="600"), Target(200)
    gateway = _gateway(primary, mirror, clock)
    gateway.fetch_lyrics(HINTS)
    gateway.fetch_lyrics(HINTS)
    clock.t += BREAKER_BASE_COOLDOWN_S  # past the 60 s backoff, inside Retry-After
    assert not gateway.breaker.allows_primary()
    clock.t += 600 - BREAKER_BASE_COOLDOWN_S
    assert gateway.breaker.allows_primary()


def test_an_empty_mirror_search_during_an_outage_is_unavailable():
    # [] would read as "no such song" to nightcore detection.
    primary, mirror = Target(503), Target(404)
    gateway = _gateway(primary, mirror)
    with pytest.raises(PipelineError) as err:
        gateway.search_candidates("Sean Paul Got 2 Luv U")
    assert err.value.error_type == "lrclib_unavailable"


def test_search_falls_through_to_the_mirror():
    primary, mirror = Target(429), Target(200)
    assert _gateway(primary, mirror).search_candidates("q") == [RECORD]
    assert mirror.paths == ["/api/search"]


def test_mirror_only_lookup_never_touches_the_breaker_or_the_primary():
    primary, mirror = Target(200), Target(404)
    gateway = _gateway(primary, mirror)
    assert gateway.mirror_only_lookup(HINTS) is None
    assert primary.paths == []
    assert gateway.breaker.opens == 0
    mirror.status = 200
    found = gateway.mirror_only_lookup(HINTS)
    assert found is not None
    assert found.source_id == 42


def test_mirror_only_lookup_without_a_mirror_is_a_miss():
    assert _gateway(Target(200), None).mirror_only_lookup(HINTS) is None
