"""Faz 3.5 Step 3: the summarizer A/B, two requests per song (prose, then JSON).

Arms, on identical text (``audit.pack``):
  opus           Claude Opus 5.5 through the Message Batches API: effort set
                 explicitly (EFFORT), thinking left to the model (always on),
                 no sampling parameters (it takes none), cost read from usage
  opus-pictures  the same plus the moment pictures (the spectrogram-reading test)
  gemma          the installed Gemma 4 26B-A4B on the desktop's llama.cpp server
                 (16k context, reasoning off, temperature 0)

Each arm runs SHUFFLED_RUNS times with the moment table shuffled (seeded by
song and run: the shuffle is reproducible, the models are not seeded). One
output per song and run under ``runs/step3/<arm>/run<k>/``, written to a temp
file and renamed; every command resumes where the last one stopped.

    python -m audit.summarizer count --doc D --verdicts R [--claude] [--local URL]
    python -m audit.summarizer submit --doc D --verdicts R --arm opus --phase prose
    python -m audit.summarizer collect --doc D --verdicts R --arm opus   # until all ended
    python -m audit.summarizer submit --doc D --verdicts R --arm opus --phase json
    python -m audit.summarizer collect --doc D --verdicts R --arm opus
    python -m audit.summarizer local --doc D --verdicts R --arm gemma --url http://desktop:8080
    python -m audit.summarizer evaluate --verdicts R --arm opus [--listening L]

The Anthropic SDK is not a project dependency (manual tooling, like
``qwen-asr``): ``uv run --with anthropic python -m audit.summarizer ...``.
Batches are kept 29 days and are not zero-retention eligible; the pack carries
lyric lines and numbers, never audio.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from audit import gates
from audit.pack import JSON_TASK, PROMPT_SHA, PROSE_TASK, RULES, SCHEMA, Pack, build_pack

MODEL = "claude-opus-5-5"
EFFORT = "high"            # the note: "effort set explicitly"; high for judgement work
MAX_TOKENS = 16_000        # thinking counts toward it; non-streaming batch requests
# Claude Opus 5.5 per MTok: $4 in / $20 out / $5 cache write / $0.20 cache read;
# Message Batches halve every one of them.
PRICE_PER_MTOK = {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.20}
BATCH_DISCOUNT = 0.5
LOCAL_MAX_TOKENS = 4000    # 16k context minus a <= 12k input
PHASES = ("prose", "json")


# --- requests -------------------------------------------------------------------

def system_blocks(pack: Pack) -> list[dict]:
    """The rules, then the song's pack; the pack is cached for the JSON request."""
    return [{"type": "text", "text": RULES},
            {"type": "text", "text": pack.text, "cache_control": {"type": "ephemeral"}}]


def image_blocks(pictures: Sequence[tuple[str, Path]]) -> list[dict]:
    """Each moment's picture, named by its moment id before it."""
    out: list[dict] = []
    for moment, path in pictures:
        out.append({"type": "text", "text": f"Picture of {moment}:"})
        out.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": base64.b64encode(path.read_bytes()).decode()}})
    return out


def prose_params(pack: Pack, pictures: Sequence[tuple[str, Path]] = (),
                 effort: str = EFFORT) -> dict:
    return {
        "model": MODEL, "max_tokens": MAX_TOKENS, "system": system_blocks(pack),
        "messages": [{"role": "user",
                      "content": image_blocks(pictures) + [{"type": "text", "text": PROSE_TASK}]}],
        "output_config": {"effort": effort},
    }


def json_params(pack: Pack, prose: str, effort: str = EFFORT) -> dict:
    return {
        "model": MODEL, "max_tokens": MAX_TOKENS, "system": system_blocks(pack),
        "messages": [{"role": "user", "content": [{"type": "text", "text":
                      f"{JSON_TASK}\n\n<assessment>\n{prose}\n</assessment>"}]}],
        "output_config": {"effort": effort,
                          "format": {"type": "json_schema", "schema": SCHEMA}},
    }


def cost_usd(usage: Any, discount: float = BATCH_DISCOUNT) -> float:
    def n(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)
    total = (n("input_tokens") * PRICE_PER_MTOK["input"]
             + n("output_tokens") * PRICE_PER_MTOK["output"]
             + n("cache_creation_input_tokens") * PRICE_PER_MTOK["cache_write"]
             + n("cache_read_input_tokens") * PRICE_PER_MTOK["cache_read"])
    return round(total * discount / 1e6, 6)


def message_text(message: Any) -> str | None:
    """The text blocks of a finished message; None when the model declined."""
    if getattr(message, "stop_reason", None) == "refusal":
        return None
    return "".join(b.text for b in message.content if getattr(b, "type", None) == "text")


