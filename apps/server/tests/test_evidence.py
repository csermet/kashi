"""pipeline/evidence.py: what a full job records, what a replay serves, and every way a replay must
refuse (the job then runs in full). Pure: numpy arrays stand in for torch tensors, no database."""

import base64
import hashlib
import inspect
import zlib
from types import SimpleNamespace

import numpy as np
import pytest

from kashi_server.pipeline import evidence
from kashi_server.pipeline.beats import Beats
from kashi_server.pipeline.energy import Energy, Section
from kashi_server.pipeline.level import Envelope

N = 16_000 * 12  # a 12 s track


def _track() -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.standard_normal(N).astype(np.float32)


def _model(name="org/aligner-eng"):
    return SimpleNamespace(config=SimpleNamespace(_name_or_path=name))


def _stride_of(n, frames, lib):
    """alignment.exact_stride_ms' contract: 20.0 when the frames fit a 320-sample hop."""
    return 20.0 if frames > 0 and abs(frames * 320 - n) < 640 else float(lib)


def _fake_emit(calls):
    """An emission pass: a deterministic function of the audio, with the library's own stride."""

    def emit(model, audio, *, exact_stride=False):
        calls.append((evidence.checkpoint_name(model), int(audio.shape[-1])))
        n = int(audio.shape[-1])
        frames = max(1, n // 320)
        body = np.asarray(audio[: frames * 320], dtype=np.float32).reshape(frames, 320)
        em = np.stack([body.mean(1), body.std(1), body.min(1), body.max(1)], axis=1)
        lib = evidence.library_stride(n, frames)
        return em.astype(np.float32), (_stride_of(n, frames, lib) if exact_stride else lib)

    return emit


SPANS = [(0, N), (16_000, 16_000 * 5), (16_000 * 4, 16_000 * 9), (16_000 * 8, N + 5_000)]


def _settings(**over):
    base = {
        "separation_mode": "always",
        "separation_model_filename": "vocals_mel_band_roformer.ckpt",
        "separation_mixback": 0.0,
        "structure_sections": True,
        "align_model_revisions": {"org/aligner-eng": "abc123"},
    }
    return SimpleNamespace(**{**base, **over})


VERSIONS = {"ctc-forced-aligner": "0.3.0+264e7a1", "librosa": "0.11.0",
            "audio-separator": "0.44.3", "ffmpeg": "8.1.2", "torch": "2.9.1+cpu",
            "transformers": "5.13.0", "numpy": "2.4.6", "yt-dlp": "2026.8.19"}

BEATS = Beats(bpm=118.3, confidence=0.71, times_ms=[500, 1007, 1514], downbeat_indices=[0])
ENERGY = Energy(rate_hz=4, values=[3, 50, 99, 0])
SECTIONS = [Section("high", 1000, 4000), Section("chorus", 2000, 9000)]
ENVELOPE = Envelope(bytes([0, 1, 254, 255, 7] * 120), crossings=bytes([3, 0, 255, 40, 9] * 120),
                    loudest=(123_456_789_012, 320))  # N / 320 = 600 frames


def _record(spans=SPANS, revision="abc123", track=None):
    """One full job's boundaries through a Recorder. Returns (record, what the job saw)."""
    track = _track() if track is None else track
    calls: list = []
    emit = _fake_emit(calls)
    recorder = evidence.Recorder(
        "youtube", "vid", evidence.expected_key(_settings(), VERSIONS, separate_first=True),
        made_by={"worker": "test"})
    seen: dict = {}
    with evidence.active(recorder):
        seen["download"] = evidence.download(
            lambda: SimpleNamespace(duration_s=12.3, abr=130.9, acodec="opus",
                                    info={"format_id": "251", "filesize": 3_000_000,
                                          "http_headers": {"x": "y"}}), None)
        seen["wav_s"] = evidence.wav_duration(lambda: 12.0)
        loaded = evidence.track(lambda: track)
        seen["emissions"] = [
            evidence.emissions(_model(), loaded[lo:hi], True, emit, _stride_of, revision)
            for lo, hi in spans
        ]
        # the vote's second whole-track pass, on a second load of the same wav
        again = evidence.track(lambda: track.copy())
        seen["emissions"].append(
            evidence.emissions(_model(), again, True, emit, _stride_of, revision))
        seen["onsets"] = evidence.onsets(lambda: [120, 480, 960])
        seen["envelope"] = evidence.envelope(lambda: ENVELOPE)
        seen["mix"] = evidence.mix_features(lambda: (BEATS, ENERGY, SECTIONS))
    seen["calls"] = calls
    return recorder, seen


def _replay(record, spans=SPANS, revision="abc123", name="org/aligner-eng"):
    replayer, why = evidence.replayer_for(record, _settings(), VERSIONS, separate_first=True)
    assert replayer is not None, why
    out: dict = {}
    with evidence.active(replayer):
        out["download"] = evidence.download(lambda: pytest.fail("downloaded"), "placeholder")
        out["wav_s"] = evidence.wav_duration(lambda: pytest.fail("read the wav"))
        loaded = evidence.track(lambda: pytest.fail("loaded the track"))
        no_pass = _fake_emit([])

        def forbidden(*a, **k):
            pytest.fail("an emission pass ran during the replay")
            return no_pass(*a, **k)

        out["emissions"] = [
            evidence.emissions(_model(name), loaded[..., lo:hi], True, forbidden, _stride_of,
                               revision)
            for lo, hi in spans
        ]
        out["emissions"].append(
            evidence.emissions(_model(name), loaded, True, forbidden, _stride_of, revision))
        out["onsets"] = evidence.onsets(lambda: pytest.fail("onsets"))
        out["envelope"] = evidence.envelope(lambda: pytest.fail("envelope"))
        out["mix"] = evidence.mix_features(lambda: pytest.fail("mix"))
    return out


# --- arrays ----------------------------------------------------------------------------------


@pytest.mark.parametrize("dtype", ["float32", "float16", "float64"])
def test_encoding_is_lossless(dtype):
    rng = np.random.default_rng(1)
    a = (rng.standard_normal((97, 39)) * 30).astype(dtype)
    a[0, 0], a[1, 1], a[2, 2] = -np.inf, np.nan, -0.0
    back = evidence.decode_array(evidence.encode_array(a), str(a.dtype), a.shape)
    assert back.tobytes() == a.tobytes()
    assert back.flags.writeable


def test_library_stride_is_the_aligners_own():
    # generate_emissions: math.ceil(float(n * 1000 / frames / 16000))
    for n, frames in [(16_000 * 30, 1499), (95_999, 299), (16_000 * 213, 10_650), (321, 1)]:
        assert evidence.library_stride(n, frames) == int(np.ceil(n * 1000 / frames / 16_000))


# --- the replayed track ----------------------------------------------------------------------


@pytest.mark.parametrize("a,b", [(0, N), (5, 9), (N - 3, N + 50), (N + 10, N + 20), (7, 7),
                                 (0, 1), (100, 50)])
def test_virtual_track_slices_like_the_real_one(a, b):
    real = np.arange(N)
    virtual = evidence.VirtualTrack(N)
    expected = real[..., a:b]
    got = virtual[..., a:b]
    assert got.shape == expected.shape
    if expected.size:
        assert (got.lo, got.lo + got.n) == (int(expected[0]), int(expected[-1]) + 1)
    nested = virtual[..., 1000:9000][..., 10:20]
    assert (nested.lo, nested.n) == (1010, 10)


@pytest.mark.parametrize("index", [(slice(0, 5), 0), slice(0, 10, 2), 3, (0, slice(0, 4))])
def test_virtual_track_refuses_what_the_aligner_never_does(index):
    with pytest.raises(evidence.EvidenceMissing):
        evidence.VirtualTrack(N)[index]


def test_view_span_of_numpy_slices():
    t = _track()
    assert evidence._view_span(t, t) == (0, N)
    assert evidence._view_span(t[100:900], t) == (100, 900)
    assert evidence._view_span(t[..., 100:900], t) == (100, 900)
    assert evidence._view_span(t[N - 5 :], t) == (N - 5, N)
    assert evidence._view_span(t.copy(), t) is None  # same values, other memory
    assert evidence._view_span(t[::2], t) is None  # not contiguous
    assert evidence._view_span(t.view(np.int16)[10:20], t) is None  # another element size
    assert evidence._view_span(t.reshape(2, -1), t) is None  # not 1-D


# --- keys ------------------------------------------------------------------------------------


def test_the_key_names_what_computed_the_values():
    key = evidence.expected_key(_settings(), VERSIONS, separate_first=True)
    assert key == {
        "format": evidence.EVIDENCE_FORMAT,
        "code": evidence.EVIDENCE_CODE,
        "separation": {"mode": "always", "model": "vocals_mel_band_roformer.ckpt", "mixback": 0.0,
                       "first": True},
        "structure_sections": True,
        "libraries": {"ctc-forced-aligner": "0.3.0+264e7a1", "librosa": "0.11.0",
                      "audio-separator": "0.44.3", "ffmpeg": "8.1.2"},
    }


def _key_with(path, value):
    key = evidence.expected_key(_settings(), VERSIONS, separate_first=True)
    node = key
    *parents, leaf = path
    for name in parents:
        node[name] = dict(node[name])
        node = node[name]
    node[leaf] = value
    return key


@pytest.mark.parametrize("path,value", [
    (("format",), evidence.EVIDENCE_FORMAT + 1),
    (("code", "emissions"), evidence.EVIDENCE_CODE["emissions"] + 1),
    (("code", "onsets"), evidence.EVIDENCE_CODE["onsets"] + 1),
    (("code", "mix"), evidence.EVIDENCE_CODE["mix"] + 1),
    (("separation", "mode"), "second_pass"),
    (("separation", "model"), "mel_band_roformer_kim_ft_unwa.ckpt"),
    (("separation", "mixback"), 0.1),
    (("separation", "first"), False),
    (("structure_sections",), False),
    (("libraries", "ctc-forced-aligner"), "0.3.0+0000000"),
    (("libraries", "librosa"), "0.11.1"),
    (("libraries", "audio-separator"), "0.45.0"),
    (("libraries", "ffmpeg"), "8.2"),
])
def test_any_key_field_that_differs_refuses_the_replay(path, value):
    record, _ = _record()
    stored = record.pack()
    stored.key = _key_with(path, value)
    replayer, why = evidence.replayer_for(stored, _settings(), VERSIONS, separate_first=True)
    assert replayer is None
    assert ".".join(path) in why


def test_torch_and_transformers_builds_are_provenance_not_key():
    """The GPU helper's CUDA build records, the cluster's CPU build replays: the replay serves the
    recorder's own emissions, so the build that would recompute them does not matter."""
    record, _ = _record()
    other = {**VERSIONS, "torch": "2.9.1+cu128", "transformers": "5.14.0", "numpy": "2.5.0",
             "yt-dlp": "2026.11.1"}
    replayer, why = evidence.replayer_for(record.pack(), _settings(), other, separate_first=True)
    assert replayer is not None, why


def test_a_bool_is_not_an_int_in_a_key():
    assert evidence.key_difference({"a": True}, {"a": 1}) is not None
    assert evidence.key_difference({"a": 0.0}, {"a": 0}) is None
    assert evidence.key_difference({"a": {"b": 1}}, {"a": {"b": 1, "c": None}}) is not None


def test_no_evidence_is_a_full_job():
    assert evidence.replayer_for(None, _settings(), VERSIONS, separate_first=True) == (
        None, "no evidence stored")


# --- record, then replay ---------------------------------------------------------------------


def test_replay_serves_exactly_what_the_job_read(tmp_path):
    recorder, seen = _record()
    assert recorder.usable
    store = evidence.DirStore(tmp_path)
    store.save(recorder.pack())
    loaded = store.load("youtube", "vid")
    assert loaded is not None
    out = _replay(loaded)
    for (em, stride), (em2, stride2) in zip(seen["emissions"], out["emissions"], strict=True):
        assert np.asarray(em2).tobytes() == np.asarray(em).tobytes()
        assert (stride2, type(stride2)) == (stride, type(stride))
    assert out["download"].duration_s == 12.3
    assert (out["download"].abr, out["download"].acodec) == (130.9, "opus")
    assert out["wav_s"] == 12.0
    assert out["onsets"] == [120, 480, 960]
    assert out["envelope"] == ENVELOPE
    assert out["mix"] == (BEATS, ENERGY, SECTIONS)


def test_every_served_array_is_its_own():
    """A consumer that writes into the emissions it got cannot change what the next pass gets."""
    recorder, _ = _record()
    replayer, _why = evidence.replayer_for(recorder.pack(), _settings(), VERSIONS,
                                           separate_first=True)
    assert replayer is not None
    with evidence.active(replayer):
        track = evidence.track(lambda: None)
        first, _ = evidence.emissions(_model(), track, True, _fake_emit([]), _stride_of, "abc123")
        kept = np.asarray(first).copy()
        np.asarray(first)[:] = 0
        again, _ = evidence.emissions(_model(), track, True, _fake_emit([]), _stride_of, "abc123")
    assert np.asarray(again).tobytes() == kept.tobytes()


def test_the_library_clock_replays_too():
    """A pass without exact_stride gets the library's int stride back, as from _emit."""
    recorder, _ = _record()
    replayer, _why = evidence.replayer_for(recorder.pack(), _settings(), VERSIONS,
                                           separate_first=True)
    assert replayer is not None
    with evidence.active(replayer):
        track = evidence.track(lambda: None)
        em, stride = evidence.emissions(_model(), track[..., 16_000 : 16_000 * 5], False,
                                        _fake_emit([]), _stride_of, "abc123")
    expected = _fake_emit([])(_model(), _track()[16_000 : 16_000 * 5], exact_stride=False)
    assert np.asarray(em).tobytes() == expected[0].tobytes()
    assert (stride, type(stride)) == (expected[1], int)


def test_the_second_whole_track_pass_is_served_from_the_first():
    _recorder, seen = _record()
    whole = [c for c in seen["calls"] if c[1] == N]
    assert len(whole) == 1  # recorded once, the vote's pass answered from it
    first, last = seen["emissions"][0][0], seen["emissions"][-1][0]
    assert np.asarray(first).tobytes() == np.asarray(last).tobytes()


@pytest.mark.parametrize("change", [
    {"revision": "def456"},
    {"name": "org/aligner-tur"},
    {"spans": [(0, N), (16_000 + 1, 16_000 * 5)]},
    {"spans": [(0, N), (16_000, 16_000 * 5 - 1)]},
    {"spans": [(0, N), (16_000 * 2, 16_000 * 6)]},
])
def test_an_emission_the_record_does_not_hold_is_missing(change):
    recorder, _ = _record()
    with pytest.raises(evidence.EvidenceMissing):
        _replay(recorder.pack(), **change)


@pytest.mark.parametrize("name", evidence.REQUIRED_META)
def test_a_record_lacking_what_every_replay_reads_is_refused_before_it_starts(name):
    """Refused up front, so a replay cannot stop after line QA has counted the job."""
    recorder, _ = _record()
    record = recorder.pack()
    meta = {k: v for k, v in record.meta.items() if k != name}
    stripped = evidence.Evidence(record.source_type, record.source_id, record.key, meta,
                                 record.blob)
    replayer, why = evidence.replayer_for(stripped, _settings(), VERSIONS, separate_first=True)
    assert replayer is None
    assert name in why
    with evidence.active(evidence.Replayer(stripped)), pytest.raises(evidence.EvidenceMissing):
        {"download": lambda: evidence.download(lambda: None, None),
         "track_samples": lambda: evidence.track(lambda: None),
         "track_sha256": lambda: evidence.Replayer(stripped).meta("track_sha256"),
         "wav_duration_s": lambda: evidence.wav_duration(lambda: None),
         "onsets": lambda: evidence.onsets(lambda: None),
         "envelope": lambda: evidence.envelope(lambda: None),
         "mix": lambda: evidence.mix_features(lambda: (None, None, None))}[name]()


def test_onset_detection_that_found_nothing_replays_as_nothing():
    """None from a detector that ran is a value ("no onsets"); a failure is not (below)."""
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        assert evidence.onsets(lambda: None) is None
    assert recorder.usable
    replayer = evidence.Replayer(recorder.pack())
    with evidence.active(replayer):
        assert evidence.onsets(lambda: pytest.fail("onsets")) is None


def test_mix_features_that_found_nothing_replay_as_nothing():
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        evidence.mix_features(lambda: (None, None, None))
    with evidence.active(evidence.Replayer(recorder.pack())):
        assert evidence.mix_features(lambda: pytest.fail("mix")) == (None, None, None)


# --- what makes a recording unusable ---------------------------------------------------------


def test_a_stride_other_than_the_derived_one_is_not_stored():
    recorder = evidence.Recorder("youtube", "vid", {})
    t = _track()
    with evidence.active(recorder):
        track = evidence.track(lambda: t)

        def odd(model, audio, *, exact_stride=False):
            em, _ = _fake_emit([])(model, audio, exact_stride=exact_stride)
            return em, 21

        evidence.emissions(_model(), track[0:16_000], True, odd, _stride_of, "abc123")
    assert not recorder.usable


def test_emissions_of_audio_that_is_not_the_track_are_not_stored():
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        evidence.track(_track)
        evidence.emissions(_model(), _track()[0:16_000].copy(), True, _fake_emit([]), _stride_of,
                           "abc123")
    assert not recorder.usable
    assert recorder.order == []


def test_two_tracks_of_different_length_are_not_stored():
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        evidence.track(_track)
        evidence.track(lambda: _track()[:-320])
    assert not recorder.usable


def test_two_wav_durations_are_not_stored():
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        evidence.wav_duration(lambda: 12.0)
        evidence.wav_duration(lambda: 12.5)
    assert not recorder.usable


def test_other_audio():
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        evidence.other_audio("the by-ear rung")  # keyed: the record still holds
        assert recorder.usable
        evidence.other_audio("a nightcore clock", keyed=False)
    assert not recorder.usable
    assert recorder.why_not == "a nightcore clock"
    with evidence.active(evidence.Replayer(recorder.pack())), pytest.raises(
        evidence.EvidenceMissing, match="by-ear"
    ):
        evidence.other_audio("the by-ear rung")


# --- the aligner during a replay ------------------------------------------------------------


def test_a_replay_loads_no_aligner_weights(monkeypatch):
    from kashi_server.pipeline import alignment

    def no_weights(*a, **k):
        raise AssertionError("loaded weights")

    tokenizer = object()
    monkeypatch.setattr(alignment, "_loaded", {})
    monkeypatch.setattr(alignment, "_load_checkpoint", no_weights)
    monkeypatch.setattr(alignment, "_load_tokenizer", lambda name: tokenizer)
    recorder, _ = _record()
    with evidence.active(evidence.Replayer(recorder.pack())):
        model, tok = alignment._load_model("org/aligner-tur")
    assert isinstance(model, evidence.StandInModel)
    assert evidence.checkpoint_name(model) == "org/aligner-tur"
    assert tok is tokenizer
    with pytest.raises(AssertionError, match="loaded weights"):
        alignment._load_model("org/aligner-tur")  # no session: the real load


# --- with no session nothing changes ---------------------------------------------------------


def test_without_a_session_every_hook_is_the_computation_itself():
    assert evidence.current() is None
    marker = object()
    assert evidence.download(lambda: marker, None) is marker
    assert evidence.separated(lambda: marker, None) is marker
    assert evidence.aligner_wav(lambda: marker, None) is marker
    assert evidence.wav_duration(lambda: marker) is marker
    assert evidence.onsets(lambda: marker) is marker
    assert evidence.mix_features(lambda: marker) is marker
    assert evidence.track(lambda: marker) is marker
    calls: list = []

    def compute(model, audio, *, exact_stride=False):
        calls.append((model, audio, exact_stride))
        return marker, 21

    assert evidence.emissions("m", "a", True, compute, _stride_of, None) == (marker, 21)
    assert calls == [("m", "a", True)]
    evidence.other_audio("anything", keyed=False)
    assert not evidence.replaying()


# --- the code that computes the recorded values ----------------------------------------------


def _source_hash(*objects) -> str:
    h = hashlib.sha256()
    for obj in objects:
        h.update(inspect.getsource(obj).encode())
    return h.hexdigest()[:16]


def test_changing_code_that_computes_evidence_bumps_its_version():
    """Stored evidence is only as good as the code that computed it. When one of these changes,
    bump the matching EVIDENCE_CODE entry (stored records then refuse and the next job of each
    song runs in full); a change that cannot alter a value (a comment) updates the pin only."""
    from kashi_server.pipeline import alignment, beats, download, energy, level, onsets, structure
    from kashi_server.vdl_kit import ytdlp_opts
    from kashi_server.worker import process

    pins = {
        ("emissions", evidence.EVIDENCE_CODE["emissions"]): _source_hash(
            alignment._emit, alignment._emissions, alignment._load_track,
            alignment._load_checkpoint),
        ("onsets", evidence.EVIDENCE_CODE["onsets"]): _source_hash(onsets),
        ("mix", evidence.EVIDENCE_CODE["mix"]): _source_hash(
            process._mix_features, beats, energy, structure),
        ("stem", evidence.EVIDENCE_CODE["stem"]): _source_hash(
            process._decode, process._separate_vocals, process._mix_back,
            process._aligner_decode, download.download_audio, download._probe_duration_s)
        + hashlib.sha256(repr(ytdlp_opts.AUDIO_FORMAT_TIERED).encode()).hexdigest()[:8],
        ("envelope", evidence.EVIDENCE_CODE["envelope"]): _source_hash(level),
    }
    assert pins == PINNED, "evidence code changed: bump EVIDENCE_CODE (or the pin, for a no-op)"


PINNED = {
    # 2026-11-16 (review round): onsets / mix re-pinned for the extractor_failed line, a change
    # no value can see; emissions and stem pin more of the code that makes them.
    ("emissions", 1): "33ae3ef375f64bae",  # + _load_checkpoint (second review round)
    ("onsets", 1): "9c9e3e3df887bad8",
    ("mix", 1): "c87a490b071edbc4",
    ("stem", 1): "bdbfb7499d44e949837d0602",
    # pipeline/level.py: 1 (2026-11-16); 2 (review): crossings, the loudest frame, integer steps
    ("envelope", 2): "4745c0e7b7a924d1",
}


# --- review round (2026-11-16) ------------------------------------------------------------------


def test_the_memo_never_answers_one_track_with_another():
    """Finding 6: two tracks of the same length (a second-pass stem after the mix) cut into the
    same slices; the second track's slice must be computed, not served from the first's."""
    calls: list = []
    emit = _fake_emit(calls)
    mix, stem = _track(), -_track()
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        first = evidence.track(lambda: mix)
        from_mix, _ = evidence.emissions(_model(), first[0:16_000], True, emit, _stride_of, "r")
        second = evidence.track(lambda: stem)
        from_stem, _ = evidence.emissions(_model(), second[0:16_000], True, emit, _stride_of, "r")
    assert len(calls) == 2
    assert np.asarray(from_stem).tobytes() != np.asarray(from_mix).tobytes()
    assert not recorder.usable and recorder.why_not == "two different tracks in one job"


def test_the_track_digest_is_recorded():
    recorder, _ = _record()
    assert recorder.meta["track_sha256"] == hashlib.sha256(_track().tobytes()).hexdigest()


def test_an_unpinned_checkpoint_is_neither_recorded_nor_replayed():
    """Finding 8: revision None would match itself after the hub's main moved."""
    recorder, _ = _record(revision=None)
    assert not recorder.usable and "no pinned revision" in (recorder.why_not or "")
    pinned, _ = _record()
    record = pinned.pack()
    for entry in record.meta["emissions"]:
        entry["revision"] = None
    with pytest.raises(evidence.EvidenceMissing, match="no pinned revision"):
        _replay(record, revision=None)


@pytest.mark.parametrize("extract", ["onsets", "beats", "energy", "structure"])
def test_an_extractor_that_fails_keeps_its_record_out(tmp_path, extract):
    """Finding 2: the real extractors swallow their errors and return None; under a recorder the
    failure must make the record unusable, while the job sees exactly the None it saw before."""
    from kashi_server.pipeline import beats, energy, onsets, structure

    missing = tmp_path / "missing.wav"
    run = {"onsets": lambda: onsets.detect_onsets(missing),
           "beats": lambda: beats.extract_beats(missing),
           "energy": lambda: energy.extract_energy(missing),
           "structure": lambda: structure.extract_structure(missing, None)}[extract]
    assert run() is None  # no session: the failure is invisible, as before
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        assert run() is None
    assert not recorder.usable and "failed" in (recorder.why_not or "")


def test_extractor_failed_touches_only_a_recorder():
    evidence.extractor_failed("x")  # no session: nothing
    recorder, _ = _record()
    with evidence.active(evidence.Replayer(recorder.pack())):
        evidence.extractor_failed("x")  # a replay runs no extractor; nothing to refuse


@pytest.mark.parametrize("hint_ms,conflict", [
    (None, False), (12_300, False), (15_300, False), (9_300, False), (15_301, True),
    (9_299, True), (300_000, True),
])
def test_a_player_duration_far_from_the_recorded_download_refuses(hint_ms, conflict):
    """Finding 5: the one identity a replay can check without fetching (recorded 12.3 s)."""
    recorder, _ = _record()
    found = evidence.duration_conflict(recorder.pack(), hint_ms)
    assert (found is not None) == conflict


def test_the_download_facts_and_the_recorder_are_kept():
    recorder, _ = _record()
    record = recorder.pack()
    assert record.meta["download"]["facts"] == {"format_id": "251", "filesize": 3_000_000}
    assert record.meta["made_by"] == {"worker": "test"}


def _slices(record):
    return sorted((e["lo"], e["hi"]) for e in record.meta["emissions"])


def test_a_later_job_keeps_the_slices_it_did_not_read():
    """Nit: a full job that read fewer or other slices of the same audio must not drop the
    stored ones a later plan may ask for again."""
    old, _ = _record()
    new, _ = _record(spans=[(0, N), (16_000 * 2, 16_000 * 6)])
    both = evidence.merged(old.pack(), new.pack())
    assert _slices(both) == sorted({*_slices(old.pack()), *_slices(new.pack())})
    out_old = _replay(both)  # the old plan replays from the merged record
    out_new = _replay(both, spans=[(0, N), (16_000 * 2, 16_000 * 6)])
    assert len(out_old["emissions"]) == len(SPANS) + 1 and len(out_new["emissions"]) == 3


def test_another_audio_or_key_is_not_merged():
    old, _ = _record()
    other_audio, _ = _record(track=-_track(), spans=[(0, N)])
    assert _slices(evidence.merged(old.pack(), other_audio.pack())) == [(0, N)]
    other_key = old.pack()
    other_key.key = {**other_key.key, "format": 0}
    same_audio, _ = _record(spans=[(0, N)])
    assert _slices(evidence.merged(other_key, same_audio.pack())) == [(0, N)]
    assert evidence.merged(None, same_audio.pack()) is not None


def test_a_superseded_revision_is_not_merged():
    old, _ = _record()
    new, _ = _record(revision="def456", spans=[(0, N)])
    merged = evidence.merged(old.pack(), new.pack())
    assert {e["revision"] for e in merged.meta["emissions"]} == {"def456"}



# --- second review round (2026-11-16) -----------------------------------------------------------


@pytest.mark.parametrize("damage", ["trailing", "truncated", "garbage", "other"])
def test_an_index_applied_to_other_bytes_is_missing(damage):
    """Finding 5: zlib ignores trailing bytes, so an index read before another writer replaced the
    blob could decode a slice of the other record."""
    a = (np.arange(64, dtype=np.float32) / 7).reshape(16, 4)
    blob = evidence.encode_array(a)
    bad = {"trailing": blob + evidence.encode_array(a),
           "truncated": blob[:-3],
           "garbage": b"not zlib at all",
           "other": evidence.encode_array(a[:8])}[damage]
    with pytest.raises(evidence.EvidenceMissing):
        evidence.decode_array(bad, "float32", (16, 4))


@pytest.mark.parametrize("recorded_hint,hint_ms,conflict", [
    (25_000, 25_000, False), (25_000, 28_000, False), (25_000, 28_001, True),
    (25_000, 21_999, True), (25_000, 12_300, True), (None, 12_300, False), (None, 25_000, True),
    (25_000, None, False),
])
def test_the_player_hint_is_compared_with_the_recorded_one(recorded_hint, hint_ms, conflict):
    """Finding 4: hint against the recorded job's hint (the download sits 12.3 s); without one,
    against the download."""
    recorder, _ = _record()
    recorder.meta["hint_duration_ms"] = recorded_hint
    found = evidence.duration_conflict(recorder.pack(), hint_ms)
    assert (found is not None) == conflict


def test_a_key_library_without_a_version_is_neither_recorded_nor_replayed():
    """Finding 8: None would equal None after either side changed."""
    unknown = {**VERSIONS, "ffmpeg": None}
    recorder = evidence.Recorder(
        "youtube", "vid", evidence.expected_key(_settings(), unknown, separate_first=True))
    assert not recorder.usable and recorder.why_not == "no version for ffmpeg"
    good, _ = _record()
    replayer, why = evidence.replayer_for(good.pack(), _settings(), unknown, separate_first=True)
    assert replayer is None and why == "no version for ffmpeg"


def test_a_recorder_fault_costs_the_record_not_the_job(monkeypatch):
    """Finding 6: the recorder's own work fails; the computation's result still reaches the job."""
    def broken(audio):
        raise MemoryError("digest")

    monkeypatch.setattr(evidence, "_digest", broken)
    recorder = evidence.Recorder("youtube", "vid", {})
    t = _track()
    with evidence.active(recorder):
        assert evidence.track(lambda: t) is t
        em, _ = evidence.emissions(_model(), t[0:16_000], True, _fake_emit([]), _stride_of, "r")
        assert np.asarray(em).shape[1] == 4
    assert not recorder.usable and recorder.why_not == "recorder fault in the track: MemoryError"


def test_a_recording_that_never_aligned_reports_what_it_lacks():
    """Finding 3: such a record must not be stored over a complete one."""
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        evidence.download(lambda: SimpleNamespace(duration_s=1.0, abr=1.0, acodec="opus"), None)
        evidence.mix_features(lambda: (None, None, None))
    assert recorder.missing() == ["track_samples", "track_sha256", "onsets", "envelope"]
    complete, _ = _record()
    assert complete.missing() == []


def test_a_merged_record_is_capped():
    """Finding 10: a row must not grow by one plan per stamp change forever."""
    stored = None
    kept = []
    for start in range(2, 9):
        plan, _ = _record(spans=[(0, N), (16_000 * start, 16_000 * (start + 3))])
        new = plan.pack()
        stored = evidence.merged(stored, new)
        kept.append(len(stored.meta["emissions"]))
        assert len(stored.blob) <= evidence.MERGE_CAP_FACTOR * len(new.blob)
    assert max(kept) > 2  # earlier plans were kept, up to the cap


def test_the_ready_line_names_the_evidence_mode(monkeypatch):
    """Finding 10: a worker with a mode on says so where the pipeline settings are named."""
    from kashi_server.config import settings
    from kashi_server.worker import main

    monkeypatch.setattr(settings, "evidence_mode", "off")
    off = main._config_note()
    assert "evidence" not in off
    monkeypatch.setattr(settings, "evidence_mode", "replay")
    assert main._config_note() == off + ", evidence replay"


# --- the level envelope (2026-11-16) ---------------------------------------------------------


def test_the_envelope_is_kept_compact_and_replays_byte_for_byte():
    recorder, seen = _record()
    stored = recorder.pack().meta["envelope"]
    assert stored["frames"] == len(ENVELOPE.levels) and stored["step_ms"] == 20
    assert set(stored) == {"step_ms", "unit_db", "frames", "levels", "crossings", "loudest"}
    assert stored["loudest"] == [123_456_789_012, 320]
    assert len(stored["levels"]) < len(ENVELOPE.levels)  # zlib + base64 of a repetitive signal
    assert len(stored["crossings"]) < len(ENVELOPE.crossings)
    with evidence.active(evidence.Replayer(recorder.pack())):
        served = evidence.envelope(lambda: pytest.fail("read the wav"))
    assert served == seen["envelope"] == ENVELOPE


def _repacked(e, cut):
    """The stored envelope with `cut` frames taken off both byte strings, consistently."""
    def strings(name):
        return zlib.decompress(base64.b64decode(e[name]))[: len(ENVELOPE.levels) - cut]

    return {**e, "frames": e["frames"] - cut, "levels": evidence._pack_bytes(strings("levels")),
            "crossings": evidence._pack_bytes(strings("crossings"))}


def _replay_envelope(meta_change):
    recorder, _ = _record()
    record = recorder.pack()
    meta = {**record.meta, **meta_change(record.meta)}
    bad = evidence.Evidence(record.source_type, record.source_id, record.key, meta, record.blob)
    with evidence.active(evidence.Replayer(bad)):
        return evidence.envelope(lambda: pytest.fail("read the wav"))


@pytest.mark.parametrize("tamper", [
    lambda e: {**e, "frames": e["frames"] + 1},
    lambda e: {**e, "levels": e["levels"][:-4]},
    lambda e: {**e, "levels": "not base64 !"},
    lambda e: {k: v for k, v in e.items() if k != "levels"},
    lambda e: {**e, "crossings": evidence._pack_bytes(ENVELOPE.crossings[:-1])},
    lambda e: {k: v for k, v in e.items() if k != "crossings"},
    lambda e: {k: v for k, v in e.items() if k != "loudest"},
    lambda e: {**e, "loudest": [1, 2, 3]},
    lambda e: {**e, "step_ms": 10},
    lambda e: _repacked(e, 1),  # decodes, but 599 frames are not this track's 600
])
def test_an_envelope_that_does_not_decode_to_the_track_s_frames_is_missing(tamper):
    with pytest.raises(evidence.EvidenceMissing):
        _replay_envelope(lambda meta: {"envelope": tamper(meta["envelope"])})


@pytest.mark.parametrize(("samples", "served"), [
    (N - 320, False), (N - 319, True), (N, True), (N + 1, False)])
def test_the_envelope_s_frames_are_the_track_s_frames(samples, served):
    # 600 frames cover a track of 191 681 .. 192 000 samples (the last frame over what it has)
    def run():
        return _replay_envelope(lambda meta: {"track_samples": samples})

    if served:
        assert run() == ENVELOPE
    else:
        with pytest.raises(evidence.EvidenceMissing):
            run()


def test_a_failed_envelope_is_never_recorded():
    """None comes only from a failed read (level_envelope says so through extractor_failed):
    nothing is kept, so the record lacks what every replay reads."""
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        assert evidence.envelope(lambda: None) is None
    assert "envelope" not in recorder.meta and "envelope" in recorder.missing()


def test_without_a_session_the_envelope_is_the_computation():
    assert evidence.envelope(lambda: ENVELOPE) is ENVELOPE
