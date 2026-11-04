"""audit.ext_ear and audit.ear_gates: the segment-timed second ear and its gates.
Synthetic audio, fake transcribers; no model is loaded, nothing is downloaded."""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from audit_synth import VID, document, ear, lines_of, vocabulary

from audit import ear_gates, ext_ear, finder
from audit.ext_ear import Transcript, piece_tokens, pieces, split_long, voiced_spans

VOCAB = vocabulary(120, seed=41)
SERVER = Path(__file__).resolve().parents[1]


def test_constants_are_the_finders_and_the_pre_registered_ones():
    from audit.analysis import SILENCE_DB

    assert ext_ear.MIN_SILENCE_MS == finder.LINE_GAP_MS == 500
    assert SILENCE_DB == -25.0
    assert (ext_ear.MAX_PIECE_MS, ext_ear.STRIDE_MS, ext_ear.SAMPLE_RATE) == (30_000, 20.0, 16_000)
    assert ext_ear.WORD_TIME_LANGUAGES == {"en", "ja", "es", "de", "fr"}
    assert (ear_gates.HEALTHY_MAX_MS, ear_gates.SHIFTED_MIN_MS) == (500, 1000)
    assert (ear_gates.SILENT_PROB, ear_gates.SILENT_MIN_MS, ear_gates.INSIDE_SHARE,
            ear_gates.MAX_SILENT_SEGMENTS) == (0.2, 2000, 0.5, 0.05)
    assert ear_gates.SILENT_PROB == finder.BOXED_SILENT_PROB


def _frames(*runs):
    """1 ms frames from (ms, dB) runs."""
    out, t = [], 0
    for ms, db in runs:
        out += [(t + i, db) for i in range(ms)]
        t += ms
    return out


def test_a_silence_is_500_ms_below_minus_25_db():
    assert voiced_spans(_frames((1000, -10), (499, -30), (1500, -10)), 1) == [(0, 2999)]
    assert voiced_spans(_frames((1000, -10), (500, -30), (1500, -10)), 1) \
        == [(0, 1000), (1500, 3000)]
    # -25.0 itself is voiced (line QA: below -25 is silence)
    assert voiced_spans(_frames((1000, -10), (600, -25.0), (1000, -10)), 1) == [(0, 2600)]
    assert voiced_spans(_frames((1000, -10), (600, -25.1), (1000, -10)), 1) \
        == [(0, 1000), (1600, 2600)]


def test_leading_and_trailing_silence():
    assert voiced_spans(_frames((1000, -40), (1000, -10), (500, -40)), 1) == [(1000, 2000)]
    assert voiced_spans(_frames((1000, -40), (1000, -10), (499, -40)), 1) == [(1000, 2499)]
    assert voiced_spans(_frames((800, -40)), 1) == []


def test_pieces_are_at_most_30_s_cut_at_the_quietest_middle_frame():
    frames = _frames((30_000, -10))
    assert split_long((0, 30_000), frames) == [(0, 30_000)]
    frames = _frames((20_000, -10), (1, -24), (10_000, -10))       # one quiet-ish frame
    assert split_long((0, 30_001), frames) == [(0, 20_000), (20_000, 30_001)]
    long = _frames((70_000, -10))
    got = pieces(long, 1)
    assert all(e - s <= 30_000 for s, e in got) and got[0][0] == 0 and got[-1][1] == 70_000
    assert all(a[1] == b[0] for a, b in zip(got, got[1:], strict=False))


def test_letters_are_spread_evenly_over_the_piece():
    toks = piece_tokens(Transcript("Ab, cd!"), 1000, 1400)
    assert [(t["text"], t["start_ms"]) for t in toks] == [
        ("a", 1000), ("b", 1100), ("|", 1200), ("c", 1200), ("d", 1300)]
    timed = piece_tokens(Transcript("ab cd", words=[("ab", 0, 200), ("cd", 300, 700)]), 1000, 2000)
    assert [(t["text"], t["start_ms"]) for t in timed] == [
        ("a", 1000), ("b", 1100), ("|", 1300), ("c", 1300), ("d", 1500)]
    # script is kept as written
    assert [t["text"] for t in piece_tokens(Transcript("空に"), 0, 200)] == ["空", "に"]


class FakeTranscriber:
    name = "fake"

    def __init__(self, texts, word_times=False):
        self.texts, self.calls, self.word_times = list(texts), [], word_times

    def describe(self):
        return {"backend": "fake", "model": "fake/asr", "version": "0",
                "settings": {"aligner": "fake/aligner" if self.word_times else None}}

    def transcribe_many(self, audio, sr, language):
        self.calls.append(([len(a) for a in audio], sr, language))
        out = []
        for _ in audio:
            text = self.texts.pop(0) if self.texts else ""
            words = [(w, 0, 100) for w in text.split()] if self.word_times else None
            out.append(Transcript(text, "English", words))
        return out


