"""scripts/import_overrides.py on a SYNTHETIC payload of the 2026-11-10 repair
shape ([{id, speed_factor, lyrics_text}]), sent through the real admin API."""

import importlib.util
import io
import json
import uuid
from pathlib import Path

import pytest
from helpers import TEST_ADMIN_KEY
from sqlalchemy import text

from kashi_server import overrides
from kashi_server.db.models import Job

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_overrides.py"
_spec = importlib.util.spec_from_file_location("import_overrides", _SCRIPT)
assert _spec is not None and _spec.loader is not None
importer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(importer)

SECRET_TEXT = "synthetic verse one\nsynthetic chorus\nsynthetic verse two"
PAYLOAD = [
    {"id": "synthNc0001", "speed_factor": 1.0662, "lyrics_text": SECRET_TEXT},
    {"id": "synthNc0002", "speed_factor": 1.1289, "lyrics_text": "another synthetic text"},
    {"id": "synthNc0003", "lrclib_id": 4242, "offset_ms": -20800, "note": "sheet 20.8 s late"},
]


def _client_sender(client):
    def send(method, path, body):
        resp = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"}
        )
        return resp.status_code, (resp.json() if resp.content else None)

    return send


def _payload_file(tmp_path, payload) -> str:
    path = tmp_path / "payloads.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_import_stores_every_override_and_prints_no_text(client, db_session, tmp_path):
    out = io.StringIO()
    code = importer.main(
        [_payload_file(tmp_path, PAYLOAD), "--note", "imported from the 2026-11-10 repairs"],
        out=out,
        send=_client_sender(client),
    )
    assert code == 0
    printed = out.getvalue()
    assert SECRET_TEXT.splitlines()[0] not in printed and "synthetic" not in printed
    lines = [json.loads(line) for line in printed.splitlines()]
    assert [(line["id"], line["put"]) for line in lines] == [
        ("synthNc0001", 201),
        ("synthNc0002", 201),
        ("synthNc0003", 201),
    ]
    assert lines[0]["lyrics_text_chars"] == len(SECRET_TEXT)

    from sqlalchemy import select

    from kashi_server.db.models import SongOverride

    rows = {r.source_id: r for r in db_session.scalars(select(SongOverride)).all()}
    assert rows["synthNc0001"].speed_factor == 1.0662
    assert rows["synthNc0001"].lyrics_text == SECRET_TEXT
    assert rows["synthNc0001"].note == "imported from the 2026-11-10 repairs"
    assert rows["synthNc0003"].note == "sheet 20.8 s late"  # an entry's own note wins
    assert rows["synthNc0003"].offset_ms == -20800

    # Idempotent: a second run replaces (200), nothing duplicates.
    again = io.StringIO()
    assert (
        importer.main([_payload_file(tmp_path, PAYLOAD)], out=again, send=_client_sender(client))
        == 0
    )
    assert {json.loads(line)["put"] for line in again.getvalue().splitlines()} == {200}


def test_reprocess_applies_each_override_at_once(client, db_session, tmp_path):
    out = io.StringIO()
    code = importer.main(
        [_payload_file(tmp_path, PAYLOAD[:1]), "--reprocess"],
        out=out,
        send=_client_sender(client),
    )
    # No job history and no hints: the reprocess answers 404, and the run says so.
    assert code == 1
    line = json.loads(out.getvalue())
    assert line["put"] == 201 and line["reprocess"] == 404

    from kashi_server import queue

    queue.enqueue(
        db_session,
        source_type="youtube",
        source_id="synthNc0002",
        pipeline_major=2,
        hints={"title": "Nightcore - Song", "artist": "Chan"},
        options={},
        requested_by=None,
    )
    # A finished earlier job: its hints are what reprocess reuses.
    db_session.execute(text("UPDATE jobs SET status = 'completed'"))
    db_session.commit()
    out = io.StringIO()
    assert (
        importer.main(
            [_payload_file(tmp_path, PAYLOAD[1:2]), "--reprocess"],
            out=out,
            send=_client_sender(client),
        )
        == 0
    )
    line = json.loads(out.getvalue())
    job = db_session.get(Job, uuid.UUID(line["job"]))
    assert job is not None
    db_session.refresh(job)
    assert job.options["speed_factor"] == 1.1289
    assert job.options["lyrics_text"] == "another synthetic text"
    assert job.options[overrides.MARKER]["displaced"] == []