def _usage_dict(usage: Any) -> dict:
    return {k: int(getattr(usage, k, 0) or 0) for k in (
        "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}


# --- files ------------------------------------------------------------------------

def arm_dir(arm: str) -> Path:
    from audit.paths import RUNS_DIR

    return RUNS_DIR / "step3" / arm


def out_path(arm: str, run: int, video_id: str, phase: str) -> Path:
    return arm_dir(arm) / f"run{run}" / (f"{video_id}.prose.json" if phase == "prose"
                                         else f"{video_id}.json")


def write_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)


def custom_id(phase: str, run: int, video_id: str) -> str:
    return f"{phase}-r{run}-{video_id}"


def parse_custom_id(cid: str) -> tuple[str, int, str]:
    phase, run, video_id = cid.split("-", 2)
    return phase, int(run[1:]), video_id


def finish(arm: str, run: int, pack: Pack, prose: dict, answer_text: str | None,
           usage: dict | None, cost: float, extra: dict) -> dict:
    """The song's output file: the answer parsed and checked once (first try)."""
    from audit.step3 import validate

    answer = None
    if answer_text is not None:
        try:
            answer = json.loads(answer_text)
        except json.JSONDecodeError:
            answer = None
    reason = validate(answer, pack.moment_ids, pack.line_ids)
    out = {
        "id": pack.id, "arm": arm, "run": run, "prompt_sha": PROMPT_SHA, **extra,
        "moment_ids": pack.moment_ids, "line_ids": pack.line_ids, "probe": pack.probe,
        "prose": prose.get("prose"), "answer": answer, "valid": reason is None,
        "valid_first_try": reason is None, "invalid_reason": reason,
        "refused": prose.get("prose") is None or answer_text is None,
        "usage": {"prose": prose.get("usage"), "json": usage},
        "cost_usd": round((prose.get("cost_usd") or 0.0) + cost, 6),
    }
    write_atomic(out_path(arm, run, pack.id, "json"), out)
    return out


# --- packs ------------------------------------------------------------------------

def song_moments(records: Sequence[dict]) -> dict[str, list[int]]:
    """Every labelled moment per song (the model judges all; the metrics drop
    unsure and disputed ones)."""
    out: dict[str, set[int]] = {}
    for rec in records:
        out.setdefault(str(rec["id"]), set()).add(int(rec["t_ms"]))
    return {k: sorted(v) for k, v in out.items()}


def pack_for(song, run: int) -> Pack:
    return build_pack(song, random.Random(f"{song.id}:{run}"))


def load_songs(docs: dict[str, dict], records: Sequence[dict], label: str | None):
    from audit.pack import load_song

    return [load_song(vid, docs[vid], ts, label)
            for vid, ts in sorted(song_moments(records).items()) if vid in docs]


def pictures_for(video_id: str, pack: Pack) -> list[tuple[str, Path]]:
    """(moment id, its picture) in moment order, where the picture exists."""
    from audit.paths import RUNS_DIR

    out = []
    for moment, t in sorted(pack.moment_ids.items(), key=lambda kv: int(kv[0][1:])):
        path = RUNS_DIR / video_id / f"moment-{t}.png"
        if path.exists():
            out.append((moment, path))
    return out


# --- Message Batches -------------------------------------------------------------

def _state_path(arm: str) -> Path:
    return arm_dir(arm) / "batches.json"


def _state(arm: str) -> list[dict]:
    path = _state_path(arm)
    return json.loads(path.read_text()) if path.exists() else []


def submit(client: Any, arm: str, phase: str, songs, runs: int, *, pictures: bool = False,
           effort: str = EFFORT) -> str | None:
    """One batch with every (song, run) of ``phase`` not yet done or pending."""
    pending = {cid for b in _state(arm) if not b["collected"] for cid in b["items"]}
    requests = []
    for run in range(1, runs + 1):
        for song in songs:
            cid = custom_id(phase, run, song.id)
            if cid in pending or out_path(arm, run, song.id, phase).exists():
                continue
            pack = pack_for(song, run)
            if phase == "prose":
                params = prose_params(pack, pictures_for(song.id, pack) if pictures else (),
                                      effort)
            else:
                prose_file = out_path(arm, run, song.id, "prose")
                if not prose_file.exists():
                    continue
                prose = json.loads(prose_file.read_text()).get("prose")
                if prose is None:
                    finish(arm, run, pack, {"prose": None}, None, None, 0.0,
                           {"model": MODEL, "effort": effort})
                    continue
                params = json_params(pack, prose, effort)
            requests.append({"custom_id": cid, "params": params})
    if not requests:
        return None
    batch = client.messages.batches.create(requests=requests)
    state = _state(arm) + [{"id": batch.id, "phase": phase, "effort": effort,
                            "items": [r["custom_id"] for r in requests], "collected": False}]
    write_atomic(_state_path(arm), state)
    return batch.id


def collect(client: Any, arm: str, songs) -> dict:
    """Every ended, uncollected batch: results written by custom_id (results come
    in any order). Returns counts by status."""
    by_id = {s.id: s for s in songs}
    state = _state(arm)
    counts: dict[str, int] = {}
    for entry in state:
        if entry["collected"]:
            continue
        batch = client.messages.batches.retrieve(entry["id"])
        if batch.processing_status != "ended":
            counts["pending"] = counts.get("pending", 0) + len(entry["items"])
            continue
        for result in client.messages.batches.results(entry["id"]):
            phase, run, vid = parse_custom_id(result.custom_id)
            kind = result.result.type
            counts[kind] = counts.get(kind, 0) + 1
            message = result.result.message if kind == "succeeded" else None
            text = message_text(message) if message is not None else None
            usage = _usage_dict(message.usage) if message is not None else None
            cost = cost_usd(message.usage) if message is not None else 0.0
            if text is None:
                counts["no_text"] = counts.get("no_text", 0) + 1
            if phase == "prose":
                write_atomic(out_path(arm, run, vid, "prose"),
                             {"id": vid, "run": run, "prose": text, "result": kind,
                              "stop_reason": getattr(message, "stop_reason", None),
                              "usage": usage, "cost_usd": cost})
            elif vid in by_id:
                prose = json.loads(out_path(arm, run, vid, "prose").read_text())
                finish(arm, run, pack_for(by_id[vid], run), prose, text, usage, cost,
                       {"model": MODEL, "effort": entry.get("effort", EFFORT)})
        entry["collected"] = True
    write_atomic(_state_path(arm), state)
    return counts


def anthropic_client() -> Any:
    import anthropic  # pyright: ignore[reportMissingImports]

    return anthropic.Anthropic()


# --- the local server ------------------------------------------------------------

def local_chat(http: Any, system: str, user: str, schema: dict | None = None) -> str:
    """One llama.cpp chat completion (OpenAI-compatible route, temperature 0,
    prompt cache reused across the two requests of a song)."""
    body: dict = {"messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "temperature": 0, "cache_prompt": True, "max_tokens": LOCAL_MAX_TOKENS}
    if schema is not None:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "song_audit", "schema": schema}}
    response = http.post("/v1/chat/completions", json=body)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def run_local(http: Any, arm: str, songs, runs: int) -> int:
    done = 0
    for run in range(1, runs + 1):
        for song in songs:
            if out_path(arm, run, song.id, "json").exists():
                continue
            pack = pack_for(song, run)
            system = RULES + "\n\n" + pack.text
            prose_file = out_path(arm, run, song.id, "prose")
            if prose_file.exists():
                prose = json.loads(prose_file.read_text())
            else:
                prose = {"id": song.id, "run": run,
                         "prose": local_chat(http, system, PROSE_TASK), "cost_usd": 0.0}
                write_atomic(prose_file, prose)
            answer = local_chat(http, system,
                                f"{JSON_TASK}\n\n<assessment>\n{prose['prose']}\n</assessment>",
                                SCHEMA)
            finish(arm, run, pack, prose, answer, None, 0.0, {"model": "local"})
            done += 1
    return done


