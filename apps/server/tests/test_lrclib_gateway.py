"""LyricFix P3: the worker's lrclib gateway (primary -> mirror -> defer) and
its breaker. Real lrclib.py code runs underneath, against one
httpx.MockTransport per target, so the error mapping is the shipped one."""

import logging

import httpx
import pytest
from prometheus_client import REGISTRY

from kashi_server.pipeline import lrclib
from kashi_server.pipeline.lrclib_gateway import (
    BREAKER_BASE_COOLDOWN_S,
    BREAKER_FAILURE_THRESHOLD,
    BREAKER_MAX_BACKOFF_S,
    BREAKER_WINDOW_S,
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


def test_a_success_between_failures_does_not_reset_the_window():
    # The 2026-10 weather: 503, 200, 503 ... used to never open the breaker.
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    clock.t += 600
    breaker.record_success()
    clock.t += 600
    breaker.record_failure()
    assert not breaker.allows_primary()
    assert breaker.opens == 1


def test_two_failures_inside_the_window_open_the_breaker():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    clock.t += BREAKER_WINDOW_S - 0.001  # still inside
    breaker.record_failure()
    assert not breaker.allows_primary()


def test_a_failure_that_left_the_window_no_longer_counts():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    clock.t += BREAKER_WINDOW_S  # exactly one window old: out
    breaker.record_failure()
    assert breaker.allows_primary()
    assert breaker.opens == 0


def test_the_window_and_threshold_come_from_the_constructor():
    clock = Clock()
    breaker = LrclibBreaker(now=clock, window_s=60, failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.allows_primary()  # 2 < 3
    clock.t += 60
    breaker.record_failure()
    assert breaker.allows_primary()  # the first two left the 60 s window
    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.allows_primary()


def test_settings_defaults_match_the_breaker_constants():
    from kashi_server.config import Settings

    settings = Settings()
    assert settings.lrclib_breaker_window_s == BREAKER_WINDOW_S
    assert settings.lrclib_breaker_failure_threshold == BREAKER_FAILURE_THRESHOLD


def test_the_worker_breaker_is_built_from_settings(monkeypatch):
    from kashi_server.config import settings
    from kashi_server.worker import process

    monkeypatch.setattr(settings, "lrclib_breaker_window_s", 60.0)
    monkeypatch.setattr(settings, "lrclib_breaker_failure_threshold", 3)
    breaker = process.build_gateway().breaker
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.allows_primary()
    breaker.record_failure()
    assert not breaker.allows_primary()


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


def test_a_half_open_success_closes_but_keeps_the_backoff_while_the_window_is_dirty():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    breaker.record_failure()  # open #1: 60 s
    clock.t += BREAKER_BASE_COOLDOWN_S
    breaker.record_success()
    assert breaker.allows_primary()  # closed
    assert breaker.opens == 1  # the two failures are still in the window
    breaker.record_failure()  # one more busy answer re-opens at once ...
    assert not breaker.allows_primary()
    clock.t += 2 * BREAKER_BASE_COOLDOWN_S - 0.001  # ... on the next step, 120 s
    assert not breaker.allows_primary()
    clock.t += 0.001
    assert breaker.allows_primary()


def test_a_success_with_a_clean_window_resets_the_backoff():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    breaker.record_failure()  # open #1
    clock.t += BREAKER_WINDOW_S
    breaker.record_success()
    assert breaker.opens == 0
    breaker.record_failure()
    assert breaker.allows_primary()  # closed again: needs two failures anew
    breaker.record_failure()
    clock.t += BREAKER_BASE_COOLDOWN_S  # back to the first step, not 120 s
    assert breaker.allows_primary()


def test_the_breaker_logs_when_it_closes(caplog, monkeypatch):
    # Alembic's env.py runs logging.config.fileConfig, which disables every
    # logger that already exists; an earlier migration test leaves this one off.
    log = logging.getLogger("kashi_server.pipeline.lrclib_gateway")
    monkeypatch.setattr(log, "disabled", False)
    breaker = LrclibBreaker(now=Clock())
    with caplog.at_level(logging.INFO, logger=log.name):
        breaker.record_success()  # already closed: nothing to say
        assert "closed" not in caplog.text
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
    assert "lrclib breaker closed (after open #1)" in caplog.text


def test_a_half_open_breaker_lapses_after_a_quiet_window():
    def gauge() -> float | None:
        return REGISTRY.get_sample_value("kashi_lrclib_breaker_open", {"target": "primary"})

    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    breaker.record_failure()  # open #1 until t+60
    clock.t += BREAKER_BASE_COOLDOWN_S + BREAKER_WINDOW_S  # a whole quiet window after it
    assert breaker.allows_primary()
    assert breaker.opens == 0
    assert gauge() == 0
    breaker.record_failure()  # one busy answer hours later: not a failed probe
    assert breaker.allows_primary()


def test_a_half_open_breaker_inside_the_quiet_window_still_reopens():
    clock = Clock()
    breaker = LrclibBreaker(now=clock)
    breaker.record_failure()
    breaker.record_failure()  # open #1 until t+60
    clock.t += BREAKER_BASE_COOLDOWN_S + BREAKER_WINDOW_S - 0.001
    breaker.record_failure()  # still half-open: the probe failed
    assert not breaker.allows_primary()
    assert breaker.opens == 2


def test_a_busy_answer_below_the_threshold_is_logged(caplog, monkeypatch):
    log = logging.getLogger("kashi_server.pipeline.lrclib_gateway")
    monkeypatch.setattr(log, "disabled", False)  # see test_the_breaker_logs_when_it_closes
    with caplog.at_level(logging.INFO, logger=log.name):
        LrclibBreaker(now=Clock()).record_failure()
    assert "lrclib busy answer (1 in the last 30 min; the breaker opens at 2)" in caplog.text


@pytest.mark.parametrize(
    ("field", "value"),
    [("lrclib_breaker_window_s", 0), ("lrclib_breaker_failure_threshold", 0)],
)
def test_breaker_settings_reject_values_that_disable_it(field, value):
    from pydantic import ValidationError

    from kashi_server.config import Settings

    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_the_window_gauge_counts_busy_answers():
    clock = Clock()

    def gauge() -> float | None:
        return REGISTRY.get_sample_value("kashi_lrclib_window_failures", {"target": "primary"})

    breaker = LrclibBreaker(now=clock)
    assert gauge() == 0
    breaker.record_failure()
    assert gauge() == 1
    clock.t += BREAKER_WINDOW_S
    breaker.allows_primary()
    assert gauge() == 0


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
