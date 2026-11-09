"""audit.run_local: one song through the worker's alignment without a database.

No model is downloaded and no torch is needed: ``ctc_forced_aligner`` is
replaced by a deterministic stand-in whose emissions come from the real
synthetic audio (frame loudness) and whose Viterbi spreads a text's words over
the voiced frames it is given. Everything of ours in between runs for real:
windows, the exact stride, hybrid stars, seams, the whole-track vote, regroup,
the offsets, line QA on the stem's onsets and vocal energy, the document
builder and its schema gate. Synthetic audio and lyrics only.
"""

import hashlib
import json
import math
import shutil
import sys
import types
import uuid
import wave
from pathlib import Path

import numpy as np
import pytest

from audit import compare, run_local
from kashi_server.pipeline import alignment

VID = "synthVid001"
SR = 16_000
HOP = 320  # the model family's frame: 20 ms
LINES = [
    "one two three", "four five six", "seven eight nine",
    "ten eleven twelve", "thirteen fourteen fifteen", "sixteen seventeen eighteen",
]
LINE_STARTS_MS = [2000, 5500, 9000, 12500, 16000, 19500]
WORD_STEP_MS, WORD_MS, TOTAL_MS = 600, 400, 24_000
# The stand-in's "segment" stars sit this far after its "edges" ones: past the
# hybrid's 400 ms, so every word keeps the edges clock (and its lead).
SEGMENT_SHIFT_FRAMES = 25
VOICED_RMS = 0.05


