"""audit.inject: the Faz 3.5 Step 1 injected-error trials on synthetic songs, and
the CLI on a JamendoLyrics-shaped root (synthetic, same schema)."""

import csv
import json
import random

import audit_synth as syn
import pytest

from audit import anchors, gates, inject
from audit.inject import Trial, read_trial, run_trials, summarize, trials_for


def _sheet(seed=1, n=16):
    texts, true, ear, _ = syn.song(seed, n)
    return syn.sheet(texts, true), ear


def test_damage_shapes():
    sheet, _ = _sheet()
    assert [ln.start_ms for ln in inject.shift_all(sheet, 2000)] == \
        [ln.start_ms + 2000 for ln in sheet]
    drifted = inject.drift(sheet, 5000)
    assert drifted[0].start_ms == sheet[0].start_ms
    assert drifted[-1].start_ms == sheet[-1].start_ms + 5000
    moved = inject.shift_block(sheet, (3, 6), -4000)
    assert [b.start_ms - a.start_ms for a, b in zip(sheet, moved, strict=True)] == \
        [0, 0, 0, -4000, -4000, -4000, -4000] + [0] * 9
    assert len(inject.delete_block(sheet, (3, 6))) == 12
    extra = inject.append_lines(sheet, ["x", "y"])
    assert [ln.start_ms for ln in extra[-2:]] == [sheet[-1].start_ms + 4000,
                                                  sheet[-1].start_ms + 8000]


def test_a_verse_never_touches_the_first_or_last_line():
    rng = random.Random(0)
    blocks = {inject.verse_block(10, rng) for _ in range(500)}
    assert min(b[0] for b in blocks) == 1 and max(b[1] for b in blocks) == 8
    assert all(b[1] - b[0] + 1 == inject.VERSE_LINES for b in blocks)
    assert inject.verse_block(5, rng) is None
    assert inject.verse_block(6, rng) == (1, 4)


def test_every_song_gets_the_registered_trials():
    sheet, _ = _sheet()
    trials = [t for t, _ in trials_for("s", sheet, syn.lines(9, 4), random.Random(3))]
    kinds = [t.kind for t in trials]
    assert kinds == ["none"] + ["global"] * 4 + ["drift"] * 2 + ["verse"] * 2 + ["deleted", "extra"]
    assert [t.size_ms for t in trials if t.kind == "global"] == [2000, 5000, 10_000, 20_000]
    verse = [t.size_ms for t in trials if t.kind == "verse"]
    assert all(3000 <= abs(v) <= 20_000 and v % 100 == 0 for v in verse)
    # seeded: the same seed, the same trials
    again = [t for t, _ in trials_for("s", sheet, syn.lines(9, 4), random.Random(3))]
    assert again == trials


def test_which_trials_are_gated():
    assert Trial("s", "global", 5000).gated and not Trial("s", "global", 2000).gated
    assert Trial("s", "verse", -5000).gated and not Trial("s", "verse", 4999).gated
    assert Trial("s", "drift", 5000).gated
    assert not Trial("s", "deleted", 4).gated and not Trial("s", "none").gated


def test_a_verse_counts_only_when_a_shifted_section_covers_it():
    result = {"flags": ["shift"], "lines": 16, "left_out": [], "shift_kind": "section",
              "sections": [{"first_line": 10, "last_line": 12, "shifted": True}],
              "anchors": [], "activity": {"offset_ms": None}}
    assert read_trial(Trial("s", "verse", 6000, (2, 5)), result)["detected"] is False
    assert read_trial(Trial("s", "verse", 6000, (9, 12)), result)["detected"] is True
    # touching at one line is covering it, on either edge
    assert read_trial(Trial("s", "verse", 6000, (12, 15)), result)["detected"] is True
    assert read_trial(Trial("s", "verse", 6000, (7, 10)), result)["detected"] is True
    assert read_trial(Trial("s", "verse", 6000, (13, 15)), result)["detected"] is False


def test_offset_error_is_heard_minus_sheet_against_the_shift():
    result = {"flags": ["shift"], "lines": 4, "left_out": [3], "shift_kind": "global",
              "sections": [{"first_line": 0, "last_line": 3, "shifted": True}],
              "anchors": [{"line": k, "delta_ms": d} for k, d in
                          enumerate([-5000, -5200, -4900, 900])],
              "activity": {"offset_ms": -5000}}
    row = read_trial(Trial("s", "global", 5000), result)
    assert row["detected"] and row["offset_error_ms"] == 0   # median of the 3 kept: -5000
    assert read_trial(Trial("s", "global", 4000), result)["offset_error_ms"] == 1000


def test_a_false_alarm_is_a_shift_or_wrong_sheet_on_an_unmodified_song():
    base = {"flags": [], "sections": [], "anchors": [], "left_out": [], "lines": 1}
    assert not read_trial(Trial("s", "none"), base)["false_alarm"]
    for flag, alarm in (("shift", True), ("wrong_sheet", True), ("unsheeted_singing", False),
                        ("unsung_lines", False)):
        assert read_trial(Trial("s", "none"), {**base, "flags": [flag]})["false_alarm"] is alarm


