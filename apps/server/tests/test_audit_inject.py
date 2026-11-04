"""audit.inject: seeded sheet damage and the anchor check's bench gate.
Synthetic songs only."""

import json

import pytest
from audit_synth import ear, lines_of, record, vocabulary

from audit import inject
from audit.anchors import sheet_from_record
from audit.inject import Case, cases_for, gate

VOCAB = vocabulary(200, seed=17)


def test_gates_are_the_pre_registered_numbers():
    assert inject.GLOBAL_SHIFTS_MS == (2000, 5000, 10_000, 20_000)
    assert inject.DRIFT_RANGE_MS == (2000, 10_000)
    assert inject.VERSE_SHIFT_RANGE_MS == (3000, 20_000)
    assert (inject.EXTRA_LINES, inject.EXTRA_GAP_MS, inject.GATED_SHIFT_MS) == (4, 4000, 5000)
    assert (inject.MIN_DETECTION, inject.MAX_OFFSET_ERROR_MS, inject.OFFSET_ERROR_PERCENTILE,
            inject.MAX_FALSE_ALARM) == (0.90, 500, 0.90, 0.05)


def _song(seed, n_sections=4, per_section=5):
    lines = lines_of(VOCAB, n_sections * per_section, 5, seed=seed)
    paras, t = [], 9000
    for s in range(n_sections):
        paras.append([(lines[s * per_section + k], t + 4000 * k) for k in range(per_section)])
        t += 4000 * per_section + 3000
    rec = record(paras)
    sheet = sheet_from_record(rec)
    return rec, ear([(ln.text, ln.start_ms + 50) for ln in sheet])


def _sheet():
    return sheet_from_record(_song(1)[0])


def test_each_case_damages_the_sheet_as_named():
    sheet = _sheet()
    starts = [ln.start_ms for ln in sheet]
    assert [ln.start_ms for ln in inject.inject(sheet, Case("s", "none"))] == starts
    assert [ln.start_ms for ln in inject.inject(sheet, Case("s", "global", 5000))] \
        == [s + 5000 for s in starts]
    drift = inject.inject(sheet, Case("s", "drift", 4000))
    assert drift[0].start_ms == starts[0] and drift[-1].start_ms == starts[-1] + 4000
    mid = len(sheet) // 2
    expected = round(4000 * (starts[mid] - starts[0]) / (starts[-1] - starts[0]))
    assert drift[mid].start_ms - starts[mid] == expected
    verse = inject.inject(sheet, Case("s", "verse", 7000, section=2))
    assert all((v.start_ms - o.start_ms) == (7000 if o.section == 2 else 0)
               for v, o in zip(verse, sheet, strict=True))
    deleted = inject.inject(sheet, Case("s", "deleted", section=1))
    assert len(deleted) == len(sheet) - 5 and 1 not in {ln.section for ln in deleted}
    assert [ln.index for ln in deleted] == list(range(len(deleted)))
    donor = sheet_from_record(_song(2)[0])
    extra = inject.inject(sheet, Case("s", "extra"), donor)
    assert len(extra) == len(sheet) + 4
    assert [ln.start_ms for ln in extra[-4:]] == [starts[-1] + 4000 * k for k in (1, 2, 3, 4)]
    assert {ln.section for ln in extra[-4:]} == {sheet[-1].section + 1}


def test_record_of_round_trips_through_the_parser():
    sheet = _sheet()
    again = sheet_from_record(inject.record_of(sheet))
    assert [(ln.text, ln.start_ms, ln.section) for ln in again] \
        == [(ln.text, ln.start_ms, ln.section) for ln in sheet]


def test_cases_cover_every_kind_with_seeded_ranges():
    import random

    got = cases_for("s", _sheet(), random.Random(3))
    assert [c.kind for c in got] == ["none", "global", "global", "global", "global", "drift",
                                     "verse", "deleted", "extra"]
    drift, verse = got[5], got[6]
    assert 2000 <= drift.shift_ms <= 10_000 and 3000 <= verse.shift_ms <= 20_000
    # a one-section song has no verse to shift or delete
    one = sheet_from_record(record([[(t, 1000 + 4000 * i)
                                      for i, t in enumerate(lines_of(VOCAB, 6, 5, seed=4))]]))
    assert {c.kind for c in cases_for("s", one, random.Random(3))} \
        == {"none", "global", "drift", "extra"}


