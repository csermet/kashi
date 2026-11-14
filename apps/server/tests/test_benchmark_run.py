"""benchmarks.run's scoring on hand-made songs (no torch, no audio)."""

import argparse
from pathlib import Path

import pytest

from benchmarks import datasets, run
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming


def _song(lines: list[list[tuple[int, int]]], verified=None) -> datasets.JamendoSong:
    """lines: per line, (truth start, truth end) per word; tokens are w<line>_<k>."""
    texts, starts, words, ends = [], [], [], []
    for i, line in enumerate(lines):
        tokens = [f"w{i}_{k}" for k in range(len(line))]
        texts.append(" ".join(tokens))
        starts.append(line[0][0])
        words += [(s, t) for (s, _), t in zip(line, tokens, strict=True)]
        ends += [e for _, e in line]
    return datasets.JamendoSong(
        stem="hand", artist="a", title="t", language="eng", license_type="CC",
        audio_path=Path("/nonexistent.mp3"), line_texts=texts, line_starts_ms=starts,
        words=words, word_ends_ms=ends, duration_hint_s=ends[-1] / 1000, verified=verified,
    )


def _result(song: datasets.JamendoSong, hyp: list[list[tuple[int, int]]]) -> AlignResult:
    chunks = [
        [AlignedWord(s, e, tok, 0.5) for (s, e), tok in zip(line, text.split(), strict=True)]
        for line, text in zip(hyp, song.line_texts, strict=True)
    ]
    lines = [
        LineTiming(c[0].start_ms if c else 0, c[-1].end_ms if c else 0, text, 0.5)
        for c, text in zip(chunks, song.line_texts, strict=True)
    ]
    return AlignResult(sync="word", lines=lines, words_per_line=chunks, quality_score=0.5)


def _args(**over) -> argparse.Namespace:
    base = {"dump_words": False, "signals": False, "legacy": True}
    return argparse.Namespace(**(base | over))


def test_a_scored_song_carries_line_starts_dwell_and_gross_errors():
    song = _song([[(1000, 1300), (1400, 1700)], [(3000, 3200), (3300, 3600)]])
    result = _result(song, [[(1300, 1350), (1360, 1700)], [(2600, 3200), (3800, 3900)]])
    entry: dict = {}
    assert run._word_metrics(_args(), song, result, None, (100, 300, 500), entry)
    assert entry["fluency"] == {
        "lines": 2, "line_late": 1, "line_early": 1,  # +300 late, -400 early
        "pairs": 2, "hyp_short": 1, "truth_short": 0, "both_short": 0,  # 1300 -> 1360
        "words": 4, "gross": 0, "untimed": 0,
        "wordless_lines": 0, "wordless_late": 0, "wordless_early": 0,
    }
    agg = run._jamendo_aggregate([{**entry, "language": "eng", "duration_s": 4.0,
                                   "align_s": 1.0}])
    assert agg["fluency"]["line_starts"]["noticeable_per_song"] == 1.0
    assert agg["fluency"]["dwell"]["pipeline_only_pooled"] == 0.5
    assert agg["per_language"]["eng"]["fluency"] == agg["fluency"]


# --- shipped mode: pairing a result line QA has emptied lines in ----------------


def test_a_wordless_line_leaves_its_words_untimed_and_is_read_at_its_start():
    song = _song([[(1000, 1300), (1400, 1700)], [(3000, 3200), (3300, 3600)],
                  [(5000, 5200), (5300, 5600)]])
    result = _result(song, [[(1000, 1300), (1400, 1700)], [(3000, 3200), (3300, 3600)],
                            [(5000, 5200), (5300, 5600)]])
    emptied = AlignResult(
        sync="word",
        lines=[*result.lines[:1], LineTiming(3400, 3600, song.line_texts[1], 0.0),
               *result.lines[2:]],
        words_per_line=[result.words_per_line[0], [], result.words_per_line[2]],
        quality_score=0.5,
    )
    assert run._pair(emptied, song, by_line=False) is None  # the legacy pairing cannot
    paired = run._pair(emptied, song, by_line=True)
    assert paired is not None
    assert paired.starts == [1000, 1400, None, None, 5000, 5300]
    assert paired.lines == [0, 0, 1, 1, 2, 2] and paired.wordless_starts == {1: 3400}
    entry: dict = {}
    assert run._word_metrics(_args(legacy=False), song, emptied, None, (100, 300), entry)
    assert entry["untimed_words"] == 2
    assert entry["words"]["count"] == 4 and entry["words"]["pcs"]["0.1"] == 1.0
    assert entry["pco_all"] == {"0.1": 4 / 6, "0.3": 4 / 6}
    f = entry["fluency"]
    assert (f["lines"], f["line_late"], f["untimed"]) == (3, 1, 2)  # line 1 shown 400 ms late
    assert f["pairs"] == 2  # the wordless line has no dwell to judge


