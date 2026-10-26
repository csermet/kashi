"""Worker entrypoint: warmup gate -> orphan sweep -> claim/process loop."""

import logging
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram, start_http_server
from sqlalchemy.exc import OperationalError

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
# The claim loop spins every couple of seconds; a retention DELETE has no
# business running that often. Hourly is far finer than a 30-day window needs
# and survives a worker that restarts before its first sweep.
TELEMETRY_SWEEP_INTERVAL_S = 3600

# Waiting out a database that is not there. Restarting instead reloads every
# model (warmup) and, while the outage lasts, crash-loops into CrashLoopBackOff;
# the API process already rides the same outages out.
DB_RETRY_FIRST_S = 1.0
DB_RETRY_MAX_S = 60.0


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


def db_retry_delay_s(failures: int) -> float:
    """Wait after `failures` consecutive iterations without a database:
    1, 2, 4 ... seconds, capped at a minute — quick over a restart, quiet over
    a long outage."""
    return min(DB_RETRY_MAX_S, DB_RETRY_FIRST_S * 2 ** (failures - 1))


def serve(
    iteration: Callable[[], None],
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run `iteration` until told to stop, riding out database outages.

    Only a lost database is waited out. Anything else still ends the process,
    as before: an unexpected error in the loop itself is a bug, and a restart
    is the honest answer to it.
    """
    failures = 0
    while not should_stop():
        try:
            iteration()
        except OperationalError as exc:
            failures += 1
            delay = db_retry_delay_s(failures)
            logger.warning(
                "database unavailable (%s) — retry %d in %.0fs",
                str(exc.orig or exc).splitlines()[0],
                failures,
                delay,
            )
            _sleep_unless_stopped(delay, should_stop, sleep)
        else:
            if failures:
                logger.info("database back after %d failed attempt(s)", failures)
            failures = 0


def _sleep_unless_stopped(
    seconds: float, should_stop: Callable[[], bool], sleep: Callable[[float], None]
) -> None:
    """Sleep in short slices so SIGTERM during an outage still exits promptly."""
    remaining = seconds
    while remaining > 0 and not should_stop():
        step = min(1.0, remaining)
        sleep(step)
        remaining -= step


def run_forever() -> None:  # pragma: no cover - the loop; pieces are tested
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    os.environ.setdefault("HF_HOME", str(settings.model_cache_dir))

    _warm_up()
    sweep_orphans(settings.data_dir)
    start_http_server(settings.metrics_port)
    from kashi_server.version import PIPELINE_VERSION

    # Two workers can share the queue (Faz 9.3): the first line to check is
    # that both run the same pipeline — documents carry the version that made them.
    logger.info(
        "worker %s ready: pipeline %s (poll %.1fs, metrics :%d)",
        settings.worker_name,
        PIPELINE_VERSION,
        settings.worker_poll_interval_s,
        settings.metrics_port,
    )

    from kashi_server.db.engine import SessionLocal
    from kashi_server.worker.process import HeartbeatThread, process_job

    stopping = {"flag": False}
    last_telemetry_sweep: float | None = None

    def should_stop() -> bool:
        return stopping["flag"]

    def _graceful(signum, _frame):
        logger.info("signal %s: finishing the current job, then exiting", signum)
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    def iteration() -> None:
        nonlocal last_telemetry_sweep
        with SessionLocal() as session:
            last_telemetry_sweep = _housekeeping(session, last_telemetry_sweep)
            job = queue.claim_next(session)
            if job is None:
                _idle_slot(session, should_stop=should_stop)
                return
            _run_claimed(session, job, SessionLocal, HeartbeatThread, process_job)

    serve(iteration, should_stop)

    logger.info("worker stopped")


def _warm_up() -> None:  # pragma: no cover - loads models
    # Warmup gate: a worker that cannot align must not claim jobs.
    from kashi_server.worker.warmup import ensure_model, ensure_semantics, ensure_separator

    ensure_model()
    if settings.separation_mode != "off":
        ensure_separator()
    if settings.fx_embeddings:
        ensure_semantics()  # loads the singleton process.py reuses


def _housekeeping(session, last_telemetry_sweep: float | None) -> float | None:
    """Per-iteration upkeep before a claim; returns when telemetry was last swept."""
    queue.reclaim_expired(session)
    purged = queue.purge_expired_uploads(session)
    if purged:
        logger.info("purged %d expired staged upload(s)", purged)
    if (
        last_telemetry_sweep is None
        or time.monotonic() - last_telemetry_sweep > TELEMETRY_SWEEP_INTERVAL_S
    ):
        last_telemetry_sweep = time.monotonic()
        expired = queue.purge_old_telemetry(session, settings.telemetry_retention_days)
        if expired:
            logger.info(
                "purged %d telemetry row(s) older than %d days",
                expired,
                settings.telemetry_retention_days,
            )
    session.commit()
    return last_telemetry_sweep


def _idle_slot(session, should_stop) -> None:
    """No job to claim: drain at most one lrclib publish request (P6) — PoW
    may cost minutes and must never delay a lyrics job — else wait a poll."""
    from kashi_server.worker.publisher import process_one_publish

    if process_one_publish(session, should_stop=should_stop):
        return
    QUEUE_DEPTH.set(queue.queue_depth(session))
    session.commit()
    time.sleep(settings.worker_poll_interval_s)


def _run_claimed(session, job, session_factory, heartbeat_cls, process_job) -> None:
    logger.info(
        "claimed job %s (%s:%s) attempt %d on %s",
        job.id, job.source_type, job.source_id, job.attempts, settings.worker_name,
    )
    session.commit()
    started = time.monotonic()
    with heartbeat_cls(job.id, session_factory, attempts=job.attempts):
        process_job(session, job)
    JOB_SECONDS.observe(time.monotonic() - started)
    session.refresh(job)
    JOBS_TOTAL.labels(status=job.status).inc()