def _write_stem(path: Path) -> None:
    rng = np.random.default_rng(7)
    audio = rng.normal(0, 1e-4, TOTAL_MS * SR // 1000)
    t = np.arange(WORD_MS * SR // 1000) / SR
    burst = 0.3 * np.sin(2 * np.pi * 220 * t) * np.hanning(t.size) ** 0.1
    for start in LINE_STARTS_MS:
        for k in range(3):
            i = (start + k * WORD_STEP_MS) * SR // 1000
            audio[i : i + burst.size] += burst
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm.tobytes())


def _samples(path) -> np.ndarray:
    with wave.open(str(path), "rb") as f:
        raw = f.readframes(f.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768


def _record(**over):
    synced = "\n".join(f"[{s // 60000:02d}:{s // 1000 % 60:02d}.{s % 1000 // 10:02d}] {text}"
                       for s, text in zip(LINE_STARTS_MS, LINES, strict=True))
    rec = {"id": 4242, "trackName": "Synthetic", "artistName": "Nobody", "duration": 24.0,
           "instrumental": False, "syncedLyrics": synced, "plainLyrics": "\n".join(LINES)}
    return rec | over


@pytest.fixture(autouse=True)
def _live_settings(monkeypatch):
    """The shipped pipeline (conftest switches separation and windows off)."""
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "separation_mode", "always")
    monkeypatch.setattr(settings, "windowed_alignment", True)


@pytest.fixture
def heard(monkeypatch):
    """The ctc_forced_aligner stand-in; records what every pass was given."""
    seen: dict = {"loaded": [], "stars": []}

    def load_audio(path, dtype=None, device=None):
        samples = _samples(path)
        seen["loaded"].append(hashlib.sha256(samples.tobytes()).hexdigest())
        return samples

    def generate_emissions(model, audio, batch_size=4):
        n = audio.shape[-1]
        frames = (n - 400) // HOP + 1
        rms = np.array([math.sqrt(float(np.mean(audio[i * HOP : i * HOP + 400] ** 2)))
                        for i in range(frames)])
        # the library's own frame step is rounded up (exact_stride_ms fixes it)
        return rms[:, None], math.ceil(n / (SR / 1000) / frames)

    def preprocess_text(text, romanize, language, split_size, star_frequency):
        seen["stars"].append(star_frequency)
        return (text.split(), star_frequency), text.split()

    def get_alignments(emissions, tokens_starred, tokenizer):
        words, star = tokens_starred
        voiced = np.flatnonzero(emissions[:, 0] > VOICED_RMS)
        if not words or voiced.size == 0:
            return [], [], 0
        first, span = int(voiced[0]), int(voiced[-1]) + 1 - int(voiced[0])
        shift = SEGMENT_SHIFT_FRAMES if star == "segment" else 0
        last = emissions.shape[0] - 1
        segments = [(min(last, first + span * i // len(words) + shift),
                     min(last, first + span * (i + 1) // len(words) + shift))
                    for i in range(len(words))]
        return segments, [-0.1] * len(words), 0

    def postprocess_results(text_starred, spans, stride, scores):
        return [{"text": w, "start": s * stride / 1000, "end": e * stride / 1000, "score": sc}
                for w, (s, e), sc in zip(text_starred, spans, scores, strict=True)]

    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", types.SimpleNamespace(
        load_audio=load_audio, generate_emissions=generate_emissions,
        preprocess_text=preprocess_text, get_alignments=get_alignments,
        get_spans=lambda tokens, segments, blank: segments,
        postprocess_results=postprocess_results,
    ))
    model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model",
                        lambda name: (model, types.SimpleNamespace(get_vocab=dict)))
    # Its passes are no trellis: the line regions (2.31.0) have nothing to cut.
    monkeypatch.setattr(alignment, "_hold_line_starts", lambda *a, **k: None)
    # The stand-in has no letters to hear: the sheet shift check (2.29.1)
    # reads nothing and moves nothing, on both sides of every comparison.
    from kashi_server.worker import process
    monkeypatch.setattr(process, "heard_tokens", lambda wav, language: [])
    return seen


@pytest.fixture
def stem(tmp_path, monkeypatch):
    import audit.paths

    monkeypatch.setattr(audit.paths, "STEMS_DIR", tmp_path / "audit" / "stems")
    path = audit.paths.stem_16k_path(VID)
    _write_stem(path)
    return path


@pytest.fixture
def record_file(tmp_path):
    path = tmp_path / "record.json"
    path.write_text(json.dumps(_record()))
    return path


def _local(record_file, tmp_path, *extra):
    out, extras = tmp_path / "doc.json", tmp_path / "extras.json"
    assert run_local.main([VID, "--lyrics", str(record_file), "--out", str(out),
                           "--extras", str(extras), *extra]) == 0
    return json.loads(out.read_text()), json.loads(extras.read_text())


def _without_clock(doc):
    return {k: v for k, v in doc.items() if k != "generated_at"}


# --- the local run ------------------------------------------------------------

def test_an_anchored_run_writes_a_valid_windowed_document(stem, heard, record_file, tmp_path):
    doc, extras = _local(record_file, tmp_path)
    a = doc["alignment"]
    assert a["method"] == "ctc-forced-aligner/mms-300m+line-windowed"
    assert (a["vocals_separated"], a["lyrics_source"], a["lyrics_source_id"]) == (True, "lrclib",
                                                                                  4242)
    assert doc["track"]["source"] == {"type": "youtube", "id": VID}
    assert (doc["track"]["title"], doc["track"]["duration_ms"]) == ("Synthetic", 24_000)
    assert [ln["text"] for ln in doc["lines"]] == LINES
    assert all(len(ln["words"]) == 3 for ln in doc["lines"])
    assert "edges" in heard["stars"] and "segment" in heard["stars"]  # hybrid windows ran
    assert extras["anchors"] is True and extras["separations"] == 1
    assert extras["onsets"] and extras["vocal_energy"]["frames"]
    assert {w["source"] for chunk in extras["words"] for w in chunk} == {"edges", "whole"}
    assert extras["settings"]["separation_mode"] == "always"


def test_the_aligner_hears_the_cached_stem_and_nothing_is_separated(
        stem, heard, record_file, tmp_path):
    from kashi_server.worker import process

    real_separate = process._separate_vocals
    _local(record_file, tmp_path)
    stem_hash = hashlib.sha256(_samples(stem).tobytes()).hexdigest()
    assert heard["loaded"] and set(heard["loaded"]) == {stem_hash}
    assert process._separate_vocals is real_separate  # the seam is put back


def test_no_anchors_keeps_the_lines_and_aligns_the_whole_track(
        stem, heard, record_file, tmp_path):
    doc, extras = _local(record_file, tmp_path, "--no-anchors")
    assert doc["alignment"]["method"] == "ctc-forced-aligner/mms-300m"
    assert [ln["text"] for ln in doc["lines"]] == LINES
    assert doc["alignment"]["lyrics_source"] == "lrclib"
    assert extras["anchors"] is False and "edges" not in heard["stars"]


def test_plain_text_lyrics_are_caller_lyrics(stem, heard, tmp_path):
    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("\n".join(LINES) + "\n\n")
    out = tmp_path / "doc.json"
    assert run_local.main([VID, "--lyrics", str(lyrics), "--out", str(out)]) == 2  # no artist
    doc, extras = _local(lyrics, tmp_path, "--title", "Typed", "--artist", "Me")
    assert doc["alignment"]["lyrics_source"] == "caller"
    assert "lyrics_source_id" not in doc["alignment"]
    assert doc["track"]["title"] == "Typed" and extras["anchors"] is False


def test_a_search_list_needs_its_record_picked(stem, heard, tmp_path):
    listing = tmp_path / "search.json"
    listing.write_text(json.dumps([_record(), _record(id=99, syncedLyrics=None)]))
    out = tmp_path / "doc.json"
    assert run_local.main([VID, "--lyrics", str(listing), "--out", str(out)]) == 2
    assert run_local.main([VID, "--lyrics", str(listing), "--out", str(out),
                           "--record-id", "99"]) == 0
    assert json.loads(out.read_text())["alignment"]["lyrics_source_id"] == 99


def test_a_missing_stem_is_an_error_not_a_separation(heard, record_file, tmp_path, monkeypatch,
                                                      capsys):
    import audit.paths

    monkeypatch.setattr(audit.paths, "STEMS_DIR", tmp_path / "empty")
    assert run_local.main([VID, "--lyrics", str(record_file),
                           "--out", str(tmp_path / "doc.json")]) == 2
    assert "audit.stems" in capsys.readouterr().err
    assert not (tmp_path / "doc.json").exists()


def test_only_the_live_separation_mode_is_replayed(stem, heard, record_file, tmp_path,
                                                    monkeypatch):
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "separation_mode", "second_pass")
    assert run_local.main([VID, "--lyrics", str(record_file),
                           "--out", str(tmp_path / "doc.json")]) == 2


