"""process_job under settings.evidence_mode (pipeline/evidence.py): a full job stores what it read
off the audio, a later job of the song is rebuilt from it without one audio read, and every way a
replay cannot serve the job runs it in full. The stand-in aligner reads its emissions through the
real hooks (alignment._load_track / _emissions) and places its words from them, so a replay that
served other numbers would build another document."""

import logging
import types

import numpy as np
import pytest
from sqlalchemy import select

from kashi_server import queue
from kashi_server.config import settings
from kashi_server.db.models import ProcessedTrack, TrackEvidence
from kashi_server.pipeline import alignment, evidence
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.beats import Beats
from kashi_server.pipeline.download import DownloadResult
from kashi_server.pipeline.energy import Energy, Section
from kashi_server.pipeline.lrclib import LyricsText
from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.worker import process as wp

SECONDS = 20
N = 16_000 * SECONDS
LINES = ["hello world", "again here"]
STAMPS = [1_000, 9_000]
MODEL = "org/aligner-eng"
PALETTE = {"source": "default", "primary": "#e84545", "secondary": "#f5d76e",
           "background": "#1a1a2e", "text": "#ffffff", "accent": "#903749"}
TRACK = np.random.default_rng(11).standard_normal(N).astype(np.float32)


class Audio:
    """Every read of the audio the job makes, by kind."""

    def __init__(self) -> None:
        self.reads: dict[str, int] = {}
        self.slices = [(0, 16_000 * 9), (16_000 * 8, N)]
        self.fail_align = False
        self.weather = False

    def read(self, kind: str) -> None:
        self.reads[kind] = self.reads.get(kind, 0) + 1

    def total(self) -> int:
        return sum(self.reads.values())


def _model():
    return types.SimpleNamespace(config=types.SimpleNamespace(_name_or_path=MODEL), dtype=None,
                                 device="cpu")


@pytest.fixture
def audio(tmp_path, monkeypatch, lrclib_has_no_record):
    a = Audio()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "retry_delays_s", [0, 0, 0])
    monkeypatch.setattr(settings, "separation_mode", "always")
    monkeypatch.setattr(settings, "windowed_alignment", True)
    monkeypatch.setattr(settings, "align_model_revisions", {MODEL: "rev-1"})

    def download(source_id, dest_dir, **kw):
        a.read("download")
        return DownloadResult(path=dest_dir / "audio.webm", abr=129.5, acodec="opus",
                              duration_s=float(SECONDS), info={})

    def separate(audio_path, tmp, **kw):
        a.read("separate")
        return tmp / "vocals.wav"

    def decode(src, dest, rate, **kw):
        a.read("decode")
        return dest

    def wav_duration(path):
        a.read("wav_duration")
        return float(SECONDS)

    def onsets(path):
        a.read("onsets")
        return [990, 9_020]

    def mix(path):
        a.read("mix")
        return (Beats(120.0, 0.5, [0, 500, 1000], [0]), Energy(2, [10, 20] * SECONDS),
                [Section("high", 0, 5_000)])

    def load_audio(path, dtype, device):
        a.read("load_audio")
        return TRACK.copy()

    def emit(model, piece, *, exact_stride=False):
        a.read("emit")
        n = int(piece.shape[-1])
        frames = n // 320
        body = np.asarray(piece[: frames * 320]).reshape(frames, 320)
        em = np.stack([body.max(1), body.min(1), body.mean(1)], axis=1).astype(np.float32)
        lib = evidence.library_stride(n, frames)
        return em, (alignment.exact_stride_ms(n, frames, lib) if exact_stride else lib)

    def heard(wav, language):  # the sheet shift's ear: the whole-track pass, hearing nothing
        alignment._emissions(_model(), alignment._load_track(wav, _model()), exact_stride=True)
        return []

    def align(wav, line_texts, language, synced_starts_ms=None, **kw):
        if a.fail_align and evidence.replaying():
            raise RuntimeError("a replay-only fault")
        if a.weather and evidence.replaying():
            raise PipelineError("network", "connection reset")
        model = _model()
        track = alignment._load_track(wav, model)
        words = []
        for (lo, hi), text in zip(a.slices, line_texts, strict=True):
            em, stride = alignment._emissions(model, track[..., lo:hi], exact_stride=True)
            first = int(np.argmax(np.asarray(em)[:, 0]))  # placed by the emissions themselves
            start = round(lo / 16 + first * stride)
            words.append([AlignedWord(start, start + 300, text.split()[0], 0.8),
                          AlignedWord(start + 300, start + 600, text.split()[1], 0.8)])
        alignment._emissions(model, track, exact_stride=True)  # the vote's whole-track pass
        lines = [LineTiming(w[0].start_ms, w[-1].end_ms, t, 0.8)
                 for w, t in zip(words, line_texts, strict=True)]
        return AlignResult(sync="word", lines=lines, words_per_line=words, quality_score=0.8,
                           windowed=True, model_name=MODEL)

    monkeypatch.setattr(wp, "download_audio", download)
    monkeypatch.setattr(wp, "_separate_vocals", separate)
    monkeypatch.setattr(wp, "_decode", decode)
    monkeypatch.setattr(wp, "_wav_duration_s", wav_duration)
    monkeypatch.setattr(wp, "detect_onsets", onsets)
    monkeypatch.setattr(wp, "_mix_features", mix)
    monkeypatch.setattr(wp, "heard_tokens", heard)
    monkeypatch.setattr(wp, "align", align)
    monkeypatch.setattr(wp, "extract_palette", lambda url: PALETTE)
    monkeypatch.setattr(alignment, "_emit", emit)
    monkeypatch.setitem(__import__("sys").modules, "ctc_forced_aligner",
                        types.SimpleNamespace(load_audio=load_audio))
    monkeypatch.setattr(
        wp, "fetch_lyrics",
        lambda hints, base_url: LyricsText(LINES, "\n".join(LINES), 77, True, list(STAMPS),
                                           record_duration_s=float(SECONDS)),
    )
    return a


