"""A summarizer for the audit: one text pack per song, two requests, scored against the verdicts.

Faz 3.5 step 3 (``docs/research/yerel-model-denetim-2026-11.md`` sections 3, 7
and the step-3 gates). The agent reads every flagged song in session today; a
summarizer that reads the numbers instead must first beat pre-registered gates
on the labelled moments. The same text goes to both candidates: Opus through
Message Batches and the local Gemma through llama-server.

pack   one text input per song: the moment table first (the finder's events
       and the numbers of each rendered ``moment-<t>.json``, every difference
       computed beforehand - the model never subtracts or counts), the question
       last; no picture, no verdict (labels never enter a pack). A seed shuffles
       the moment order (three shuffled runs measure stability). ``--kind
       probe`` asks the M4 numeric probe instead and stores its answer.
run    two requests per song: (1) a prose judgement; (2) the same prefix plus
       (1)'s text, answered as JSON against ``summarize_schema.json`` (strict
       JSON costs reasoning [json-mode], hence prose first). One output file per
       song (model, prompt hash, settings inside; temp file + atomic rename);
       a rerun resumes where it stopped.
         --backend local   llama-server's OpenAI-compatible /v1/chat/completions,
                           temperature 0, reasoning off, json_schema response format
         --backend batch   Anthropic Message Batches (claude-opus-5-5, adaptive
                           thinking, effort set explicitly, output_config.format);
                           two batches in a row, results matched by custom_id
count  token counts of the packs (free count_tokens endpoint): does the 16k
       local context fit, what does a run cost.
score  M1-M5 against review.jsonl (and later the owner's listening set, same
       shape); thresholds pinned by ``tests/test_audit_summarize.py``.

    python -m audit.summarize pack <id>... [--all] [--seed S] [--kind review|probe] --out DIR
    python -m audit.summarize run --packs DIR --backend local|batch --out DIR [--effort medium]
    python -m audit.summarize count --packs DIR
    python -m audit.summarize score --runs R1 [R2 R3] --labels review.jsonl [--probe P] [--json out]

The Anthropic key is read from ANTHROPIC_API_KEY only and never written anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMAS = json.loads(Path(__file__).with_name("summarize_schema.json").read_text())
CLASSES = ("ok", "pile", "late", "early", "missing", "wrong_text", "unsure")
KINDS = ("review", "probe")
MODEL = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 16_000
TOKEN_TARGET = 12_000
LOCAL_CONTEXT = 16_384
CHARS_PER_TOKEN = 3.5      # an estimate for the pack's own check; `count` has the real number
TEXT_CAP = 80
EAR_CAP = 160
POLL_S = 60
# Batches, $ per million tokens: every standard Opus 5.5 rate halved - input $4,
# output $20, cache writes 1.25x (5 min) / 2x (1 h) of input, cache reads $0.20
# (yerel-model-denetim-2026-11.md section 7; confirm on the pricing page before the A/B).
PRICES = {MODEL: {"input": 2.0, "output": 10.0, "cache_write_5m": 2.5, "cache_write_1h": 4.0,
                  "cache_read": 0.10}}

# --- score: pre-registered gates (Faz 3.5 step 3) -------------------------------------
POSITIVE_SEVERITY = 1      # the model flags a moment at this severity or above
LABEL_SEVERITY = {"ok": 0, "pile": 1, "late": 2, "early": 2, "missing": 3, "wrong_text": 3}
REAL = frozenset({"pile", "late", "early", "missing", "wrong_text"})
VISIBLE = frozenset({"late", "early", "missing", "wrong_text"})
M1_MIN_RECALL = 0.90
M1_MIN_PRECISION = 0.60
M2_MIN_SPEARMAN = 0.5
M2_MIN_PRECISION_AT = 0.7
PRECISION_AT = 3
M3_MIN_RECALL = 0.7
M4_MIN_SHARE = 0.95        # 19 of 20 ...
M4_MIN_SONGS = 20          # ... on at least 20 songs with a unique answer
PROBE_TOLERANCE_MS = 10
M5_MIN_VALID = 0.99
M5_MIN_AGREEMENT = 0.90
M5_MIN_RUNS = 3            # shuffled runs, each with its own seed

SYSTEM = """\
You read measurements of a lyrics overlay for YouTube Music and judge what a viewer would notice.

The overlay shows one line at a time and lights its words karaoke-style. A document gives every \
word a start time; the screen lights a word at its start minus the overlay's offset, and the \
"screen" column already applies it. "voice" is where the separated vocal comes back after a \
silence, nearest to the line's start; "doc-voice" and "screen-voice" are those differences in ms \
(positive: after the voice). "ear" is what the aligner's own speech model hears, as letters. All \
differences and counts you need are in the table: never recompute them.

What a viewer notices: a line lit more than about 220 ms after it is sung, or more than about 330 \
ms before; words that never light (0 ms words, lines without words); text that is not what is \
sung. Piles - several words starting within 60 ms - are real but mostly invisible below about 0.4 \
s. A voice return can also be an ad-lib, a breath or an instrument leaking into the vocal stem; \
the ear's letters help tell singing from noise. When two document versions are listed, judge the \
last one; the delta columns compare it with the one before.