def test_a_bad_entry_stops_the_run_before_anything_is_sent(tmp_path, capsys):
    sent = []
    bad = PAYLOAD + [{"id": "synthNc0004", "speedfactor": 1.2}]
    code = importer.main(
        [_payload_file(tmp_path, bad)],
        out=io.StringIO(),
        send=lambda *a: sent.append(a) or (200, {}),
    )
    assert code == 2 and sent == []
    assert "speedfactor" in capsys.readouterr().err


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "x"},  # not a list
        [{"speed_factor": 1.2}],  # no id
        [{"id": "has/slash", "speed_factor": 1.2}],
        [{"id": "dupId000001", "speed_factor": 1.2}, {"id": "dupId000001", "lrclib_id": 3}],
        [{"id": "noFields001"}],
    ],
)
def test_payload_shape_is_checked(tmp_path, payload):
    assert importer.main([_payload_file(tmp_path, payload), "--dry-run"], out=io.StringIO()) == 2


def test_dry_run_needs_no_key_and_sends_nothing(tmp_path):
    out = io.StringIO()
    assert importer.main([_payload_file(tmp_path, PAYLOAD), "--dry-run"], out=out, env={}) == 0
    assert "synthetic" not in out.getvalue()
    assert all(json.loads(line)["dry_run"] for line in out.getvalue().splitlines())


def test_no_key_no_request(tmp_path):
    assert importer.main([_payload_file(tmp_path, PAYLOAD)], out=io.StringIO(), env={}) == 2


def test_stdin_payload(tmp_path):
    out = io.StringIO()
    code = importer.main(["-", "--dry-run"], stdin=io.StringIO(json.dumps(PAYLOAD)), out=out)
    assert code == 0 and len(out.getvalue().splitlines()) == 3


def test_api_refusals_are_reported_without_echoing_input(client, tmp_path):
    out = io.StringIO()
    payload = [{"id": "synthNc0009", "lyrics_text": "x" * 20_001}]
    assert (
        importer.main([_payload_file(tmp_path, payload)], out=out, send=_client_sender(client)) == 1
    )
    line = json.loads(out.getvalue())
    assert line["put"] == 422
    assert "x" * 50 not in out.getvalue()


# --- release 0.37.0 review: does the job that answered carry the override? ----


def test_a_live_job_without_the_override_is_reported_and_fails_the_run(
    client, db_session, tmp_path
):
    """enqueue_reprocess returns a LIVE job when one exists; such a job was
    enqueued before the override and runs without it. The importer must not
    print it as the rebuild."""
    from kashi_server import queue

    live, _ = queue.enqueue(
        db_session,
        source_type="youtube",
        source_id="synthNc0002",
        pipeline_major=2,
        hints={"title": "Nightcore - Song", "artist": "Chan"},
        options={},
        requested_by=None,
    )
    db_session.commit()
    out = io.StringIO()
    code = importer.main(
        [_payload_file(tmp_path, PAYLOAD[1:2]), "--reprocess"], out=out, send=_client_sender(client)
    )
    line = json.loads(out.getvalue())
    assert (line["put"], line["reprocess"], line["job"]) == (201, 202, str(live.id))
    assert line["job_carries_override"] is False
    assert "again" in line["next"]
    assert code == 1

    # Once the live job is done, the next --reprocess makes a job that carries it.
    db_session.execute(text("UPDATE jobs SET status = 'completed'"))
    db_session.commit()
    out = io.StringIO()
    code = importer.main(
        [_payload_file(tmp_path, PAYLOAD[1:2]), "--reprocess"], out=out, send=_client_sender(client)
    )
    line = json.loads(out.getvalue())
    assert line["job"] != str(live.id)
    assert line["job_carries_override"] is True and "next" not in line
    assert code == 0


def test_the_reprocess_answer_is_null_for_a_song_without_an_override(client, db_session):
    from kashi_server import queue

    queue.enqueue(
        db_session,
        source_type="youtube",
        source_id="noOverride1",
        pipeline_major=2,
        hints={"title": "T", "artist": "A"},
        options={},
        requested_by=None,
    )
    db_session.execute(text("UPDATE jobs SET status = 'completed'"))
    db_session.commit()
    resp = client.post(
        "/v1/admin/reprocess",
        json={"source": {"type": "youtube", "id": "noOverride1"}},
        headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"},
    )
    assert resp.status_code == 202 and resp.json()["song_override_applied"] is None


def test_an_api_that_does_not_say_is_reported_as_unknown(tmp_path):
    """An API before 0.37.0 answers without the field: the line says so."""

    def old_api(method, path, body):
        if method == "PUT":
            return 201, {}
        return 202, {"job_id": "00000000-0000-0000-0000-000000000001", "status": "queued"}

    out = io.StringIO()
    code = importer.main(
        [_payload_file(tmp_path, PAYLOAD[:1]), "--reprocess"], out=out, send=old_api
    )
    line = json.loads(out.getvalue())
    assert line["job_carries_override"] == "unknown" and code == 0
