"""In-process token buckets, keyed per API key.

Deliberately in-memory: the API deploys as a SINGLE replica (k8s + compose),
so shared state is unnecessary. If the API ever scales out, replace with a
counter table — noted in the plan (R-F3-5).
"""

import threading
import time

# Bucket parameters looked up at REQUEST time (not captured at import), so
# tests can monkeypatch entries: name -> (capacity, refill_per_second).
RATE_LIMITS: dict[str, tuple[float, float]] = {
    "lyrics_get": (120.0, 120.0 / 60.0),  # 120/min
    "ingest": (20.0, 20.0 / 3600.0),  # 20/h
}


class TokenBuckets:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, updated)

    def allow(
        self,
        key: str,
        *,
        capacity: float,
        refill_per_s: float,
        now: float | None = None,
    ) -> tuple[bool, float]:
        """Consume one token. Returns (allowed, retry_after_seconds)."""
        ts = time.monotonic() if now is None else now
        with self._lock:
            tokens, updated = self._buckets.get(key, (capacity, ts))
            tokens = min(capacity, tokens + (ts - updated) * refill_per_s)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, ts)
                return True, 0.0
            self._buckets[key] = (tokens, ts)
            retry_after = (1.0 - tokens) / refill_per_s if refill_per_s > 0 else float("inf")
            return False, retry_after


buckets = TokenBuckets()