def _song_audio(lines):
    """Loudness frames (16 ms hop) loud while each line is sung, plus silence."""
    import numpy as np

    hop = 16.0
    end = lines[-1][1] + 6000
    frames = [(round(i * hop), -40.0) for i in range(int(end / hop))]
    for text, t0 in lines:
        t1 = t0 + len("".join(text.split())) * 90
        frames = [(t, -8.0 if t0 <= t < t1 else db) for t, db in frames]
    return frames, hop, np.zeros(int(end * 16), dtype="float32")


def _hear_span_keys():
    tree = ast.parse((SERVER / "audit" / "ear.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_hear_span")
    ret = next(n for n in ast.walk(fn)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict))
    assert isinstance(ret.value, ast.Dict)
    return {k.value for k in ret.value.keys if isinstance(k, ast.Constant)}


def _lines(n=8, seed=5):
    texts = lines_of(VOCAB, n, 5, seed=seed)
    return [(t, 6000 + 5000 * i) for i, t in enumerate(texts)]


def test_the_ext_ear_has_the_whole_ear_shape_and_textmatch_reads_it():
    lines = _lines()
    frames, hop, samples = _song_audio(lines)
    fake = FakeTranscriber([t for t, _ in lines])
    out = ext_ear.transcribe_song(VID, frames, hop, samples, fake, "en")
    keys = _hear_span_keys()
    assert keys <= set(out) and keys == {"id", "model", "start_ms", "stride_ms", "text", "tokens",
                                         "letter_prob", "letter_prob_max", "frames", "finite"}
    assert out["timing"] == "segment" and out["word_times"] is None
    assert out["source"]["backend"] == "fake" and out["input"]["audio"] == "stem"
    assert len(out["segments"]) == len(lines)
    # the transcriber heard each piece's own samples (16 per ms), with the language
    lengths, sr, lang = fake.calls[0]
    assert sr == 16_000 and lang == "en"
    assert lengths == [(e - s) * 16 for s, e in pieces(frames, hop)]
    # finder and textmatch read the file unchanged: every line placed near its start
    doc = document(lines)
    offsets, borrowed = finder.line_offsets(doc, out)
    assert len(offsets) == len(lines) and not borrowed
    assert all(abs(o) <= 50 for o in offsets.values())


def test_word_times_are_marked():
    lines = _lines(3)
    frames, hop, samples = _song_audio(lines)
    out = ext_ear.transcribe_song(VID, frames, hop, samples,
                                  FakeTranscriber([t for t, _ in lines], word_times=True), "en")
    assert out["word_times"] == "fake/aligner" and all(s["word_times"] for s in out["segments"])


def test_write_atomic_leaves_only_the_file(tmp_path):
    dest = tmp_path / "ear" / VID / "whole-fake.json"
    ext_ear.write_atomic(dest, {"a": 1})
    assert json.loads(dest.read_text()) == {"a": 1}
    assert [p.name for p in dest.parent.iterdir()] == ["whole-fake.json"]


def test_cli_with_a_fake_backend(tmp_path, monkeypatch):
    import soundfile as sf

    from audit import analysis, stems
    from audit.analysis import SongAnalysis

    lines = _lines(3)
    frames, hop, samples = _song_audio(lines)
    wav = tmp_path / "stem.16k.wav"
    sf.write(str(wav), samples, 16_000)
    monkeypatch.setattr(analysis, "analyse", lambda vid: SongAnalysis(hop, frames, [], []))
    monkeypatch.setattr(stems, "decode_16k", lambda vid: wav)
    made = {}

    def factory(**kw):
        made.update(kw)
        return FakeTranscriber([t for t, _ in lines])

    monkeypatch.setitem(ext_ear.BACKENDS, "fake", factory)
    out = tmp_path / "out.json"
    assert ext_ear.main([VID, "--backend", "fake", "--language", "eng", "--out", str(out)]) == 0
    got = json.loads(out.read_text())
    assert got["id"] == VID and len(got["segments"]) == 3 and made == {}
    with pytest.raises(SystemExit):
        ext_ear.main([VID, "--backend", "fake", "--language", "xx"])
    with pytest.raises(SystemExit):
        ext_ear.main([VID, "--backend", "faster-whisper", "--word-times"])