# --- token counts (Step 0) -------------------------------------------------------

def count_claude(client: Any, pack: Pack) -> int:
    params = prose_params(pack)
    return client.messages.count_tokens(model=MODEL, system=params["system"],
                                        messages=params["messages"]).input_tokens


def count_local(http: Any, pack: Pack) -> int:
    response = http.post("/tokenize", json={"content": RULES + "\n\n" + pack.text + "\n\n"
                                            + PROSE_TASK})
    response.raise_for_status()
    return len(response.json()["tokens"])


# --- CLI ----------------------------------------------------------------------------

def _outputs(arm: str, runs: int) -> list[list[dict]]:
    out = []
    for run in range(1, runs + 1):
        folder = arm_dir(arm) / f"run{run}"
        files = sorted(p for p in folder.glob("*.json") if not p.name.endswith(".prose.json")) \
            if folder.exists() else []
        out.append([json.loads(p.read_text()) for p in files])
    return out


def _print_eval(arm: str, result: dict, outputs: Iterable[dict]) -> None:
    for k, (m, g) in enumerate(zip(result["runs"], result["run_gates"], strict=True), 1):
        print(f"run {k}: M1 recall {m['m1_recall']} precision {m['m1_precision']} "
              f"({m['hits']} of {m['real_errors']} real errors, {m['flagged']} flagged); "
              f"M2 spearman {m['m2_spearman']} p@3 {m['m2_precision_at_3']}; "
              f"M3 {m['m3_visible_recall']}; M4 {m['m4_probe_correct']}/{m['m4_probed']}; "
              f"valid {m['valid']}/{m['outputs']} -> "
              + ", ".join(f"{name} {gates.verdict(ok)}" for name, ok in g.items()))
    print(f"M5 schema-valid first try {result['m5_schema_valid']}, agreement across runs "
          f"{result['m5_agreement']} -> {gates.verdict(result['gates']['m5'])}")
    for k, acc in enumerate(result.get("listening") or [], 1):
        print(f"listening set, run {k}: {acc}")
    cost = sum(o.get("cost_usd") or 0.0 for o in outputs)
    print(f"cost read from usage: ${cost:.2f}")
    print(f"Step 3 ({arm}): {gates.verdict(result['passed'])}")