Classes: ok (nothing noticeable), pile, late, early, missing (sung words that never light), \
wrong_text, unsure (the numbers cannot decide). Severity: 0 nothing a viewer notices, 1 minor, 2 \
noticeable, 3 obvious."""

QUESTION = {
    "review": ("Question. For every moment above, by its t: would a viewer notice something wrong "
               "on the overlay there, and what? Give a class, a severity 0-3 and a short reason "
               "that cites the numbers. Write prose: one short paragraph per moment, starting "
               "with its t in ms."),
    "probe": ("Question. Among the rows of the last document version above, which line has the "
              "second-largest absolute screen-voice difference, and how large is that absolute "
              "value in ms? Name the line as L<number>. Answer in prose."),
}
CONVERT = {
    "review": ("Now give exactly the judgements you wrote above as JSON: one entry per moment "
               "with its t_ms, class, severity and reason. Do not change any judgement."),
    "probe": "Now give exactly that answer as JSON: line (the number after L) and value_ms.",
}


def mmss(t_ms: int) -> str:
    return f"{t_ms // 60000}:{(t_ms // 1000) % 60:02d}.{(t_ms % 1000) // 100}"


def _cap(text: str | None, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _signed(ms: int | None) -> str:
    return "-" if ms is None else f"{ms:+d}"


# --- pack ------------------------------------------------------------------------

def load_song(run_dir: Path) -> tuple[dict, dict[int, dict]]:
    """``moments.json`` (``audit.find``) and every ``moment-<t>.json`` (``audit.render``;
    suffixed variants such as the real-frame pictures are left out)."""
    moments = json.loads((run_dir / "moments.json").read_text())
    files: dict[int, dict] = {}
    for path in sorted(run_dir.glob("moment-*.json")):
        m = re.fullmatch(r"moment-(\d+)\.json", path.name)
        if m:
            files[int(m.group(1))] = json.loads(path.read_text())
    return moments, files


def finder_info(moments: dict, t_ms: int) -> dict:
    for rank, m in enumerate(moments.get("moments") or [], 1):
        if m["t_ms"] == t_ms:
            return {"source": "finder", "rank": rank, "score": m["score"],
                    "events": [(e["tag"], e["version"], e.get("detail") or "")
                               for e in m.get("events") or []]}
    if t_ms in (moments.get("controls") or []):
        return {"source": "control"}
    return {"source": "other"}


def _line_row(row: dict, before: dict | None) -> str:
    words = row.get("words") or []
    flags = Counter(f for w in words for f in w.get("flags") or [])
    voice = row.get("nearest_voice_onset_ms")
    parts = [f"L{row['line']} \"{_cap(row.get('text'), TEXT_CAP)}\"",
             f"doc {row['start_ms']}", f"screen {row['screen_lights_ms']}",
             f"voice {'-' if voice is None else voice}",
             f"doc-voice {_signed(row.get('doc_minus_voice_ms'))}",
             f"screen-voice {_signed(row.get('screen_minus_voice_ms'))}"]
    if words:
        parts.append(f"words {len(words)} (0 ms {flags.get('zero', 0)}, <60 ms "
                     f"{flags.get('short', 0)}, piled {flags.get('pile', 0)})")
    else:
        parts.append("no words")
    if row.get("syllables") is not None:
        bumps = len(row.get("bumps_ms") or [])
        parts.append(f"syllables {row['syllables']}, loudness bumps {bumps} "
                     f"({row.get('bump_mapping')})")
    if before is not None:
        parts.append(f"delta doc vs previous version {row['start_ms'] - before['start_ms']:+d}")
    marks = [k for k in ("uncertain", "adlib", "words_derived") if row.get(k)]
    if marks:
        parts.append("marked " + ",".join(marks))
    if row.get("ear_letters"):
        parts.append(f"ear \"{_cap(row['ear_letters'], TEXT_CAP)}\"")
    return " | ".join(parts)


def moment_block(t_ms: int, numbers: dict, info: dict) -> str:
    """One moment of the table. Only whitelisted numbers go in (never a verdict)."""
    if info["source"] == "finder":
        head = f"finder rank {info['rank']}, score {info['score']}"
    elif info["source"] == "control":
        head = "control (a quiet moment picked at random)"
    else:
        head = "rendered moment"
    out = [f"### t={t_ms} ms ({mmss(t_ms)}) - {head}"]
    if info.get("events"):
        out.append("finder events: " + "; ".join(
            f"{tag} [{ver}] {_cap(detail, TEXT_CAP)}".rstrip()
            for tag, ver, detail in info["events"]))
    out.append("voice returns in the window (ms): "
               + (", ".join(str(v) for v in numbers.get("voice_onsets_ms") or []) or "none"))
    if numbers.get("ear"):
        out.append(f"ear over the window: \"{_cap(numbers['ear'].get('text'), EAR_CAP)}\"")
    previous: dict[int, dict] = {}
    for label, version in (numbers.get("versions") or {}).items():
        out.append(f"version {label}:")
        rows = version.get("lines") or []
        for row in rows:
            before = previous.get(row["line"])
            if before is not None and before.get("text") != row.get("text"):
                before = None
            out.append("  " + _line_row(row, before))
        previous = {row["line"]: row for row in rows}
    return "\n".join(out)


def probe_answer(files: dict[int, dict]) -> dict | None:
    """The M4 truth: the line of the LAST version with the second-largest
    |screen - voice| over the song's moments (a line in two windows counts once);
    None when it is not unique."""
    values: dict[int, int] = {}
    for numbers in files.values():
        versions = list((numbers.get("versions") or {}).values())
        if not versions:
            continue
        for row in versions[-1].get("lines") or []:
            if row.get("screen_minus_voice_ms") is not None:
                values[row["line"]] = abs(row["screen_minus_voice_ms"])
    ranked = sorted(values.items(), key=lambda kv: -kv[1])
    if len(ranked) < 2 or ranked[1][1] == ranked[0][1] \
            or (len(ranked) > 2 and ranked[2][1] == ranked[1][1]):
        return None
    return {"line": ranked[1][0], "value_ms": ranked[1][1]}


def build_pack(video_id: str, moments: dict, files: dict[int, dict], kind: str = "review",
               seed: int | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown pack kind {kind!r}")
    order = sorted(files)
    if seed is not None:
        random.Random(seed).shuffle(order)
    blocks = [moment_block(t, files[t], finder_info(moments, t)) for t in order]
    versions = list((files[order[0]].get("versions") or {}) if order else [])
    user = "\n\n".join([
        f"Song {video_id}: {len(order)} moments; document versions {', '.join(versions) or '-'}.",
        *blocks,
        QUESTION[kind],
    ])
    pack = {"id": video_id, "kind": kind, "seed": seed, "moments": order, "system": SYSTEM,
            "user": user, "convert": CONVERT[kind],
            "est_tokens": math.ceil((len(SYSTEM) + len(user)) / CHARS_PER_TOKEN)}
    if kind == "probe":
        pack["probe_answer"] = probe_answer(files)
    pack["prompt_hash"] = prompt_hash(pack)
    return pack


def prompt_hash(pack: dict) -> str:
    payload = json.dumps({k: pack[k] for k in ("system", "user", "convert")}
                         | {"schema": SCHEMAS[pack["kind"]]}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --- run -------------------------------------------------------------------------

def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)


def parse_answer(text: str, kind: str) -> tuple[Any, bool, str | None]:
    """(parsed JSON or None, schema-valid, problem)."""
    import jsonschema

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, False, f"invalid JSON: {exc}"
    try:
        jsonschema.validate(data, SCHEMAS[kind])
    except jsonschema.ValidationError as exc:
        return data, False, f"schema: {exc.message}"
    return data, True, None


def coverage_problem(pack: dict, data: Any) -> str | None:
    """A review answer must judge every moment of the pack exactly once."""
    if pack["kind"] != "review" or not isinstance(data, dict):
        return None
    got = [m.get("t_ms") for m in data.get("moments") or []]
    if sorted(got) != sorted(pack["moments"]):
        return f"moments {sorted(got)} != pack {sorted(pack['moments'])}"
    return None


def cost_usd(usage: dict | None, model: str) -> float | None:
    prices = PRICES.get(model)
    if not usage or prices is None:
        return None
    created = usage.get("cache_creation") or {}
    write_1h = created.get("ephemeral_1h_input_tokens") or 0
    write_5m = (usage.get("cache_creation_input_tokens") or 0) - write_1h
    total = (usage.get("input_tokens", 0) * prices["input"]
             + usage.get("output_tokens", 0) * prices["output"]
             + write_5m * prices["cache_write_5m"] + write_1h * prices["cache_write_1h"]
             + (usage.get("cache_read_input_tokens") or 0) * prices["cache_read"])
    return round(total / 1_000_000, 6)


# The model's own answers (the first one per song is what M5 counts); the
# infrastructure failures run again by themselves.
MODEL_OUTCOMES = frozenset({"ok", "schema_invalid", "invalid_json", "refusal", "empty",
                            "truncated"})
TRANSIENT = frozenset({"errored", "expired", "canceled", "missing"})


def _record(pack: dict, backend: str, model: str, settings: dict,
            old: dict | None = None) -> dict:
    """A fresh record; a retry keeps the song's first answer (M5 counts that one)."""
    out = {"id": pack["id"], "kind": pack["kind"], "seed": pack["seed"], "backend": backend,
           "model": model, "prompt_hash": pack["prompt_hash"], "settings": settings,
           "moments": pack["moments"], "status": "pending", "prose": None, "raw_json": None,
           "json": None, "schema_valid": None, "problem": None, "usage": {}, "cost_usd": None,
           "batches": {}, "first_answer": (old or {}).get("first_answer"), "r2_failures": 0}
    if pack["kind"] == "probe":
        out["probe_answer"] = pack.get("probe_answer")
    return out


