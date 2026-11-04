"""audit.summarize: packs, the two backends and the M1-M5 score.

No API call is made: the Anthropic SDK talks to a fake Batches server through
httpx2.MockTransport, llama-server is an httpx.MockTransport. Synthetic songs,
no real ids or lyrics."""

import json
import re

import httpx
import httpx2
import pytest

from audit import summarize as S

VID = "synthVid001"


# --- synthetic moment files ------------------------------------------------------

def _row(line, start, voice, text=None, words=3, zero=0, offset=200):
    flags = [["zero"]] * zero + [[]] * (words - zero)
    row = {"line": line, "text": text or f"pseudo line {line}", "start_ms": start,
           "screen_lights_ms": start - offset, "nearest_voice_onset_ms": voice,
           "level_db_at_start": -10.0, "nearest_onset_ms": start,
           "words": [{"text": f"w{i}", "start_ms": start + 100 * i, "dur_ms": 0 if f else 90,
                      "flags": f} for i, f in enumerate(flags)],
           "syllables": 4, "bumps_ms": [start, start + 200, start + 400],
           "bump_mapping": "fewer bumps",
           "ear_letters": "kala mito"}
    if voice is not None:
        row["doc_minus_voice_ms"] = start - voice
        row["screen_minus_voice_ms"] = start - offset - voice
    return row


def _numbers(t, rows_new, rows_old=None):
    versions = {}
    if rows_old is not None:
        versions["2.28"] = {"pipeline_version": "2.28.0", "lines": rows_old}
    versions["2.29"] = {"pipeline_version": "2.29.0", "lines": rows_new}
    return {"id": VID, "t_ms": t, "offset_ms": 200, "versions": versions,
            "voice_onsets_ms": [t - 400, t + 900], "beats_ms": [], "bar_starts_ms": [],
            "ear": {"model": "synthetic", "text": "kala mito rasu"}}


MOMENTS = [10_000, 30_000, 50_000, 70_000]


def _song_dir(tmp_path, vid=VID, extra=None):
    d = tmp_path / "runs" / vid
    d.mkdir(parents=True)
    finder = {"id": vid, "moments": [
        {"t_ms": 30_000, "score": 4.5, "tags": {"line_vs_voice": 1}, "versions": ["2.29"],
         "events": [{"t_ms": 30_000, "tag": "line_vs_voice", "version": "2.29",
                     "detail": "+940 ms: pseudo line 4", "size": 1}]},
        {"t_ms": 50_000, "score": 2.0, "tags": {"pile": 1}, "versions": ["2.28"],
         "events": [{"t_ms": 50_000, "tag": "pile", "version": "2.28", "detail": "3: a b c",
                     "size": 2}]},
        {"t_ms": 70_000, "score": 1.0, "tags": {"zero": 1}, "versions": ["2.29"], "events": []},
    ], "controls": [10_000, 90_000]}
    (d / "moments.json").write_text(json.dumps(finder))
    for k, t in enumerate(MOMENTS):
        new = [_row(3 * k + i, t + 1000 * i, t + 1000 * i - (60 + 300 * k + 40 * i))
               for i in range(3)]
        old = [dict(r, start_ms=r["start_ms"] - 120) for r in new]
        numbers = _numbers(t, new, old)
        if extra:
            numbers |= extra
        (d / f"moment-{t}.json").write_text(json.dumps(numbers))
    (d / "moment-30000-real.json").write_text("{}")   # a suffixed variant: not a moment
    return d


def _pack(tmp_path, kind="review", seed=None, extra=None):
    d = _song_dir(tmp_path, extra=extra)
    moments, files = S.load_song(d)
    return S.build_pack(VID, moments, files, kind, seed)


