"""pipeline/evidence.py: what a full job records, what a replay serves, and every way a replay must
refuse (the job then runs in full). Pure: numpy arrays stand in for torch tensors, no database."""

import hashlib
import inspect
from types import SimpleNamespace

import numpy as np
import pytest

from kashi_server.pipeline import evidence
from kashi_server.pipeline.beats import Beats
from kashi_server.pipeline.energy import Energy, Section

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


VERSIONS = {"ctc-forced-aligner": "0.3.0+264e7a1", "librosa": "0.11.0", "torch": "2.9.1+cpu",
            "transformers": "5.13.0", "numpy": "2.4.6"}

BEATS = Beats(bpm=118.3, confidence=0.71, times_ms=[500, 1007, 1514], downbeat_indices=[0])
ENERGY = Energy(rate_hz=4, values=[3, 50, 99, 0])
SECTIONS = [Section("high", 1000, 4000), Section("chorus", 2000, 9000)]


def _record(spans=SPANS, revision="abc123", track=None):
    """One full job's boundaries through a Recorder. Returns (record, what the job saw)."""
    track = _track() if track is None else track
    calls: list = []
    emit = _fake_emit(calls)
    recorder = evidence.Recorder("youtube", "vid", evidence.expected_key(_settings(), VERSIONS))
    seen: dict = {}
    with evidence.active(recorder):
        seen["download"] = evidence.download(
            lambda: SimpleNamespace(duration_s=12.3, abr=130.9, acodec="opus"), None)
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
        seen["mix"] = evidence.mix_features(lambda: (BEATS, ENERGY, SECTIONS))
    seen["calls"] = calls
    return recorder, seen


def _replay(record, spans=SPANS, revision="abc123", name="org/aligner-eng"):
    replayer, why = evidence.replayer_for(record, _settings(), VERSIONS)
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
    key = evidence.expected_key(_settings(), VERSIONS)
    assert key == {
        "format": evidence.EVIDENCE_FORMAT,
        "code": evidence.EVIDENCE_CODE,
        "separation": {"mode": "always", "model": "vocals_mel_band_roformer.ckpt", "mixback": 0.0},
        "structure_sections": True,
        "libraries": {"ctc-forced-aligner": "0.3.0+264e7a1", "librosa": "0.11.0"},
    }


def _key_with(path, value):
    key = evidence.expected_key(_settings(), VERSIONS)
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
    (("structure_sections",), False),
    (("libraries", "ctc-forced-aligner"), "0.3.0+0000000"),
    (("libraries", "librosa"), "0.11.1"),
])
def test_any_key_field_that_differs_refuses_the_replay(path, value):
    record, _ = _record()
    stored = record.pack()
    stored.key = _key_with(path, value)
    replayer, why = evidence.replayer_for(stored, _settings(), VERSIONS)
    assert replayer is None
    assert ".".join(path) in why


def test_torch_and_transformers_builds_are_provenance_not_key():
    """The GPU helper's CUDA build records, the cluster's CPU build replays: the replay serves the
    recorder's own emissions, so the build that would recompute them does not matter."""
    record, _ = _record()
    other = {**VERSIONS, "torch": "2.9.1+cu128", "transformers": "5.14.0", "numpy": "2.5.0"}
    replayer, why = evidence.replayer_for(record.pack(), _settings(), other)
    assert replayer is not None, why


def test_a_bool_is_not_an_int_in_a_key():
    assert evidence.key_difference({"a": True}, {"a": 1}) is not None
    assert evidence.key_difference({"a": 0.0}, {"a": 0}) is None
    assert evidence.key_difference({"a": {"b": 1}}, {"a": {"b": 1, "c": None}}) is not None


def test_no_evidence_is_a_full_job():
    assert evidence.replayer_for(None, _settings(), VERSIONS) == (None, "no evidence stored")


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
    assert out["mix"] == (BEATS, ENERGY, SECTIONS)


def test_the_library_clock_replays_too():
    """A pass without exact_stride gets the library's int stride back, as from _emit."""
    recorder, _ = _record()
    replayer, _why = evidence.replayer_for(recorder.pack(), _settings(), VERSIONS)
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


def test_a_value_never_recorded_is_missing():
    recorder, _ = _record()
    record = recorder.pack()
    for name in ("onsets", "mix", "download", "wav_duration_s", "track_samples"):
        meta = {k: v for k, v in record.meta.items() if k != name}
        stripped = evidence.Evidence(record.source_type, record.source_id, record.key, meta,
                                     record.blob)
        with pytest.raises(evidence.EvidenceMissing):
            _replay(stripped)


def test_failed_onset_detection_replays_as_failed():
    recorder = evidence.Recorder("youtube", "vid", {})
    with evidence.active(recorder):
        assert evidence.onsets(lambda: None) is None
    replayer = evidence.Replayer(recorder.pack())
    with evidence.active(replayer):
        assert evidence.onsets(lambda: pytest.fail("onsets")) is None


def test_mix_features_that_failed_replay_as_failed():
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
    from kashi_server.pipeline import alignment, beats, energy, onsets, structure
    from kashi_server.worker import process

    pins = {
        ("emissions", evidence.EVIDENCE_CODE["emissions"]): _source_hash(alignment._emit),
        ("onsets", evidence.EVIDENCE_CODE["onsets"]): _source_hash(onsets),
        ("mix", evidence.EVIDENCE_CODE["mix"]): _source_hash(
            process._mix_features, beats, energy, structure),
    }
    assert pins == PINNED, "evidence code changed: bump EVIDENCE_CODE (or the pin, for a no-op)"


PINNED = {
    ("emissions", 1): "fe5364920aa4324e",
    ("onsets", 1): "c4ce9dcc3f9d5e04",
    ("mix", 1): "c7ffac48abdc8fb6",
}