def test_a_song_line_qa_degraded_keeps_its_stages_and_counts_in_the_line_starts(monkeypatch):
    """No timed word left, so no PCO: the row still carries its stages and its
    line starts, and the aggregate's fluency and stages include it."""
    song = _song([[(1000, 1300), (1400, 1700)], [(3000, 3200), (3300, 3600)]])
    healthy = _result(song, [[(1000, 1300), (1400, 1700)], [(3000, 3200), (3300, 3600)]])
    degraded = AlignResult(sync="line", lines=[LineTiming(1500, 1700, song.line_texts[0], 0.0),
                                               LineTiming(3000, 3600, song.line_texts[1], 0.0)],
                           words_per_line=[], quality_score=0.0)
    monkeypatch.setattr(run, "_ship_jamendo_song", lambda args, s, entry: (
        Path("x.wav"), 0.0, degraded, 1.0, {"aligned": healthy, "line_qa": degraded}))
    entry = run._score_jamendo_song(_args(legacy=False), song, (100, 300), 1, 1)
    assert "words" not in entry and entry["error"].startswith("no verified word has a time")
    assert entry["stages"]["aligned"]["pcs"]["0.1"] == 1.0
    assert entry["stages"]["shipped"]["untimed_words"] == 4
    agg = run._jamendo_aggregate([entry])
    assert agg["failed"] == ["hand"] and agg["scored"] == 0
    starts = agg["fluency"]["line_starts"]
    assert (starts["lines"], starts["late"], starts["wordless_lines"]) == (2, 1, 2)
    assert agg["stages"]["aligned"]["pco"]["0.1"] == 1.0
    assert agg["stages"]["shipped"]["pco"] is None and agg["pco_all"]["0.1"] == 0.0


def test_a_line_whose_word_count_changed_cannot_be_paired():
    song = _song([[(1000, 1300), (1400, 1700)], [(3000, 3200), (3300, 3600)]])
    result = _result(song, [[(1000, 1300), (1400, 1700)], [(3000, 3200), (3300, 3600)]])
    broken = AlignResult(sync="word", lines=result.lines,
                         words_per_line=[result.words_per_line[0], result.words_per_line[1][:1]],
                         quality_score=0.5)
    assert run._pair(broken, song, by_line=True) is None
    every_line_lost = AlignResult(sync="line", lines=result.lines, words_per_line=[],
                                  quality_score=0.5)
    paired = run._pair(every_line_lost, song, by_line=True)
    assert paired is not None and paired.starts == [None] * 4
    entry: dict = {}
    assert not run._word_metrics(_args(legacy=False), song, every_line_lost, None, (100,), entry)
    assert entry["error"].startswith("no verified word has a time")
    assert entry["pco_all"] == {"0.1": 0.0} and entry["fluency"]["lines"] == 2


def test_the_sheet_is_the_seeded_jitter_then_the_whole_sheet_offset():
    song = _song([[(100, 300)], [(5000, 5200)], [(9000, 9300)]])
    plain = argparse.Namespace(anchor_jitter_ms=400, stamp_offset_ms=0)
    moved = argparse.Namespace(anchor_jitter_ms=400, stamp_offset_ms=-800)
    jittered = run._jittered(song.line_starts_ms, song.stem, 400)
    assert run._sheet(song, plain) == jittered
    assert run._sheet(song, moved) == [max(0, s - 800) for s in jittered]
    assert run._sheet(song, moved)[0] == 0  # clamped, never negative


# --- shipped mode: arguments and configuration ----------------------------------


@pytest.fixture
def live_settings(monkeypatch):
    """Every pipeline setting at its code default, put back after the test
    (the shipped bench configures the process-wide settings)."""
    from helpers import settings_from

    from kashi_server.config import PIPELINE_FIELDS, settings
    from kashi_server.pipeline import line_qa

    bare = settings_from(monkeypatch, {})
    for name in PIPELINE_FIELDS:
        monkeypatch.setattr(settings, name, getattr(bare, name))
    monkeypatch.setattr(line_qa, "_border_case_drops", line_qa._border_case_drops)
    from kashi_server.worker import process

    for name in ("_shift_sheet", "detect_onsets"):
        monkeypatch.setattr(process, name, getattr(process, name))
    return settings