def _outcome(kind, shift=0, detected=False, error=None, flagged=False):
    return {"kind": kind, "shift_ms": shift, "detected": detected, "error_ms": error,
            "flagged": flagged}


def test_gate_boundaries():
    ok = [_outcome("global", 5000, True, 500)] * 9 + [_outcome("global", 5000, False)]
    clean = [_outcome("none")] * 19 + [_outcome("none", flagged=True)]
    g = gate(ok + clean)
    assert g["detection_ge_5s"] == 0.9 and g["false_alarm"] == 0.05 and g["passed"]
    worse = [_outcome("global", 5000, True, 500)] * 8 + [_outcome("global", 5000, False)] * 2
    assert not gate(worse + clean)["checks"]["detection_ge_5s"]
    late = [_outcome("global", 5000, True, 501)] * 10
    assert not gate(late + clean)["checks"]["offset_error"]
    noisy = [_outcome("none")] * 18 + [_outcome("none", flagged=True)] * 2
    assert not gate(ok + noisy)["checks"]["false_alarm"]
    # 2 s shifts are reported, not gated
    small = [_outcome("global", 2000, False)] * 10
    g = gate(ok + clean + small)
    assert g["detection_lt_5s"] == 0.0 and g["passed"]


def test_offset_error_is_the_90th_percentile():
    errors = [100] * 9 + [900]
    rows = [_outcome("global", 10_000, True, e) for e in errors]
    assert gate(rows + [_outcome("none")])["offset_error_p90_ms"] == 100
    errors = [100] * 8 + [900, 900]
    rows = [_outcome("global", 10_000, True, e) for e in errors]
    assert gate(rows + [_outcome("none")])["offset_error_p90_ms"] == 900


def test_the_harness_passes_on_synthetic_songs_and_is_seeded():
    pairs = [(f"s{k}", _song(k)[1], _song(k)[0]) for k in range(1, 5)]
    a = inject.run(pairs, seed=5)
    b = inject.run(pairs, seed=5)
    assert a["outcomes"] == b["outcomes"]
    g = a["gate"]
    assert g["cases"] == 4 * 9
    assert g["detection_ge_5s"] == 1.0 and g["false_alarm"] == 0.0
    assert g["offset_error_max_ms"] is not None and g["offset_error_max_ms"] <= 100
    assert g["passed"]
    # the order is random: not song by song
    assert [o["song"] for o in a["outcomes"]] != sorted(o["song"] for o in a["outcomes"])
    verse = [o for o in a["outcomes"] if o["kind"] == "verse"]
    assert all(o["detected"] and abs(o["read_ms"] + o["shift_ms"]) <= 100 for o in verse)


def test_cli_run_reads_a_manifest(tmp_path, capsys):
    entries = []
    for k in (1, 2):
        rec, e = _song(k)
        (tmp_path / f"s{k}.record.json").write_text(json.dumps(rec))
        (tmp_path / f"s{k}.ear.json").write_text(json.dumps(e))
        entries.append({"id": f"s{k}", "record": f"s{k}.record.json", "ear": f"s{k}.ear.json"})
    entries.append({"id": "missing", "record": "x.json", "ear": "nope.json"})
    (tmp_path / "manifest.json").write_text(json.dumps(entries))
    out = tmp_path / "out.json"
    code = inject.main(["run", "--pairs", str(tmp_path / "manifest.json"), "--seed", "2",
                        "--json", str(out)])
    printed = capsys.readouterr()
    assert "skip missing" in printed.err
    assert code == 0 and json.loads(out.read_text())["gate"]["passed"]


@pytest.mark.parametrize("kind", ["deleted", "extra"])
def test_deleted_and_extra_are_reported_with_their_coverage(kind):
    rec, e = _song(1)
    sheet = sheet_from_record(rec)
    donor = sheet_from_record(_song(2)[0])
    case = Case("s", kind, section=1 if kind == "deleted" else None)
    damaged = inject.inject(sheet, case, donor)
    from audit.anchors import check

    out = inject.evaluate(case, damaged, check(damaged, e))
    if kind == "deleted":
        assert out["unmatched_sung_share"] > 0.15 and out["unmatched_line_share"] == 0
    else:
        assert out["unmatched_line_share"] == pytest.approx(4 / 24, abs=0.01)
