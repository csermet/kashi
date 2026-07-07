"""FastAPI application.

Phase 3 adds: /v1/lyrics, /v1/ingest, /v1/jobs, /v1/admin/keys, Bearer auth,
Postgres-backed queue. Until then only the (unauthenticated) probes exist.
"""

from fastapi import FastAPI

from kashi_server import __version__

app = FastAPI(title="kashi-server", version=__version__)


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/ready")
def ready() -> dict[str, str]:
    # Phase 3: verify DB + queue reachability here (k8s readiness probe).
    return {"status": "ready"}