def test_the_shipped_defaults_are_the_workers(live_settings):
    args = run._finalize_args(run._parse_args(["--dataset", "jamendo"]))
    assert (args.separation, args.mixback, args.windowed, args.trim_ends) == (
        "kim-base", 0.0, True, False)
    assert args.align_kwargs == {} and args.deviations == []
    assert run._config_name(args) == "shipped-kim-base-mb0-win"


def test_the_legacy_defaults_are_the_old_benchs(live_settings):
    args = run._finalize_args(run._parse_args(["--legacy"]))
    assert (args.separation, args.mixback, args.windowed, args.trim_ends) == (
        "full-mix", 0.15, False, True)
    assert args.deviations is None
    old = run._finalize_args(run._parse_args(
        ["--legacy", "--separation", "kim-base", "--mixback", "0", "--windowed"]))
    assert run._config_name(old) == "kim-base-mb0-win"  # the label old runs carry


def test_a_shipped_run_names_every_way_it_differs_from_the_worker(live_settings):
    args = run._finalize_args(run._parse_args([
        "--whole-vote-t-ms", "400", "--line-region-ms", "0", "--separation", "kim-melband",
        "--no-density-drop", "--align-offset-ms", "0",
    ]))
    assert args.align_kwargs == {"whole_vote_t_ms": 400, "line_region_ms": None}
    assert args.deviations == [
        "separation kim-melband (production: kim-base)",
        "align whole_vote_t_ms=400", "align line_region_ms=None",
        "--align-offset-ms 0", "line QA density drop off",
    ]


@pytest.mark.parametrize("argv", [["--trim-ends"], ["--signals"],
                                  ["--line-postprocess", "soft-median"]])
def test_the_shipped_mode_refuses_what_production_does_not_run(live_settings, argv):
    with pytest.raises(SystemExit):
        run._finalize_args(run._parse_args(argv))


@pytest.mark.parametrize("flag", ["--no-density-drop", "--no-sheet-shift"])
def test_switching_a_shipped_step_off_needs_the_shipped_mode(live_settings, flag):
    with pytest.raises(SystemExit):
        run._finalize_args(run._parse_args(["--legacy", flag]))


def test_sheet_shift_off_leaves_the_stamps_and_says_so(live_settings):
    from kashi_server.worker import process

    args = run._finalize_args(run._parse_args(["--no-sheet-shift"]))
    assert args.deviations == ["sheet shift off"]
    run._configure_shipped(args)
    lyrics = object()
    assert process._shift_sheet(None, None, None, lyrics, "a.wav", 1.0) == (lyrics, "a.wav", [])


def test_a_named_model_keeps_the_languages_offsets_unless_they_are_zeroed(live_settings):
    from kashi_server.config import ALIGNER_REVISIONS
    from kashi_server.pipeline.alignment import resolve_aligner

    args = run._finalize_args(run._parse_args(["--align-model", "some/omni"]))
    run._configure_shipped(args)
    eng, deu = resolve_aligner("eng"), resolve_aligner("deu")
    assert eng.offset_by_initial is not None
    assert (eng.model_name, eng.offset_ms, eng.offset_by_initial["vowel"]) == (
        "some/omni", -50, -80)
    assert (deu.model_name, deu.offset_ms) == ("some/omni", 0)
    assert resolve_aligner("tur").romanize is True  # the flag or the global, not mpoyraz's
    assert "some/omni" not in ALIGNER_REVISIONS  # a bake-off model loads unpinned
    zeroed = run._finalize_args(run._parse_args(
        ["--align-model", "some/omni", "--align-offset-ms", "0",
         "--align-offset-by-initial", "none"]))
    run._configure_shipped(zeroed)
    assert resolve_aligner("eng").offset_ms == 0
    assert resolve_aligner("eng").offset_by_initial is None