def _settle(record: dict, status: str, **fields: Any) -> None:
    record.update(status=status, **fields)
    if status in MODEL_OUTCOMES and record.get("first_answer") is None:
        record["first_answer"] = status


def _finish(record: dict, pack: dict, text: str | None, truncated: bool = False) -> None:
    record["raw_json"] = text
    if truncated:
        _settle(record, "truncated", problem="request 2 hit max_tokens")
        return
    data, valid, problem = parse_answer(text or "", pack["kind"])
    problem = problem or coverage_problem(pack, data)
    record.update(json=data, schema_valid=valid)
    _settle(record, "ok" if valid and problem is None
            else ("invalid_json" if data is None else "schema_invalid"), problem=problem)


def _prose(record: dict, text: str, truncated: bool) -> None:
    if truncated:
        _settle(record, "truncated", prose=text, problem="request 1 hit max_tokens")
    elif not text:
        _settle(record, "empty", problem="no text in request 1")
    else:
        record.update(prose=text, status="prose")


def _r2_failed(record: dict, problem: str) -> None:
    """A request 2 that never reached an answer: the prose stays, the next run
    resumes at request 2."""
    record.update(status="prose", problem=f"request 2: {problem}",
                  r2_failures=record.get("r2_failures", 0) + 1)


def _cost(record: dict) -> None:
    costs = [cost_usd(u, record["model"]) for u in record["usage"].values()]
    record["cost_usd"] = round(sum(c for c in costs if c is not None), 6) if any(
        c is not None for c in costs) else None


