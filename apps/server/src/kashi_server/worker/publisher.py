"""Idle-slot lrclib publisher (Faz 5 P6).

The worker drains queued publish requests only when no alignment job is
claimable: PoW may cost minutes of CPU and must never delay lyrics
processing. The gate re-runs here (defense in depth — the document may
have been reprocessed since the API accepted the request), and dry-run
mode logs the exact YAML instead of publishing until the operator flips
lrclib_publish_dry_run off.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from kashi_server.config import settings
from kashi_server.db.models import LrclibPublish, ProcessedTrack
from kashi_server.pipeline.publish import generate_lyricsfile, publish_document, publish_gate
from kashi_server.vdl_kit.errors import PipelineError

logger = logging.getLogger(__name__)


def process_one_publish(s: Session, *, should_stop: object = None) -> bool:
    """Handle at most ONE queued publish request; True when one was taken.
    Terminal either way — a failed publish is re-requested by a human, not
    retried by a loop (etiquette: no automatic hammering of a free service).
    """
    if not settings.lrclib_publish_enabled:
        return False  # kill switch also halts the drain (rows stay queued)
    row = s.scalars(
        select(LrclibPublish)
        .where(LrclibPublish.status == "queued")
        .order_by(LrclibPublish.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if row is None:
        return False

    try:
        doc_row = s.scalars(
            select(ProcessedTrack).where(
                ProcessedTrack.source_type == row.source_type,
                ProcessedTrack.source_id == row.source_id,
                ProcessedTrack.schema_version == 1,
            )
        ).first()
        if doc_row is None or doc_row.etag != row.etag:
            raise PipelineError(
                "other", "document changed or vanished since the request — request again"
            )
        reasons = publish_gate(doc_row.document)
        if reasons:
            raise PipelineError("other", "gate: " + "; ".join(reasons))
        if settings.lrclib_publish_dry_run:
            yaml_text = generate_lyricsfile(doc_row.document)
            logger.info(
                "DRY RUN lrclib publish %s:%s etag=%s — %d bytes of Lyricsfile:\n%s",
                row.source_type,
                row.source_id,
                row.etag,
                len(yaml_text.encode()),
                yaml_text[:800],
            )
            row.status = "dry_run"
        else:
            publish_document(
                doc_row.document, base_url=settings.lrclib_base_url, should_stop=should_stop
            )
            row.status = "published"
    except PipelineError as exc:
        logger.error("publish %s:%s failed: %s", row.source_type, row.source_id, exc.message)
        row.status = "failed"
        row.error = exc.message[:500]
    except Exception as exc:  # noqa: BLE001 - the worker loop must survive
        logger.exception("publish %s:%s crashed", row.source_type, row.source_id)
        row.status = "failed"
        row.error = str(exc)[:500]
    row.finished_at = datetime.now(UTC)
    s.commit()
    return True