def test_onsets_are_remembered_per_audio_content(tmp_path):
    calls: list[str] = []

    def detect(wav):
        calls.append(Path(wav).name)
        return [10, 20] if Path(wav).read_bytes() == b"a" else None

    cached = run._cached_onsets(detect, tmp_path / "cache")
    (tmp_path / "x.wav").write_bytes(b"a")
    (tmp_path / "y.wav").write_bytes(b"a")  # same content, another path
    (tmp_path / "z.wav").write_bytes(b"b")
    assert [cached(tmp_path / n) for n in ("x.wav", "y.wav", "z.wav", "z.wav")] == [
        [10, 20], [10, 20], None, None]
    assert calls == ["x.wav", "z.wav"]


def test_density_drop_off_switches_exactly_that_rule_off(live_settings):
    from kashi_server.pipeline import line_qa

    run._configure_shipped(run._finalize_args(run._parse_args(["--no-density-drop"])))
    assert line_qa._border_case_drops(None, [], {1}) == set()


# --- shipped mode, end to end on a synthetic song (the run_local stand-in) -----

from test_audit_run_local import (  # noqa: E402  (fixtures for the stand-in aligner)
    LINE_STARTS_MS,
    LINES,
    WORD_MS,
    WORD_STEP_MS,
    _write_stem,
    heard,  # noqa: F401
)

GERMAN = ["ich bin heute nicht zu hause", "du bist mein bester freund",
          "wir gehen morgen in die stadt", "das wetter ist sehr schoen",
          "er hat keine zeit fuer uns", "sie singt ein lied fuer dich"]


