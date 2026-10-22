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

from prometheus_client import Counter
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from kashi_server.config import settings
from kashi_server.db.models import Job
from kashi_server.pipeline.alignment import AlignResult
from kashi_server.pipeline.beats import Beats
from kashi_server.pipeline.energy import Energy, Section
from kashi_server.pipeline.line_qa import LineQAOutcome, is_adlib
from kashi_server.pipeline.lrclib import LyricsText, normalize_artist
from kashi_server.pipeline.semantics import FxTags
from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.version import PIPELINE_MAJOR, PIPELINE_VERSION

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


def alignment_method(align_result: AlignResult) -> str:
    """Provenance string for `alignment.method`: which aligner ran, and whether
    it ran windowed.

    Built from the result rather than hardcoded (Faz 8 P-B1). The model used to
    be spelled out as a literal here, so a swapped checkpoint would have gone
    into the archive still claiming mms-300m — and a swap is now a certainty,
    not a maybe: the default weights are CC-BY-NC-4.0 and cannot ship in a paid
    product. The archive has to be able to say which documents came from which
    aligner, or a later comparison has nothing to group by.

    Bare model ids keep today's shape (`ctc-forced-aligner/mms-300m…`); an
    org-qualified id keeps its org so two forks of one name stay distinct.
    """
    short = align_result.model_name.rsplit("/", 1)[-1]
    for prefix in ("mms-300m-1130-forced-aligner", "mms-300m"):
        if short.startswith(prefix):
            short = "mms-300m" + short[len(prefix) :]
            break
    suffix = "+line-windowed" if align_result.windowed else ""
    return f"ctc-forced-aligner/{short}{suffix}"


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
    qa: LineQAOutcome | None = None,
    fx: FxTags | None = None,
    energy: Energy | None = None,
    sections: list[Section] | None = None,
) -> dict:
    hints = job.hints or {}
    # track.duration_ms is REQUIRED by the schema. The MEASURED audio wins:
    # the worker ffprobes what it actually aligned, while the hint is the
    # client's claim about what it is playing. Where the two can disagree the
    # worker has already ruled (CLIENT_EDIT_MISMATCH_S), so preferring the
    # hint only ever imported client noise — 47 archived documents carry an
    # impossible duration this way (LMFAO "Hot Dog": 3279s stored against
    # lyrics that end at 121s), which then propagates into canonical_group's
    # 5s buckets and the processed_tracks column. The hint stays as the
    # fallback for callers that build a document without downloaded audio.
    duration_ms = fallback_duration_ms or hints.get("duration_ms")
    lines = _document_lines(align_result, qa)

    track = _track_block(job, hints, duration_ms)

    doc: dict = {
        "schema_version": 1,
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "track": track,
        "sync": align_result.sync,
        "alignment": {
            # Human word-sync consumed as-is (Faz 5 P3) carries its own
            # method string — consumers distinguish it from aligner output.
            "method": "lrclib-lyricsfile/1.0"
            if lyrics.source == "lyricsfile"
            else alignment_method(align_result),
            # Provenance must be honest: caller-supplied lyrics_text is NOT an
            # lrclib record, and a fake id 0 would poison R-8's "source
            # changed → offer reprocess" future (reviewer, Faz 4).
            "lyrics_source": lyrics.source,
            "vocals_separated": vocals_separated,
            "quality_score": round(align_result.quality_score, 4),
            # What quality_score actually MEASURED (Faz 6 P1). Read from the
            # producer rather than derived from `windowed` (Faz 8 P-B2): the
            # proxy mislabelled every line-mode document as "anchors" while
            # the number was prob-based. Only the human path is decided here,
            # because it is a property of the LYRICS, not of the aligner.
            "quality_basis": "human"
            if lyrics.source == "lyricsfile"
            else align_result.quality_basis,
            "speed_factor": speed_factor,
        },
        "lines": lines,
        "palette": palette,
    }
    if lyrics.source_id:
        doc["alignment"]["lyrics_source_id"] = lyrics.source_id
    if qa is not None:
        # QA provenance (Faz 5 P1): how much the document was repaired. The
        # publish quality gate (P6) and field debugging read this — counts
        # only, never indexes (indexes would drift the etag on no-op edits).
        doc["alignment"]["qa"] = _qa_counts(qa)
    if beats is not None:
        doc["beats"] = {
            "bpm": beats.bpm,
            "confidence": beats.confidence,
            "times_ms": beats.times_ms,
            "downbeat_indices": beats.downbeat_indices,
        }
    # FX data foundation (Faz 6 P3) — all additive, all optional; a document
    # without them renders exactly like before (old clients ignore unknowns).
    if fx is not None and (fx.words or fx.lines):
        doc["fx"] = _fx_block(fx)
    if energy is not None:
        doc["energy"] = {"rate_hz": energy.rate_hz, "values": energy.values}
    if sections:
        doc["sections"] = [
            {"type": s.type, "start_ms": s.start_ms, "end_ms": s.end_ms} for s in sections
        ]
    return doc