@pytest.fixture(scope="module")
def three_songs():
    out = []
    for seed in (1, 2, 3):
        texts, true, ear, _ = syn.song(seed, 16)
        out.append((f"song{seed}", syn.sheet(texts, true), ear))
    return out


def test_synthetic_songs_pass_the_registered_gates(three_songs):
    summary = summarize(run_trials(three_songs, seed=35))
    assert summary["trials"] == 33 and summary["unmodified"] == 3
    assert summary["detection"] == 1.0 and summary["false_alarm"] == 0.0
    assert summary["offset_error_median_ms"] == 0
    assert summary["detection_deleted"] == 1.0 and summary["detection_extra"] == 1.0
    assert summary["passed"]


def test_a_blind_check_fails_the_detection_gate(three_songs, monkeypatch):
    monkeypatch.setattr(anchors, "SHIFT_FLAG_MS", 60_000)
    summary = summarize(run_trials(three_songs, seed=35))
    assert summary["detection"] < gates.INJECT_DETECTION_MIN
    assert not summary["gates"]["detection"] and not summary["passed"]


def test_summary_gates_sit_on_their_numbers():
    def rows(detected, errors, alarms):
        out = [{"song": "s", "kind": "global", "size_ms": 5000, "detected": d,
                "offset_error_ms": e} for d, e in zip(detected, errors, strict=True)]
        out += [{"song": "s", "kind": "none", "size_ms": 0, "detected": None,
                 "offset_error_ms": None, "false_alarm": a} for a in alarms]
        return out

    ok = summarize(rows([True] * 9 + [False], [500] * 9 + [None], [True] + [False] * 19))
    assert ok["detection"] == 0.9 and ok["offset_error_median_ms"] == 500
    assert ok["false_alarm"] == 0.05 and ok["passed"]
    worse = summarize(rows([True] * 8 + [False] * 2, [501] * 8 + [None] * 2,
                           [True] * 2 + [False] * 18))
    assert worse["gates"] == {"detection": False, "offset_error": False, "false_alarm": False}


def test_placements_are_computed_once_per_text(three_songs, monkeypatch):
    calls = []
    real = inject.placements

    def counting(sheet, ear, candidates):
        calls.append(sheet[0].text)
        return real(sheet, ear, candidates)

    monkeypatch.setattr(inject, "placements", counting)
    name, sheet, ear = three_songs[0]
    cache = inject.PlacementCache(ear)
    cache.for_sheet(sheet)
    cache.for_sheet(inject.shift_all(sheet, 5000))
    assert len(calls) == len(set(calls)) == 16


# --- the CLI on a JamendoLyrics-shaped root ----------------------------------------

def _jamendo_root(tmp_path, songs):
    root = tmp_path / "jamendo"
    (root / "lyrics").mkdir(parents=True)
    (root / "annotations" / "words").mkdir(parents=True)
    ears = tmp_path / "ears"
    ears.mkdir()
    with (root / "JamendoLyrics.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, ["Filepath", "Artist", "Title", "Language", "LicenseType"])
        w.writeheader()
        for name, texts, true, ear in songs:
            w.writerow({"Filepath": f"{name}.mp3", "Artist": "A", "Title": name,
                        "Language": "English", "LicenseType": "CC BY"})
            tokens, rows = [], []
            for text, start in zip(texts, true, strict=True):
                words = text.split()
                for k, word in enumerate(words):
                    t = (start + 500 * k) / 1000
                    tokens.append(word)
                    rows.append({"word_start": t, "word_end": t + 0.4,
                                 "line_end": t + 0.4 if k == len(words) - 1 else "nan"})
            (root / "lyrics" / f"{name}.words.txt").write_text(" ".join(tokens))
            with (root / "annotations" / "words" / f"{name}.csv").open("w", newline="") as f2:
                w2 = csv.DictWriter(f2, ["word_start", "word_end", "line_end"])
                w2.writeheader()
                w2.writerows(rows)
            (ears / f"{name}.json").write_text(json.dumps(ear))
    return root, ears


def test_the_cli_runs_the_trials_and_reports_the_gates(tmp_path, capsys):
    songs = [(f"syn{seed}", *syn.song(seed, 16)[:3]) for seed in (1, 2)]
    root, ears = _jamendo_root(tmp_path, songs)
    out = tmp_path / "step1.json"
    assert inject.main(["run", "--jamendo-root", str(root), "--ears", str(ears),
                        "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "Step 1 injected errors: passed" in printed
    result = json.loads(out.read_text())
    assert result["summary"]["trials"] == 22 and len(result["trials"]) == 22
    empty = tmp_path / "none"
    empty.mkdir()
    assert inject.main(["run", "--jamendo-root", str(root), "--ears", str(empty)]) == 2