def _jamendo_root(tmp_path: Path) -> Path:
    """Two synthetic songs in the JamendoLyrics layout, their kim-base stems cached."""
    data = tmp_path / "data"
    root = data / "jamendolyrics"
    for sub in ("lyrics", "annotations/words", "mp3", "../stems/kim-base"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    rows = ["Filepath,Artist,Title,Language,LicenseType"]
    for stem, lines, language in (("Synth_En", LINES, "English"),
                                  ("Synth_De", GERMAN, "German")):
        rows.append(f"{stem}.mp3,Nobody,{stem},{language},CC BY")
        tokens, words = [], ["word_start,word_end,line_end"]
        for start, text in zip(LINE_STARTS_MS, lines, strict=True):
            line_tokens = text.split()[:3]
            for k, token in enumerate(line_tokens):
                s = (start + k * WORD_STEP_MS) / 1000
                e = s + WORD_MS / 1000
                last = k == len(line_tokens) - 1
                words.append(f"{s},{e},{e if last else 'nan'}")
                tokens.append(token)
        (root / "lyrics" / f"{stem}.words.txt").write_text(" ".join(tokens))
        (root / "annotations/words" / f"{stem}.csv").write_text("\n".join(words) + "\n")
        _write_stem(root / "mp3" / f"{stem}.mp3")  # never decoded: the stem is cached
        _write_stem(data / "stems" / "kim-base" / f"{stem}.wav")
    (root / "JamendoLyrics.csv").write_text("\n".join(rows) + "\n")
    return data


def _bench(argv: list[str], tmp_path, monkeypatch) -> dict:
    import json

    data = _jamendo_root(tmp_path)
    monkeypatch.setattr(run, "DATA_DIR", data)
    monkeypatch.setattr(run, "RESULTS_DIR", tmp_path / "results")
    label = "t" + str(abs(hash(tuple(argv))))
    assert run.main(["--dataset", "jamendo", "--dump-words", "--label", label, *argv]) == 0
    (out,) = (tmp_path / "results").glob(f"*-{label}.json")
    return json.loads(out.read_text())


@pytest.mark.usefixtures("heard")
def test_the_shipped_bench_runs_the_workers_chain_and_says_what_ran(
        live_settings, tmp_path, monkeypatch):
    from kashi_server.config import ALIGNER_REVISIONS

    report = _bench([], tmp_path, monkeypatch)
    meta, jam = report["meta"], report["jamendo"]
    assert (meta["bench_mode"], meta["deviations"], meta["pipeline_overrides"]) == (
        "shipped", [], [])
    eng, deu = meta["aligners"]["eng"], meta["aligners"]["deu"]
    assert (eng["model_name"], eng["offset_ms"], eng["offset_by_initial"]["vowel"]) == (
        "jonatasgrosman/wav2vec2-xls-r-1b-english", -50, -80)
    assert eng["revision"] == ALIGNER_REVISIONS[eng["model_name"]]
    assert (deu["model_name"], deu["offset_ms"]) == (
        "MahmoudAshraf/mms-300m-1130-forced-aligner", 0)
    assert meta["alignment_model"] == "per-language (see aligners)"
    assert meta["align_offset_ms"] is None and meta["offset_table"].startswith("production")
    song = jam["songs"][0]
    assert meta["trim_ends"] is False
    assert all("trimmed_ends" not in s for s in jam["songs"])  # no private trim here
    assert set(song["stages"]) == {"aligned", "line_qa", "shipped"}
    assert song["shipped"]["line_qa"]["degraded_to_line"] is False
    assert song["aligner"]["language"] == "eng"
    agg = jam["aggregate"]
    assert agg["scored"] == 2 and agg["shipped"]["songs"] == 2
    assert set(agg["stages"]) == {"aligned", "line_qa", "shipped"}
    assert agg["stages"]["shipped"]["pco"] == agg["pco"]
    assert agg["fluency"] == agg["stages"]["shipped"]["fluency"]
    assert agg["pco_all"] == agg["pco"]  # no line lost its words here


@pytest.mark.usefixtures("heard")
def test_the_shipped_aligned_stage_is_the_legacy_benchs_alignment(
        live_settings, tmp_path, monkeypatch):
    """Same stamps, same aligner resolution: before line QA the shipped chain
    has exactly what the legacy bench scores without its trim."""
    shipped = _bench([], tmp_path, monkeypatch)
    legacy = _bench(["--legacy", "--windowed", "--separation", "kim-base", "--mixback", "0",
                     "--no-trim-ends"], tmp_path, monkeypatch)
    assert legacy["meta"]["bench_mode"] == "legacy"
    assert legacy["meta"]["align_offset_ms"] is None  # eng -50, deu 0: no single answer
    for new, old in zip(shipped["jamendo"]["songs"], legacy["jamendo"]["songs"], strict=True):
        assert new["stages"]["aligned"]["pcs"] == old["words"]["pcs"]
        assert new["stages"]["aligned"]["fluency"] == old["fluency"]
        assert new["stages"]["aligned"]["word_end_mae_ms"] == round(old["word_ends"]["mae_ms"], 1)
        assert new["aligner"] == old["aligner"]
    # ...and the restore then gives the cut ends back (the stand-in's vote cuts them).
    restored = sum(s["shipped"]["restored_ends"] for s in shipped["jamendo"]["songs"])
    assert restored > 0


@pytest.mark.usefixtures("heard")
def test_a_legacy_named_model_is_reported_as_bypassing_the_table(
        live_settings, tmp_path, monkeypatch):
    report = _bench(["--legacy", "--windowed", "--align-model", "some/model"], tmp_path,
                    monkeypatch)
    eng = report["meta"]["aligners"]["eng"]
    assert (eng["model_name"], eng["offset_ms"], eng["offset_by_initial"]) == (
        "some/model", 0, None)
    assert report["meta"]["offset_table"].startswith("none from the table")


@pytest.mark.usefixtures("heard")
@pytest.mark.parametrize("mode", [[], ["--legacy", "--windowed"]])
def test_a_case_run_says_what_ran_too(live_settings, tmp_path, monkeypatch, mode):
    """The field-case suite (real lrclib stamps) through either mode: its meta
    names the aligner each case ran with, not the fallback."""
    import json

    stem = tmp_path / "case.wav"
    _write_stem(stem)
    case = datasets.KashiCase(id="synth", title="t", artist="a", youtube_id="x", lrclib_id=1,
                              language="eng", window_s=None)
    monkeypatch.setattr(datasets, "load_cases", lambda path: [case])
    monkeypatch.setattr(run, "_case_reference",
                        lambda c: list(zip(LINE_STARTS_MS, LINES, strict=True)))
    monkeypatch.setattr(run, "_case_audio", lambda c: stem)
    monkeypatch.setattr(run, "RESULTS_DIR", tmp_path / "results")
    label = "cases" + str(len(mode))
    assert run.main(["--dataset", "cases", "--separation", "full-mix", "--label", label,
                     *mode]) == 0
    (out,) = (tmp_path / "results").glob(f"*-{label}.json")
    report = json.loads(out.read_text())
    assert report["cases"]["results"][0]["aligner"]["model_name"] == (
        "jonatasgrosman/wav2vec2-xls-r-1b-english")
    assert report["meta"]["alignment_model"] == "jonatasgrosman/wav2vec2-xls-r-1b-english"
    assert report["meta"]["align_offset_ms"] == -50
    assert ("shipped" in report["cases"]["results"][0]) == (not mode)