def test_constants_are_pinned():
    assert S.MODEL == "claude-opus-5-5" and S.DEFAULT_EFFORT == "medium"
    assert S.EFFORTS == ("low", "medium", "high", "xhigh", "max")
    assert (S.MAX_TOKENS, S.TOKEN_TARGET, S.LOCAL_CONTEXT) == (16_000, 12_000, 16_384)
    assert S.PRICES["claude-opus-5-5"] == {"input": 2.0, "output": 10.0, "cache_write_5m": 2.5,
                                          "cache_write_1h": 4.0, "cache_read": 0.10}
    assert S.POSITIVE_SEVERITY == 1
    assert S.LABEL_SEVERITY == {"ok": 0, "pile": 1, "late": 2, "early": 2, "missing": 3,
                                "wrong_text": 3}
    assert S.REAL == {"pile", "late", "early", "missing", "wrong_text"}
    assert S.VISIBLE == {"late", "early", "missing", "wrong_text"}
    assert (S.M1_MIN_RECALL, S.M1_MIN_PRECISION) == (0.90, 0.60)
    assert (S.M2_MIN_SPEARMAN, S.M2_MIN_PRECISION_AT, S.PRECISION_AT) == (0.5, 0.7, 3)
    assert S.M3_MIN_RECALL == 0.7
    assert (S.M4_MIN_SHARE, S.PROBE_TOLERANCE_MS) == (0.95, 10)
    assert (S.M5_MIN_VALID, S.M5_MIN_AGREEMENT) == (0.99, 0.90)


def test_the_schema_file_holds_the_classes():
    review = S.SCHEMAS["review"]["properties"]["moments"]["items"]
    assert review["properties"]["class"]["enum"] == list(S.CLASSES)
    assert review["properties"]["severity"]["enum"] == [0, 1, 2, 3]
    assert set(review["required"]) == {"t_ms", "class", "severity", "reason"}
    assert set(S.SCHEMAS["probe"]["required"]) == {"line", "value_ms"}


# --- pack ------------------------------------------------------------------------

def test_the_pack_carries_precomputed_differences_and_asks_last(tmp_path):
    pack = _pack(tmp_path)
    text = pack["user"]
    assert pack["moments"] == MOMENTS
    assert text.endswith(S.QUESTION["review"])
    assert "t=30000 ms (0:30.0) - finder rank 1, score 4.5" in text
    assert "t=10000 ms (0:10.0) - control" in text
    assert "line_vs_voice [2.29] +940 ms: pseudo line 4" in text
    # doc 31000, voice 31000 - 400: doc-voice +400, screen-voice +200
    assert "L4 \"pseudo line 4\" | doc 31000 | screen 30800 | voice 30600 | doc-voice +400 | " \
           "screen-voice +200" in text
    assert "delta doc vs previous version +120" in text
    assert "words 3 (0 ms 0, <60 ms 0, piled 0)" in text
    assert "syllables 4, loudness bumps 3 (fewer bumps)" in text
    assert pack["system"] == S.SYSTEM and "never recompute" in S.SYSTEM


def test_no_verdict_ever_enters_a_pack(tmp_path):
    d = _song_dir(tmp_path, extra={"verdict": "late", "claim": "LEAKCLAIM", "note": "LEAKNOTE"})
    (d / "review.jsonl").write_text(json.dumps({"id": VID, "t_ms": 30_000, "verdict": "late"}))
    moments, files = S.load_song(d)
    files[30_000]["versions"]["2.29"]["lines"][0]["caner"] = "LEAKOWNER"
    pack = S.build_pack(VID, moments, files)
    blob = json.dumps(pack)
    for leak in ("LEAKCLAIM", "LEAKNOTE", "LEAKOWNER", "verdict"):
        assert leak not in blob


