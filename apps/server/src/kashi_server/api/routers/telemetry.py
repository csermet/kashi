"""POST /v1/telemetry — field diagnostics from the overlay (Faz 6.7 P1).

Contract: 202 with a count of what was stored and what was dropped. This
endpoint must never punish a client for being newer, or for having one bad
event in an otherwise good batch — a diagnostics channel that returns errors
teaches clients to stop sending. So unknown kinds and uncontracted fields are
filtered out silently (see telemetry_contract) and reported back as a number,
while the batch itself still succeeds. The only failures are authentication,
rate limiting, an oversized body (middleware), and the feature being off.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from kashi_server.api.deps import get_db, rate_limited
from kashi_server.api.schemas import TelemetryAck, TelemetryBatchIn
from kashi_server.config import settings
from kashi_server.db.models import ApiKey, Telemetry
from kashi_server.telemetry_contract import sanitize_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


@router.post("/telemetry", status_code=202, response_model=TelemetryAck)
def ingest_telemetry(
    body: TelemetryBatchIn,
    key: ApiKey = Depends(rate_limited("telemetry")),
    db: Session = Depends(get_db),
):
    if not settings.telemetry_enabled:
        raise HTTPException(status_code=503, detail="telemetry_disabled")

    rows: list[Telemetry] = []
    dropped = 0
    filtered = 0
    emptied = 0
    for event in body.events:
        result = sanitize_payload(event.kind, event.payload)
        if result is None:
            dropped += 1  # unknown kind — older server, newer client
            continue
        payload, removed = result
        filtered += removed
        if removed and not payload:
            emptied += 1
        rows.append(
            Telemetry(
                session_id=body.session_id,
                ts=event.ts,
                kind=event.kind,
                payload=payload,
                reported_by=key.id,
            )
        )
    if rows:
        db.add_all(rows)
        # Flush INSIDE the handler: get_db commits AFTER the response is sent,
        # so without this a row the database rejects would still be answered
        # with a cheerful "stored". An ack has to mean something.
        db.flush()
    if dropped:
        logger.info("telemetry: dropped %d event(s) of unknown kind", dropped)
    if emptied:
        # Every field of a known event was uncontracted: almost certainly a
        # client whose field NAMES drifted from this server's list. Silence
        # here means empty rows for a whole retention period.
        logger.warning(
            "telemetry: %d event(s) stored with an EMPTY payload — client field names "
            "may have drifted from TELEMETRY_FIELDS",
            emptied,
        )
    return TelemetryAck(stored=len(rows), dropped=dropped, filtered=filtered)