def test_the_stems_meta_duration_is_the_tracks(stem, heard, record_file, tmp_path):
    import audit.paths

    audit.paths.stem_meta_path(VID).write_text(json.dumps({"duration_s": 24.4}))
    doc, _ = _local(record_file, tmp_path)
    assert doc["track"]["duration_ms"] == 24_400


LIVE_SEPARATOR = "vocals_mel_band_roformer.ckpt"


@pytest.mark.parametrize(("model", "mixback", "code"), [
    (LIVE_SEPARATOR, 0, 0),
    ("mel_band_roformer_kim_ft_unwa.ckpt", 0, 2),
    (LIVE_SEPARATOR, 0.1, 2),
])
def test_the_stem_must_come_from_the_live_separator(model, mixback, code, stem, heard,
                                                    record_file, tmp_path, monkeypatch, capsys):
    import audit.paths
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "separation_model_filename", LIVE_SEPARATOR)
    monkeypatch.setattr(settings, "separation_mixback", 0.0)
    audit.paths.stem_meta_path(VID).write_text(json.dumps({"model": model, "mixback": mixback}))
    out = tmp_path / "doc.json"
    assert run_local.main([VID, "--lyrics", str(record_file), "--out", str(out)]) == code
    assert out.exists() == (code == 0)
    if code:
        assert "remake the stem" in capsys.readouterr().err


def test_the_vocal_stem_is_not_a_mix(stem, heard, record_file, tmp_path, capsys):
    import audit.paths

    full_rate = audit.paths.stem_path(VID)
    _write_stem(full_rate)
    for mix in (full_rate, stem):
        out = tmp_path / "doc.json"
        assert run_local.main([VID, "--lyrics", str(record_file), "--out", str(out),
                               "--mix", str(mix)]) == 2
        assert "not the full mix" in capsys.readouterr().err
        assert not out.exists()


# --- the same document as the worker --------------------------------------------

