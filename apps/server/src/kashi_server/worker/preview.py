"""Preview before refresh: what a reprocess would store for each archive song, measured against the
stored document, with nothing written (docs/research/refresh-from-evidence-2026-11.md).

    python -m kashi_server.worker.preview [--source-id ID ...] [--limit N] [--after ID]
        [--out FILE] [--documents DIR] [--lyrics stored|ladder] [--lrclib-gap-s S]

For each YouTube song with a stored document and stored evidence (or the songs given), in
source-id order: the worker's own job code (`process._run_pipeline`, `persist=False`) runs under a
`Replayer` of the song's evidence, on an in-memory job built as a reprocess builds one (the latest
job's hints, the song's override; worker/backfill.py), whatever `settings.evidence_mode` says. The
document it builds is compared with the stored one (worker/preview_rules.py) and judged:
`refresh`, `unchanged`, `keep` (a veto, or the persist guard), `sheet changed`, `needs a full job`
or `failed`. The last line is a JSON summary with the counts and the ids to refresh, one admin
reprocess call each.

Nothing is written, by construction:
- every read (hints, override, stored document, evidence) goes through a session whose
  transactions are READ ONLY (`read_only`): the database refuses any write;
- the job code gets a `PreviewSession`, which cannot reach the database at all: the in-memory
  job's heartbeats are answered as touching no row, commit and flush have nothing to do, anything
  else raises `PreviewWriteError`;
- no job row (the job is in memory), no document (`persist=False`), no evidence (no recorder), no
  override bookkeeping (the persist guard's question is asked with `stored_document_kept`, which
  writes nothing). The job code's Prometheus counters count in this process, which serves no
  metrics.

It cannot fall back to a full job: a song whose evidence does not serve (none, another key, a
nightcore clock, a duration conflict, a slice another window plan cuts, the by-ear rung) is
reported as `needs a full job: <reason>`; nothing is downloaded, separated or aligned.

Lyrics: `--lyrics stored` (default) asks lrclib for the one record the stored document names, by
id (one request a song, through the worker's gateway: lrclib.net, then the mirror when it is busy)
— the preview then measures the release on the same sheet, but cannot see the ladder choosing
another record today. `--lyrics ladder` runs the lookup ladder exactly as a reprocess would (up
to five requests a song). An override's lyrics are always the override's. Requests to lrclib.net
are at least `--lrclib-gap-s` apart (default 0.5 s, the top of the gap lrclib asks for in batch
work). No preview can run without the record: its stamps cut the windows and judge line QA, and
neither the document nor the evidence keeps them.

Exit codes: 0 the run finished; 2 the database schema is behind this code; 143 stopped by
SIGTERM (the summary still says what was previewed).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from kashi_server import overrides
from kashi_server.config import settings
from kashi_server.db.models import ProcessedTrack, TrackEvidence
from kashi_server.pipeline import evidence
from kashi_server.pipeline.document import stored_document_kept
from kashi_server.vdl_kit.errors import PipelineError, is_transient_error
from kashi_server.worker import backfill
from kashi_server.worker import preview_rules as rules

logger = logging.getLogger(__name__)

#: Scratch directories of a preview (nothing is written in them during a replay; matched by the
#: worker's own orphan sweep of `job-*`).
DIR_PREFIX = "job-preview-"
LYRICS_MODES = ("stored", "ladder")
EXIT_OK, EXIT_SCHEMA, EXIT_TERMINATED = 0, 2, 143

#: The one statement the job code sends through its session that a preview answers itself: the
#: lease heartbeat of its in-memory job (queue.heartbeat), which would touch no row anyway.
_HEARTBEAT = "UPDATE jobs SET lease_expires_at"


class PreviewWriteError(RuntimeError):
    """The job code reached for the database during a preview."""


class _NoRow:
    rowcount = 0


class PreviewSession:
    """The session the job code gets in a preview. The job is not a row and nothing is stored, so
    there is nothing to commit, flush, expire or refresh; the in-memory job's heartbeat is
    answered as one that touched no row; anything else raises PreviewWriteError."""

    def __init__(self, job_id: Any) -> None:
        self._job_id = job_id

    def commit(self) -> None: ...

    def flush(self, *_a: Any, **_k: Any) -> None: ...

    def rollback(self) -> None: ...

    def expire(self, *_a: Any, **_k: Any) -> None: ...

    def refresh(self, *_a: Any, **_k: Any) -> None: ...

    def execute(self, statement: Any, params: Any = None, *_a: Any, **_k: Any) -> _NoRow:
        sql = getattr(statement, "text", None)
        if (
            isinstance(sql, str)
            and sql.startswith(_HEARTBEAT)
            and isinstance(params, dict)
            and params.get("id") == self._job_id
        ):
            return _NoRow()
        raise PreviewWriteError(f"the job code sent {str(statement)[:80]!r} in a preview")

    def __getattr__(self, name: str) -> Any:
        raise PreviewWriteError(f"the job code used the session's {name!r} in a preview")


def read_only(s: Session) -> Session:
    """`s` with every transaction READ ONLY: Postgres refuses any write in it."""

    @event.listens_for(s, "after_begin")
    def _read_only(_session: Any, _transaction: Any, connection: Any) -> None:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")

    return s


def candidates(s: Session, limit: int | None = None, after: str | None = None) -> list[str]:
    """YouTube songs with a stored document and stored evidence, in source-id order after
    `after`, at most `limit` of them."""
    stmt = (
        select(ProcessedTrack.source_id)
        .join(
            TrackEvidence,
            (TrackEvidence.source_type == ProcessedTrack.source_type)
            & (TrackEvidence.source_id == ProcessedTrack.source_id),
        )
        .where(ProcessedTrack.source_type == "youtube", ProcessedTrack.schema_version == 1)
        .order_by(ProcessedTrack.source_id)
    )
    if after is not None:
        stmt = stmt.where(ProcessedTrack.source_id > after)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(s.scalars(stmt).all())


def without_evidence(s: Session) -> int:
    """YouTube documents without an evidence row (each needs a full job, or a back-fill)."""
    return int(
        s.scalar(
            select(func.count())
            .select_from(ProcessedTrack)
            .outerjoin(
                TrackEvidence,
                (TrackEvidence.source_type == ProcessedTrack.source_type)
                & (TrackEvidence.source_id == ProcessedTrack.source_id),
            )
            .where(ProcessedTrack.source_type == "youtube", TrackEvidence.id.is_(None))
        )
        or 0
    )


def _stored(s: Session, source_id: str) -> Any:
    """The stored row as the persist guard reads it."""
    return s.execute(
        select(
            ProcessedTrack.document,
            ProcessedTrack.etag,
            ProcessedTrack.pipeline_version,
            ProcessedTrack.pipeline_major,
        ).where(
            ProcessedTrack.source_type == "youtube",
            ProcessedTrack.source_id == source_id,
            ProcessedTrack.schema_version == 1,
        )
    ).first()


def _lyrics(job: Any, stored_document: dict, mode: str) -> str:
    """Which lyrics the preview aligns: the override's, the record the stored document names
    (pinned on the in-memory job, as an override's record would be), or the ladder's. A sheet
    offset without the override's own lyrics is left to the ladder, as a reprocess does (a
    pinned record would take the offset)."""
    options = dict(job.options or {})
    if any(
        key in options
        for key in ("lyrics_text", overrides.LRC_TEXT_KEY, overrides.PINNED_RECORD_KEY)
    ):
        return "override"
    if mode == "stored" and overrides.SHEET_OFFSET_KEY not in options:
        alignment = stored_document.get("alignment") or {}
        record = alignment.get("lyrics_source_id")
        if (
            alignment.get("lyrics_source") in ("lrclib", "lyricsfile")
            and isinstance(record, int)
            and not isinstance(record, bool)
            and record > 0
        ):
            job.options = {**options, overrides.PINNED_RECORD_KEY: record}
            return f"record {record}"
    return "ladder"


def _no_audio() -> Any:
    raise RuntimeError("a preview never reads audio")


def _build(job: Any, replayer: evidence.Replayer, pinned: bool) -> tuple[dict | None, tuple]:
    """(the document the job code builds from the evidence, None) or (None, (verdict, why))."""
    from kashi_server.worker import process

    tmp = settings.data_dir / f"{DIR_PREFIX}{job.id}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with evidence.active(replayer):
            doc = process._run_pipeline(
                cast(Session, PreviewSession(job.id)), job, tmp, persist=False
            )
    except PreviewWriteError:
        raise
    except evidence.EvidenceMissing as exc:
        return None, (rules.FULL_JOB, str(exc))
    except PipelineError as exc:
        why = f"{exc.error_type}: {exc.message[:200]}"
        if is_transient_error(exc.error_type):
            return None, (rules.FAILED, why)
        if pinned and exc.error_type == "other":
            return None, (rules.FAILED, f"the stored document's lyrics record: {why}")
        return None, (rules.FULL_JOB, why)
    except OperationalError:
        raise
    except Exception as exc:  # noqa: BLE001 - as in a replay: a fault costs a full job
        logger.warning("preview %s: the replay failed", job.source_id, exc_info=True)
        return None, (rules.FULL_JOB, f"the replay failed ({type(exc).__name__}: {str(exc)[:200]})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if doc is None:
        why = f"lrclib unavailable: a reprocess now would be deferred ({job.error_message})"
        return None, (rules.FAILED, why[:300])
    return doc, ()


def _preview(s: Session, source_id: str, mode: str, documents: Path | None) -> dict:
    stored = _stored(s, source_id)
    if stored is None:
        return {"verdict": rules.FAILED, "detail": "no stored document"}
    out: dict[str, Any] = {"old_pipeline": stored.pipeline_version}
    hints = backfill._latest_hints(s, "youtube", source_id)
    if hints is None:
        return {
            **out,
            "verdict": rules.FAILED,
            "detail": "no job history: a reprocess has no hints to replay",
        }
    job = backfill._job_for(s, "youtube", source_id, hints)
    never = backfill._never_recorded(stored.document, job.options or {})
    if never is not None:
        return {**out, "verdict": rules.FULL_JOB, "detail": never}
    from kashi_server.worker import process

    record = evidence.DbStore(s).load("youtube", source_id)
    replayer, why = evidence.replayer_for(
        record, settings, separate_first=process._separates_first(job)
    )
    if replayer is None or record is None:
        return {**out, "verdict": rules.FULL_JOB, "detail": why}
    conflict = evidence.duration_conflict(record, process._plausible_hint(job.hints))
    if conflict is not None:
        return {
            **out,
            "verdict": rules.FULL_JOB,
            "detail": f"{conflict}: the audio behind the id may have changed",
        }
    out["lyrics"] = _lyrics(job, stored.document, mode)
    doc, failure = _build(job, replayer, pinned=out["lyrics"].startswith("record"))
    out["served"] = dict(sorted(replayer.served.items()))
    if doc is None:
        return {**out, "verdict": failure[0], "detail": failure[1]}
    if documents is not None:
        documents.mkdir(parents=True, exist_ok=True)
        (documents / f"{source_id}.json").write_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n"
        )
    with evidence.active(replayer):
        envelope = evidence.envelope(_no_audio)
    # No lock: a read-only transaction cannot take one, and nothing here acts on the answer.
    guard = stored_document_kept(stored, overrides.find(s, "youtube", source_id), job, doc)
    comparison = rules.compare(stored.document, doc, envelope)
    verdict, detail = rules.verdict(comparison, guard)
    return {**out, "verdict": verdict, "detail": detail, **comparison}


def preview_song(
    s: Session, source_id: str, *, lyrics: str = "stored", documents: Path | None = None
) -> dict:
    """One song's preview row (numbers only: line indexes and times, no lyric text). `s` only
    reads (see `read_only`); its transaction ends with the song."""
    if lyrics not in LYRICS_MODES:
        raise ValueError(f"lyrics mode {lyrics!r} is not one of {LYRICS_MODES}")
    started = time.monotonic()
    row: dict[str, Any] = {"source_id": source_id}
    try:
        row.update(_preview(s, source_id, lyrics, documents))
    except OperationalError:
        raise
    except Exception as exc:  # noqa: BLE001 - one song's fault must not stop the run
        logger.warning("preview %s failed", source_id, exc_info=True)
        row.update(verdict=rules.FAILED, detail=f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        s.rollback()
    row["seconds"] = round(time.monotonic() - started, 2)
    return row


def summarize(rows: list[dict]) -> dict:
    """Counts per verdict, the ids to refresh (one admin reprocess call each) and why the others
    are not."""
    verdicts = {
        name: 0
        for name in (
            rules.REFRESH,
            rules.UNCHANGED,
            rules.VETOED,
            rules.SHEET,
            rules.FULL_JOB,
            rules.FAILED,
        )
    }
    vetoes: dict[str, int] = {}
    others: dict[str, dict[str, str]] = {
        "keep": {},
        "sheet_changed": {},
        "needs_full_job": {},
        "failed": {},
    }
    bucket = {
        rules.VETOED: "keep",
        rules.SHEET: "sheet_changed",
        rules.FULL_JOB: "needs_full_job",
        rules.FAILED: "failed",
    }
    for row in rows:
        verdict = row["verdict"]
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        if verdict in bucket:
            others[bucket[verdict]][row["source_id"]] = row.get("detail", "")
        if verdict == rules.VETOED and row.get("detail", "").startswith("veto: "):
            for name in row["detail"][len("veto: ") :].split(", "):
                vetoes[name] = vetoes.get(name, 0) + 1
    return {
        "songs": len(rows),
        "verdicts": verdicts,
        "refresh_ids": [r["source_id"] for r in rows if r["verdict"] == rules.REFRESH],
        "unchanged_ids": [r["source_id"] for r in rows if r["verdict"] == rules.UNCHANGED],
        "vetoes": dict(sorted(vetoes.items())),
        **others,
        "next_after": rows[-1]["source_id"] if rows else None,
    }


def lrclib_requests() -> dict[str, int]:
    """lrclib requests this process has sent so far, by target (primary / mirror)."""
    from kashi_server.pipeline.lrclib import LRCLIB_REQUESTS

    out: dict[str, int] = {}
    for metric in LRCLIB_REQUESTS.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total"):
                target = sample.labels["target"]
                out[target] = out.get(target, 0) + int(sample.value)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="What a reprocess would store, with nothing "
        "written: rebuilt from stored evidence and "
        "compared with the stored document."
    )
    parser.add_argument("--source-id", nargs="*", default=None, help="these songs only")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--after", default=None, help="songs after this id (a previous next_after)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON lines, one per song, then the summary (default: "
        "<data_dir>/preview-<UTC time>.jsonl)",
    )
    parser.add_argument(
        "--documents",
        type=Path,
        default=None,
        help="also write each built document here (they carry lyric text)",
    )
    parser.add_argument("--lyrics", choices=LYRICS_MODES, default="stored")
    parser.add_argument(
        "--lrclib-gap-s",
        type=float,
        default=0.5,
        help="at least this long between two requests to lrclib.net",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from kashi_server.db.engine import SessionLocal, engine
    from kashi_server.db.schema import schema_gap
    from kashi_server.version import PIPELINE_VERSION

    with engine.connect() as conn:
        gap = schema_gap(conn)
    if gap is not None:
        print(f"preview refused: database schema behind this code: {gap}", file=sys.stderr)
        return EXIT_SCHEMA
    settings.lrclib_request_gap_s = max(settings.lrclib_request_gap_s, args.lrclib_gap_s)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path: Path = args.out or settings.data_dir / f"preview-{stamp}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"preview: pipeline {PIPELINE_VERSION}, lyrics {args.lyrics}, rows to {out_path}",
        flush=True,
    )
    rows: list[dict] = []
    code = EXIT_OK
    started = time.monotonic()
    requests_before = lrclib_requests()
    missing = None
    with open(out_path, "w") as out:
        try:
            with backfill._sigterm_raises(), SessionLocal() as s:
                read_only(s)
                ids = args.source_id or candidates(s, args.limit, args.after)
                if not args.source_id:
                    missing = without_evidence(s)
                    s.rollback()
                for source_id in ids:
                    row = preview_song(s, source_id, lyrics=args.lyrics, documents=args.documents)
                    rows.append(row)
                    out.write(json.dumps(row, sort_keys=True) + "\n")
                    out.flush()
                    detail = f" ({row['detail']})" if row.get("detail") else ""
                    print(f"{source_id}: {row['verdict']}{detail}", flush=True)
        except backfill.Terminated:
            code = EXIT_TERMINATED
        requests_after = lrclib_requests()
        summary = {
            "pipeline": PIPELINE_VERSION,
            "lyrics": args.lyrics,
            **summarize(rows),
            "without_evidence": missing,
            "lrclib_requests": {
                k: v - requests_before.get(k, 0) for k, v in sorted(requests_after.items())
            },
            "seconds": round(time.monotonic() - started, 1),
            "terminated": code == EXIT_TERMINATED,
        }
        out.write(json.dumps({"summary": summary}, sort_keys=True) + "\n")
    print(f"preview summary {json.dumps(summary)}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