def todo(packs: Sequence[dict], out_dir: Path, settings: dict, retry_failed: bool = False
         ) -> tuple[list[tuple[dict, dict | None]], list[tuple[dict, dict]]]:
    """((pack, old record) for request 1, (pack, record) for request 2 only). A
    song whose output has this prompt hash and these settings is done when
    ``ok``; a finished prose resumes at request 2 (also after a failed request
    2); infrastructure failures (errored, expired, canceled, missing) run
    again, the model's own failed answers only with ``retry_failed``."""
    first: list[tuple[dict, dict | None]] = []
    second: list[tuple[dict, dict]] = []
    for pack in packs:
        path = out_dir / f"{pack['id']}.json"
        old = json.loads(path.read_text()) if path.exists() else None
        if old is None or old["prompt_hash"] != pack["prompt_hash"] or old["settings"] != settings:
            first.append((pack, None))
        elif old["status"] == "ok":
            continue
        elif old["status"] == "prose":
            second.append((pack, old))
        elif old["status"] in TRANSIENT or retry_failed:
            first.append((pack, old))
    return first, second


class LocalBackend:
    """llama-server's OpenAI-compatible chat completions (httpx)."""

    name = "local"

    def __init__(self, url: str, model: str, max_tokens: int = 4096,
                 extra_body: dict | None = None, client: Any = None, timeout_s: float = 600):
        import httpx

        self.url, self.model, self.max_tokens = url.rstrip("/"), model, max_tokens
        self.extra = {"chat_template_kwargs": {"enable_thinking": False}} | (extra_body or {})
        self.http = client or httpx.Client(timeout=timeout_s)

    def settings(self) -> dict:
        return {"url": self.url, "model": self.model, "max_tokens": self.max_tokens,
                "temperature": 0, **self.extra}

    def _ask(self, messages: list[dict], schema: dict | None) -> tuple[str, dict, bool]:
        """(text, usage, truncated)."""
        body = {"model": self.model, "messages": messages, "temperature": 0,
                "max_tokens": self.max_tokens, **self.extra}
        if schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "answer", "strict": True,
                                                       "schema": schema}}
        response = self.http.post(f"{self.url}/v1/chat/completions", json=body)
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        return (choice["message"]["content"] or "", data.get("usage") or {},
                choice.get("finish_reason") == "length")

    def run(self, first: Sequence[tuple[dict, dict | None]],
            second: Sequence[tuple[dict, dict]], out_dir: Path,
            log: Callable[[str], None] = print) -> dict:
        import httpx

        counts: Counter = Counter()
        settings = self.settings()
        jobs = [(p, _record(p, self.name, self.model, settings, old)) for p, old in first]
        jobs += list(second)
        for pack, record in jobs:
            base = [{"role": "system", "content": pack["system"]},
                    {"role": "user", "content": pack["user"]}]
            if record["status"] != "prose":
                try:
                    text, record["usage"]["r1"], cut = self._ask(base, None)
                    _prose(record, text, cut)
                except httpx.HTTPError as exc:
                    _settle(record, "errored", problem=f"request 1: {type(exc).__name__}: {exc}")
                write_atomic(out_dir / f"{pack['id']}.json", record)
            if record["status"] == "prose":
                try:
                    text, record["usage"]["r2"], cut = self._ask(
                        base + [{"role": "assistant", "content": record["prose"]},
                                {"role": "user", "content": pack["convert"]}],
                        SCHEMAS[pack["kind"]])
                    _finish(record, pack, text, cut)
                except httpx.HTTPError as exc:
                    _r2_failed(record, f"{type(exc).__name__}: {exc}")
            counts[record["status"]] += 1
            record["written"] = datetime.now(UTC).isoformat(timespec="seconds")
            write_atomic(out_dir / f"{pack['id']}.json", record)
            log(f"{pack['id']}: {record['status']}")
        return dict(counts)


