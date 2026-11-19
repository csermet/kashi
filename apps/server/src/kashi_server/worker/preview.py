"""Preview before refresh: what a reprocess would store for each archive song, measured against the
stored document, with nothing written (docs/research/refresh-from-evidence-2026-11.md).

    python -m kashi_server.worker.preview [--source-id ID ...] [--limit N] [--after ID]
        [--out FILE [--append]] [--documents DIR] [--lyrics stored|ladder] [--confirm-all]
        [--lrclib-gap-s S] [--beside-live-jobs]

For each YouTube song with a stored document and stored evidence (or the songs given), in
source-id order: the worker's own job code (`process._run_pipeline`, `persist=False`) runs under a
`Replayer` of the song's evidence, on an in-memory job built as a reprocess builds one (the latest
job's hints, the song's override; worker/backfill.py), whatever `settings.evidence_mode` says. The
document it builds is compared with the stored one (worker/preview_rules.py) and judged:
`refresh`, `unchanged`, `review` (for a person), `keep` (a veto, or the persist guard),
`sheet changed`, `needs a full job` or `failed`. The last line is a JSON summary with the counts,
the ids to refresh (one admin reprocess call each) and what a worker needs for those reprocesses
to replay.

Nothing is written, by construction:
- every read (hints, override, stored document, evidence, live jobs) goes through a session whose
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
id: the release is measured on the same sheet. A song that comes out `refresh` that way is then
previewed AGAIN with the lookup ladder, exactly as its reprocess will run it, and stays `refresh`
only when the ladder builds the same document on the same record; otherwise its verdict says what
the ladder did (another record, the by-ear rung, a failure). `--confirm-all` runs that ladder pass
for every song that built a document on its stored record, whatever its verdict (which stays).
Every row says `ladder_same`: true only when the document it judged is the one the reprocess's own
lookup builds; only such ids may be reprocessed. `--lyrics ladder` runs the ladder for
every song. An override's lyrics are always the override's. Requests to lrclib.net are at least
`--lrclib-gap-s` apart (default 0.5 s, the top of the gap lrclib asks for in batch work). No
preview can run without the record: its stamps cut the windows and judge line QA, and neither the
document nor the evidence keeps them.

The report carries numbers and fixed reasons only: line indexes, times, error classes; never lyric
text, titles or exception messages (those go to the log).

Exit codes: 0 the run finished; 2 the database schema is behind this code; 3 `--out` exists
(resume with `--append`); 4 a job is live (refused at the start, or stopped between two songs;
`--beside-live-jobs` previews anyway); 5 the database went away; 128 + the signal's number when
stopped by SIGHUP (129), SIGINT (130) or SIGTERM (143). Every exit after the start writes the
summary of what was previewed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import resource
import shutil
import signal
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from kashi_server import overrides
from kashi_server.config import settings
from kashi_server.db.models import LIVE_STATUSES, Job, ProcessedTrack, TrackEvidence
from kashi_server.pipeline import evidence
from kashi_server.pipeline.document import stored_document_kept, with_stored_palette
from kashi_server.vdl_kit.errors import PipelineError, is_transient_error
from kashi_server.worker import backfill
from kashi_server.worker import preview_rules as rules

logger = logging.getLogger(__name__)

#: Scratch directories of a preview (nothing is written in them during a replay; matched by the
#: worker's own orphan sweep of `job-*`).
DIR_PREFIX = "job-preview-"
LYRICS_MODES = ("stored", "ladder")
#: The layout and rules of the out file's rows; bumped whenever a row of an earlier format could
#: be read wrongly by this code (2: segments carry their judge, refreshes their confirmation;
#: 3: W4 counts, W5, `ladder_same`, the palette carried; 4: `ladder_same`'s other values, every
#: veto and review reason beside the verdict).
PREVIEW_FORMAT = 4
#: A row's `ladder_same` other than True / False: a document was built but no ladder pass ran (not
#: asked, or it faulted: the `ladder` note names the fault) ...
LADDER_NOT_RUN = "not run"
#: ... no document was built ...
LADDER_NO_DOCUMENT = "no document"
#: ... or the passes built the same document, but one of them ran the aligner library in its
#: overrun region (undefined behaviour: the reprocess may store another path).
LADDER_OVERRUN = "overrun"
EXIT_OK, EXIT_SCHEMA, EXIT_OUT_EXISTS, EXIT_LIVE_JOBS, EXIT_DATABASE = 0, 2, 3, 4, 5
#: Signals that stop a run with its summary written; the exit code is 128 + the signal's number.
STOP_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)

#: The one statement the job code sends through its session that a preview answers itself: the
#: lease heartbeat of its in-memory job (queue.heartbeat), which would touch no row anyway.
_HEARTBEAT = "UPDATE jobs SET lease_expires_at"
#: overrides.override_lyrics' own words for a pinned record that cannot serve lyrics: in `stored`
#: mode the pin is the preview's.
_PIN_FAILURE = "song override: pinned lrclib record"


class PreviewWriteError(RuntimeError):
    """The job code reached for the database during a preview."""


class Stopped(BaseException):  # noqa: N818 - like KeyboardInterrupt: not caught by `except Exception`
    """A stop signal during a preview: unwinds the song (its scratch is deleted), then the run
    ends with its summary."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


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