@pytest.fixture
def log(caplog, monkeypatch):
    """caplog for the worker's logger (a migration test earlier in the run disables it: see
    test_worker_process.worker_log)."""
    worker = logging.getLogger("kashi_server.worker.process")
    monkeypatch.setattr(worker, "disabled", False)
    caplog.set_level(logging.INFO, logger=worker.name)
    return caplog


def _job(db_session, options=None):
    queue.enqueue_reprocess(db_session, source_type="youtube", source_id="evidVid01",
                            pipeline_major=2, hints={"title": "T", "artist": "A"},
                            options=options or {}, requested_by=None)
    db_session.commit()
    job = queue.claim_next(db_session)
    db_session.commit()
    assert job is not None
    return job


def _run(db_session, monkeypatch, mode, options=None):
    monkeypatch.setattr(settings, "evidence_mode", mode)
    job = _job(db_session, options)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed", (job.error_type, job.error_message)
    doc = db_session.scalars(select(ProcessedTrack)).one().document
    return {k: v for k, v in doc.items() if k != "generated_at"}


def _row(db_session):
    db_session.expire_all()
    return db_session.scalars(select(TrackEvidence)).first()


def test_off_never_touches_the_store(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "off")
    assert audio.reads["download"] == 1
    assert _row(db_session) is None


def test_off_and_record_build_the_same_document(db_session, audio, monkeypatch):
    off = _run(db_session, monkeypatch, "off")
    recorded = _run(db_session, monkeypatch, "record")
    assert recorded == off


def test_a_recorded_song_is_rebuilt_without_one_audio_read(db_session, audio, monkeypatch, log):
    recorded = _run(db_session, monkeypatch, "record")
    row = _row(db_session)
    assert row is not None and row.size_bytes == len(row.emissions) > 0
    assert row.key == evidence.expected_key(settings)
    assert [(e["lo"], e["hi"]) for e in row.meta["emissions"]] == [(0, N), *audio.slices]
    assert row.meta["download"] == {"duration_s": 20.0, "abr": 129.5, "acodec": "opus"}
    assert audio.reads["emit"] == 3  # the vote's whole-track pass came from the sheet shift's
    audio.reads.clear()
    replayed = _run(db_session, monkeypatch, "replay")
    assert audio.total() == 0, audio.reads
    assert replayed == recorded
    assert "rebuilt from stored evidence, no download" in log.text


def test_replay_mode_without_evidence_runs_in_full_and_records(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "replay")
    assert audio.reads["download"] == 1
    assert _row(db_session) is not None


@pytest.mark.parametrize("field,value", [
    ("separation_model_filename", "mel_band_roformer_kim_ft_unwa.ckpt"),
    ("separation_mixback", 0.25),
    ("structure_sections", False),
])
def test_a_key_that_no_longer_matches_runs_in_full(db_session, audio, monkeypatch, field, value):
    _run(db_session, monkeypatch, "record")
    monkeypatch.setattr(settings, field, value)
    audio.reads.clear()
    _run(db_session, monkeypatch, "replay")
    assert audio.reads["download"] == 1
    assert _row(db_session).key == evidence.expected_key(settings)  # the full job re-recorded


