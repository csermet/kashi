"""Turn hand-made repair payloads into song overrides, through the admin API (C.1).

One-off tool for the owner (never ships in the image). Before song_overrides
existed, per-song repairs were sent as one reprocess's options and lost at the
next refresh; their payloads live outside the repo as a JSON list:

    [{"id": "<youtube id>", "speed_factor": 1.0662, "lyrics_text": "..."}, ...]

Each entry becomes `PUT /v1/admin/overrides/<type>/<id>`. Accepted keys: `id`
(required), `speed_factor`, `lyrics_text`, `lrclib_id`, `offset_ms`,
`plain_only`, `note`; any other key stops the run before anything is sent.
With `--reprocess` each stored override is applied at once by a reprocess with
NO options (exactly what the archive feeder sends), so the stored document is
rebuilt under it and from then on protected by the persist guard.

Nothing secret or lyrical is printed: the admin key is read from the
ADMIN_API_KEY environment variable, and output lines carry ids, statuses,
field names and the text's length only. Standard library only, no __file__:
it runs inside the API pod as `python3 -c "$(cat import_overrides.py)" ...`.

Usage (inside the API pod, where ADMIN_API_KEY already is):
    kubectl exec -i -n kashi-server deploy/kashi-api -- \\
        python3 -c "$(cat apps/server/scripts/import_overrides.py)" - --reprocess \\
        < ~/kashi-nightcore-onarim/payloads.json
Dry run anywhere (no key, no request): add --dry-run.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, TextIO

ACCEPTED = ("speed_factor", "lyrics_text", "lrclib_id", "offset_ms", "plain_only", "note")
SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
DEFAULT_BASE_URL = "http://localhost:8080"

# (method, path, body) -> (status, parsed JSON or None)
Sender = Callable[[str, str, dict | None], tuple[int, Any]]


def http_sender(base_url: str, api_key: str, timeout_s: float = 30.0) -> Sender:
    def send(method: str, path: str, body: dict | None) -> tuple[int, Any]:
        data = None
        headers = {"Authorization": "Bearer " + api_key}
        if body is not None:
            # UTF-8, not \\u escapes: a 20 000-character non-Latin text must fit
            # the API's 64 KB body cap.
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw) if raw else None
            except ValueError:
                return exc.code, None

    return send


def parse_payload(raw: Any) -> list[dict]:
    """Validated entries, or ValueError naming every problem at once."""
    if not isinstance(raw, list):
        raise ValueError("payload must be a JSON list of objects")
    problems: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            problems.append(f"entry {index}: not an object")
            continue
        source_id = entry.get("id")
        if not isinstance(source_id, str) or not SOURCE_ID.match(source_id):
            problems.append(f"entry {index}: missing or malformed id")
        elif source_id in seen:
            problems.append(f"entry {index}: id {source_id} appears twice")
        else:
            seen.add(source_id)
        unknown = sorted(set(entry) - set(ACCEPTED) - {"id"})
        if unknown:
            problems.append(f"entry {index}: unknown keys {unknown}")
        if not set(entry) & set(ACCEPTED):
            problems.append(f"entry {index}: nothing to override")
    if problems:
        raise ValueError("; ".join(problems))
    return raw


def _summary(body: dict) -> dict:
    """What may be printed about a body: field names, the text's length."""
    out: dict[str, Any] = {"fields": sorted(body)}
    if isinstance(body.get("lyrics_text"), str):
        out["lyrics_text_chars"] = len(body["lyrics_text"])
    return out


def run(
    entries: list[dict],
    send: Sender | None,
    out: TextIO,
    *,
    source_type: str = "youtube",
    reprocess: bool = False,
    note: str | None = None,
) -> int:
    failures = 0
    for entry in entries:
        source_id = entry["id"]
        body = {key: entry[key] for key in ACCEPTED if key in entry}
        if note and "note" not in body:
            body["note"] = note
        line: dict[str, Any] = {"id": source_id, **_summary(body)}
        if send is None:
            line["dry_run"] = True
            print(json.dumps(line), file=out)
            continue
        status, answer = send("PUT", f"/v1/admin/overrides/{source_type}/{source_id}", body)
        line["put"] = status
        if status not in (200, 201):
            failures += 1
            # FastAPI's 422 detail names the field and the rule, never echoes
            # a long text back in full; keep only the messages.
            detail = (answer or {}).get("detail") if isinstance(answer, dict) else None
            if isinstance(detail, list):
                line["error"] = [d.get("msg") for d in detail if isinstance(d, dict)]
            elif detail is not None:
                line["error"] = detail
        elif reprocess:
            status, answer = send(
                "POST",
                "/v1/admin/reprocess",
                {"source": {"type": source_type, "id": source_id}},
            )
            line["reprocess"] = status
            if status == 202 and isinstance(answer, dict):
                line["job"] = answer.get("job_id")
            else:
                failures += 1
        print(json.dumps(line), file=out)
    return 1 if failures else 0


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    send: Sender | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="import_overrides", description=__doc__.split("\n")[0])
    parser.add_argument("payload", help="JSON file, or - for stdin")
    parser.add_argument("--base-url", default=None, help=f"API root (default {DEFAULT_BASE_URL})")
    parser.add_argument("--source-type", default="youtube", choices=("youtube", "plex", "upload"))
    parser.add_argument("--reprocess", action="store_true", help="apply each override now")
    parser.add_argument("--note", default=None, help="note for entries that carry none")
    parser.add_argument("--dry-run", action="store_true", help="validate and print, send nothing")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    out = out or sys.stdout

    try:
        if args.payload == "-":
            raw = json.load(stdin or sys.stdin)
        else:
            with open(args.payload, encoding="utf-8") as handle:
                raw = json.load(handle)
        entries = parse_payload(raw)
    except (OSError, ValueError) as exc:
        print(f"payload rejected, nothing sent: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        send = None
    elif send is None:
        key = env.get("ADMIN_API_KEY")
        if not key:
            print("ADMIN_API_KEY is not set; nothing sent", file=sys.stderr)
            return 2
        base_url = (args.base_url or env.get("KASHI_API_URL") or DEFAULT_BASE_URL).rstrip("/")
        send = http_sender(base_url, key)
    return run(
        entries,
        send,
        out,
        source_type=args.source_type,
        reprocess=args.reprocess,
        note=args.note,
    )


if __name__ == "__main__":
    sys.exit(main())