def _document_lines(align_result: AlignResult, qa: LineQAOutcome | None) -> list[dict]:
    # Line indexes whose word spans are synthetic (rederived across the line,
    # not aligner-measured). Downstream consumers that treat word boundaries
    # as evidence — the Faz 5 lrclib publish gate above all — must not present
    # these as measured timings.
    derived_lines = set(qa.adlib_rederived) if qa is not None else set()
    # Lines the drift threshold flagged but the audio vouched for (Faz 8 B4).
    # Their words survive, shifted onto the anchor; the flag lets a client
    # de-emphasise what the server chose not to destroy.
    uncertain_lines = set(qa.uncertain) if qa is not None else set()

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
            if index in derived_lines:
                entry["words_derived"] = True
            if index in uncertain_lines:
                entry["uncertain"] = True
        lines.append(entry)
    return lines


def _track_block(job: Job, hints: dict, duration_ms: int | None) -> dict:
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
    return track


def _qa_counts(qa: LineQAOutcome) -> dict:
    return {
            "flagged": len(qa.flagged),
            "density_dropped": len(qa.density_dropped),
            "adlib_shifted": len(qa.adlib_shifted),
            "nudged": len(qa.nudged),
            "adlib_rederived": len(qa.adlib_rederived),
            "response_shifted": len(qa.response_shifted),
            "offset_ms": qa.offset_ms,
            "trimmed_ends": qa.trimmed_ends,
            "uncertain": len(qa.uncertain),
        }


def _fx_block(fx: FxTags) -> dict:
    fx_block: dict = {"lexicon": fx.lexicon_version, "engine": fx.engine}
    # Which words fire has already been decided (fx_select.py). Without
    # this marker a newer client cannot tell a chosen list from a legacy
    # dense one, and would render EVERY tag on a line — on the archive
    # that means two or three effects where there is one today, which is
    # the opposite of what the selection exists to do.
    if fx.select:
        fx_block["select"] = fx.select
    if fx.words:
        fx_block["words"] = [
            {"line": t.line, "word": t.word, "tag": t.tag, "intensity": t.intensity}
            for t in fx.words
        ]
    if fx.lines:
        fx_block["lines"] = [{"line": t.line, "tag": t.tag} for t in fx.lines]
    return fx_block



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


#: How far a new document's quality may fall below the stored one's (same
#: quality basis) before it is refused as a regression, and how few of the
#: stored document's worded lines it may keep. Field, 2026-10-16 wave: lrclib's
#: own record for "Never Gonna Give You Up" degraded to one line ("probe"), and
#: the reprocess replaced a 58-line, 0.93 document with it. The largest drop
#: seen on a legitimate reprocess in that wave was 0.24 (and across different
#: quality bases, which are not compared at all).
QUALITY_DROP_LIMIT = 0.25
WORDED_LINES_FLOOR = 0.5

REGRESSIONS_KEPT = Counter(
    "kashi_persist_regressions_kept_total",
    "New documents refused because they regress the stored one (Faz 9)",
)

_EXISTING_SQL = sa_text(
    """
    SELECT document, etag, pipeline_major FROM processed_tracks
    WHERE source_type = :source_type AND source_id = :source_id AND schema_version = 1
    """
)


def _worded_lines(doc: dict) -> int:
    return sum(1 for line in doc.get("lines") or [] if line.get("words"))


def regression(stored: dict | None, new: dict) -> str | None:
    """Why `new` must not replace `stored`, or None when it may.

    Two independent rules. Losing more than half of the stored document's
    worded lines is a regression whatever the scores say (a degenerate lyrics
    record still aligns "perfectly"). A quality drop of QUALITY_DROP_LIMIT or
    more counts only when both scores come from the same formula: a document
    scored on anchors and one scored on CTC probabilities are not comparable.
    """
    if not stored:
        return None
    old_lines, new_lines = _worded_lines(stored), _worded_lines(new)
    if old_lines and new_lines < old_lines * WORDED_LINES_FLOOR:
        return f"worded lines {old_lines} -> {new_lines}"
    old_al, new_al = stored.get("alignment") or {}, new.get("alignment") or {}
    basis = old_al.get("quality_basis")
    old_q, new_q = old_al.get("quality_score"), new_al.get("quality_score")
    if (
        basis is not None
        and basis == new_al.get("quality_basis")
        and isinstance(old_q, (int, float))
        and isinstance(new_q, (int, float))
        and round(old_q - new_q, 4) >= QUALITY_DROP_LIMIT
    ):
        return f"quality {old_q:.3f} -> {new_q:.3f} ({basis})"
    return None


def persist_processed_track(s: Session, job: Job, doc: dict) -> str:
    """Validate (hard gate), then upsert — unless the new document regresses the
    stored one (see `regression`). Returns the etag of what is stored.

    A refused document still completes its job: the work ran, the result was
    worse, and the listener keeps the better one. A new pipeline MAJOR always
    replaces (the old one is invalidated by definition), and an admin
    reprocess with `options.force_persist` accepts a regression on purpose.
    """
    validate_document(doc)
    etag = compute_etag(doc)
    existing = s.execute(
        _EXISTING_SQL, {"source_type": job.source_type, "source_id": job.source_id}
    ).first()
    if (
        existing is not None
        and existing.pipeline_major >= PIPELINE_MAJOR
        and not (job.options or {}).get("force_persist")
    ):
        reason = regression(existing.document, doc)
        if reason is not None:
            REGRESSIONS_KEPT.inc()
            logger.warning(
                "kept the stored %s:%s document: the new one regresses it (%s); "
                "an admin reprocess with force_persist overrides",
                job.source_type, job.source_id, reason,
            )
            return existing.etag
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