def _worker_stages(monkeypatch, tmp_path, stem):
    """The worker's own process_job with its I/O replaced: the download is a
    file, the separator returns the stem (as audit.stems made it), lrclib
    serves the record, and the full-mix features are absent as in a local run
    without --mix."""
    from kashi_server.config import settings
    from kashi_server.pipeline.download import DownloadResult
    from kashi_server.pipeline.lrclib import lyrics_from_record
    from kashi_server.worker import process as wp

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    def download(source_id, dest_dir, **kw):
        path = Path(dest_dir) / "audio.webm"
        path.write_bytes(b"mix")
        return DownloadResult(path=path, abr=128.0, acodec="opus", duration_s=24.0, info={})

    def separate(audio, tmp, **kw):
        out = Path(tmp) / "separated" / "vocals.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(stem, out)
        return out

    monkeypatch.setattr(wp, "download_audio", download)
    monkeypatch.setattr(wp, "_separate_vocals", separate)
    monkeypatch.setattr(wp, "fetch_lyrics", lambda hints, base_url: lyrics_from_record(_record()))
    monkeypatch.setattr(wp, "search_candidates", lambda query, base_url: [])
    for name in ("extract_beats", "extract_energy"):
        monkeypatch.setattr(wp, name, lambda path: None)
    return wp


HINTS = {"title": "Synthetic", "artist": "Nobody", "duration_ms": 24_000}


def test_the_local_document_is_the_workers_document(stem, heard, record_file, tmp_path,
                                                     monkeypatch):
    from kashi_server.db.models import Job

    wp = _worker_stages(monkeypatch, tmp_path, stem)
    persisted: list[dict] = []
    monkeypatch.setattr(wp, "persist_processed_track",
                        lambda s, job, doc: persisted.append(json.loads(json.dumps(doc))))
    job = Job(id=uuid.uuid4(), source_type="youtube", source_id=VID, hints=dict(HINTS),
              options={}, attempts=1, status="downloading")
    wp.process_job(run_local.NullSession(), job)  # pyright: ignore[reportArgumentType]
    assert job.status == "completed" and len(persisted) == 1
    local, _ = _local(record_file, tmp_path)
    assert _without_clock(local) == _without_clock(persisted[0])


def test_the_local_document_is_what_the_worker_stores(stem, heard, record_file, tmp_path,
                                                       monkeypatch, db_session):
    from sqlalchemy import select

    from kashi_server import queue
    from kashi_server.db.models import ProcessedTrack

    wp = _worker_stages(monkeypatch, tmp_path, stem)
    queue.enqueue(db_session, source_type="youtube", source_id=VID, pipeline_major=1,
                  hints=dict(HINTS), options={}, requested_by=None)
    db_session.commit()
    job = queue.claim_next(db_session)
    db_session.commit()
    assert job is not None
    wp.process_job(db_session, job)
    stored = db_session.scalars(select(ProcessedTrack)).one().document
    local, _ = _local(record_file, tmp_path)
    assert _without_clock(local) == _without_clock(stored)


# --- a timing change shows ------------------------------------------------------

def test_a_changed_timing_constant_shows_in_the_local_document(stem, heard, record_file,
                                                                tmp_path, monkeypatch):
    """The windowed lead 20 ms shorter: every word the edges pass placed lands
    exactly 20 ms earlier, the whole-track vote's words net the lead out and
    stay (Faz 9 round 5), and the document is no longer the same one."""
    before, sources_before = _local(record_file, tmp_path)
    monkeypatch.setattr(alignment, "WINDOWED_LEAD_MS", alignment.WINDOWED_LEAD_MS - 20)
    after, sources_after = _local(record_file, tmp_path)
    by_source: dict[str, set[int]] = {}
    for lb, la, sb, sa in zip(before["lines"], after["lines"], sources_before["words"],
                              sources_after["words"], strict=True):
        for wb, wa, xb, xa in zip(lb["words"], la["words"], sb, sa, strict=True):
            if xb["source"] == xa["source"]:
                by_source.setdefault(xb["source"], set()).add(wa["start_ms"] - wb["start_ms"])
    assert by_source == {"edges": {-20}, "whole": {0}}
    ident = compare.identity(*compare.pair_documents(before, after))
    assert ident["moved_words"] > 0 and not compare.same_within(ident, 19)


