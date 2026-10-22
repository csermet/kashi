"""The worker's one door to lrclib: lrclib.net, then our mirror, then defer.

LyricFix P3 (docs/research/lyricfix-lrclib-availability-2026-10.md). lrclib.net
is a free, single-maintainer service that answered 429/503 for long stretches
in 2026-10. Processing has no hurry, but each retry used to cost a YouTube
download. So:

- lrclib.net stays the PRIMARY: freshest data, and the only source of
  `lyricsfile` word sync.
- A transient primary failure (429, 5xx, 403, timeout) goes to the mirror, a
  stock lrclib server on a trimmed dump. A mirror hit is a normal answer.
- A mirror MISS while the primary is unavailable is `lrclib_unavailable`,
  never `lyrics_not_found`: the dump lags lrclib.net, and a permanent miss
  would take the 7-day block for a song lrclib.net may well have.
- A clean primary 404 is `lyrics_not_found` as before; the mirror is not asked
  (it cannot know more than lrclib.net does).
- After repeated primary failures a breaker keeps the worker off lrclib.net for
  a cooldown, so an outage costs a request or two, not one per job.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from prometheus_client import Gauge

from kashi_server.pipeline.lrclib import LyricsText
from kashi_server.vdl_kit.errors import PipelineError

logger = logging.getLogger(__name__)

# What "lrclib is busy" looks like from lrclib.py: 429 -> rate_limited;
# 5xx, 403, timeouts and DNS -> network.
BUSY_ERROR_TYPES = frozenset({"rate_limited", "network"})

# Two in a row, not one: a single 503 is lrclib's documented "busy, retry in
# a second" and should not send the next jobs to the mirror for a minute.
BREAKER_FAILURE_THRESHOLD = 2
BREAKER_BASE_COOLDOWN_S = 60.0
# Cap on the exponential part only. A Retry-After longer than this is still
# honoured (lrclib's rules: ignoring it "may result in a temporary ban");
# lrclib.parse_retry_after caps the header itself at an hour.
BREAKER_MAX_BACKOFF_S = 900.0

BREAKER_OPEN = Gauge(
    "kashi_lrclib_breaker_open",
    "1 from the moment the breaker opens until a request succeeds again",
    ["target"],
)


class LrclibBreaker:
    """closed -> open after BREAKER_FAILURE_THRESHOLD consecutive transient
    failures; open for max(60 s * 2^(n-1) capped at 15 min, Retry-After), where
    n counts the opens since the last success; then half-open: the next request
    is the probe. A probe that fails re-opens at once with the next n; any
    success closes and resets n.

    A failure below the threshold that carries Retry-After pauses the primary
    for that long without opening (and without touching n)."""

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._failures = 0
        self._opens = 0
        self._open_until: float | None = None
        self._pause_until = 0.0
        BREAKER_OPEN.labels("primary").set(0)

    @property
    def opens(self) -> int:
        return self._opens

    def allows_primary(self) -> bool:
        """False while open or paused; True when closed or half-open."""
        now = self._now()
        if now < self._pause_until:
            return False
        return self._open_until is None or now >= self._open_until

    def record_success(self) -> None:
        self._failures = 0
        self._opens = 0
        self._open_until = None
        self._pause_until = 0.0
        BREAKER_OPEN.labels("primary").set(0)

    def record_failure(self, retry_after_s: float | None = None) -> None:
        half_open = self._open_until is not None
        self._failures += 1
        retry_after = retry_after_s or 0.0
        if half_open or self._failures >= BREAKER_FAILURE_THRESHOLD:
            self._opens += 1
            backoff = min(BREAKER_BASE_COOLDOWN_S * 2 ** (self._opens - 1), BREAKER_MAX_BACKOFF_S)
            cooldown = max(backoff, retry_after)
            self._open_until = self._now() + cooldown
            self._failures = 0
            BREAKER_OPEN.labels("primary").set(1)
            logger.warning(
                "lrclib breaker OPEN for %.0fs (open #%d since the last success)",
                cooldown,
                self._opens,
            )
        elif retry_after > 0:
            self._pause_until = self._now() + retry_after


# (hints, base_url) -> LyricsText and (query, base_url) -> list[dict]. The
# worker passes lambdas that look lrclib.fetch_lyrics / search_candidates up
# in its own module at CALL time, so the tests' monkeypatched fakes still see
# exactly `(hints, base_url=url)` / `(query, base_url=url)`.
FetchFn = Callable[[dict, str], LyricsText]
SearchFn = Callable[[str, str], list[dict]]


class LrclibGateway:
    def __init__(
        self,
        *,
        fetch: FetchFn,
        search: SearchFn,
        primary_url: str,
        mirror_url: str | None,
        breaker: LrclibBreaker | None = None,
    ) -> None:
        self._fetch = fetch
        self._search = search
        self.primary_url = primary_url
        self.mirror_url = mirror_url
        self.breaker = breaker or LrclibBreaker()

    def fetch_lyrics(self, hints: dict) -> LyricsText:
        context = f"{hints.get('artist')} - {hints.get('title')}"
        return self._ask(lambda url: self._fetch(hints, url), context, lambda _: False)

    def search_candidates(self, query: str) -> list[dict]:
        # An empty mirror answer during an outage is a miss like a 404: the
        # caller would read [] as "no such song" (nightcore detection would
        # fall back to r=1 on a nightcore upload).
        return self._ask(lambda url: self._search(query, url), f"q={query!r}", lambda r: not r)

    def mirror_only_lookup(self, hints: dict) -> LyricsText | None:
        """The pre-download check while the primary is unavailable: lyrics
        from the mirror, or None (no mirror, a miss, or the mirror failing).
        Never touches the breaker."""
        if self.mirror_url is None:
            return None
        try:
            return self._fetch(hints, self.mirror_url)
        except PipelineError as exc:
            logger.info("mirror-only lookup missed (%s): %s", exc.error_type, exc.message[:200])
            return None

    def _ask(self, call: Callable[[str], Any], context: str, is_miss: Callable[[Any], bool]) -> Any:
        primary_error: PipelineError | None = None
        if self.breaker.allows_primary():
            try:
                result = call(self.primary_url)
            except PipelineError as exc:
                if exc.error_type not in BUSY_ERROR_TYPES:
                    # lrclib.net answered (a clean miss or a rejected query).
                    self.breaker.record_success()
                    raise
                self.breaker.record_failure(exc.retry_after_s)
                primary_error = exc
            else:
                self.breaker.record_success()
                return result
        return self._from_mirror(call, context, is_miss, primary_error)

    def _from_mirror(
        self,
        call: Callable[[str], Any],
        context: str,
        is_miss: Callable[[Any], bool],
        primary_error: PipelineError | None,
    ) -> Any:
        why = (
            f"lrclib {primary_error.error_type}"
            if primary_error is not None
            else "lrclib breaker open"
        )
        retry_after_s = primary_error.retry_after_s if primary_error is not None else None
        if self.mirror_url is None:
            raise PipelineError(
                "lrclib_unavailable",
                f"{why}, no mirror configured: {context}",
                retry_after_s=retry_after_s,
            )
        try:
            result = call(self.mirror_url)
        except PipelineError as exc:
            raise PipelineError(
                "lrclib_unavailable",
                f"{why}, mirror {exc.error_type}: {context}",
                retry_after_s=retry_after_s,
            ) from exc
        if is_miss(result):
            raise PipelineError(
                "lrclib_unavailable",
                f"{why}, mirror has nothing: {context}",
                retry_after_s=retry_after_s,
            )
        logger.info("lrclib answer for %s via mirror (%s)", context, why)
        return result