def test_pack_size_stays_under_the_target(tmp_path):
    pack = _pack(tmp_path)
    assert pack["est_tokens"] <= S.TOKEN_TARGET
    assert pack["est_tokens"] == -(-(len(S.SYSTEM) + len(pack["user"])) // 3.5)


def test_a_seed_shuffles_the_moment_order(tmp_path):
    a = _pack(tmp_path / "a", seed=1)
    b = _pack(tmp_path / "b", seed=1)
    c = _pack(tmp_path / "c", seed=2)
    assert a["moments"] == b["moments"] and a["user"] == b["user"]
    assert sorted(a["moments"]) == MOMENTS and a["moments"] != MOMENTS
    assert a["moments"] != c["moments"] and a["prompt_hash"] != c["prompt_hash"]
    blocks = re.findall(r"### t=(\d+) ms", a["user"])
    assert [int(t) for t in blocks] == a["moments"]


def test_probe_answer_is_the_unique_second_largest_of_the_last_version():
    def files(values, old=None):
        rows = [_row(i, 1000 * i, None) | {"screen_minus_voice_ms": v} for i, v in values]
        return {1: _numbers(1, rows, old)}
    assert S.probe_answer(files([(0, 100), (1, -900), (2, 400)])) == {"line": 2, "value_ms": 400}
    assert S.probe_answer(files([(0, 900), (1, -900), (2, 400)])) is None      # tie on top
    assert S.probe_answer(files([(0, 900), (1, 400), (2, -400)])) is None      # tie at second
    # the older version never counts
    old = [_row(9, 0, None) | {"screen_minus_voice_ms": 5000}]
    assert S.probe_answer(files([(0, 100), (1, 300)], old)) == {"line": 0, "value_ms": 100}
    # a line in two windows counts once
    rows = [_row(0, 0, None) | {"screen_minus_voice_ms": 700},
            _row(1, 0, None) | {"screen_minus_voice_ms": 300}]
    two = {1: _numbers(1, rows), 2: _numbers(2, rows[:1])}
    assert S.probe_answer(two) == {"line": 1, "value_ms": 300}


def test_probe_pack(tmp_path):
    pack = _pack(tmp_path, kind="probe")
    assert pack["user"].endswith(S.QUESTION["probe"]) and pack["convert"] == S.CONVERT["probe"]
    # |screen-voice| of moment k, line i: 60 + 300k + 40i - 200; second largest: k=3, i=1
    assert pack["probe_answer"] == {"line": 10, "value_ms": 60 + 900 + 40 - 200}


# --- the fake Batches server -------------------------------------------------------

def _message(text, stop="end_turn", usage=None):
    return {"id": "msg_x", "type": "message", "role": "assistant", "model": S.MODEL,
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                        {"type": "text", "text": text}],
            "stop_reason": stop, "stop_sequence": None,
            "stop_details": {"type": "refusal", "category": "cyber", "explanation": "x"}
            if stop == "refusal" else None,
            "usage": usage or {"input_tokens": 1000, "output_tokens": 500,
                               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


class FakeBatches:
    def __init__(self, behave, polls_until_ended=2):
        self.behave, self.polls_until_ended = behave, polls_until_ended
        self.batches: dict[str, list] = {}
        self.polls: dict[str, int] = {}
        self.headers: list[dict] = []

    def _batch(self, bid, status):
        return {"id": bid, "type": "message_batch", "processing_status": status,
                "request_counts": {"processing": 0, "succeeded": 0, "errored": 0, "canceled": 0,
                                   "expired": 0},
                "created_at": "2026-11-04T00:00:00Z", "expires_at": "2026-11-05T00:00:00Z",
                "ended_at": None, "archived_at": None, "cancel_initiated_at": None,
                "results_url": f"https://api.anthropic.com/v1/messages/batches/{bid}/results"}

    def handler(self, request):
        self.headers.append(dict(request.headers))
        path = request.url.path
        if request.method == "POST" and path == "/v1/messages/batches":
            bid = f"msgbatch_{len(self.batches) + 1}"
            self.batches[bid] = json.loads(request.content)["requests"]
            return httpx2.Response(200, json=self._batch(bid, "in_progress"))
        if request.method == "POST" and path == "/v1/messages/count_tokens":
            body = json.loads(request.content)
            return httpx2.Response(200, json={"input_tokens": len(body["messages"][0]["content"])})
        m = re.fullmatch(r"/v1/messages/batches/(msgbatch_\d+)(/results)?", path)
        assert m, path
        bid = m.group(1)
        if m.group(2):
            lines = [{"custom_id": r["custom_id"],
                      "result": self.behave(r["custom_id"], r["params"])}
                     for r in reversed(self.batches[bid])]          # any order: reversed
            return httpx2.Response(200, content="\n".join(json.dumps(x) for x in lines).encode(),
                                   headers={"content-type": "application/binary"})
        self.polls[bid] = self.polls.get(bid, 0) + 1
        ended = self.polls[bid] >= self.polls_until_ended
        return httpx2.Response(200, json=self._batch(bid, "ended" if ended else "in_progress"))


def _client(fake):
    import anthropic

    return anthropic.Anthropic(api_key="sk-test-SECRETKEY",
                               http_client=httpx2.Client(transport=httpx2.MockTransport(
                                   fake.handler)), max_retries=0)


def _packs(tmp_path, n=6):
    out = []
    for k in range(n):
        vid = f"synthVid00{k}"
        d = _song_dir(tmp_path, vid)
        moments, files = S.load_song(d)
        out.append(S.build_pack(vid, moments, files))
    return out


def _answer(params, cls="late"):
    t_list = [int(t) for t in re.findall(r"### t=(\d+) ms", params["messages"][0]["content"])]
    return json.dumps({"moments": [{"t_ms": t, "class": cls, "severity": 2, "reason": "r"}
                                   for t in t_list]})


def _behave(custom_id, params):
    stage, vid = custom_id.split("-", 1)
    k = int(vid[-1])
    if stage == "r1":
        return {0: {"type": "succeeded", "message": _message("prose for " + vid)},
                1: {"type": "errored", "error": {"type": "error", "error": {
                    "type": "invalid_request_error", "message": "bad request"}}},
                2: {"type": "expired"},
                3: {"type": "succeeded", "message": _message("", stop="refusal")},
                4: {"type": "succeeded", "message": _message("prose for " + vid)},
                5: {"type": "canceled"}}[k]
    if k == 4:
        return {"type": "succeeded", "message": _message(_answer(params, cls="bogus"))}
    return {"type": "succeeded", "message": _message(_answer(params))}


def test_batch_backend_end_to_end(tmp_path):
    fake = FakeBatches(_behave)
    sleeps = []
    backend = S.BatchBackend(_client(fake), sleep=sleeps.append, poll_s=5)
    packs = _packs(tmp_path)
    out = tmp_path / "out"
    first, second = S.todo(packs, out, backend.settings())
    assert len(first) == 6 and not second
    summary = backend.run(first, second, out, log=lambda s: None)
    assert summary["r1"] == {"succeeded": 3, "errored": 1, "expired": 1, "canceled": 1,
                             "refusal": 1}
    assert summary["r2"] == {"succeeded": 2}
    assert summary["status"] == {"ok": 1, "errored": 1, "expired": 1, "refusal": 1,
                                 "schema_invalid": 1, "canceled": 1}
    assert sleeps == [5, 5]                                     # one in_progress poll per batch
    # request shapes: thinking adaptive, effort explicit, no sampling, no fallbacks
    r1, r2 = fake.batches["msgbatch_1"], fake.batches["msgbatch_2"]
    assert [r["custom_id"] for r in r1] == [f"r1-synthVid00{k}" for k in range(6)]
    assert [r["custom_id"] for r in r2] == ["r2-synthVid000", "r2-synthVid004"]
    for req in r1 + r2:
        p = req["params"]
        assert p["model"] == "claude-opus-5-5" and p["thinking"] == {"type": "adaptive"}
        assert p["output_config"]["effort"] == "medium"
        assert not {"temperature", "top_p", "top_k", "fallbacks", "betas"} & set(p)
        assert p["messages"][-1]["role"] == "user"               # no prefill
    assert "format" not in r1[0]["params"]["output_config"]
    p2 = r2[0]["params"]
    assert p2["output_config"]["format"] == {"type": "json_schema", "schema": S.SCHEMAS["review"]}
    assert p2["messages"][1] == {"role": "assistant", "content": "prose for synthVid000"}
    assert p2["messages"][2]["content"] == S.CONVERT["review"]
    assert p2["messages"][0] == r1[0]["params"]["messages"][0]    # the same prefix
    rec = json.loads((out / "synthVid000.json").read_text())
    assert rec["status"] == "ok" and rec["schema_valid"] and rec["batches"] == {
        "r1": "msgbatch_1", "r2": "msgbatch_2"}
    assert rec["model"] == S.MODEL and rec["prompt_hash"] == packs[0]["prompt_hash"]
    assert rec["settings"] == {"model": S.MODEL, "effort": "medium", "max_tokens": 16_000,
                               "thinking": "adaptive"}
    # usage: 1000 in + 500 out per request at Batches prices, two requests
    assert rec["usage"]["r1"]["input_tokens"] == 1000
    assert rec["cost_usd"] == pytest.approx(2 * (1000 * 2.0 + 500 * 10.0) / 1e6)
    bad = json.loads((out / "synthVid004.json").read_text())
    assert bad["status"] == "schema_invalid" and bad["schema_valid"] is False
    refused = json.loads((out / "synthVid003.json").read_text())
    assert refused["status"] == "refusal" and "cyber" in refused["problem"]
    errored = json.loads((out / "synthVid001.json").read_text())
    assert errored["status"] == "errored" and "invalid_request_error" in errored["problem"]
    # the key went out as a header only, never into a file
    assert all("SECRETKEY" not in p.read_text() for p in out.rglob("*"))
    assert not list(out.glob(".batch-*"))
    # a rerun only retries the transient failures (errored, expired, canceled)
    first, second = S.todo(packs, out, backend.settings())
    assert sorted(p["id"] for p in first) == ["synthVid001", "synthVid002", "synthVid005"]
    first, _ = S.todo(packs, out, backend.settings(), retry_failed=True)
    assert len(first) == 5
    # other settings mean another run: everything again
    assert len(S.todo(packs, out, {"effort": "high"})[0]) == 6


def test_batch_resumes_a_submitted_batch_and_request_2(tmp_path):
    fake = FakeBatches(_behave, polls_until_ended=1)
    backend = S.BatchBackend(_client(fake), sleep=lambda s: None)
    packs = _packs(tmp_path, n=1)
    out = tmp_path / "out"
    # request 1 finished earlier (prose on disk): only request 2 runs
    rec = S._record(packs[0], "batch", S.MODEL, backend.settings()) | {
        "status": "prose", "prose": "earlier prose"}
    S.write_atomic(out / "synthVid000.json", rec)
    first, second = S.todo(packs, out, backend.settings())
    assert not first and [p["id"] for p, _ in second] == ["synthVid000"]
    # ... and its batch was already submitted before a crash: polled, not resubmitted
    fake.batches["msgbatch_7"] = [{"custom_id": "r2-synthVid000",
                                   "params": backend.params(packs[0], "earlier prose")}]
    S.write_atomic(out / ".batch-r2.json", {"batch_id": "msgbatch_7",
                                           "custom_ids": ["r2-synthVid000"]})
    backend.run(first, second, out, log=lambda s: None)
    assert list(fake.batches) == ["msgbatch_7"]
    done = json.loads((out / "synthVid000.json").read_text())
    assert done["status"] == "ok" and done["batches"] == {"r2": "msgbatch_7"}


def test_the_key_comes_from_the_environment_only(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        S.make_anthropic_client()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-x")
    assert S.make_anthropic_client().api_key == "sk-test-x"


def test_count_tokens_per_pack(tmp_path):
    fake = FakeBatches(_behave)
    packs = _packs(tmp_path, n=2)
    got = S.count_tokens(_client(fake), packs)
    assert got["songs"] == {p["id"]: len(p["user"]) for p in packs}
    assert got["total"] == sum(len(p["user"]) for p in packs)
    assert got["over_target"] == []


def test_cost_counts_cache_writes_and_reads():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
             "cache_creation": {"ephemeral_1h_input_tokens": 400_000}}
    assert S.cost_usd(usage, S.MODEL) == pytest.approx(2 + 10 + 0.6 * 2.5 + 0.4 * 4 + 0.1)
    assert S.cost_usd(usage, "other-model") is None


# --- the local backend -----------------------------------------------------------

def test_local_backend_two_requests(tmp_path):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" not in body:
            return httpx.Response(200, json={"choices": [{"message": {"content": "prose"}}],
                                             "usage": {"prompt_tokens": 9}})
        params = {"messages": [{"content": body["messages"][1]["content"]}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": _answer(params)}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = S.LocalBackend("http://llama:8080/", "gemma", client=client)
    packs = _packs(tmp_path, n=1)
    out = tmp_path / "out"
    counts = backend.run(packs, [], out, log=lambda s: None)
    assert counts == {"ok": 1}
    one, two = bodies
    assert one["temperature"] == 0 and one["chat_template_kwargs"] == {"enable_thinking": False}
    assert [m["role"] for m in one["messages"]] == ["system", "user"]
    assert [m["role"] for m in two["messages"]] == ["system", "user", "assistant", "user"]
    assert two["response_format"]["json_schema"]["schema"] == S.SCHEMAS["review"]
    rec = json.loads((out / "synthVid000.json").read_text())
    assert rec["status"] == "ok" and rec["usage"]["r1"] == {"prompt_tokens": 9}
    assert rec["settings"]["url"] == "http://llama:8080" and rec["cost_usd"] is None


def test_local_backend_records_an_http_error_and_resumes_from_prose(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 2:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "prose"}}]})

    backend = S.LocalBackend("http://llama:8080", "gemma",
                             client=httpx.Client(transport=httpx.MockTransport(handler)))
    packs = _packs(tmp_path, n=1)
    out = tmp_path / "out"
    backend.run(packs, [], out, log=lambda s: None)
    rec = json.loads((out / "synthVid000.json").read_text())
    assert rec["status"] == "errored" and rec["prose"] == "prose"


