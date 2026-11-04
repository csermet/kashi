"""Faz 3.5 Step 3: the pack, the two-request clients (fakes: no network, no
SDK) and the M1-M5 metrics, on synthetic songs and labels."""

import json
import random
import types

import httpx
import pytest

from audit import gates, pack, step3, summarizer
from audit.pack import LineRow, MomentInput, SongInput, build_pack, probe_answer
from audit.step3 import evaluate, labels_from, run_gates, run_metrics, spearman, validate

VID = "synthVid001"


def _row(line, start, doc_minus_voice=None):
    row = {"line": line, "text": f"line {line} words", "start_ms": start,
           "screen_lights_ms": start - 200, "level_db_at_start": -8.0,
           "nearest_voice_onset_ms": None,
           "words": [{"text": f"w{k}", "start_ms": start + 300 * k, "dur_ms": 250, "flags": []}
                     for k in range(20)]}
    if doc_minus_voice is not None:
        row |= {"nearest_voice_onset_ms": start - doc_minus_voice,
                "doc_minus_voice_ms": doc_minus_voice,
                "screen_minus_voice_ms": doc_minus_voice - 200}
    return row


def _song(n_moments=4, vid=VID):
    moments = [MomentInput(10_000 * (k + 1),
                           [{"t_ms": 10_000 * (k + 1), "tag": "pile", "detail": "2: a b"}],
                           {"lines": [_row(k, 10_000 * (k + 1), 900 if k == 0 else 20)],
                            "voice_onsets_ms": [10_000 * (k + 1) - 900]})
               for k in range(n_moments)]
    table = [LineRow(k, f"line {k}", 10_000 * (k + 1), v, "voice")
             for k, v in enumerate([40, -700, 1200, 300])]
    return SongInput(vid, "2.29.0", 12, {"placed": 4, "noticeable": 2, "median_error_ms": 170},
                     {"matched": 9, "lines": 12, "median_ms": 60, "off_1s": 1}, moments, table)


# --- the pack -------------------------------------------------------------------------

def test_the_prompts_are_pre_registered():
    # a changed prompt is a new pre-registration: update this hash on purpose
    assert pack.PROMPT_SHA == "7a26637ba402"
    import jsonschema

    jsonschema.Draft202012Validator.check_schema(pack.SCHEMA)
    assert pack.VERDICT_WORDS == ("ok", "pile", "late", "early", "missing", "wrong_text",
                                  "unsure")


def test_a_songs_pack_is_the_same_in_both_phases():
    song = _song(6)
    assert summarizer.pack_for(song, 1) == summarizer.pack_for(song, 1)
    assert summarizer.pack_for(song, 1).moment_ids != summarizer.pack_for(song, 2).moment_ids


def test_the_shuffle_is_seeded_and_changes_with_the_run():
    song = _song(6)
    a = build_pack(song, random.Random(f"{VID}:1"))
    b = build_pack(song, random.Random(f"{VID}:1"))
    c = build_pack(song, random.Random(f"{VID}:2"))
    assert a == b and a.text != c.text
    assert sorted(a.moment_ids.values()) == [10_000 * (k + 1) for k in range(6)]
    assert a.moment_ids != c.moment_ids


def test_the_pack_carries_ready_differences_and_caps_the_words():
    p = build_pack(_song(1), random.Random(0))
    assert "doc-voice +900, screen-voice +700" in p.text
    assert "lit_minus_voice_ms +1200" in p.text and "(voice)" in p.text
    assert "w15 " in p.text and "w16 " not in p.text and "+4 words" in p.text
    assert "finder events: pile (2: a b) 0:10.0" in p.text
    assert "2 noticeably off (median error +170 ms)" in p.text