def make_anthropic_client() -> Any:
    """The SDK client with the key from ANTHROPIC_API_KEY only."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY is not set (the key is read from the environment only)")
    import anthropic  # pyright: ignore[reportMissingImports]

    return anthropic.Anthropic(api_key=key)


def fingerprint(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]


class BatchBackend:
    """Anthropic Message Batches: request 1 for every song in one batch, then
    request 2 (it needs 1's text) in a second; results come in any order and
    are matched by custom_id."""

    name = "batch"

    def __init__(self, client: Any, model: str = MODEL, effort: str = DEFAULT_EFFORT,
                 max_tokens: int = MAX_TOKENS, poll_s: float = POLL_S,
                 sleep: Callable[[float], None] = time.sleep):
        if effort not in EFFORTS:
            raise ValueError(f"unknown effort {effort!r}")
        self.client, self.model, self.effort = client, model, effort
        self.max_tokens, self.poll_s, self.sleep = max_tokens, poll_s, sleep

    def settings(self) -> dict:
        return {"model": self.model, "effort": self.effort, "max_tokens": self.max_tokens,
                "thinking": "adaptive"}

    def params(self, pack: dict, prose: str | None = None) -> dict:
        """Thinking always on (adaptive), effort explicit; no sampling
        parameters, no prefill, no fallbacks (Batches rejects them)."""
        messages: list[dict] = [{"role": "user", "content": pack["user"]}]
        output_config: dict = {"effort": self.effort}
        if prose is not None:
            messages += [{"role": "assistant", "content": prose},
                         {"role": "user", "content": pack["convert"]}]
            output_config["format"] = {"type": "json_schema", "schema": SCHEMAS[pack["kind"]]}
        return {"model": self.model, "max_tokens": self.max_tokens, "system": pack["system"],
                "messages": messages, "thinking": {"type": "adaptive"},
                "output_config": output_config}

    def _batch(self, stage: str, requests: list[dict], out_dir: Path,
               log: Callable[[str], None]) -> dict[str, tuple[str, Any]]:
        """custom_id -> (batch id, result). A batch this stage already submitted
        (``.batch-<stage>.json``, one entry per batch) answers every request
        whose params are byte-identical to what it was sent; only the rest is
        submitted, so a resumed run never pays twice and never takes an answer
        to other params (another effort, another pack). ``run`` removes the
        state file once the records are written."""
        state_path = out_dir / f".batch-{stage}.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {"batches": []}
        want = {r["custom_id"]: fingerprint(r["params"]) for r in requests}
        plan: dict[str, list[str]] = {}
        covered: set[str] = set()
        for entry in state["batches"]:
            take = [cid for cid, fp in entry["requests"].items()
                    if want.get(cid) == fp and cid not in covered]
            if take:
                plan[entry["batch_id"]] = take
                covered |= set(take)
                log(f"resuming batch {entry['batch_id']} ({stage}, {len(take)} requests)")
            if len(take) < len(entry["requests"]):
                log(f"batch {entry['batch_id']}: {len(entry['requests']) - len(take)} requests "
                    f"no longer match and are not reused")
        rest = [r for r in requests if r["custom_id"] not in covered]
        if rest:
            batch_id = self.client.messages.batches.create(requests=rest).id
            state["batches"].append({"batch_id": batch_id,
                                     "requests": {r["custom_id"]: want[r["custom_id"]]
                                                  for r in rest}})
            write_atomic(state_path, state)
            plan[batch_id] = [r["custom_id"] for r in rest]
            log(f"submitted batch {batch_id} ({stage}, {len(rest)} requests)")
        out: dict[str, tuple[str, Any]] = {}
        for batch_id, take in plan.items():
            while self.client.messages.batches.retrieve(batch_id).processing_status != "ended":
                self.sleep(self.poll_s)
            results = {r.custom_id: r.result
                       for r in self.client.messages.batches.results(batch_id)}
            out |= {cid: (batch_id, results.get(cid)) for cid in take}
        return out

    @staticmethod
    def _usage(message: Any) -> dict:
        usage = message.usage
        dump = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
        keep = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "cache_creation")
        return {k: dump.get(k) for k in keep if dump.get(k) is not None}

    @staticmethod
    def _text(message: Any) -> str:
        return "".join(b.text for b in message.content if getattr(b, "type", None) == "text")

    def _apply(self, record: dict, result: Any, stage: str, pack: dict) -> None:
        """One result into the song's record; ``stage`` r1 (prose) or r2 (JSON)."""
        if result is None or result.type != "succeeded":
            kind = "missing" if result is None else result.type
            problem = None
            if result is not None and result.type == "errored":
                err = getattr(result.error, "error", None)
                problem = f"{getattr(err, 'type', '?')}: {getattr(err, 'message', '')}"
            if stage == "r2":
                _r2_failed(record, f"{kind} {problem or ''}".strip())
            else:
                _settle(record, kind, problem=problem)
            return
        message = result.message
        record["usage"][stage] = self._usage(message)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            _settle(record, "refusal", problem=f"refusal ({getattr(details, 'category', None)})")
            return
        cut = message.stop_reason == "max_tokens"
        if stage == "r1":
            _prose(record, self._text(message), cut)
        else:
            _finish(record, pack, self._text(message), cut)

    def run(self, first: Sequence[tuple[dict, dict | None]],
            second: Sequence[tuple[dict, dict]], out_dir: Path,
            log: Callable[[str], None] = print) -> dict:
        settings = self.settings()
        counts: dict[str, Counter] = {"r1": Counter(), "r2": Counter()}
        packs = {p["id"]: p for p, _ in first} | {p["id"]: p for p, _ in second}
        records = {p["id"]: _record(p, self.name, self.model, settings, old) for p, old in first}
        records |= {p["id"]: old for p, old in second}
        for stage in ("r1", "r2"):
            todo_ids = [i for i in packs if records[i]["status"] ==
                        ("pending" if stage == "r1" else "prose")]
            if not todo_ids:
                continue
            requests = [{"custom_id": f"{stage}-{i}",
                         "params": self.params(packs[i], records[i]["prose"] if stage == "r2"
                                               else None)} for i in todo_ids]
            results = self._batch(stage, requests, out_dir, log)
            for i in todo_ids:
                batch_id, result = results[f"{stage}-{i}"]
                counts[stage][result.type if result is not None else "missing"] += 1
                record = records[i]
                record["batches"][stage] = batch_id
                self._apply(record, result, stage, packs[i])
                if record["status"] in ("refusal", "truncated"):
                    counts[stage][record["status"]] += 1
                _cost(record)
                record["written"] = datetime.now(UTC).isoformat(timespec="seconds")
                write_atomic(out_dir / f"{i}.json", record)
            (out_dir / f".batch-{stage}.json").unlink(missing_ok=True)
        summary: dict[str, Any] = {stage: dict(c) for stage, c in counts.items()}
        summary["cost_usd"] = round(sum(r["cost_usd"] or 0 for r in records.values()), 6)
        summary["status"] = dict(Counter(r["status"] for r in records.values()))
        return summary


def count_tokens(client: Any, packs: Sequence[dict], model: str = MODEL,
                 max_tokens: int = MAX_TOKENS) -> dict:
    """Claude's tokenizer (count_tokens is free): request 1's input per pack and
    request 2's worst case (request 1 + a prose of max_tokens + the conversion
    line). For the local 16k question use ``count_local`` (Gemma's tokenizer)."""
    rows = {}
    convert = None
    for pack in packs:
        got = client.messages.count_tokens(model=model, system=pack["system"],
                                           messages=[{"role": "user", "content": pack["user"]}])
        if convert is None:
            convert = client.messages.count_tokens(
                model=model, messages=[{"role": "user", "content": pack["convert"]}]).input_tokens
        rows[pack["id"]] = {"r1": got.input_tokens,
                            "r2_worst": got.input_tokens + max_tokens + (convert or 0)}
    values = [r["r1"] for r in rows.values()]
    return {"model": model, "tokenizer": "claude", "songs": rows, "max": max(values, default=0),
            "total": sum(values),
            "over_target": sorted(k for k, r in rows.items() if r["r1"] > TOKEN_TARGET)}


def count_local(http: Any, url: str, packs: Sequence[dict], max_tokens: int = 4096,
                context: int = LOCAL_CONTEXT) -> dict:
    """The local server's own tokenizer: llama-server's ``/apply-template`` (the
    chat template) and ``/tokenize``. A song fits when request 2 in the worst
    case does: request 1 + a prose of max_tokens + the conversion line, plus
    max_tokens of answer, within the context."""
    def tokens(text: str) -> int:
        r = http.post(f"{url.rstrip('/')}/tokenize", json={"content": text})
        r.raise_for_status()
        return len(r.json()["tokens"])

    rows = {}
    for pack in packs:
        r = http.post(f"{url.rstrip('/')}/apply-template",
                      json={"messages": [{"role": "system", "content": pack["system"]},
                                         {"role": "user", "content": pack["user"]}]})
        r.raise_for_status()
        r1 = tokens(r.json()["prompt"])
        worst = r1 + tokens(pack["convert"]) + 2 * max_tokens
        rows[pack["id"]] = {"r1": r1, "worst": worst, "fits": worst <= context}
    return {"tokenizer": "local", "context": context, "max_tokens": max_tokens, "songs": rows,
            "max": max((r["r1"] for r in rows.values()), default=0),
            "not_fitting": sorted(k for k, r in rows.items() if not r["fits"])}


# --- score -----------------------------------------------------------------------

def load_labels(paths: Sequence[Path]) -> dict[tuple[str, int], str]:
    """(id, t_ms) -> verdict: unsure and owner-disputed verdicts left out; a
    later file (the owner's listening set) overrides an earlier one."""
    from audit.compare import load_verdicts, owner_state

    out: dict[tuple[str, int], str] = {}
    for path in paths:
        for rec in load_verdicts(path):
            key = (str(rec["id"]), int(rec["t_ms"]))
            # unsure (and render_bug) carry no severity: not labels
            if owner_state(rec) == "owner-disputed" or rec["verdict"] not in LABEL_SEVERITY:
                out.pop(key, None)
                continue
            out[key] = rec["verdict"]
    return out


def load_run(run_dir: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(run_dir.glob("*.json"))
            if not p.name.startswith(".")}


def predictions(run: dict[str, dict]) -> dict[tuple[str, int], dict]:
    out = {}
    for song, rec in run.items():
        if rec.get("kind") != "review" or not rec.get("schema_valid"):
            continue
        for m in (rec.get("json") or {}).get("moments") or []:
            out[(song, int(m["t_ms"]))] = m
    return out


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else None


def precision_at(items: Sequence[tuple[int, bool]], k: int = PRECISION_AT) -> float:
    """Expected share of real errors among the top k by model severity; a tie
    at the cut counts its real share for the slots it fills."""
    by_sev: dict[int, list[bool]] = {}
    for sev, real in items:
        by_sev.setdefault(sev, []).append(real)
    slots, hits = k, 0.0
    for sev in sorted(by_sev, reverse=True):
        group = by_sev[sev]
        take = min(slots, len(group))
        hits += take * sum(group) / len(group)
        slots -= take
        if not slots:
            break
    return hits / k


def _share(n: int, d: int) -> float | None:
    """Exact: a gate never passes on a rounded share (0.9899 is not 0.99)."""
    return n / d if d else None


def review_metrics(run: dict[str, dict], labels: dict[tuple[str, int], str]) -> dict:
    preds = predictions(run)
    rows = []
    # every labelled moment counts, so n stays fixed: a song missing from the
    # run counts as not flagged, and the run fails the ``complete`` gate
    missing = sorted({song for song, _ in labels} - set(run))
    for key, verdict in sorted(labels.items()):
        p = preds.get(key)
        sev = int(p["severity"]) if p else 0
        rows.append({"key": key, "verdict": verdict, "severity": sev, "predicted": p is not None,
                     "positive": sev >= POSITIVE_SEVERITY})
    real = [r for r in rows if r["verdict"] in REAL]
    visible = [r for r in rows if r["verdict"] in VISIBLE]
    piles = [r for r in rows if r["verdict"] == "pile"]
    flagged = [r for r in rows if r["positive"]]
    tp = sum(r["positive"] for r in real)
    rho = spearman([r["severity"] for r in rows], [LABEL_SEVERITY[r["verdict"]] for r in rows])
    per_song: dict[str, list[tuple[int, bool]]] = {}
    for r in rows:
        per_song.setdefault(r["key"][0], []).append((r["severity"], r["verdict"] in REAL))
    at_k = [precision_at(items) for items in per_song.values()
            if sum(real_ for _, real_ in items) >= PRECISION_AT]
    m = {
        "moments": len(rows), "real": len(real), "missing_songs": missing,
        "no_prediction": sum(not r["predicted"] for r in rows),
        "m1_recall": _share(tp, len(real)), "m1_precision": _share(tp, len(flagged)),
        "m2_spearman": rho,
        "m2_precision_at_3": statistics.fmean(at_k) if at_k else None,
        "m2_songs": len(at_k),
        "m3_recall": _share(sum(r["positive"] for r in visible), len(visible)),
        "m3_visible": len(visible),
        "pile_recall": _share(sum(r["positive"] for r in piles), len(piles)),
    }
    m["gates"] = {
        "complete": not missing,
        "m1": (m["m1_recall"] or 0) >= M1_MIN_RECALL
        and (m["m1_precision"] or 0) >= M1_MIN_PRECISION,
        "m2": (m["m2_spearman"] or 0) >= M2_MIN_SPEARMAN
        and (m["m2_precision_at_3"] or 0) >= M2_MIN_PRECISION_AT,
        "m3": (m["m3_recall"] or 0) >= M3_MIN_RECALL,
    }
    return m


def probe_metrics(run: dict[str, dict]) -> dict:
    rows = [r for r in run.values() if r.get("kind") == "probe"]
    asked = [r for r in rows if r.get("probe_answer")]
    correct = 0
    for r in asked:
        got, truth = r.get("json") if r.get("schema_valid") else None, r["probe_answer"]
        if got and got["line"] == truth["line"] \
                and abs(got["value_ms"] - truth["value_ms"]) <= PROBE_TOLERANCE_MS:
            correct += 1
    share = _share(correct, len(asked))
    return {"songs": len(asked), "no_unique_answer": len(rows) - len(asked), "correct": correct,
            "share": share, "gate": len(asked) >= M4_MIN_SONGS and share is not None
            and share >= M4_MIN_SHARE}


def stability(runs: Sequence[dict[str, dict]]) -> dict:
    """M5: each song's FIRST answer from the model (a refusal, an empty or
    truncated reply and an invalid JSON count against it; infrastructure
    failures never reached the model) schema-valid; class agreement over every
    pair of runs, per moment, with at least M5_MIN_RUNS runs of distinct seeds."""
    answered = [r for run in runs for r in run.values() if r.get("first_answer")]
    valid = _share(sum(r["first_answer"] == "ok" for r in answered), len(answered))
    seeds = [{r.get("seed") for r in run.values() if r.get("kind") == "review"} for run in runs]
    distinct = all(len(x) == 1 and None not in x for x in seeds) \
        and len({next(iter(x)) for x in seeds}) == len(runs)
    preds = [predictions(run) for run in runs]
    same, pairs = 0, 0
    for a in range(len(preds)):
        for b in range(a + 1, len(preds)):
            for key in preds[a].keys() & preds[b].keys():
                pairs += 1
                same += preds[a][key]["class"] == preds[b][key]["class"]
    agreement = _share(same, pairs)
    return {"answered": len(answered), "first_try_valid": valid, "pairs": pairs,
            "agreement": agreement, "runs": len(runs), "distinct_seeds": distinct,
            "gates": {"valid": valid is not None and valid >= M5_MIN_VALID,
                      "agreement": len(runs) >= M5_MIN_RUNS and distinct
                      and agreement is not None and agreement >= M5_MIN_AGREEMENT}}


def score(runs: Sequence[dict[str, dict]], labels: dict[tuple[str, int], str],
          probe: dict[str, dict] | None = None) -> dict:
    """M1-M3 on the first run (the others reported), M4 on the probe run, M5
    over all of them."""
    per_run = [review_metrics(run, labels) for run in runs]
    out: dict = {"runs": per_run, "m5": stability(runs)}
    gates = dict(per_run[0]["gates"]) if per_run else {}
    gates |= {"m5_valid": out["m5"]["gates"]["valid"],
              "m5_agreement": out["m5"]["gates"]["agreement"]}
    if probe is not None:
        out["m4"] = probe_metrics(probe)
        gates["m4"] = out["m4"]["gate"]
    out["gates"] = gates
    out["passed"] = bool(gates) and all(gates.values()) and probe is not None
    return out


# --- CLI -------------------------------------------------------------------------

def load_packs(pack_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(pack_dir.glob("*.json"))]


def _song_ids(args: argparse.Namespace, runs_dir: Path) -> list[str]:
    if args.all:
        return sorted(p.parent.name for p in runs_dir.glob("*/moments.json"))
    return list(args.ids)


def main(argv: list[str] | None = None) -> int:
    from audit.paths import RUNS_DIR

    parser = argparse.ArgumentParser(description="Summarizer packs, runs and scores.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pk = sub.add_parser("pack")
    pk.add_argument("ids", nargs="*")
    pk.add_argument("--all", action="store_true")
    pk.add_argument("--seed", type=int)
    pk.add_argument("--kind", choices=KINDS, default="review")
    pk.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    pk.add_argument("--out", type=Path, required=True)
    rn = sub.add_parser("run")
    rn.add_argument("--packs", type=Path, required=True)
    rn.add_argument("--backend", choices=("local", "batch"), required=True)
    rn.add_argument("--out", type=Path, required=True)
    rn.add_argument("--model", help=f"batch: default {MODEL}; local: the server's model name")
    rn.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT)
    rn.add_argument("--max-tokens", type=int)
    rn.add_argument("--url", default="http://127.0.0.1:8080", help="llama-server base URL")
    rn.add_argument("--extra-body", type=json.loads, default=None,
                    help="local: JSON merged into each request (reasoning switches)")
    rn.add_argument("--poll-s", type=float, default=POLL_S)
    rn.add_argument("--retry-failed", action="store_true")
    ct = sub.add_parser("count")
    ct.add_argument("--packs", type=Path, required=True)
    ct.add_argument("--backend", choices=("batch", "local"), default="batch",
                    help="batch: Claude's count_tokens; local: llama-server's own tokenizer")
    ct.add_argument("--model", default=MODEL)
    ct.add_argument("--url", default="http://127.0.0.1:8080")
    ct.add_argument("--max-tokens", type=int)
    sc = sub.add_parser("score")
    sc.add_argument("--runs", type=Path, nargs="+", required=True)
    sc.add_argument("--labels", type=Path, action="append", required=True)
    sc.add_argument("--probe", type=Path)
    sc.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "pack":
        ids = _song_ids(args, args.runs_dir)
        for vid in ids:
            moments, files = load_song(args.runs_dir / vid)
            pack = build_pack(vid, moments, files, args.kind, args.seed)
            write_atomic(args.out / f"{vid}.json", pack)
            warn = " (over the target)" if pack["est_tokens"] > TOKEN_TARGET else ""
            print(f"{vid}: {len(pack['moments'])} moments, ~{pack['est_tokens']} tokens{warn}")
        return 0
    if args.cmd == "run":
        packs = load_packs(args.packs)
        if args.backend == "batch":
            backend: Any = BatchBackend(make_anthropic_client(), args.model or MODEL, args.effort,
                                        args.max_tokens or MAX_TOKENS, args.poll_s)
        else:
            backend = LocalBackend(args.url, args.model or "local", args.max_tokens or 4096,
                                   args.extra_body)
        first, second = todo(packs, args.out, backend.settings(), args.retry_failed)
        print(f"{len(first)} songs from request 1, {len(second)} from request 2, "
              f"{len(packs) - len(first) - len(second)} done")
        summary = backend.run(first, second, args.out)
        print(json.dumps(summary))
        write_atomic(args.out / ".run-summary.json", {"summary": summary,
                                                      "settings": backend.settings()})
        return 0
    if args.cmd == "count":
        packs = load_packs(args.packs)
        if args.backend == "local":
            import httpx

            result = count_local(httpx.Client(timeout=60), args.url, packs,
                                 args.max_tokens or 4096)
        else:
            result = count_tokens(make_anthropic_client(), packs, args.model,
                                  args.max_tokens or MAX_TOKENS)
        print(json.dumps(result, indent=1))
        return 0
    runs = [load_run(d) for d in args.runs]
    result = score(runs, load_labels(args.labels), load_run(args.probe) if args.probe else None)
    print(json.dumps({"gates": result["gates"], "passed": result["passed"]}))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1, default=list) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
