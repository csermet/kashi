"""Worker entrypoint: warmup gate -> orphan sweep -> claim/process loop."""

import logging
import os
import signal
import time
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from kashi_server import queue
from kashi_server.config import settings

logger = logging.getLogger(__name__)

JOBS_TOTAL = Counter("kashi_jobs_total", "Jobs finished by final status", ["status"])
JOB_SECONDS = Histogram(
    "kashi_job_seconds",
    "Wall-clock seconds per processed job",
    buckets=(30, 60, 120, 180, 300, 600, 1200),
)
QUEUE_DEPTH = Gauge("kashi_queue_depth", "Live jobs (queued + running)")

ORPHAN_MAX_AGE_S = 3600


def sweep_orphans(data_dir: Path) -> int:
    """Leftover job dirs from a crashed worker still hold audio — delete them
    (audio-deletion guarantee survives worker crashes too)."""
    import shutil

    removed = 0
    if not data_dir.exists():
        return 0
    cutoff = time.time() - ORPHAN_MAX_AGE_S
    for path in data_dir.glob("job-*"):
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.warning("swept %d orphaned job dir(s) from %s", removed, data_dir)
    return removed


def run_forever() -> None:  # pragma: no cover - the loop; pieces are tested
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    os.environ.setdefault("HF_HOME", str(settings.model_cache_dir))

    # Warmup gate: a worker that cannot align must not claim jobs.
    from kashi_server.worker.warmup import ensure_model, ensure_separator

    ensure_model()
    if settings.separation_mode != "off":
        ensure_separator()

    sweep_orphans(settings.data_dir)
    start_http_server(settings.metrics_port)
    logger.info(
        "worker ready (poll %.1fs, metrics :%d)",
        settings.worker_poll_interval_s,
        settings.metrics_port,
    )

    from kashi_server.db.engine import SessionLocal
    from kashi_server.worker.process import HeartbeatThread, process_job

    stopping = {"flag": False}

    def _graceful(signum, _frame):
        logger.info("signal %s: finishing the current job, then exiting", signum)
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    while not stopping["flag"]:
        with SessionLocal() as session:
            queue.reclaim_expired(session)
            purged = queue.purge_expired_uploads(session)
            if purged:
                logger.info("purged %d expired staged upload(s)", purged)
            session.commit()
            job = queue.claim_next(session)
            if job is None:
                # Idle slot: drain at most one lrclib publish request (P6) —
                # PoW may cost minutes and must never delay a lyrics job.
                from kashi_server.worker.publisher import process_one_publish

                if process_one_publish(session, should_stop=lambda: stopping["flag"]):
                    continue
                QUEUE_DEPTH.set(queue.queue_depth(session))
                session.commit()
                time.sleep(settings.worker_poll_interval_s)
                continue

            logger.info("claimed job %s (%s:%s)", job.id, job.source_type, job.source_id)
            session.commit()
            started = time.monotonic()
            with HeartbeatThread(job.id, SessionLocal):
                process_job(session, job)
            JOB_SECONDS.observe(time.monotonic() - started)
            session.refresh(job)
            JOBS_TOTAL.labels(status=job.status).inc()

    logger.info("worker stopped")
