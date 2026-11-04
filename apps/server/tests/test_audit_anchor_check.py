"""audit.anchor_check: the Step 1 pilot gates and the one-song run, on synthetic
documents and ears (no real ids, no lyrics)."""

import json

import audit_synth as syn
import pytest

from audit import anchor_check, gates, inject
from audit.anchor_check import pilot_verdict, song_offset


def _doc(texts, stamps):
    return {"pipeline_version": "2.29.0", "sync": "word",
            "lines": [{"text": t, "start_ms": s, "end_ms": s + 2000,
                       "words": [{"text": w, "start_ms": s + 300 * k, "end_ms": s + 300 * k + 250}
                                 for k, w in enumerate(t.split())]}
                      for t, s in zip(texts, stamps, strict=True)]}


def _result(flags, sections=(), kind=None, activity=None):
    return {"flags": list(flags), "sections": list(sections), "shift_kind": kind,
            "activity": activity or {"offset_ms": None}}


def test_the_offset_is_the_biggest_shifted_section_or_the_activity_fit():
    secs = [{"shifted": True, "anchors": 2, "median_ms": -3000},
            {"shifted": True, "anchors": 5, "median_ms": -18_900},
            {"shifted": False, "anchors": 9, "median_ms": 0}]
    assert song_offset(_result(["shift"], secs, "section")) == -18_900
    assert song_offset(_result(["shift"], (), "activity", {"offset_ms": -4000})) == -4000
    assert song_offset(_result([])) is None


def test_an_expected_shift_must_land_within_a_second_of_the_reading():
    expect = {"expect": "shift", "offset_ms": -18_900}
    def at(ms):
        return _result(["shift"], [{"shifted": True, "anchors": 4, "median_ms": ms}], "global")
    assert pilot_verdict(at(-18_900 + 1000), expect)[0]
    assert pilot_verdict(at(-18_900 - 1000), expect)[0]
    assert not pilot_verdict(at(-18_900 + 1001), expect)[0]
    assert not pilot_verdict(at(18_900), expect)[0]                  # the sign counts
    assert not pilot_verdict(_result([]), expect)[0]


def test_a_clean_song_may_carry_reports_but_no_alarm():
    assert pilot_verdict(_result(["unsheeted_singing", "unsung_lines"]), None)[0]
    assert not pilot_verdict(_result(["shift"]), None)[0]
    assert not pilot_verdict(_result(["wrong_sheet"]), {"expect": "none"})[0]
    assert pilot_verdict(_result(["wrong_sheet"]), {"expect": "wrong_sheet"})[0]
    assert not pilot_verdict(_result([]), {"expect": "wrong_sheet"})[0]


@pytest.fixture
def audit_root(tmp_path, monkeypatch):
    import audit.ear
    import audit.paths

    monkeypatch.setattr(audit.ear, "EAR_DIR", tmp_path / "ear")
    monkeypatch.setattr(audit.paths, "RUNS_DIR", tmp_path / "runs")
    return tmp_path


def _song(root, vid, seed, shift_ms=0):
    texts, true, ear, _ = syn.song(seed, 16)
    (root / "ear" / vid).mkdir(parents=True)
    (root / "ear" / vid / "whole.json").write_text(json.dumps(ear))
    return {"id": vid, "doc": _doc(texts, [s + shift_ms for s in true])}


def test_the_pilot_passes_and_fails_on_its_expectations(audit_root, capsys):
    dump = [_song(audit_root, "synthLate01", 1, 10_000), _song(audit_root, "synthOk0001", 2),
            _song(audit_root, "synthOk0002", 3)]
    (audit_root / "dump.json").write_text(json.dumps(dump))
    expect = audit_root / "expect.json"
    expect.write_text(json.dumps({"synthLate01": {"expect": "shift", "offset_ms": -10_000,
                                                  "label": "late sheet"}}))
    args = ["--pilot", "--doc", str(audit_root / "dump.json"), "--expect", str(expect),
            "--json", str(audit_root / "pilot.json")]
    assert anchor_check.main(args) == 0
    assert "Step 1 pilot (3 songs): passed" in capsys.readouterr().out
    assert json.loads((audit_root / "pilot.json").read_text())["passed"] is True
    # a reading 1.5 s away fails the song, so the step
    expect.write_text(json.dumps({"synthLate01": {"expect": "shift", "offset_ms": -11_500}}))
    assert anchor_check.main(args) == 1
    # an expected song with no ear fails too: the gate is over the named set
    expect.write_text(json.dumps({"synthLate01": {"expect": "shift", "offset_ms": -10_000},
                                  "synthGone01": {"expect": "shift", "offset_ms": -4000}}))
    assert anchor_check.main(args) == 1
    assert "without a document or ear: synthGone01" in capsys.readouterr().out


def test_one_song_writes_its_run_file(audit_root, capsys):
    dump = [_song(audit_root, "synthLate01", 1, 6000)]
    (audit_root / "dump.json").write_text(json.dumps(dump))
    assert anchor_check.main(["synthLate01", "--doc", str(audit_root / "dump.json")]) == 0
    result = json.loads((audit_root / "runs" / "synthLate01" / "anchors.json").read_text())
    assert result["flags"] == ["shift"] and result["sections"][0]["median_ms"] == -6000
    assert "flags ['shift']" in capsys.readouterr().out
    assert anchor_check.main(["synthNone01", "--doc", str(audit_root / "dump.json")]) == 2


def test_a_record_overrides_the_document_stamps(audit_root):
    dump = [_song(audit_root, "synthOk0001", 2)]
    (audit_root / "dump.json").write_text(json.dumps(dump))
    texts, true, _, _ = syn.song(2, 16)
    late = inject.shift_all(syn.sheet(texts, true), 5000)
    synced = "\n".join(f"[{ln.start_ms // 60000:02d}:{ln.start_ms // 1000 % 60:02d}."
                       f"{ln.start_ms % 1000 // 10:02d}] {ln.text}" for ln in late)
    record = audit_root / "record.json"
    record.write_text(json.dumps({"id": 7, "duration": 70.0, "syncedLyrics": synced}))
    assert anchor_check.main(["synthOk0001", "--doc", str(audit_root / "dump.json"),
                              "--record", str(record)]) == 0
    result = json.loads((audit_root / "runs" / "synthOk0001" / "anchors.json").read_text())
    assert result["flags"] == ["shift"] and result["sections"][0]["median_ms"] == -5000


def test_the_pilot_tolerance_is_the_registered_one():
    assert gates.PILOT_OFFSET_TOL_MS == 1000
