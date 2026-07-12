"""Assemble, validate and persist the processed-track document.

`validate_document` is the HARD gate before persist (reviewer checklist):
whatever bug upstream stages develop, a document that violates
processed-track.v1.schema.json never reaches the database.
"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from kashi_server.config import settings
from kashi_server.db.models import Job
from kashi_server.pipeline.alignment import AlignResult
from kashi_server.pipeline.beats import Beats
from kashi_server.pipeline.line_qa import is_adlib
from kashi_server.pipeline.lrclib import LyricsText, normalize_artist
from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.version import PIPELINE_MAJOR, PIPELINE_VERSION

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


def canonical_group(artist: str, title: str, duration_s: float) -> str:
    """Discovery index ONLY — never a cache key (plan R-2)."""

    def norm(value: str) -> str:
        return _WHITESPACE.sub(" ", normalize_artist(value).lower()).strip()

    return f"{norm(artist)}|{norm(title)}|{round(duration_s / 5) * 5}"


def compute_etag(doc: dict) -> str:
    """THE canonical JSON definition — identical in TS (kashi-reviewer rule)."""
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def build_document(
    job: Job,
    lyrics: LyricsText,
    align_result: AlignResult,
    beats: Beats | None,
    palette: dict,
    *,
    vocals_separated: bool,
    speed_factor: float = 1.0,
    fallback_duration_ms: int | None = None,
) -> dict:
    hints = job.hints or {}
    # track.duration_ms is REQUIRED by the schema; ingest hints may omit it,
    # but the worker always knows the real duration from the downloaded audio.
    duration_ms = hints.get("duration_ms") or fallback_duration_ms

    lines: list[dict] = []
    for index, line in enumerate(align_result.lines):
        entry: dict = {
            "start_ms": line.start_ms,
            "end_ms": line.end_ms,
            "text": line.text,
            "score": round(line.score, 4),
        }
        # Faz 4 aesthetics: clients style nonlexical hooks differently. Derived
        # from the TEXT at build time (same predicate line QA uses), so line-
        # mode and degraded documents carry it too. Omitted when false.
        if is_adlib(line.text):
            entry["adlib"] = True
        # Per-line: a word-sync document may carry wordless lines (line QA drops
        # the words of a snapped line); an empty array is never written (schema
        # minItems). The overlay renders such lines as plain text.
        if align_result.sync == "word" and align_result.words_per_line[index]:
            entry["words"] = [
                {"start_ms": w.start_ms, "end_ms": w.end_ms, "text": w.text}
                for w in align_result.words_per_line[index]
            ]
        lines.append(entry)

    track: dict = {
        "source": {"type": job.source_type, "id": job.source_id},
        "title": hints.get("title"),
        "artist": hints.get("artist"),
        "duration_ms": duration_ms,
    }
    if hints.get("album"):
        track["album"] = hints["album"]
    if hints.get("title") and hints.get("artist") and duration_ms:
        track["canonical_group"] = canonical_group(
            hints["artist"], hints["title"], duration_ms / 1000
        )

    doc: dict = {
        "schema_version": 1,
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "track": track,
        "sync": align_result.sync,
        "alignment": {
            "method": "ctc-forced-aligner/mms-300m+line-windowed"
            if align_result.windowed
            else "ctc-forced-aligner/mms-300m",
            "lyrics_source": "lrclib",
            "lyrics_source_id": lyrics.source_id,
            "vocals_separated": vocals_separated,
            "quality_score": round(align_result.quality_score, 4),
            "speed_factor": speed_factor,
        },
        "lines": lines,
        "palette": palette,
    }
    if beats is not None:
        doc["beats"] = {
            "bpm": beats.bpm,
            "confidence": beats.confidence,
            "times_ms": beats.times_ms,
            "downbeat_indices": beats.downbeat_indices,
        }
    return doc


@lru_cache(maxsize=1)
def _validator():
    import jsonschema

    schema = json.loads(settings.schema_path.read_text())
    return jsonschema.Draft202012Validator(schema)


def validate_document(doc: dict) -> None:
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        path = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise PipelineError(
            "alignment_failed", f"document failed schema validation at {path}: {first.message}"
        )
    # Structural sync invariants the schema cannot express (kept in lockstep
    # with packages/schemas/scripts/validate-examples.mjs):
    #   sync=line -> no line carries a words field
    #   sync=word -> at least one line carries non-empty words
    lines = doc.get("lines") or []
    if doc.get("sync") == "line" and any("words" in line for line in lines):
        raise PipelineError("alignment_failed", "sync=line document carries word timings")
    if doc.get("sync") == "word" and not any(line.get("words") for line in lines):
        raise PipelineError("alignment_failed", "sync=word document has no word timings at all")


_UPSERT_SQL = sa_text(
    """
    INSERT INTO processed_tracks
        (source_type, source_id, schema_version, pipeline_version, pipeline_major,
         sync, quality_score, title, artist, duration_ms, document, etag, job_id)
    VALUES (:source_type, :source_id, 1, :pipeline_version, :pipeline_major,
            :sync, :quality_score, :title, :artist, :duration_ms,
            CAST(:document AS jsonb), :etag, :job_id)
    ON CONFLICT (source_type, source_id, schema_version) DO UPDATE SET
        pipeline_version = EXCLUDED.pipeline_version,
        pipeline_major = EXCLUDED.pipeline_major,
        sync = EXCLUDED.sync,
        quality_score = EXCLUDED.quality_score,
        title = EXCLUDED.title,
        artist = EXCLUDED.artist,
        duration_ms = EXCLUDED.duration_ms,
        document = EXCLUDED.document,
        etag = EXCLUDED.etag,
        job_id = EXCLUDED.job_id,
        updated_at = clock_timestamp()
    """
)


def persist_processed_track(s: Session, job: Job, doc: dict) -> str:
    """Validate (hard gate), then upsert. Returns the etag."""
    validate_document(doc)
    etag = compute_etag(doc)
    s.execute(
        _UPSERT_SQL,
        {
            "source_type": job.source_type,
            "source_id": job.source_id,
            "pipeline_version": PIPELINE_VERSION,
            "pipeline_major": PIPELINE_MAJOR,
            "sync": doc["sync"],
            "quality_score": doc["alignment"]["quality_score"],
            "title": doc["track"].get("title"),
            "artist": doc["track"].get("artist"),
            "duration_ms": doc["track"].get("duration_ms"),
            "document": json.dumps(doc, ensure_ascii=False),
            "etag": etag,
            "job_id": job.id,
        },
    )
    logger.info("persisted %s:%s (%s, etag %s)", job.source_type, job.source_id, doc["sync"], etag)
    return etag