def test_a_mix_feeds_the_mix_features_but_the_aligner_still_hears_the_stem(
        stem, heard, record_file, tmp_path, monkeypatch):
    from kashi_server.worker import process

    mix = tmp_path / "mix.wav"
    _write_stem(mix)
    with wave.open(str(mix), "rb") as f:
        params, frames = f.getparams(), bytearray(f.readframes(f.getnframes()))
    frames[1000:1010] = b"\x7f" * 10  # not the stem
    with wave.open(str(mix), "wb") as f:
        f.setparams(params)
        f.writeframes(bytes(frames))
    beats_from: list[Path] = []
    monkeypatch.setattr(process, "extract_beats", lambda p: beats_from.append(p) or None)
    monkeypatch.setattr(process, "extract_energy", lambda p: None)
    _local(record_file, tmp_path, "--mix", str(mix))
    stem_hash = hashlib.sha256(_samples(stem).tobytes()).hexdigest()
    assert set(heard["loaded"]) == {stem_hash}
    assert beats_from == [mix]


def test_a_document_the_schema_refuses_is_not_written(stem, heard, record_file, tmp_path,
                                                      monkeypatch, capsys):
    from kashi_server.worker import process

    real = process._assemble_document
    monkeypatch.setattr(process, "_assemble_document",
                        lambda *a, **k: {**real(*a, **k), "sync": "syllable"})
    out = tmp_path / "doc.json"
    assert run_local.main([VID, "--lyrics", str(record_file), "--out", str(out)]) == 1
    assert "schema" in capsys.readouterr().err and not out.exists()


# --- lrclib is never asked ------------------------------------------------------

MIRROR = "http://mirror.invalid:8080"


def _probe_during_run(monkeypatch, probe):
    """Run `probe()` in the middle of the local run (after alignment)."""
    from kashi_server.worker import process

    real = process._assemble_document

    def assemble(*a, **k):
        probe()
        return real(*a, **k)

    monkeypatch.setattr(process, "_assemble_document", assemble)


def _offline_client(reached, cls=None):
    """An httpx client whose transport never touches the network: reaching it
    means the guard let the request through."""
    import httpx

    def handler(request):
        reached.append(str(request.url))
        return httpx.Response(200, json={})

    if cls is httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return httpx.Client(transport=httpx.MockTransport(handler))


def _routes(reached):
    import asyncio

    import httpx

    from kashi_server.pipeline import publish
    from kashi_server.worker import process

    async def async_get(url):
        async with _offline_client(reached, httpx.AsyncClient) as client:
            await client.get(url)

    def swallowed():
        try:
            _offline_client(reached).get("https://lrclib.net/api/search?q=x")
        except Exception:  # noqa: BLE001 - a caller that eats the error
            pass

    return {
        "gateway": lambda: process.LRCLIB.fetch_lyrics({"title": "t", "artist": "a"}),
        "gateway search": lambda: process.LRCLIB.search_candidates("q"),
        "lookup": lambda: process.fetch_lyrics({}, "https://lrclib.net"),
        "search": lambda: process.search_candidates("q", "https://lrclib.net"),
        "publish": lambda: publish.publish_document({}, base_url="https://lrclib.net"),
        "lrclib.net": lambda: _offline_client(reached).get("https://lrclib.net/api/get/1"),
        "subdomain": lambda: _offline_client(reached).get("https://api.lrclib.net/x"),
        "primary": lambda: _offline_client(reached).get("http://127.0.0.1:9/api/get/1"),
        "mirror": lambda: _offline_client(reached).get(f"{MIRROR}/api/search?q=x"),
        "async": lambda: asyncio.run(async_get("https://lrclib.net/api/get/1")),
        "swallowed": swallowed,
    }


# route -> the layer that must refuse it (each layer names itself, so one that
# stopped working cannot hide behind the httpx layer underneath)
ROUTES = {
    "gateway": "(gateway fetch_lyrics)",
    "gateway search": "(gateway search_candidates)",
    "lookup": "(fetch_lyrics)",
    "search": "(search_candidates)",
    "publish": "(publish_document)",
    "lrclib.net": "(https://lrclib.net/api/get/1)",
    "subdomain": "(https://api.lrclib.net/x)",
    "primary": "(http://127.0.0.1:9/api/get/1)",
    "mirror": f"({MIRROR}/api/search?q=x)",
    "async": "(https://lrclib.net/api/get/1)",
    "swallowed": "tried to reach lrclib: ['https://lrclib.net/api/search?q=x']",
}


