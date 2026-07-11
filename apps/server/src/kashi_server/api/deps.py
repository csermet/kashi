"""FastAPI dependencies: DB session, Bearer-key auth, per-key rate limiting."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from kashi_server.auth import hash_key, looks_like_key
from kashi_server.db.engine import SessionLocal
from kashi_server.db.models import ApiKey
from kashi_server.ratelimit import RATE_LIMITS, buckets

LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)


def queue_full_response() -> JSONResponse:
    """Shared QueueFull -> 503 mapping (ingest + admin reprocess)."""
    return JSONResponse(status_code=503, content={"error": "queue_full"})


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="unauthorized")


def _touch_last_used(key_id, now: datetime) -> None:
    """Record the auth in its OWN transaction.

    The request session is rolled back whenever the handler raises (404 lyrics
    poll, 429, 409 …) — and those are exactly the responses a busy key sees
    most — so a piggy-backed write would silently vanish (review finding).
    """
    with SessionLocal() as session:
        session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=now))
        session.commit()


def require_key(role: str = "user"):
    """Dependency factory: authenticate the Bearer key, enforce `role`.

    role="user" admits both roles; role="admin" admits admins only (403).

    Note on pre-auth abuse: a well-formed but unknown key costs one indexed
    SELECT. Flood protection for that path lives at the ingress (nginx
    `limit-rps: 10`, plan B2) — an in-app per-IP limiter behind a proxy would
    key on a forgeable header. Malformed credentials never touch the DB, and
    oversized bodies die in ContentLengthLimitMiddleware.
    """

    def dependency(request: Request, db: Session = Depends(get_db)) -> ApiKey:
        header = request.headers.get("authorization", "")
        scheme, _, credential = header.partition(" ")
        if scheme.lower() != "bearer" or not looks_like_key(credential.strip()):
            raise _unauthorized()
        key = db.scalars(
            select(ApiKey).where(ApiKey.key_hash == hash_key(credential.strip()))
        ).first()
        if key is None or key.disabled:
            raise _unauthorized()
        if role == "admin" and key.role != "admin":
            raise HTTPException(status_code=403, detail="forbidden")
        now = datetime.now(UTC)
        # Throttled bookkeeping: one UPDATE per key per minute, not per request.
        if key.last_used_at is None or now - key.last_used_at > LAST_USED_WRITE_INTERVAL:
            _touch_last_used(key.id, now)
            key.last_used_at = now
        return key

    return dependency


def rate_limited(bucket_name: str):
    """Dependency factory: consume one token from the caller's bucket.

    Parameters are read from RATE_LIMITS at request time so tests can shrink
    them via monkeypatch.
    """

    def dependency(key: ApiKey = Depends(require_key("user"))) -> ApiKey:
        capacity, refill = RATE_LIMITS[bucket_name]
        allowed, retry_after = buckets.allow(
            f"{bucket_name}:{key.id}", capacity=capacity, refill_per_s=refill
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="rate_limited",
                headers={"Retry-After": str(max(1, round(retry_after)))},
            )
        return key

    return dependency