def test_the_probe_is_the_second_largest_absolute_and_every_tie():
    rows = [LineRow(k, "", 0, v, "ear") for k, v in enumerate([40, -700, 1200, 300])]
    ids = {k: f"L{k}" for k in range(4)}
    assert probe_answer(rows, ids) == {"lines": ["L1"], "abs_ms": 700}
    tied = rows + [LineRow(4, "", 0, 700, "ear")]
    assert probe_answer(tied, ids | {4: "L4"}) == {"lines": ["L1", "L4"], "abs_ms": 700}
    assert probe_answer(rows[:1], ids) is None


def test_load_song_reads_the_audit_files(tmp_path, monkeypatch):
    import audit.ear
    import audit.paths

    monkeypatch.setattr(audit.paths, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(audit.ear, "EAR_DIR", tmp_path / "ear")
    run = tmp_path / "runs" / VID
    run.mkdir(parents=True)
    (run / "moments.json").write_text(json.dumps({"moments": [{"events": [
        {"t_ms": 10_100, "tag": "pile", "version": "2.29", "detail": "x"},
        {"t_ms": 10_100, "tag": "moved", "version": "2.28", "detail": "y"},
        {"t_ms": 30_000, "tag": "zero", "version": "2.29", "detail": "z"}]}],
        "text_match": {"matched": 1, "lines": 2, "median_ms": 50, "off_1s": 0}}))
    (run / "moment-10000.json").write_text(json.dumps({"versions": {
        "2.28": {"lines": [_row(0, 10_000)]}, "2.29": {"lines": [_row(1, 10_000)]}},
        "voice_onsets_ms": [9100]}))
    doc = {"pipeline_version": "2.29.0", "lines": [{"text": "a", "start_ms": 10_000,
                                                    "end_ms": 11_000}]}
    song = pack.load_song(VID, doc, [10_000, 20_000], label="2.29")
    first, second = song.moments
    assert [e["tag"] for e in first.events] == ["pile"]          # 2.29 only, within 2.5 s
    assert first.numbers is not None and first.numbers["lines"][0]["line"] == 1
    assert second.numbers is None and second.events == []
    assert song.text_match["matched"] == 1 and song.line_table == []   # no ear, no stem


# --- the requests ------------------------------------------------------------------

def test_opus_requests_set_effort_and_send_no_thinking_or_sampling():
    p = build_pack(_song(), random.Random(0))
    prose = summarizer.prose_params(p)
    assert prose["model"] == "claude-opus-5-5" and prose["output_config"] == {"effort": "high"}
    assert not {"thinking", "temperature", "top_p", "top_k"} & set(prose)
    assert prose["system"][1]["text"] == p.text
    assert prose["system"][1]["cache_control"] == {"type": "ephemeral"}
    js = summarizer.json_params(p, "M1 is late.")
    assert js["system"] == prose["system"]                      # one cached prefix
    assert js["output_config"]["format"] == {"type": "json_schema", "schema": pack.SCHEMA}
    assert "<assessment>\nM1 is late.\n</assessment>" in js["messages"][0]["content"][0]["text"]
    assert not {"thinking", "temperature"} & set(js)


def test_pictures_are_named_by_their_moment(tmp_path):
    png = tmp_path / "m.png"
    png.write_bytes(b"\x89PNG")
    content = summarizer.prose_params(build_pack(_song(), random.Random(0)),
                                      [("M2", png)])["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Picture of M2:"}
    assert content[1]["type"] == "image" and content[1]["source"]["media_type"] == "image/png"
    assert content[-1]["text"] == pack.PROSE_TASK


def test_cost_is_read_from_usage_at_batch_prices():
    usage = types.SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000,
                                  cache_creation_input_tokens=200_000,
                                  cache_read_input_tokens=1_000_000)
    # (4 + 2 + 1 + 0.2) / 2
    assert summarizer.cost_usd(usage) == 3.6
    assert summarizer.cost_usd(types.SimpleNamespace(), 1.0) == 0.0