def main(argv: list[str] | None = None) -> int:
    from audit.compare import load_verdicts
    from audit.docs import documents_by_id

    parser = argparse.ArgumentParser(description="Faz 3.5 Step 3: the summarizer A/B.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--doc", type=Path, required=True, help="the 2.29 dump")
    common.add_argument("--verdicts", type=Path, required=True)
    common.add_argument("--label", default=None,
                        help="the version label of the finder/moment files (default: last)")
    common.add_argument("--runs", type=int, default=gates.SHUFFLED_RUNS)
    p_count = sub.add_parser("count", parents=[common], help="Step 0: tokens per pack")
    p_count.add_argument("--claude", action="store_true")
    p_count.add_argument("--local", help="llama.cpp server URL")
    p_sub = sub.add_parser("submit", parents=[common], help="one Message Batch")
    p_sub.add_argument("--arm", required=True)
    p_sub.add_argument("--phase", choices=PHASES, required=True)
    p_sub.add_argument("--pictures", action="store_true")
    p_sub.add_argument("--effort", default=EFFORT,
                       choices=("low", "medium", "high", "xhigh", "max"))
    p_col = sub.add_parser("collect", parents=[common], help="write ended batches' results")
    p_col.add_argument("--arm", required=True)
    p_loc = sub.add_parser("local", parents=[common], help="the local server, both requests")
    p_loc.add_argument("--arm", required=True)
    p_loc.add_argument("--url", required=True)
    p_ev = sub.add_parser("evaluate", help="M1-M5 against the labels")
    p_ev.add_argument("--verdicts", type=Path, required=True)
    p_ev.add_argument("--arm", required=True)
    p_ev.add_argument("--runs", type=int, default=gates.SHUFFLED_RUNS)
    p_ev.add_argument("--listening", type=Path, help="the owner's listening set (same format)")
    p_ev.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "evaluate":
        from audit.step3 import evaluate, labels_from

        labels = labels_from(load_verdicts(args.verdicts))
        listening = labels_from(load_verdicts(args.listening)) if args.listening else None
        runs = _outputs(args.arm, args.runs)
        result = evaluate(labels, runs, listening)
        _print_eval(args.arm, result, (o for r in runs for o in r))
        if args.json:
            args.json.write_text(json.dumps(result, indent=1) + "\n")
        return 0 if result["passed"] else 1

    docs = documents_by_id(json.loads(args.doc.read_text()))
    songs = load_songs(docs, load_verdicts(args.verdicts), args.label)
    if args.cmd == "count":
        import httpx

        claude = anthropic_client() if args.claude else None
        local = httpx.Client(base_url=args.local, timeout=60) if args.local else None
        over = 0
        for song in songs:
            pack = pack_for(song, 1)
            row = [f"{song.id[:14]:14} {len(pack.moment_ids):2} moments"]
            if claude is not None:
                n = count_claude(claude, pack)
                over += n > gates.INPUT_MAX_TOKENS
                row.append(f"claude {n}")
            if local is not None:
                n = count_local(local, pack)
                over += n > gates.INPUT_MAX_TOKENS
                row.append(f"local {n}")
            print("  ".join(row))
        print(f"{len(songs)} songs, {over} counts above {gates.INPUT_MAX_TOKENS} tokens")
        return 0 if over == 0 else 1
    if args.cmd == "local":
        import httpx

        with httpx.Client(base_url=args.url, timeout=600) as http:
            print(f"{run_local(http, args.arm, songs, args.runs)} songs answered")
        return 0
    client = anthropic_client()
    if args.cmd == "submit":
        batch = submit(client, args.arm, args.phase, songs, args.runs, pictures=args.pictures,
                       effort=args.effort)
        print(f"batch {batch}" if batch else "nothing to submit")
        return 0
    counts = collect(client, args.arm, songs)
    print(", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "no batch to collect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