def test_changed_evidence_code_runs_in_full(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    bumped = evidence.EVIDENCE_CODE["emissions"] + 1
    monkeypatch.setitem(evidence.EVIDENCE_CODE, "emissions", bumped)
    audio.reads.clear()
    _run(db_session, monkeypatch, "replay")
    assert audio.reads["download"] == 1


def test_another_revision_of_the_aligner_runs_in_full(db_session, audio, monkeypatch):
    recorded = _run(db_session, monkeypatch, "record")
    monkeypatch.setattr(settings, "align_model_revisions", {MODEL: "rev-2"})
    audio.reads.clear()
    rebuilt = _run(db_session, monkeypatch, "replay")
    assert audio.reads["download"] == 1 and audio.reads["emit"] == 3
    assert rebuilt == recorded  # same stand-in numbers: the full job agrees
    assert {e["revision"] for e in _row(db_session).meta["emissions"]} == {"rev-2"}


def test_a_window_cut_one_sample_differently_runs_in_full(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    audio.slices = [(0, 16_000 * 9 + 1), (16_000 * 8, N)]
    audio.reads.clear()
    _run(db_session, monkeypatch, "replay")
    assert audio.reads["download"] == 1


def test_a_nightcore_clock_runs_in_full_and_is_not_stored(db_session, audio, monkeypatch, log):
    _run(db_session, monkeypatch, "record")
    before = _row(db_session).updated_at
    monkeypatch.setattr(wp, "_wav_duration_s", lambda p: SECONDS * 1.25)
    audio.reads.clear()
    _run(db_session, monkeypatch, "replay", {"speed_factor": 1.25, "lyrics_text": "\n".join(LINES)})
    assert audio.reads["download"] == 1
    assert "replay stopped (a nightcore clock)" in log.text
    assert "evidence not stored (a nightcore clock)" in log.text
    assert _row(db_session).updated_at == before


def test_any_replay_fault_costs_a_download_not_the_job(db_session, audio, monkeypatch, log):
    recorded = _run(db_session, monkeypatch, "record")
    audio.fail_align = True
    audio.reads.clear()
    rebuilt = _run(db_session, monkeypatch, "replay")
    assert audio.reads["download"] == 1
    assert rebuilt == recorded
    assert "replay failed — full job" in log.text


def test_a_store_that_fails_never_fails_the_job(db_session, audio, monkeypatch, log):
    def broken(self, record):
        raise RuntimeError("disk full")

    monkeypatch.setattr(evidence.DbStore, "save", broken)
    _run(db_session, monkeypatch, "record")
    assert _row(db_session) is None
    assert "evidence not stored" in log.text
    assert "crashed" not in log.text


def test_lrclib_weather_during_a_replay_defers_like_a_full_job(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    audio.reads.clear()

    def busy(hints, base_url):
        raise PipelineError("lrclib_unavailable", "503")

    monkeypatch.setattr(wp, "fetch_lyrics", busy)
    monkeypatch.setattr(settings, "evidence_mode", "replay")
    job = _job(db_session)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "queued" and job.deferrals == 1
    assert audio.total() == 0


def test_weather_inside_a_replay_is_retried_not_downloaded(db_session, audio, monkeypatch):
    """A transient error is not the replay's fault: the job retries, as a full job would."""
    _run(db_session, monkeypatch, "record")
    audio.weather = True
    audio.reads.clear()
    monkeypatch.setattr(settings, "evidence_mode", "replay")
    job = _job(db_session)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert (job.status, job.error_type) == ("queued", None)
    assert audio.total() == 0


def test_a_deferred_full_job_stores_nothing(db_session, audio, monkeypatch):
    def busy(hints, base_url):
        raise PipelineError("lrclib_unavailable", "503")

    monkeypatch.setattr(wp, "fetch_lyrics", busy)
    monkeypatch.setattr(settings, "evidence_mode", "record")
    job = _job(db_session)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "queued"
    assert _row(db_session) is None


def test_the_by_ear_rung_needs_the_audio(db_session, audio, monkeypatch, log):
    """The lookup misses now: the rung that transcribes the mix cannot be replayed."""
    _run(db_session, monkeypatch, "record")
    record = {"id": 99, "trackName": "T", "artistName": "A", "duration": SECONDS,
              "plainLyrics": "\n".join(LINES),
              "syncedLyrics": "[00:01.00] hello world\n[00:09.00] again here"}

    def miss(hints, base_url):
        raise PipelineError("lyrics_not_found", "no record")

    monkeypatch.setattr(wp, "fetch_lyrics", miss)
    monkeypatch.setattr(wp, "search_candidates", lambda query, base_url: [record])
    monkeypatch.setattr(wp, "transcribe", lambda wav, model: "hello world again here")
    audio.reads.clear()
    _run(db_session, monkeypatch, "replay")
    assert audio.reads["download"] == 1
    assert "replay stopped (the by-ear rung)" in log.text


# --- the back-fill: evidence_only --------------------------------------------------------------


def _stored(db_session):
    db_session.expire_all()
    row = db_session.scalars(select(ProcessedTrack)).one()
    return row.etag, row.job_id


def test_evidence_only_records_and_keeps_the_stored_document(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "off")
    before = _stored(db_session)
    monkeypatch.setattr(settings, "evidence_mode", "record")
    job = _job(db_session, {"evidence_only": True})
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert audio.reads["download"] == 2
    assert _stored(db_session) == before
    assert _row(db_session) is not None


def test_evidence_only_over_matching_evidence_reads_no_audio(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "record")
    before = _stored(db_session)
    audio.reads.clear()
    monkeypatch.setattr(settings, "evidence_mode", "replay")
    job = _job(db_session, {"evidence_only": True})
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert audio.total() == 0
    assert _stored(db_session) == before


def test_evidence_only_on_a_worker_without_evidence_does_nothing(db_session, audio, monkeypatch):
    _run(db_session, monkeypatch, "off")
    before = _stored(db_session)
    audio.reads.clear()
    monkeypatch.setattr(settings, "evidence_mode", "off")
    job = _job(db_session, {"evidence_only": True})
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert audio.total() == 0
    assert _stored(db_session) == before
    assert _row(db_session) is None
