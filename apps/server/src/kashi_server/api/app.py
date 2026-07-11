"""FastAPI application.

v1 surface: /v1/health + /v1/ready (unauthenticated probes), /v1/lyrics,
/v1/ingest, /v1/jobs (Bearer auth) and /v1/admin/{keys,reprocess} (admin role).
Lifespan bootstraps the ADMIN_API_KEY env value as the `bootstrap-admin` key.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from kashi_server import __version__
from kashi_server.api.middleware import ContentLengthLimitMiddleware
from kashi_server.api.routers import admin_keys, admin_ops, ingest, jobs, lyrics
from kashi_server.auth import hash_key, looks_like_key
from kashi_server.config import settings
from kashi_server.db.models import ApiKey

BOOTSTRAP_KEY_NAME = "bootstrap-admin"
logger = logging.getLogger(__name__)


def _bootstrap_admin_key() -> None:
    """Reconcile the bootstrap admin key with ADMIN_API_KEY on every startup.

    Reconcile, not insert-once: a soft-disabled bootstrap key would otherwise
    lock the operator out permanently, and rotating the env secret would leave
    the previous key an enabled admin forever (review findings, Faz 3A/A2).
    """
    if not settings.admin_api_key:
        return
    if not looks_like_key(settings.admin_api_key):
        raise RuntimeError("ADMIN_API_KEY is not a valid kashi key (expected ksh_<32 hex>)")
    from kashi_server.db.engine import SessionLocal

    with SessionLocal() as session:
        wanted_hash = hash_key(settings.admin_api_key)
        for stale in session.scalars(
            select(ApiKey).where(
                ApiKey.name == BOOTSTRAP_KEY_NAME,
                ApiKey.key_hash != wanted_hash,
                ApiKey.disabled.is_(False),
            )
        ):
            stale.disabled = True  # rotation revokes the previous bootstrap key
            logger.warning("bootstrap admin key rotated: disabled the previous one")

        existing = session.scalars(select(ApiKey).where(ApiKey.key_hash == wanted_hash)).first()
        if existing is None:
            session.add(ApiKey(key_hash=wanted_hash, name=BOOTSTRAP_KEY_NAME, role="admin"))
            logger.info("bootstrap admin key created from ADMIN_API_KEY")
        elif existing.disabled or existing.role != "admin":
            existing.disabled = False
            existing.role = "admin"
            logger.warning("bootstrap admin key was disabled/demoted — restored from ADMIN_API_KEY")
        session.commit()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _bootstrap_admin_key()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="kashi-server", version=__version__, lifespan=_lifespan)
    # Outermost: bodies are capped before FastAPI parses them (pre-auth OOM).
    app.add_middleware(ContentLengthLimitMiddleware)
    app.include_router(lyrics.router)
    app.include_router(ingest.router)
    app.include_router(jobs.router)
    app.include_router(admin_keys.router)
    app.include_router(admin_ops.router)

    @app.get("/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/ready")
    def ready():
        # Readiness = the DB answers (k8s probe; migrate runs as initContainer).
        from kashi_server.db.engine import engine

        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    return app


app = create_app()  # uvicorn entrypoint: kashi_server.api.app:app
