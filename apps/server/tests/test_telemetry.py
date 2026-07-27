"""Telemetry contract (Faz 6.7 P1): field filtering, batch API, retention."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import auth as _auth

from kashi_server.telemetry_contract import (
    MAX_VALUE_CHARS,
    REDACTED,
    TELEMETRY_FIELDS,
    redact_secrets,
    sanitize_payload,
)


def _clean(kind: str, payload: dict) -> dict | None:
    result = sanitize_payload(kind, payload)
    return None if result is None else result[0]


_SESSION = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


def _batch(kind: str, payload: dict, ts: str = "2026-07-26T20:00:00Z") -> dict:
    return {"session_id": _SESSION, "events": [{"ts": ts, "kind": kind, "payload": payload}]}


# --- the privacy contract, as a pure function -----------------------------


def test_unknown_kind_is_rejected_not_guessed():
    assert sanitize_payload("keystrokes", {"a": 1}) is None


def test_uncontracted_keys_are_removed():
    clean = _clean(
        "track_changed",
        {"video_id": "dQw4w9WgXcQ", "title": "Never Gonna Give You Up", "listener_email": "x@y.z"},
    )
    assert clean == {"video_id": "dQw4w9WgXcQ", "title": "Never Gonna Give You Up"}


def test_removed_values_are_counted_so_field_drift_is_visible():
    result = sanitize_payload("error", {"code": "E_X", "nope": 1, "also_nope": 2})
    assert result == ({"code": "E_X"}, 2)


def test_api_keys_and_url_credentials_never_survive_free_text():
    # An error string built from a failed request is the realistic way a
    # bearer token ends up in a payload nobody meant to store.
    key = "ksh_" + "0123456789abcdef" * 2
    clean = _clean("error", {"message": f"401 from https://u:p@host with key {key}"})
    assert clean is not None
    assert key not in clean["message"]
    assert "u:p@" not in clean["message"]
    assert REDACTED in clean["message"]


def test_redaction_catches_a_bearer_header():
    assert "Bearer ksh_abc" not in redact_secrets("sent Bearer ksh_abcdef012345 upstream")


def test_non_finite_numbers_are_dropped_before_postgres_sees_them():
    # NaN survives JSON parsing but jsonb rejects it — one bad number would
    # otherwise fail the INSERT for the whole batch.
    assert _clean("position_anomaly", {"position_ms": float("nan")}) == {}
    assert _clean("position_anomaly", {"delta_ms": float("inf")}) == {}


def test_millisecond_fields_are_integers_within_a_sane_range():
    clean = _clean("position_anomaly", {"position_ms": 1234.7, "duration_ms": 10**40})
    assert clean == {"position_ms": 1234}  # float coerced, absurd value dropped


def test_nested_structures_cannot_ride_an_allowed_key():
    # The easy way to smuggle unbounded data is a dict behind a legal name.
    assert _clean("error", {"message": {"nested": "payload"}, "code": "E_X"}) == {"code": "E_X"}


def test_long_values_are_clipped_not_dropped():
    clean = _clean("error", {"message": "x" * (MAX_VALUE_CHARS + 50)})
    assert clean is not None
    assert len(clean["message"]) == MAX_VALUE_CHARS


def test_every_kind_has_a_non_empty_field_list():
    # A kind with no fields would silently store empty payloads forever.
    assert TELEMETRY_FIELDS
    for kind, fields in TELEMETRY_FIELDS.items():
        assert fields, f"{kind} has no contracted fields"


def test_position_anomaly_carries_what_the_timing_fix_needs():
    # This event exists to confirm the Faz 6.7 P0 guards in the field; losing
    # these fields would make it decorative.
    assert {"reason", "position_ms", "duration_ms"} <= TELEMETRY_FIELDS["position_anomaly"]


# --- the endpoint ----------------------------------------------------------


def test_batch_is_stored_and_counted(client, user_key, db_session):
    from sqlalchemy import select

    from kashi_server.db.models import Telemetry

    body = {
        "session_id": _SESSION,
        "events": [
            {
                "ts": "2026-07-26T20:00:00Z",
                "kind": "session_start",
                "payload": {"app_version": "0.13.0", "os": "darwin"},
            },
            {
                "ts": "2026-07-26T20:00:01Z",
                "kind": "position_anomaly",
                "payload": {"reason": "overshoot", "position_ms": 275000, "duration_ms": 180000},
            },
        ],
    }
    resp = client.post("/v1/telemetry", json=body, headers=_auth(user_key))
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"stored": 2, "dropped": 0, "filtered": 0}

    rows = db_session.scalars(select(Telemetry).order_by(Telemetry.ts)).all()
    assert [r.kind for r in rows] == ["session_start", "position_anomaly"]
    assert rows[0].payload == {"app_version": "0.13.0", "os": "darwin"}
    assert rows[0].reported_by is not None  # attributed to the calling key


def test_unknown_kind_is_dropped_without_failing_the_batch(client, user_key):
    body = {
        "session_id": _SESSION,
        "events": [
            {"ts": "2026-07-26T20:00:00Z", "kind": "from_the_future", "payload": {"x": 1}},
            {"ts": "2026-07-26T20:00:01Z", "kind": "watchdog", "payload": {"reason": "stall"}},
        ],
    }
    resp = client.post("/v1/telemetry", json=body, headers=_auth(user_key))
    assert resp.status_code == 202
    assert resp.json() == {"stored": 1, "dropped": 1, "filtered": 0}


def test_uncontracted_field_never_reaches_the_database(client, user_key, db_session):
    from sqlalchemy import select

    from kashi_server.db.models import Telemetry

    resp = client.post(
        "/v1/telemetry",
        json=_batch("track_changed", {"video_id": "abc", "secret_note": "do not store"}),
        headers=_auth(user_key),
    )
    assert resp.status_code == 202
    stored = db_session.scalars(select(Telemetry)).one()
    assert stored.payload == {"video_id": "abc"}


def test_a_rejected_row_cannot_be_acked_as_stored(client, user_key, db_session):
    from sqlalchemy import select

    from kashi_server.db.models import Telemetry

    # NaN passes JSON parsing; before the flush landed inside the handler the
    # client got "stored: 1" while PostgreSQL rejected the row after the
    # response had already gone out.
    resp = client.post(
        "/v1/telemetry",
        content=(
            f'{{"session_id":"{_SESSION}","events":[{{"ts":"2026-07-26T20:00:00Z",'
            '"kind":"position_anomaly","payload":{"position_ms":NaN}}]}'
        ),
        headers={**_auth(user_key), "content-type": "application/json"},
    )
    assert resp.status_code == 202, resp.text
    stored = db_session.scalars(select(Telemetry)).all()
    assert len(stored) == 1
    assert stored[0].payload == {}  # the bad value was dropped, the row is real


def test_naive_timestamps_are_rejected(client, user_key):
    # The column is timestamptz: a naive value would be read in the server's
    # session timezone and shift the entire timeline of a report.
    body = _batch("watchdog", {"reason": "stall"}, ts="2026-07-26T20:00:00")
    assert client.post("/v1/telemetry", json=body, headers=_auth(user_key)).status_code == 422


def test_requires_authentication(client):
    assert client.post("/v1/telemetry", json=_batch("watchdog", {})).status_code == 401


def test_empty_and_oversized_batches_are_rejected(client, user_key):
    empty = {"session_id": _SESSION, "events": []}
    assert client.post("/v1/telemetry", json=empty, headers=_auth(user_key)).status_code == 422

    too_many = {
        "session_id": _SESSION,
        "events": [
            {"ts": "2026-07-26T20:00:00Z", "kind": "watchdog", "payload": {}} for _ in range(101)
        ],
    }
    assert client.post("/v1/telemetry", json=too_many, headers=_auth(user_key)).status_code == 422


def test_disabled_telemetry_refuses(client, user_key, monkeypatch):
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "telemetry_enabled", False)
    resp = client.post("/v1/telemetry", json=_batch("watchdog", {}), headers=_auth(user_key))
    assert resp.status_code == 503


def test_rate_limited(client, user_key, monkeypatch):
    from kashi_server import ratelimit

    monkeypatch.setitem(ratelimit.RATE_LIMITS, "telemetry", (1.0, 0.001))
    body = _batch("watchdog", {"reason": "stall"})
    assert client.post("/v1/telemetry", json=body, headers=_auth(user_key)).status_code == 202
    limited = client.post("/v1/telemetry", json=body, headers=_auth(user_key))
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


# --- retention -------------------------------------------------------------


@pytest.mark.parametrize("retention_days", [30])
def test_retention_sweeps_on_the_server_clock(db_session, retention_days):
    from sqlalchemy import select, text

    from kashi_server import queue
    from kashi_server.db.models import Telemetry

    old = Telemetry(session_id=_SESSION, ts=datetime.now(UTC), kind="watchdog", payload={})
    recent = Telemetry(session_id=_SESSION, ts=datetime.now(UTC), kind="watchdog", payload={})
    db_session.add_all([old, recent])
    db_session.flush()
    # Age the first row by OUR clock. Its client-side ts stays fresh, which is
    # the whole point: a skewed device must not control retention.
    db_session.execute(
        text("UPDATE telemetry SET received_at = now() - interval '31 days' WHERE id = :i"),
        {"i": old.id},
    )

    removed = queue.purge_old_telemetry(db_session, retention_days)
    assert removed == 1
    survivors = db_session.scalars(select(Telemetry)).all()
    assert [r.id for r in survivors] == [recent.id]


def test_retention_keeps_rows_whose_client_clock_is_in_the_past(db_session):
    from sqlalchemy import select

    from kashi_server import queue
    from kashi_server.db.models import Telemetry

    # ts a year ago (wrong client clock), received just now → must survive.
    db_session.add(
        Telemetry(
            session_id=_SESSION,
            ts=datetime.now(UTC) - timedelta(days=365),
            kind="watchdog",
            payload={},
        )
    )
    db_session.flush()
    assert queue.purge_old_telemetry(db_session, 30) == 0
    assert len(db_session.scalars(select(Telemetry)).all()) == 1