def live_jobs(s: Session) -> int:
    """Jobs queued or running: a worker that runs one may be separating, the step that needs its
    container's memory (a preview beside it shares that limit)."""
    return int(
        s.scalar(select(func.count()).select_from(Job).where(Job.status.in_(LIVE_STATUSES))) or 0
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


def _build(
    job: Any, replayer: evidence.Replayer, pinned: bool, hits: list[dict]
) -> tuple[dict | None, tuple]:
    """(the document the job code builds from the evidence, ()) or (None, (verdict, why)); the
    passes the aligner library ran in its overrun region are added to `hits`. The reasons are
    fixed words and error classes: an exception's message can carry a title or a line of the
    lyrics, so it goes to the log only."""
    from kashi_server.pipeline.alignment import AlignerRefusal, overruns
    from kashi_server.worker import process

    tmp = settings.data_dir / f"{DIR_PREFIX}{job.id}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with evidence.active(replayer), overruns() as found:
            try:
                doc = process._run_pipeline(
                    cast(Session, PreviewSession(job.id)), job, tmp, persist=False
                )
            finally:
                hits.extend(found)
    except PreviewWriteError:
        raise
    except AlignerRefusal as exc:
        # The window plan, the text and the emissions are the evidence's: a full job meets them
        # again and fails the same way, permanently. Nobody should pay a download for it.
        logger.info("preview %s: %s", job.source_id, exc.message[:300])
        return None, (rules.FAILED, "alignment_failed")
    except evidence.EvidenceChanged:
        return None, (rules.FAILED, "evidence replaced while it was read: preview the song again")
    except evidence.EvidenceMissing as exc:
        # Our own reasons (which slice, which pass, which key field): no lyrics, no title.
        return None, (rules.FULL_JOB, str(exc))
    except PipelineError as exc:
        logger.info("preview %s: %s: %s", job.source_id, exc.error_type, exc.message[:300])
        if is_transient_error(exc.error_type):
            return None, (rules.FAILED, f"transient {exc.error_type}")
        if pinned and exc.error_type == "other" and exc.message.startswith(_PIN_FAILURE):
            return None, (rules.FAILED, "the stored document's lyrics record is gone or unusable")
        return None, (rules.FULL_JOB, f"pipeline error {exc.error_type}")
    except OperationalError:
        raise
    except Exception as exc:  # noqa: BLE001 - as in a replay: a fault costs a full job
        logger.warning("preview %s: the replay failed", job.source_id, exc_info=True)
        return None, (rules.FULL_JOB, f"replay fault {type(exc).__name__}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if doc is None:
        return None, (rules.FAILED, "lrclib unavailable: a reprocess now would be deferred")
    return doc, ()


def _preview(
    s: Session, source_id: str, mode: str, documents: Path | None
) -> tuple[dict, dict | None]:
    """(the row's fields, the document built or None)."""
    stored = _stored(s, source_id)
    if stored is None:
        return {"verdict": rules.FAILED, "detail": "no stored document"}, None
    out: dict[str, Any] = {"old_pipeline": stored.pipeline_version}
    hints = backfill._latest_hints(s, "youtube", source_id)
    if hints is None:
        return {
            **out,
            "verdict": rules.FAILED,
            "detail": "no job history: a reprocess has no hints to replay",
        }, None
    job = backfill._job_for(s, "youtube", source_id, hints)
    # Which override the build applies (its fingerprint, as a job's marker carries it), or None.
    out["override"] = overrides.applied_fingerprint(job.options)
    never = backfill._never_recorded(stored.document, job.options or {})
    if never is not None:
        return {**out, "verdict": rules.FULL_JOB, "detail": never}, None
    from kashi_server.worker import process

    record = evidence.DbStore(s).load("youtube", source_id)
    replayer, why = evidence.replayer_for(
        record, settings, separate_first=process._separates_first(job)
    )
    if replayer is None or record is None:
        return {**out, "verdict": rules.FULL_JOB, "detail": why}, None
    conflict = evidence.duration_conflict(record, process._plausible_hint(job.hints))
    if conflict is not None:
        return {
            **out,
            "verdict": rules.FULL_JOB,
            "detail": f"{conflict}: the audio behind the id may have changed",
        }, None
    out["lyrics"] = _lyrics(job, stored.document, mode)
    hits: list[dict] = []
    doc, failure = _build(job, replayer, out["lyrics"].startswith("record"), hits)
    out["served"] = dict(sorted(replayer.served.items()))
    if doc is None:
        return {**out, "verdict": failure[0], "detail": failure[1]}, None
    # What persist stores: a palette that fell back to the default (a failed artwork fetch) over
    # one read off the artwork is the stored one (not under force_persist, not when it fails the
    # schema).
    built, doc = doc, with_stored_palette(stored.document, doc, job)
    out["palette_carried"] = doc is not built
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
    verdict, detail = rules.verdict(comparison, guard, len(hits))
    out["aligner_overruns"] = hits
    # Every veto and review reason, whichever the verdict names: a guard hides the vetoes and a
    # veto the review reasons (2026-11-19: six songs released from W4 by hand were `review`).
    out.update(rules.reasons(comparison, len(hits)))
    return {**out, "verdict": verdict, "detail": detail, **comparison}, doc


def _same_document(a: dict, b: dict) -> bool:
    return {k: v for k, v in a.items() if k != "generated_at"} == {
        k: v for k, v in b.items() if k != "generated_at"
    }


def _record(doc: dict | None) -> Any:
    return None if doc is None else (doc.get("alignment") or {}).get("lyrics_source_id")


def ladder_same(doc: dict, ladder_doc: dict | None) -> bool:
    """The ladder pass built the same document (`generated_at` aside) on the same lyrics record."""
    return (
        ladder_doc is not None
        and _record(ladder_doc) == _record(doc)
        and _same_document(doc, ladder_doc)
    )


def _same(doc: dict, ladder: dict, ladder_doc: dict | None, overran: bool) -> bool | str:
    """A row's `ladder_same` after a ladder pass (see `preview_song`)."""
    if not ladder_same(doc, ladder_doc):
        return False
    return LADDER_OVERRUN if overran or ladder.get("aligner_overruns") else True


def ladder_note(ladder: dict, ladder_doc: dict | None) -> dict:
    """What the ladder pass said: its verdict and detail, the record it aligned, and the passes it
    ran in the aligner library's overrun region."""
    return {
        "verdict": ladder["verdict"],
        "detail": ladder.get("detail", ""),
        "lyrics_record": _record(ladder_doc),
        "aligner_overruns": ladder.get("aligner_overruns", []),
    }


def confirmed(row: dict, doc: dict, ladder: dict, ladder_doc: dict | None) -> dict:
    """The ladder's word on a `stored`-mode `refresh`: the fields that replace the row's verdict.
    It stays `refresh` only when the ladder also says `refresh`, on the same lyrics record, with
    the same document (`generated_at` aside); else the verdict is the ladder's own (another record
    is a changed sheet, the by-ear rung a full job, weather a failure, an overrun pass a review),
    or `review` when the ladder says `refresh` with another document."""
    same = ladder["verdict"] == rules.REFRESH and ladder_same(doc, ladder_doc)
    note = {**ladder_note(ladder, ladder_doc), "confirmed": same}
    if same:
        return {"ladder": note}
    if ladder["verdict"] == rules.REFRESH:
        return {
            "ladder": note,
            "verdict": rules.REVIEW,
            "detail": "the ladder builds another document than the stored record does",
        }
    return {
        "ladder": note,
        "verdict": ladder["verdict"],
        "detail": f"ladder: {ladder.get('detail') or ladder['verdict']}",
    }


def preview_song(
    s: Session,
    source_id: str,
    *,
    lyrics: str = "stored",
    documents: Path | None = None,
    confirm: bool = True,
    confirm_all: bool = False,
) -> dict:
    """One song's preview row (numbers and fixed reasons only). `s` only reads (see
    `read_only`); its transaction ends with the song. In `stored` mode a `refresh` on the stored
    record is previewed again with the ladder (`confirmed`); with `confirm_all`, every row that
    built a document on the stored record is (its verdict stays; `ladder_same` says what the
    ladder built).

    `ladder_same`: True when the document this row judged is the one the reprocess's own lookup
    builds (a ladder pass built it on the same record, or the ladder or the song's override built
    it in the first place) and no pass ran the aligner library in its overrun region; False when
    the ladder pass built another document, another record or none; LADDER_OVERRUN when the same
    document came out of passes one of which overran; LADDER_NOT_RUN when no ladder pass ran (or
    it faulted: the verdict stays, the `ladder` note names the fault); LADDER_NO_DOCUMENT when no
    document was built."""
    if lyrics not in LYRICS_MODES:
        raise ValueError(f"lyrics mode {lyrics!r} is not one of {LYRICS_MODES}")
    started = time.monotonic()
    row: dict[str, Any] = {"source_id": source_id}
    # Names the song before its replay: a crash inside the aligner library ends the process
    # without a row, and `--after` this id resumes past it.
    logger.info("preview of %s starts", source_id)
    try:
        fields, doc = _preview(s, source_id, lyrics, documents)
        row.update(fields)
        pinned = str(row.get("lyrics", "")).startswith("record")
        overran = bool(row.get("aligner_overruns"))
        if doc is None:
            row["ladder_same"] = LADDER_NO_DOCUMENT
        elif not pinned:  # built by the reprocess's own lookup (or override)
            row["ladder_same"] = LADDER_OVERRUN if overran else True
        elif confirm and row["verdict"] == rules.REFRESH:
            s.rollback()
            ladder, ladder_doc = _preview(s, source_id, "ladder", None)
            row.update(confirmed(row, doc, ladder, ladder_doc))
            row["ladder_same"] = _same(doc, ladder, ladder_doc, overran)
        elif confirm and confirm_all:
            s.rollback()
            try:
                ladder, ladder_doc = _preview(s, source_id, "ladder", None)
            except OperationalError:
                raise
            except Exception as exc:  # noqa: BLE001 - the extra pass never changes the verdict
                logger.warning("preview %s: the ladder pass failed", source_id, exc_info=True)
                row["ladder"] = {"fault": type(exc).__name__}
            else:
                row["ladder"] = ladder_note(ladder, ladder_doc)
                row["ladder_same"] = _same(doc, ladder, ladder_doc, overran)
        if row["verdict"] == rules.REFRESH:
            # What the reprocess will run: the ladder (or the override), as this row now says.
            row["refresh_confirmed"] = not pinned or (
                bool((row.get("ladder") or {}).get("confirmed"))
            )
    except OperationalError:
        raise
    except Exception as exc:  # noqa: BLE001 - one song's fault must not stop the run
        logger.warning("preview %s failed", source_id, exc_info=True)
        row.update(
            verdict=rules.FAILED,
            detail=f"preview fault {type(exc).__name__}",
            ladder_same=LADDER_NOT_RUN,
        )
    finally:
        s.rollback()
    row.setdefault("ladder_same", LADDER_NOT_RUN)
    row["seconds"] = round(time.monotonic() - started, 2)
    return row


def _counted(row: dict) -> dict:
    """The row as the summary counts it: a `refresh` its reprocess's own lyrics did not confirm
    (an earlier format, a hand-edited file) is a `review`, never an id to refresh."""
    if row.get("verdict") == rules.REFRESH and row.get("refresh_confirmed") is not True:
        return {**row, "verdict": rules.REVIEW, "detail": "refresh not confirmed by the ladder"}
    return row


def summarize(rows: list[dict]) -> dict:
    """Counts per verdict, the ids to refresh (one admin reprocess call each) and why the others
    are not."""
    verdicts = {
        name: 0
        for name in (
            rules.REFRESH,
            rules.UNCHANGED,
            rules.REVIEW,
            rules.VETOED,
            rules.SHEET,
            rules.FULL_JOB,
            rules.FAILED,
        )
    }
    vetoes: dict[str, int] = {}
    others: dict[str, dict[str, Any]] = {
        "review": {},
        "keep": {},
        "sheet_changed": {},
        "needs_full_job": {},
        "failed": {},
    }
    bucket = {
        rules.REVIEW: "review",
        rules.VETOED: "keep",
        rules.SHEET: "sheet_changed",
        rules.FULL_JOB: "needs_full_job",
        rules.FAILED: "failed",
    }
    rows = [_counted(row) for row in rows]
    for row in rows:
        verdict = row["verdict"]
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        if verdict == rules.VETOED:
            # What the verdict hides, where a person reads: a guard's vetoes, a veto's reviews.
            others["keep"][row["source_id"]] = {
                "detail": row.get("detail", ""),
                "vetoes": row.get("vetoes", []),
                "reviews": row.get("reviews", []),
            }
        elif verdict in bucket:
            others[bucket[verdict]][row["source_id"]] = row.get("detail", "")
        if verdict == rules.VETOED and row.get("detail", "").startswith("veto: "):
            for name in row["detail"][len("veto: ") :].split(", "):
                vetoes[name] = vetoes.get(name, 0) + 1
    # The archive-wide counts (the rig's aggregate view), over the songs judged on the same sheet.
    same_sheet = [row for row in rows if "moved" in row]
    totals: dict[str, Any] = {"songs": len(same_sheet)}
    for side in ("old", "new"):
        totals[side] = {}
        for row in same_sheet:
            for name, value in row[side].items():
                totals[side][name] = totals[side].get(name, 0) + value
    totals["moved_lines"] = {
        key: sum(row["moved"][key] for row in same_sheet)
        for key in (f"over_{rules.MOVED_MS}", *(f"over_{far}" for far in rules.MOVED_FAR_MS))
    }
    same = {row["source_id"]: row.get("ladder_same", LADDER_NOT_RUN) for row in rows}
    return {
        "songs": len(rows),
        "verdicts": verdicts,
        "same_sheet_totals": totals,
        "refresh_ids": [r["source_id"] for r in rows if r["verdict"] == rules.REFRESH],
        "unchanged_ids": [r["source_id"] for r in rows if r["verdict"] == rules.UNCHANGED],
        # Per song: may it be reprocessed at all (only `True`), whatever its verdict.
        "ladder_same": same,
        "ladder_same_counts": {
            "true": sum(v is True for v in same.values()),
            "false": sum(v is False for v in same.values()),
            **{
                name: sum(v == name for v in same.values())
                for name in (LADDER_OVERRUN, LADDER_NOT_RUN, LADDER_NO_DOCUMENT)
            },
        },
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


def replay_requirements() -> dict:
    """What a worker needs for a reprocess of a `refresh` song to replay instead of downloading:
    the mode, and the evidence key this image records and replays (a worker of another image or
    configuration logs `key differs at ...` and downloads)."""
    return {
        "evidence_mode": "replay",
        "this_environment_evidence_mode": settings.evidence_mode,
        "key": evidence.expected_key(settings, separate_first=settings.separation_mode == "always"),
    }


def peak_rss_mb() -> float:
    """This process's peak resident memory so far (Linux reports KiB)."""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def judge(lyrics: str) -> dict:
    """What judges a run's rows: the preview's format, the pipeline, the evidence this image
    replays (its key: format, code versions, separation, libraries) and the lyrics mode. Rows of
    one out file must all come from one judge."""
    from kashi_server.version import PIPELINE_VERSION

    return {
        "preview_format": PREVIEW_FORMAT,
        "pipeline": PIPELINE_VERSION,
        "evidence": evidence.expected_key(
            settings, separate_first=settings.separation_mode == "always"
        ),
        "lyrics": lyrics,
    }


def rows_in(path: Path) -> dict:
    """An earlier out file: its rows (each with whether a cursor run made it), the judges of its
    segments and whether each ran `--confirm-all`, and how many lines could not be read (a run
    killed mid-line leaves a truncated last line; such lines are skipped and counted)."""
    rows: list[dict] = []
    judges: list[Any] = []
    confirm_all: list[bool] = []
    unreadable = 0
    cursor = False
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            if not isinstance(item, dict):
                unreadable += 1
            elif "segment" in item:
                judges.append(item["segment"].get("judge"))
                confirm_all.append(item["segment"].get("confirm_all") is True)
                cursor = bool(item["segment"].get("cursor"))
            elif "source_id" in item:
                rows.append({**item, "_cursor": cursor})
    return {"rows": rows, "judges": judges, "confirm_all": confirm_all, "unreadable": unreadable}


def confirm_all_of(segments: list[bool]) -> str:
    """Whether the file's segments ran `--confirm-all`: "all", "none" or "mixed" (each row's
    `ladder_same` says what its own run did)."""
    if all(segments):
        return "all"
    return "none" if not any(segments) else "mixed"


def frontier(rows: list[dict], fallback: str | None) -> str | None:
    """How far the cursor runs got: the highest source id a cursor run previewed (a
    `--source-id` run previews given songs and moves no cursor)."""
    reached = [row["source_id"] for row in rows if row.get("_cursor")]
    return max(reached) if reached else fallback


def whole(earlier: list[dict], rows: list[dict]) -> list[dict]:
    """Every song once, its latest row winning, in source-id order."""
    latest = {row["source_id"]: row for row in [*earlier, *rows]}
    return [{k: v for k, v in latest[key].items() if k != "_cursor"} for key in sorted(latest)]


@contextlib.contextmanager
def _stop_signals_raise() -> Iterator[None]:
    def handler(signum: int, _frame: Any) -> None:
        raise Stopped(signum)

    previous = {sig: signal.signal(sig, handler) for sig in STOP_SIGNALS}
    try:
        yield
    finally:
        for sig, handler_before in previous.items():
            signal.signal(sig, handler_before)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="What a reprocess would store, with nothing written: rebuilt from stored "
        "evidence and compared with the stored document."
    )
    parser.add_argument("--source-id", nargs="+", default=None, help="these songs only")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--after", default=None, help="songs after this id (a previous next_after)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON lines, one per song, then the summary (default: "
        "<data_dir>/preview-<UTC time>.jsonl); never overwritten",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="continue an earlier --out: a segment marker, the rows, and a summary of the whole "
        "file (each song's latest row)",
    )
    parser.add_argument(
        "--documents",
        type=Path,
        default=None,
        help="also write each built document here (they carry lyric text)",
    )
    parser.add_argument("--lyrics", choices=LYRICS_MODES, default="stored")
    parser.add_argument(
        "--confirm-all",
        action="store_true",
        help="in stored mode, a ladder pass for every song that built a document, not only for "
        "`refresh` (one more lookup and replay a song): every row then says `ladder_same`",
    )
    parser.add_argument(
        "--lrclib-gap-s",
        type=float,
        default=0.5,
        help="at least this long between two requests to lrclib.net",
    )
    parser.add_argument(
        "--beside-live-jobs",
        action="store_true",
        help="preview even while jobs are queued or running (a preview shares the worker "
        "container's memory)",
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
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path: Path = args.out or settings.data_dir / f"preview-{stamp}.jsonl"
    if out_path.exists() and not args.append:
        print(f"preview refused: {out_path} exists (continue it with --append)", file=sys.stderr)
        return EXIT_OUT_EXISTS
    current = judge(args.lyrics)
    before = (
        rows_in(out_path)
        if out_path.exists()
        else {"rows": [], "judges": [], "confirm_all": [], "unreadable": 0}
    )
    if before["rows"] and not before["judges"] or any(j != current for j in before["judges"]):
        print(
            f"preview refused: {out_path} was written by another preview (format, pipeline, "
            "evidence key or lyrics mode differ); start a new file",
            file=sys.stderr,
        )
        return EXIT_OUT_EXISTS
    earlier, segments = before["rows"], len(before["judges"])
    if before["unreadable"]:
        print(f"note: {before['unreadable']} unreadable line(s) in {out_path} skipped", flush=True)
    with SessionLocal() as s:
        live = live_jobs(read_only(s))
        s.rollback()
    if live and not args.beside_live_jobs:
        print(
            f"preview refused: {live} job(s) queued or running; a preview shares the worker "
            "container's memory (--beside-live-jobs to preview anyway)",
            file=sys.stderr,
        )
        return EXIT_LIVE_JOBS
    settings.lrclib_request_gap_s = max(settings.lrclib_request_gap_s, args.lrclib_gap_s)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    replay = replay_requirements()
    print(
        f"preview: pipeline {PIPELINE_VERSION}, lyrics {args.lyrics}, rows to {out_path}",
        flush=True,
    )
    if replay["this_environment_evidence_mode"] != "replay":
        print(
            f"note: EVIDENCE_MODE here is {replay['this_environment_evidence_mode']!r}: a worker "
            "configured like this downloads on reprocess; refresh from workers in 'replay' with "
            "the summary's replay.key, and reprocess ONE id first (its log must say 'rebuilt from "
            "stored evidence, no download')",
            flush=True,
        )
    rows: list[dict] = []
    code = EXIT_OK
    started = time.monotonic()
    requests_before = lrclib_requests()
    missing = None
    with open(out_path, "a") as out:
        out.write(
            json.dumps(
                {
                    "segment": {
                        "started": stamp,
                        "judge": current,
                        "cursor": not args.source_id,
                        "confirm_all": args.confirm_all,
                        "after": args.after,
                        "limit": args.limit,
                        "source_ids": len(args.source_id or []),
                    }
                },
                sort_keys=True,
            )
            + "\n"
        )
        out.flush()
        try:
            with _stop_signals_raise(), SessionLocal() as s:
                read_only(s)
                ids = args.source_id or candidates(s, args.limit, args.after)
                if not args.source_id:
                    missing = without_evidence(s)
                s.rollback()
                for source_id in ids:
                    if not args.beside_live_jobs and live_jobs(s):
                        s.rollback()
                        print(
                            "preview stopped: a job went live (resume with --after and --append)",
                            flush=True,
                        )
                        code = EXIT_LIVE_JOBS
                        break
                    s.rollback()
                    row = preview_song(
                        s,
                        source_id,
                        lyrics=args.lyrics,
                        documents=args.documents,
                        confirm_all=args.confirm_all,
                    )
                    rows.append(row)
                    out.write(json.dumps(row, sort_keys=True) + "\n")
                    out.flush()
                    detail = f" ({row['detail']})" if row.get("detail") else ""
                    same = str(row["ladder_same"]).lower()
                    print(f"{source_id}: {row['verdict']}{detail} [ladder_same {same}]", flush=True)
        except Stopped as stop:
            code = 128 + stop.signum
        except OperationalError:
            logger.exception("preview stopped: the database went away")
            code = EXIT_DATABASE
        requests_after = lrclib_requests()
        everything = whole(earlier, rows)
        cursor = not args.source_id
        summary = {
            "pipeline": PIPELINE_VERSION,
            "lyrics": args.lyrics,
            "preview_format": PREVIEW_FORMAT,
            **summarize(everything),
            "next_after": frontier(
                [*earlier, *({**r, "_cursor": cursor} for r in rows)], args.after
            ),
            "segments": segments + 1,
            "confirm_all": confirm_all_of([*before["confirm_all"], args.confirm_all]),
            "unreadable_lines": before["unreadable"],
            "without_evidence": missing,
            "replay": replay,
            "this_run": {
                "songs": len(rows),
                "lrclib_requests": {
                    k: v - requests_before.get(k, 0) for k, v in sorted(requests_after.items())
                },
                "seconds": round(time.monotonic() - started, 1),
                "peak_rss_mb": peak_rss_mb(),
                "exit_code": code,
            },
        }
        out.write(json.dumps({"summary": summary}, sort_keys=True) + "\n")
    print(f"preview summary {json.dumps(summary)}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