def test_a_refusal_has_no_text():
    def message(stop):
        return types.SimpleNamespace(stop_reason=stop, content=[
            types.SimpleNamespace(type="thinking", thinking=""),
            types.SimpleNamespace(type="text", text="hello")])
    assert summarizer.message_text(message("end_turn")) == "hello"
    assert summarizer.message_text(message("refusal")) is None


# --- Message Batches with a fake client ----------------------------------------------

class FakeBatches:
    """messages.batches as the SDK shapes it: create / retrieve / results."""

    def __init__(self, answer):
        self.answer = answer
        self.created: list[list[dict]] = []
        self.ended = True

    def create(self, requests):
        self.created.append(requests)
        return types.SimpleNamespace(id=f"batch{len(self.created)}")

    def retrieve(self, batch_id):
        return types.SimpleNamespace(processing_status="ended" if self.ended else "in_progress")

    def results(self, batch_id):
        requests = self.created[int(batch_id[5:]) - 1]
        out = []
        for req in reversed(requests):                        # any order
            phase, run, vid = summarizer.parse_custom_id(req["custom_id"])
            text = self.answer(phase, req)
            message = types.SimpleNamespace(
                stop_reason="refusal" if text is None else "end_turn",
                content=[types.SimpleNamespace(type="text", text=text or "")],
                usage=types.SimpleNamespace(input_tokens=1000, output_tokens=100,
                                            cache_creation_input_tokens=0,
                                            cache_read_input_tokens=0))
            result = types.SimpleNamespace(type="succeeded", message=message)
            out.append(types.SimpleNamespace(custom_id=req["custom_id"], result=result))
        return out


def _answer_for(text: str, verdict="late"):
    ids = sorted({m for m in __import__("re").findall(r"\bM\d+\b", text)})
    return json.dumps({"moments": [{"moment": m, "evidence": "e", "verdict": verdict,
                                    "severity": 2} for m in ids],
                       "probe": {"line": "L1", "abs_ms": 700}})


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    import audit.paths

    monkeypatch.setattr(audit.paths, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


def test_two_batches_one_per_phase_and_resume(runs_dir):
    songs = [_song(3, "synthVid001"), _song(3, "synthVid002")]

    def answer(phase, req):
        if phase == "prose":
            return "M1 late, M2 ok, M3 pile."
        return _answer_for(req["params"]["system"][1]["text"])

    batches = FakeBatches(answer)
    client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
    assert summarizer.submit(client, "opus", "prose", songs, 2) == "batch1"
    assert len(batches.created[0]) == 4                       # 2 songs x 2 runs
    assert summarizer.submit(client, "opus", "prose", songs, 2) is None   # pending: not again
    assert summarizer.collect(client, "opus", songs) == {"succeeded": 4}
    assert summarizer.submit(client, "opus", "prose", songs, 2) is None   # done: not again
    assert summarizer.submit(client, "opus", "json", songs, 2) == "batch2"
    summarizer.collect(client, "opus", songs)
    out = json.loads((runs_dir / "step3" / "opus" / "run2" / "synthVid002.json").read_text())
    assert out["valid"] and out["valid_first_try"] and out["prompt_sha"] == pack.PROMPT_SHA
    assert out["prose"] == "M1 late, M2 ok, M3 pile." and out["cost_usd"] == 0.006  # 2 x 0.003
    assert out["moment_ids"] == summarizer.pack_for(songs[1], 2).moment_ids
    assert summarizer.submit(client, "opus", "json", songs, 2) is None


def test_a_pending_batch_is_left_for_later(runs_dir):
    batches = FakeBatches(lambda phase, req: "prose")
    batches.ended = False
    client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
    summarizer.submit(client, "opus", "prose", [_song()], 1)
    assert summarizer.collect(client, "opus", [_song()]) == {"pending": 1}
    assert not (runs_dir / "step3" / "opus" / "run1" / f"{VID}.prose.json").exists()


def test_a_refused_prose_ends_the_song_without_an_answer(runs_dir):
    batches = FakeBatches(lambda phase, req: None)
    client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
    summarizer.submit(client, "opus", "prose", [_song()], 1)
    assert summarizer.collect(client, "opus", [_song()]) == {"no_text": 1, "succeeded": 1}
    assert summarizer.submit(client, "opus", "json", [_song()], 1) is None   # nothing to ask
    out = json.loads((runs_dir / "step3" / "opus" / "run1" / f"{VID}.json").read_text())
    assert out["refused"] and not out["valid"] and out["invalid_reason"] == "no answer"


# --- the local server -------------------------------------------------------------

def test_the_local_server_gets_two_requests_per_song(runs_dir):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "response_format" in body:
            content = _answer_for(body["messages"][0]["content"])
        else:
            content = "M1 late. M2 ok."
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(base_url="http://local", transport=httpx.MockTransport(handler))
    assert summarizer.run_local(http, "gemma", [_song(2)], 1) == 1
    prose_req, json_req = seen
    assert prose_req["temperature"] == 0 and prose_req["cache_prompt"] is True
    assert prose_req["messages"][0]["content"] == json_req["messages"][0]["content"]
    assert "response_format" not in prose_req
    assert json_req["response_format"]["json_schema"]["schema"] == pack.SCHEMA
    assert summarizer.run_local(http, "gemma", [_song(2)], 1) == 0      # resumes: done
    out = json.loads((runs_dir / "step3" / "gemma" / "run1" / f"{VID}.json").read_text())
    assert out["valid"] and out["cost_usd"] == 0.0


def test_token_counts_for_step0():
    p = build_pack(_song(), random.Random(0))
    calls = {}

    def count_tokens(**kw):
        calls.update(kw)
        return types.SimpleNamespace(input_tokens=4321)

    client = types.SimpleNamespace(messages=types.SimpleNamespace(count_tokens=count_tokens))
    assert summarizer.count_claude(client, p) == 4321
    assert calls["model"] == "claude-opus-5-5" and calls["system"][1]["text"] == p.text
    http = httpx.Client(base_url="http://local", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"tokens": list(range(77))})))
    assert summarizer.count_local(http, p) == 77