def test_backends_are_imported_lazily():
    code = ("import sys; import audit.ext_ear, audit.ear_gates; "
            "print(sorted(m for m in ('torch', 'qwen_asr', 'faster_whisper') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=SERVER, capture_output=True, text=True,
                         env={"PYTHONPATH": f"{SERVER / 'src'}:{SERVER}", "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# --- gates ----------------------------------------------------------------------

def test_placements_equal_the_finders_at_the_default_window():
    lines = _lines()
    doc = document(lines)
    e = ear([(t, s - 3000 if i % 2 else s + 40) for i, (t, s) in enumerate(lines)])
    assert ear_gates.placements(doc, e) == finder.line_offsets(doc, e)


def test_silent_regions_are_longer_than_2_s_below_02():
    def curve(n_silent, value=0.1):
        return {"stride_ms": 20.0, "start_ms": 0,
                "letter_prob": [0.9] * 10 + [value] * n_silent + [0.9] * 10}
    assert ear_gates.silent_regions(curve(100)) == []              # exactly 2000 ms
    assert ear_gates.silent_regions(curve(101)) == [(200, 2220)]
    assert ear_gates.silent_regions(curve(200, value=0.2)) == []   # 0.2 is not below 0.2


def test_a_segment_counts_as_hallucinated_from_half_inside():
    regions = [(1000, 4000)]
    ext = {"segments": [{"start_ms": 3000, "end_ms": 5000, "text": "x"},    # 50 % inside
                        {"start_ms": 3001, "end_ms": 5000, "text": "x"},    # 49.95 %
                        {"start_ms": 0, "end_ms": 900, "text": " "}]}       # no text
    got = ear_gates.silent_segments(ext, regions)
    assert (got["segments"], got["inside_silence"]) == (2, 1)


def _report(ext_shift, cur_shift=40, expect="healthy", n=8, ext_lines=None):
    lines = _lines(n)
    doc = document(lines)
    cur = ear([(t, s + cur_shift) for t, s in lines])
    ext = ear([(t, s + ext_shift) for t, s in (ext_lines or lines)])
    ext["segments"] = [{"start_ms": s + ext_shift, "end_ms": s + ext_shift + 2000, "text": t}
                       for t, s in (ext_lines or lines)]
    return ear_gates.report(doc, cur, ext, expect, language="eng")


@pytest.mark.parametrize(("shift", "ok"), [(500, True), (501, False), (-500, True)])
def test_healthy_offset_gate(shift, ok):
    assert _report(shift)["gates"]["offset"] is ok


@pytest.mark.parametrize(("expect", "shift", "ok"), [
    ("sheet-late", -1000, False), ("sheet-late", -1001, True), ("sheet-late", 1500, False),
    ("sheet-early", 1001, True), ("sheet-early", 1000, False)])
def test_shifted_offset_gate_needs_the_sign(expect, shift, ok):
    assert _report(shift, cur_shift=shift, expect=expect)["gates"]["offset"] is ok


def test_placed_share_gate_and_silence_gate():
    lines = _lines()
    r = _report(40, ext_lines=lines[:7])                 # the external ear misses a line
    assert r["external"]["placed"] == 7 and r["current"]["placed"] == 8
    assert r["gates"]["placed_share"] is False and r["passed"] is False
    r = _report(40)
    assert r["gates"] == {"placed_share": True, "silence": True, "offset": True} and r["passed"]


def test_japanese_and_turkish_are_reported_not_gated():
    lines = _lines()
    doc = document(lines)
    cur = ear([(t, s) for t, s in lines])
    for lang in ("jpn", "tur"):
        r = ear_gates.report(doc, cur, cur, "healthy", language=lang)
        assert r["gated"] is False and r["passed"] is None
    doc["lines"][0]["text"] = "空に光る"
    assert ear_gates.song_language(doc) == "jpn"


def test_arm_pools_songs_and_leaves_reported_ones_out():
    good, bad = _report(40), _report(800)
    lines = _lines()
    jp = ear_gates.report(document(lines), ear(lines), ear(lines), "healthy", language="jpn")
    a = ear_gates.arm({"a": good, "b": good, "j": jp})
    assert a["passed"] and a["reported_only"] == ["j"] and a["songs"] == 2
    a = ear_gates.arm({"a": good, "b": bad})
    assert not a["passed"] and a["offset_misses"] == ["b"]
    assert not ear_gates.arm({"j": jp})["passed"]


def test_gates_cli_with_a_manifest(tmp_path, capsys):
    lines = _lines()
    (tmp_path / "doc.json").write_text(json.dumps(document(lines)))
    (tmp_path / "cur.json").write_text(json.dumps(ear([(t, s + 40) for t, s in lines])))
    ext = ear([(t, s + 60) for t, s in lines])
    ext["segments"] = []
    (tmp_path / "ext.json").write_text(json.dumps(ext))
    (tmp_path / "m.json").write_text(json.dumps([{"id": VID, "doc": "doc.json", "ear": "cur.json",
                                                  "ext": "ext.json", "expect": "healthy"}]))
    assert ear_gates.main(["--manifest", str(tmp_path / "m.json"), "--json",
                           str(tmp_path / "o.json")]) == 0
    printed = capsys.readouterr().out
    assert f"{VID} [other] pass" in printed and "arm: pass" in printed
    assert json.loads((tmp_path / "o.json").read_text())["arm"]["passed"]


def test_pieces_never_merge_into_one_word():
    from audit.anchors import heard_words

    out = ext_ear.build(VID, [(0, 400), (900, 1300)], [Transcript("kala"), Transcript("mito")],
                        2000, {"model": "fake"}, "stem")
    assert [(w.text, w.start_ms) for w in heard_words(out["tokens"])] == [("kala", 0),
                                                                           ("mito", 900)]
    assert out["text"] == "kala mito"