def test_answers_are_validated_and_must_cover_every_moment(tmp_path):
    pack = _pack(tmp_path)
    rec = S._record(pack, "x", "m", {})
    S._finish(rec, pack, "not json")
    assert rec["status"] == "invalid_json" and rec["json"] is None
    S._finish(rec, pack, json.dumps({"moments": [{"t_ms": 1, "class": "ok", "severity": 4,
                                                   "reason": ""}]}))
    assert rec["status"] == "schema_invalid"
    partial = [{"t_ms": t, "class": "ok", "severity": 0, "reason": ""} for t in MOMENTS[:3]]
    S._finish(rec, pack, json.dumps({"moments": partial}))
    assert rec["schema_valid"] and rec["status"] == "schema_invalid" and "pack" in rec["problem"]
    full = partial + [{"t_ms": MOMENTS[3], "class": "ok", "severity": 0, "reason": ""}]
    S._finish(rec, pack, json.dumps({"moments": full}))
    assert rec["status"] == "ok"


# --- score -----------------------------------------------------------------------

def _labels_file(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def _run_of(preds, kind="review"):
    """{song: [(t, class, severity)]} -> a run (records as the backends write them)."""
    return {song: {"kind": kind, "status": "ok", "schema_valid": True,
                   "json": {"moments": [{"t_ms": t, "class": c, "severity": s, "reason": ""}
                                        for t, c, s in rows]}}
            for song, rows in preds.items()}


def test_labels_drop_unsure_and_disputed_and_a_later_file_wins(tmp_path):
    a = _labels_file(tmp_path / "a.jsonl", [
        {"id": "s", "t_ms": 1, "verdict": "late"},
        {"id": "s", "t_ms": 2, "verdict": "unsure"},
        {"id": "s", "t_ms": 3, "verdict": "pile", "agreement": "disagree"},
        {"id": "s", "t_ms": 4, "verdict": "ok", "agreement": "partial", "caner": "late really"},
    ])
    b = _labels_file(tmp_path / "b.jsonl", [{"id": "s", "t_ms": 1, "verdict": "ok"}])
    assert S.load_labels([a]) == {("s", 1): "late", ("s", 4): "ok"}
    assert S.load_labels([a, b]) == {("s", 1): "ok", ("s", 4): "ok"}


def _m1_case(n_real, hits, false_pos):
    labels = {("s", t): "late" for t in range(n_real)}
    labels |= {("s", 1000 + t): "ok" for t in range(20)}
    preds = [(t, "late", 2 if t < hits else 0) for t in range(n_real)]
    preds += [(1000 + t, "late" if t < false_pos else "ok", 2 if t < false_pos else 0)
              for t in range(20)]
    return S.review_metrics(_run_of({"s": preds}), labels)


def test_m1_recall_090_and_precision_060():
    m = _m1_case(10, 9, 6)          # recall 0.9, precision 9/15 = 0.6
    assert (m["m1_recall"], m["m1_precision"], m["gates"]["m1"]) == (0.9, 0.6, True)
    assert _m1_case(10, 8, 0)["gates"]["m1"] is False                 # recall 0.8
    assert _m1_case(10, 9, 7)["gates"]["m1"] is False                 # precision 9/16


def test_severity_1_is_already_a_flag():
    m = S.review_metrics(_run_of({"s": [(1, "pile", 1)]}), {("s", 1): "pile"})
    assert m["m1_recall"] == 1.0


def test_m2_needs_both_spearman_and_precision_at_3():
    # the three real errors on top, but piles ranked under oks: p@3 = 1, Spearman low
    labels = {("a", 0): "late", ("a", 1): "missing", ("a", 2): "wrong_text"}
    labels |= {("a", 10 + k): "pile" for k in range(4)} | {("a", 20 + k): "ok" for k in range(4)}
    preds = [(0, "late", 3), (1, "missing", 3), (2, "wrong_text", 3)]
    preds += [(10 + k, "ok", 0) for k in range(4)] + [(20 + k, "late", 2) for k in range(4)]
    m = S.review_metrics(_run_of({"a": preds}), labels)
    assert m["m2_precision_at_3"] == 1.0 and m["m2_spearman"] < 0.5 and not m["gates"]["m2"]
    # well ranked overall (Spearman high), but one song's top three are its oks
    labels = {("a", k): "late" for k in range(3)} | {("a", 10 + k): "ok" for k in range(3)}
    preds = {"a": [(k, "late", 2) for k in range(3)] + [(10 + k, "late", 3) for k in range(3)]}
    order = ["ok", "pile", "late", "missing"] * 6
    labels |= {("b", k): v for k, v in enumerate(order)}
    preds["b"] = [(k, v, S.LABEL_SEVERITY[v]) for k, v in enumerate(order)]
    m = S.review_metrics(_run_of(preds), labels)
    assert m["m2_spearman"] >= 0.5 and m["m2_precision_at_3"] < 0.7 and not m["gates"]["m2"]


def test_a_moment_without_a_prediction_counts_as_not_flagged():
    labels = {("s", 1): "late", ("s", 2): "late"}
    m = S.review_metrics(_run_of({"s": [(1, "late", 3)]}), labels)
    assert m["m1_recall"] == 0.5 and m["no_prediction"] == 1


def test_spearman_with_ties():
    assert S.spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert S.spearman([1, 1, 2, 2], [0, 1, 2, 3]) == pytest.approx(0.894, abs=1e-3)
    assert S.review_metrics(_run_of({"s": [(1, "late", 2)]}), {("s", 1): "late"})[
        "m2_spearman"] is None
    assert S.spearman([1, 1], [0, 1]) is None


def test_precision_at_3_shares_a_tie_at_the_cut():
    assert S.precision_at([(3, True), (3, True), (3, True), (0, False)]) == 1.0
    # two certain hits, then one slot among a tie of (real, ok)
    assert S.precision_at([(3, True), (3, True), (1, True), (1, False)]) == pytest.approx(
        (2 + 0.5) / 3)


def test_m2_gate_boundaries():
    labels = {("a", t): v for t, v in enumerate(["late", "missing", "pile", "ok", "ok"])}
    preds = {"a": [(0, "late", 2), (1, "missing", 3), (2, "pile", 1), (3, "ok", 0), (4, "ok", 0)]}
    m = S.review_metrics(_run_of(preds), labels)
    assert m["m2_spearman"] == pytest.approx(1.0) and m["m2_precision_at_3"] == 1.0
    assert m["gates"]["m2"] is True
    # top three are one real and two oks: precision@3 = 1/3
    preds = {"a": [(0, "late", 3), (1, "missing", 0), (2, "pile", 0), (3, "ok", 3), (4, "ok", 3)]}
    assert S.review_metrics(_run_of(preds), labels)["gates"]["m2"] is False


def test_m3_is_the_recall_of_the_visible_classes():
    labels = {("s", t): "late" for t in range(10)} | {("s", 100): "pile"}
    run = _run_of({"s": [(t, "late", 2 if t < 7 else 0) for t in range(10)] + [(100, "ok", 0)]})
    m = S.review_metrics(run, labels)
    assert (m["m3_recall"], m["m3_visible"], m["gates"]["m3"]) == (0.7, 10, True)
    assert m["pile_recall"] == 0.0
    run = _run_of({"s": [(t, "late", 2 if t < 6 else 0) for t in range(10)]})
    assert S.review_metrics(run, labels)["gates"]["m3"] is False


def _probe_run(n, correct, off_by=0):
    out = {}
    for k in range(n):
        ok = k < correct
        out[f"s{k}"] = {"kind": "probe", "status": "ok", "schema_valid": True,
                        "probe_answer": {"line": 4, "value_ms": 500},
                        "json": {"line": 4 if ok else 5, "value_ms": 500 + off_by}}
    return out


def test_m4_needs_19_of_20_within_10_ms():
    assert S.probe_metrics(_probe_run(20, 19))["gate"] is True
    assert S.probe_metrics(_probe_run(20, 18))["gate"] is False
    assert S.probe_metrics(_probe_run(20, 20, off_by=10))["gate"] is True
    assert S.probe_metrics(_probe_run(20, 20, off_by=11))["gate"] is False
    run = _probe_run(3, 3) | {"tie": {"kind": "probe", "probe_answer": None}}
    assert S.probe_metrics(run)["no_unique_answer"] == 1


def test_m5_validity_and_agreement():
    good = _run_of({"s": [(1, "late", 2), (2, "ok", 0)]})
    st = S.stability([good | {f"x{k}": {"status": "ok"} for k in range(98)}
                      | {"bad": {"status": "schema_invalid"}}])
    assert st["first_try_valid"] == 0.99 and st["gates"]["valid"] is True
    st = S.stability([good | {"bad": {"status": "schema_invalid"}}
                      | {f"x{k}": {"status": "ok"} for k in range(97)}])
    assert st["first_try_valid"] == pytest.approx(98 / 99) and st["gates"]["valid"] is False
    # errored or refused requests never answered: not counted
    st = S.stability([good | {"e": {"status": "errored"}}])
    assert st["answered"] == 1
    other = _run_of({"s": [(1, "late", 2), (2, "pile", 1)]})
    st = S.stability([good, good, other])
    # pairs: (good, good) 2 same, (good, other) x2: 1 same each -> 4 of 6
    assert st["agreement"] == pytest.approx(4 / 6, abs=1e-3) and st["gates"]["agreement"] is False
    ten = {f"t{k}": (k, "late", 2) for k in range(10)}
    a = _run_of({"s": list(ten.values())})
    b = _run_of({"s": [(k, "late" if k else "ok", 2) for k in range(10)]})
    assert S.stability([a, b])["agreement"] == 0.9 and S.stability([a, b])["gates"]["agreement"]


def test_score_gates_on_the_first_run_and_needs_the_probe(tmp_path):
    labels = {("s", t): "late" for t in range(10)} | {("s", t): "ok" for t in range(10, 20)}
    good = _run_of({"s": [(t, "late", 2) for t in range(10)]
                    + [(t, "ok", 0) for t in range(10, 20)]})
    weak = _run_of({"s": [(t, "late", 0) for t in range(20)]})
    res = S.score([good, good], labels, _probe_run(20, 20))
    assert res["passed"] and res["gates"]["m1"]
    assert S.score([weak, good], labels, _probe_run(20, 20))["gates"]["m1"] is False
    assert S.score([good, good], labels, None)["passed"] is False


# --- CLI ---------------------------------------------------------------------------

def test_cli_pack_and_score(tmp_path, capsys):
    _song_dir(tmp_path)
    packs = tmp_path / "packs"
    assert S.main(["pack", "--all", "--runs-dir", str(tmp_path / "runs"), "--out", str(packs),
                   "--seed", "3"]) == 0
    assert "synthVid001: 4 moments" in capsys.readouterr().out
    pack = json.loads((packs / f"{VID}.json").read_text())
    assert pack["seed"] == 3 and sorted(pack["moments"]) == MOMENTS
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rec = _run_of({VID: [(t, "late", 2) for t in MOMENTS]})[VID]
    (run_dir / f"{VID}.json").write_text(json.dumps(rec))
    (run_dir / ".run-summary.json").write_text("{}")
    labels = _labels_file(tmp_path / "review.jsonl",
                          [{"id": VID, "t_ms": t, "verdict": "late"} for t in MOMENTS])
    out = tmp_path / "score.json"
    assert S.main(["score", "--runs", str(run_dir), "--labels", str(labels),
                   "--json", str(out)]) == 0
    result = json.loads(out.read_text())
    assert result["runs"][0]["m1_recall"] == 1.0 and result["passed"] is False   # no probe