@pytest.mark.parametrize("route", ROUTES)
def test_every_lrclib_route_fails_the_run(route, stem, heard, record_file, tmp_path,
                                          monkeypatch, capsys):
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "lrclib_fallback_base_url", MIRROR)
    reached: list[str] = []
    _probe_during_run(monkeypatch, _routes(reached)[route])
    out = tmp_path / "doc.json"
    assert run_local.main([VID, "--lyrics", str(record_file), "--out", str(out)]) == 2
    assert ROUTES[route] in capsys.readouterr().err
    assert reached == [] and not out.exists()


def test_publishing_is_off_for_the_run_and_everything_is_put_back(
        stem, heard, record_file, tmp_path, monkeypatch):
    import httpx

    from kashi_server.config import settings
    from kashi_server.pipeline import publish
    from kashi_server.worker import process

    monkeypatch.setattr(settings, "lrclib_publish_enabled", True)  # as in prod
    monkeypatch.setattr(settings, "lrclib_publish_dry_run", False)
    during: list[tuple] = []
    _probe_during_run(monkeypatch, lambda: during.append(
        (settings.lrclib_publish_enabled, settings.lrclib_publish_dry_run)))
    before = (process.LRCLIB, process.fetch_lyrics, process.search_candidates,
              publish.publish_document, httpx.Client.send, httpx.AsyncClient.send)
    _local(record_file, tmp_path)
    assert during == [(False, True)]
    assert (settings.lrclib_publish_enabled, settings.lrclib_publish_dry_run) == (True, False)
    assert (process.LRCLIB, process.fetch_lyrics, process.search_candidates,
            publish.publish_document, httpx.Client.send, httpx.AsyncClient.send) == before


def test_other_hosts_still_go_through(stem, heard, record_file, tmp_path, monkeypatch):
    """The model hub must stay reachable (a first run downloads the checkpoint)."""
    reached: list[str] = []
    _probe_during_run(monkeypatch, lambda: _offline_client(reached).get(
        "https://huggingface.co/api/models/x"))
    _local(record_file, tmp_path)
    assert reached == ["https://huggingface.co/api/models/x"]


# --- the worker env example -------------------------------------------------------

def _env_file() -> dict[str, str]:
    """docker --env-file semantics: KEY=VALUE taken literally, # comments."""
    path = Path(run_local.__file__).with_name("worker.env.example")
    pairs = {}
    for raw in path.read_text().splitlines():
        if raw.strip() and not raw.lstrip().startswith("#"):
            key, _, value = raw.partition("=")
            pairs[key.strip()] = value
    return pairs


def test_the_worker_env_example_is_the_live_aligner(stem, heard, record_file, tmp_path,
                                                    monkeypatch):
    from kashi_server.config import Settings, settings

    env = _env_file()
    assert set(env) == {"ALIGN_MODELS", "STRUCTURE_SECTIONS", "SEPARATION_MODEL_FILENAME",
                        "OMP_NUM_THREADS"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    fresh = Settings()
    for key in env:
        if hasattr(fresh, key.lower()):
            monkeypatch.setattr(settings, key.lower(), getattr(fresh, key.lower()))
    eng, tur = settings.align_models["eng"], settings.align_models["tur"]
    assert (eng.checkpoint, eng.offset_ms) == ("jonatasgrosman/wav2vec2-xls-r-1b-english", -50)
    assert eng.offset_by_initial == {"vowel": -80, "fricative": -50, "plosive": -30,
                                     "sonorant": -20}
    assert (tur.checkpoint, tur.romanize) == ("mpoyraz/wav2vec2-xls-r-300m-cv7-turkish", False)
    assert settings.structure_sections is True
    doc, extras = _local(record_file, tmp_path)
    assert doc["alignment"]["method"] == \
        "ctc-forced-aligner/wav2vec2-xls-r-1b-english+line-windowed"
    assert extras["language"] == "eng"
    assert extras["aligner"]["offset_by_initial"]["vowel"] == -80