# --- metrics ----------------------------------------------------------------------

def _output(vid, verdicts, *, valid=True, probe_ok=True):
    """An output answering moments at t = 1000 * k with the given verdicts."""
    ids = {f"M{k}": 1000 * k for k in range(1, len(verdicts) + 1)}
    moments = [{"moment": m, "evidence": "", "verdict": v, "severity": sev}
               for (m, _), (v, sev) in zip(ids.items(), verdicts, strict=True)]
    return {"id": vid, "valid": valid, "valid_first_try": valid, "moment_ids": ids,
            "probe": {"lines": ["L1"], "abs_ms": 700},
            "answer": {"moments": moments, "probe": {"line": "L1" if probe_ok else "L2",
                                                     "abs_ms": 700}}}


def _labels(vid, verdicts):
    return {(vid, 1000 * k): v for k, v in enumerate(verdicts, 1)}


def test_labels_drop_unsure_and_disputed_verdicts():
    records = [{"id": VID, "t_ms": 1, "verdict": "late"},
               {"id": VID, "t_ms": 2, "verdict": "unsure"},
               {"id": VID, "t_ms": 3, "verdict": "ok", "agreement": "disagree"},
               {"id": VID, "t_ms": 4, "verdict": "pile", "agreement": "partial"}]
    assert labels_from(records) == {(VID, 1): "late", (VID, 4): "pile"}


def test_validate_names_what_is_wrong():
    ids, lines = {"M1": 1, "M2": 2}, {"L1": 0}
    good = {"moments": [{"moment": m, "evidence": "", "verdict": "ok", "severity": 0}
                        for m in ("M2", "M1")], "probe": {"line": "L1", "abs_ms": 3}}
    assert validate(good, ids, lines) is None
    assert validate(None, ids, lines) == "no answer"
    assert validate({**good, "probe": {"line": "L9", "abs_ms": 1}}, ids, lines).startswith("probe")
    assert validate({**good, "moments": good["moments"][:1]}, ids, lines).startswith("moments")
    bad = {**good, "moments": [{**good["moments"][0], "verdict": "fine"}, good["moments"][1]]}
    assert validate(bad, ids, lines).startswith("schema")


def test_m1_recall_and_precision_sit_on_their_gates():
    # 10 real errors, 9 hit, 6 false flags among 10 oks -> recall 0.9, precision 0.6
    labels = _labels(VID, ["late"] * 10 + ["ok"] * 10)
    verdicts = [("late", 2)] * 9 + [("ok", 0)] + [("pile", 1)] * 6 + [("ok", 0)] * 4
    m = run_metrics(labels, [_output(VID, verdicts)])
    assert (m["m1_recall"], m["m1_precision"]) == (0.9, 0.6) and run_gates(m)["m1"]
    verdicts = [("late", 2)] * 9 + [("ok", 0)] + [("pile", 1)] * 7 + [("ok", 0)] * 3
    m = run_metrics(labels, [_output(VID, verdicts)])
    assert m["m1_precision"] < 0.6 and not run_gates(m)["m1"]


def test_unsure_is_not_a_flag():
    labels = _labels(VID, ["late", "ok"])
    m = run_metrics(labels, [_output(VID, [("late", 2), ("unsure", 1)])])
    assert (m["flagged"], m["m1_precision"]) == (1, 1.0)


def test_a_probe_needs_the_line_and_its_value():
    out = _output(VID, [("ok", 0)])
    assert step3.probe_correct(out) is True
    out["answer"]["probe"]["abs_ms"] = 701
    assert step3.probe_correct(out) is False
    assert step3.probe_correct({**out, "probe": None}) is None


def test_a_moment_answered_twice_is_invalid():
    ids, lines = {"M1": 1, "M2": 2}, {"L1": 0}
    twice = {"moments": [{"moment": m, "evidence": "", "verdict": "ok", "severity": 0}
                         for m in ("M1", "M1", "M2")], "probe": {"line": "L1", "abs_ms": 3}}
    assert validate(twice, ids, lines).startswith("moments")


def test_a_missing_or_invalid_output_flags_nothing():
    labels = _labels(VID, ["late", "ok"])
    m = run_metrics(labels, [_output(VID, [("late", 2), ("ok", 0)], valid=False)])
    assert (m["hits"], m["flagged"], m["m1_recall"]) == (0, 0, 0.0)
    assert run_metrics(labels, [])["m1_recall"] == 0.0


def test_spearman_with_ties():
    assert spearman([0, 1, 2, 3], [0, 1, 2, 3]) == 1.0
    assert spearman([0, 1, 2, 3], [3, 2, 1, 0]) == -1.0
    assert spearman([0, 0, 2, 2], [1, 1, 3, 3]) == 1.0
    assert spearman([1, 1, 1], [0, 1, 2]) is None
    assert spearman([0, 1, 2], [0, 2, 1]) == 0.5
    assert spearman([0, 0, 1], [0, 1, 2]) == 0.866
    assert spearman([0, 0, 1, 1, 1, 2], [0, 1, 2, 3, 4, 5]) == 0.9258  # = scipy, ties averaged


def test_precision_at_three_is_pooled_over_songs_with_three_errors():
    labels = _labels("a", ["late", "late", "late", "ok", "ok"]) | _labels("b", ["late", "ok"])
    out_a = _output("a", [("late", 3), ("late", 3), ("ok", 0), ("ok", 3), ("ok", 0)])
    out_b = _output("b", [("ok", 0), ("ok", 3)])            # 1 real error: not a p@3 song
    m = run_metrics(labels, [out_a, out_b])
    assert m["m2_precision_at_3"] == round(2 / 3, 4)
    # the three most severe, not the three least
    aligned = _output("a", [("late", 3), ("late", 3), ("late", 3), ("ok", 0), ("ok", 0)])
    assert run_metrics(labels, [aligned, out_b])["m2_precision_at_3"] == 1.0


def test_m3_needs_a_visible_class_and_m4_is_a_share_of_19_in_20():
    labels = _labels(VID, ["late", "early", "missing", "wrong_text", "pile"])
    m = run_metrics(labels, [_output(VID, [("early", 2), ("pile", 1), ("late", 2), ("ok", 0),
                                           ("pile", 1)])])
    assert m["m3_visible_recall"] == 0.5
    outs = [_output(f"s{k}", [("ok", 0)], probe_ok=k != 0) for k in range(20)]
    m = run_metrics({}, outs)
    assert (m["m4_probe_correct"], m["m4_probed"]) == (19, 20) and run_gates(m)["m4"]
    outs = [_output(f"s{k}", [("ok", 0)], probe_ok=k > 1) for k in range(20)]
    assert not run_gates(run_metrics({}, outs))["m4"]


def test_m5_and_the_worst_run_decide():
    labels = _labels(VID, ["late"] * 10)
    same = [_output(VID, [("late", 2)] * 10)]
    flip = [_output(VID, [("late", 2)] * 9 + [("early", 2)])]
    assert evaluate(labels, [same, same, flip])["m5_agreement"] == 0.9
    assert evaluate(labels, [same, same, same])["gates"]["m5"]
    broken = [_output(VID, [("late", 2)] * 10, valid=False)]
    assert evaluate(labels, [same, same, broken])["m5_agreement"] == 0.0  # unanswered disagrees
    assert not evaluate(labels, [same, same])["gates"]["m5"]          # three runs needed
    worse = [_output(VID, [("ok", 0)] * 10)]
    result = evaluate(labels, [same, same, worse])
    assert not result["gates"]["m1"] and not result["passed"]


def test_the_listening_set_is_reported():
    out = [_output(VID, [("late", 2), ("ok", 0), ("pile", 1)])]
    listening = _labels(VID, ["early", "ok", "ok"])
    acc = evaluate({}, [out], listening)["listening"][0]
    assert acc == {"moments": 3, "no_prediction": 0, "error_or_not": round(2 / 3, 4),
                   "exact_class": round(1 / 3, 4)}
    unsure = [_output(VID, [("unsure", 1)])]
    assert evaluate({}, [unsure], _labels(VID, ["ok"]))["listening"][0]["error_or_not"] == 1.0


def test_a_listening_moment_without_a_prediction_is_skipped_not_scored_ok():
    out = [_output(VID, [("late", 2)])]                    # answers (VID, 1000) only
    listening = {(VID, 1000): "late", (VID, 2000): "ok", ("otherVid01", 1000): "ok"}
    acc = evaluate({}, [out], listening)["listening"][0]
    assert acc == {"moments": 1, "no_prediction": 2, "error_or_not": 1.0, "exact_class": 1.0}
    invalid = [_output(VID, [("late", 2)], valid=False)]
    assert evaluate({}, [invalid], listening)["listening"][0] == {"moments": 0,
                                                                  "no_prediction": 3}


def test_the_gate_numbers_are_the_registered_ones():
    assert step3.LABEL_SEVERITY == {"ok": 0, "pile": 1, "late": 2, "early": 2, "missing": 2,
                                    "wrong_text": 2}
    assert (summarizer.MODEL, summarizer.EFFORT, summarizer.MAX_TOKENS) == \
        ("claude-opus-5-5", "high", 16_000)
    assert summarizer.PRICE_PER_MTOK == {"input": 4.0, "output": 20.0, "cache_write": 5.0,
                                         "cache_read": 0.20}
    assert (summarizer.BATCH_DISCOUNT, summarizer.LOCAL_MAX_TOKENS) == (0.5, 4000)
    assert gates.SHUFFLED_RUNS == 3
