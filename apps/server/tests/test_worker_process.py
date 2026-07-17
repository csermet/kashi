"""process_job with mocked stages: status flow, retries, cancellation, and the
audio-deletion guarantee on every exit path."""

from pathlib import Path

import pytest

from kashi_server import queue
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.download import DownloadResult
from kashi_server.pipeline.lrclib import LyricsText
from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.worker import process as wp
from kashi_server.worker.main import sweep_orphans


@pytest.fixture()
def job(db_session):
    queue.enqueue(
        db_session,
        source_type="youtube",
        source_id="workerVid01",
        pipeline_major=1,
        hints={"title": "T", "artist": "A", "duration_ms": 200_000},
        options={},
        requested_by=None,
    )
    db_session.commit()
    claimed = queue.claim_next(db_session)
    db_session.commit()
    assert claimed is not None
    return claimed


@pytest.fixture()
def scratch(tmp_path, monkeypatch):
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "retry_delays_s", [0, 0, 0])
    return tmp_path


def _happy_stages(monkeypatch, scratch):
    def fake_download(source_id, dest_dir, **kw):
        path = Path(dest_dir) / "audio.webm"
        path.write_bytes(b"x" * 1024)
        return DownloadResult(path=path, abr=128.0, acodec="opus", duration_s=200.0, info={})

    def fake_align_stage(s, j, tmp, audio, lyrics, **kw):
        result = AlignResult(
            sync="word",
            lines=[LineTiming(0, 1000, "hello world", 0.8)],
            words_per_line=[
                [AlignedWord(0, 400, "hello", 0.8), AlignedWord(500, 1000, "world", 0.8)]
            ],
            quality_score=0.8,
        )
        return result, False

    monkeypatch.setattr(wp, "download_audio", fake_download)
    monkeypatch.setattr(wp, "_align_stage", fake_align_stage)
    monkeypatch.setattr(
        wp,
        "fetch_lyrics",
        lambda hints, base_url: LyricsText(["hello world"], "hello world", 5, True),
    )
    monkeypatch.setattr(wp, "extract_beats", lambda p: None)
    monkeypatch.setattr(
        wp,
        "extract_palette",
        lambda url: {
            "source": "default",
            "primary": "#e84545",
            "secondary": "#f5d76e",
            "background": "#1a1a2e",
            "text": "#ffffff",
            "accent": "#903749",
        },
    )


def test_happy_path_completes_and_cleans_tmp(db_session, job, scratch, monkeypatch):
    _happy_stages(monkeypatch, scratch)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert list(scratch.glob("job-*")) == []  # audio gone

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    row = db_session.scalars(select(ProcessedTrack)).one()
    assert row.source_id == "workerVid01" and row.sync == "word"
    assert row.document["track"]["duration_ms"] == 200_000


def test_transient_failure_retries_then_cleans(db_session, job, scratch, monkeypatch):
    def flaky_download(source_id, dest_dir, **kw):
        raise PipelineError("network", "connection reset")

    monkeypatch.setattr(wp, "download_audio", flaky_download)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "queued"  # retry scheduled
    assert job.attempts == 1
    assert list(scratch.glob("job-*")) == []


def test_permanent_failure_fails_once(db_session, job, scratch, monkeypatch):
    monkeypatch.setattr(
        wp,
        "download_audio",
        lambda *a, **k: (_ for _ in ()).throw(PipelineError("copyright", "blocked")),
    )
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "copyright"
    assert list(scratch.glob("job-*")) == []


def test_transient_exhaustion_becomes_failed(db_session, job, scratch, monkeypatch):
    monkeypatch.setattr(
        wp,
        "download_audio",
        lambda *a, **k: (_ for _ in ()).throw(PipelineError("network", "reset")),
    )
    job.attempts = job.max_attempts  # already at the cap
    db_session.flush()
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "network"


def test_unexpected_crash_maps_to_other_and_cleans(db_session, job, scratch, monkeypatch):
    monkeypatch.setattr(
        wp, "download_audio", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    job.attempts = job.max_attempts
    db_session.flush()
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "other"
    assert list(scratch.glob("job-*")) == []


def test_cancel_between_stages_aborts_quietly(db_session, job, scratch, monkeypatch):
    def cancel_then_download(source_id, dest_dir, **kw):
        # An admin cancels while the download runs; the NEXT checkpoint aborts.
        from sqlalchemy import text

        db_session.execute(text("UPDATE jobs SET status='canceled' WHERE id=:id"), {"id": job.id})
        db_session.commit()
        path = Path(dest_dir) / "audio.webm"
        path.write_bytes(b"x")
        return DownloadResult(path=path, abr=128.0, acodec="opus", duration_s=200.0, info={})

    monkeypatch.setattr(wp, "download_audio", cancel_then_download)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "canceled"
    assert list(scratch.glob("job-*")) == []  # tmp cleaned even on abort


def test_line_qa_snaps_drifted_line_in_persisted_document(db_session, job, scratch, monkeypatch):
    """End-to-end through process_job: a line far off its lrclib synced time is
    snapped and persisted WITHOUT words, while the good lines keep theirs."""
    _happy_stages(monkeypatch, scratch)
    texts = ["one a", "two b", "three c", "four d"]
    lines = [
        LineTiming(1000, 1800, texts[0], 0.9),
        LineTiming(5000, 5800, texts[1], 0.9),
        LineTiming(9000, 9800, texts[2], 0.9),
        LineTiming(34_000, 34_800, texts[3], 0.1),  # sung at 46 s — drifted
    ]
    # Words spread across the line (realistic density — the border-case gate
    # must not fire on the healthy neighbours of the flagged line).
    words = [
        [
            AlignedWord(ln.start_ms, ln.start_ms + 1400, ln.text.split()[0], 0.8),
            AlignedWord(ln.start_ms + 1500, ln.start_ms + 2900, ln.text.split()[1], 0.8),
        ]
        for ln in lines
    ]
    monkeypatch.setattr(
        wp,
        "_align_stage",
        lambda s, j, tmp, audio, lyrics, **kw: (
            AlignResult(sync="word", lines=lines, words_per_line=words, quality_score=0.8),
            False,
        ),
    )
    monkeypatch.setattr(
        wp,
        "fetch_lyrics",
        lambda hints, base_url: LyricsText(
            texts, " ".join(texts), 5, True, synced_starts_ms=[1000, 5000, 9000, 46_000]
        ),
    )
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    row = db_session.scalars(select(ProcessedTrack)).one()
    doc = row.document
    from kashi_server.version import PIPELINE_VERSION

    assert doc["pipeline_version"] == PIPELINE_VERSION
    assert doc["sync"] == "word"
    assert doc["lines"][3]["start_ms"] == 46_000
    assert "words" not in doc["lines"][3]  # dropped by QA
    assert doc["lines"][0]["words"] and doc["lines"][2]["words"]  # healthy neighbours keep karaoke


def _align_result(quality: float) -> AlignResult:
    return AlignResult(
        sync="word",
        lines=[LineTiming(0, 1000, "hello world", quality)],
        words_per_line=[[AlignedWord(0, 400, "hello", 0.8), AlignedWord(500, 1000, "world", 0.8)]],
        quality_score=quality,
    )


def test_second_pass_runs_on_low_quality_and_keeps_the_better_result(
    db_session, job, scratch, monkeypatch
):
    """separation_mode=second_pass: a low first-pass score triggers vocal
    separation + realign; the better result wins and the flag lands in the doc."""
    real_align_stage = wp._align_stage  # keep the real one; _happy_stages mocks it
    _happy_stages(monkeypatch, scratch)
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "separation_mode", "second_pass")
    monkeypatch.setattr(wp, "_align_stage", real_align_stage)

    statuses: list[str] = []
    real_set_status = queue.set_status

    def spy_set_status(s, j, status):
        statuses.append(status)
        real_set_status(s, j, status)

    monkeypatch.setattr(wp.queue, "set_status", spy_set_status)
    monkeypatch.setattr(wp, "_decode", lambda src, dest, rate, **kw: dest)
    monkeypatch.setattr(wp, "detect_language", lambda text: "eng")
    separated = []
    monkeypatch.setattr(
        wp, "_separate_vocals", lambda audio, tmp: separated.append(audio) or (tmp / "vocals.wav")
    )
    aligns = iter([_align_result(0.2), _align_result(0.7)])
    monkeypatch.setattr(wp, "align", lambda wav, texts, lang, **kw: next(aligns))

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert len(separated) == 1
    assert "separating" in statuses and "aligning" in statuses

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["vocals_separated"] is True
    assert doc["alignment"]["quality_score"] == pytest.approx(0.7)


def test_second_pass_worse_result_is_discarded(db_session, job, scratch, monkeypatch):
    real_align_stage = wp._align_stage
    _happy_stages(monkeypatch, scratch)
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "separation_mode", "second_pass")
    monkeypatch.setattr(wp, "_align_stage", real_align_stage)
    monkeypatch.setattr(wp, "_decode", lambda src, dest, rate, **kw: dest)
    monkeypatch.setattr(wp, "detect_language", lambda text: "eng")
    monkeypatch.setattr(wp, "_separate_vocals", lambda audio, tmp: tmp / "vocals.wav")
    aligns = iter([_align_result(0.2), _align_result(0.1)])  # second pass is WORSE
    monkeypatch.setattr(wp, "align", lambda wav, texts, lang, **kw: next(aligns))

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["vocals_separated"] is False
    assert doc["alignment"]["quality_score"] == pytest.approx(0.2)


def test_good_first_pass_skips_separation(db_session, job, scratch, monkeypatch):
    real_align_stage = wp._align_stage
    _happy_stages(monkeypatch, scratch)
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "separation_mode", "second_pass")
    monkeypatch.setattr(wp, "_align_stage", real_align_stage)
    monkeypatch.setattr(wp, "_decode", lambda src, dest, rate, **kw: dest)
    monkeypatch.setattr(wp, "detect_language", lambda text: "eng")

    def never(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("separation must not run above the gate")

    monkeypatch.setattr(wp, "_separate_vocals", never)
    monkeypatch.setattr(wp, "align", lambda wav, texts, lang, **kw: _align_result(0.8))

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"


def test_sweep_orphans_removes_stale_dirs_only(tmp_path):
    import os

    old = tmp_path / "job-old"
    old.mkdir()
    (old / "audio.webm").write_bytes(b"x")
    os.utime(old, (1, 1))  # ancient mtime
    fresh = tmp_path / "job-fresh"
    fresh.mkdir()

    assert sweep_orphans(tmp_path) == 1
    assert not old.exists() and fresh.exists()


def test_always_mode_separates_first_and_keeps_beats_on_the_full_mix(
    db_session, job, scratch, monkeypatch
):
    """The shipped 2.0.0 path: separation runs BEFORE alignment, the doc says
    vocals_separated, and beats still read the ORIGINAL mix (not the stem)."""
    _happy_stages(monkeypatch, scratch)
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "separation_mode", "always")

    statuses: list[str] = []
    real_set_status = queue.set_status

    def spy_set_status(s, j, status):
        statuses.append(status)
        real_set_status(s, j, status)

    monkeypatch.setattr(wp.queue, "set_status", spy_set_status)
    separated: list[Path] = []
    monkeypatch.setattr(
        wp, "_separate_vocals", lambda audio, tmp: separated.append(audio) or (tmp / "vocals.wav")
    )
    beats_inputs: list[Path] = []
    monkeypatch.setattr(wp, "extract_beats", lambda p: beats_inputs.append(p) or None)

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert len(separated) == 1
    assert "separating" in statuses

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["vocals_separated"] is True
    assert beats_inputs == [separated[0]]  # the download path, not vocals.wav


def test_windowed_flag_forwards_the_lrclib_anchors(db_session, job, scratch, monkeypatch):
    """Reverting the anchors= wiring in process.py must fail a test: windowed
    alignment silently degrades to whole-audio otherwise (reviewer)."""
    real_align_stage = wp._align_stage
    _happy_stages(monkeypatch, scratch)
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "windowed_alignment", True)
    monkeypatch.setattr(wp, "_align_stage", real_align_stage)
    monkeypatch.setattr(wp, "_decode", lambda src, dest, rate, **kw: dest)
    monkeypatch.setattr(wp, "detect_language", lambda text: "eng")
    monkeypatch.setattr(
        wp,
        "fetch_lyrics",
        lambda hints, base_url: LyricsText(
            ["hello world"], "hello world", 5, True, synced_starts_ms=[12_000]
        ),
    )
    seen: dict = {}

    def spy_align(wav, texts, lang, synced_starts_ms=None):
        seen["anchors"] = synced_starts_ms
        return _align_result(0.8)

    monkeypatch.setattr(wp, "align", spy_align)

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert seen["anchors"] == [12_000]


# --- nightcore branch (Faz 4, pipeline 2.2.0) ---


def _nightcore_job(db_session, *, title="Nightcore - Song", options=None, source_id="ncVid0001"):
    queue.enqueue(
        db_session,
        source_type="youtube",
        source_id=source_id,
        pipeline_major=1,
        hints={"title": title, "artist": "Chan", "duration_ms": 200_000},
        options=options or {},
        requested_by=None,
    )
    db_session.commit()
    claimed = queue.claim_next(db_session)
    db_session.commit()
    assert claimed is not None
    return claimed


def _nightcore_stages(monkeypatch, scratch, *, slow_duration_s):
    """Happy stages + the nightcore seams: a fake slow decode and a fixed
    measured duration for the sanity gate."""
    _happy_stages(monkeypatch, scratch)

    def fake_decode(src, dest, rate, **kw):
        Path(dest).write_bytes(b"wav")
        return Path(dest)

    monkeypatch.setattr(wp, "_decode", fake_decode)
    monkeypatch.setattr(wp, "_wav_duration_s", lambda p: slow_duration_s)
    # The detection/lyrics record: the ORIGINAL song, 240 s (r = 1.2 vs 200 s).
    record = {
        "id": 99,
        "trackName": "Song",  # plausibility guard needs title+artist overlap
        "artistName": "Chan",
        "duration": 240.0,
        "syncedLyrics": "[00:01.00] hello world",
        "plainLyrics": "hello world",
    }
    calls: list[str] = []
    monkeypatch.setattr(
        wp, "search_candidates", lambda query, *, base_url: calls.append(query) or [record]
    )
    return calls


def test_nightcore_detected_job_rescales_onto_the_played_clock(
    db_session, scratch, monkeypatch
):
    job = _nightcore_job(db_session)
    calls = _nightcore_stages(monkeypatch, scratch, slow_duration_s=240.0)

    # fetch_lyrics must NOT run — the detection record supplies the lyrics
    # without a second lrclib request (etiquette).
    monkeypatch.setattr(
        wp,
        "fetch_lyrics",
        lambda hints, base_url: (_ for _ in ()).throw(AssertionError("fetch_lyrics called")),
    )

    def slowed_align(s, j, tmp, audio, lyrics, **kw):
        assert kw.get("tempo") == 1.0 / 1.2  # slow-down reached the align stage
        result = AlignResult(
            sync="word",
            # Slowed (≈ original) clock: 1.2 s — must persist as 1.0 s.
            lines=[LineTiming(1200, 2400, "hello world", 0.8)],
            words_per_line=[
                [AlignedWord(1200, 1800, "hello", 0.8), AlignedWord(1800, 2400, "world", 0.8)]
            ],
            quality_score=0.8,
        )
        return result, False

    monkeypatch.setattr(wp, "_align_stage", slowed_align)

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert calls == ["Chan Song"]  # cleaned title, normalized artist

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["speed_factor"] == 1.2
    assert doc["alignment"]["lyrics_source_id"] == 99
    assert doc["lines"][0]["start_ms"] == 1000 and doc["lines"][0]["end_ms"] == 2000
    assert doc["lines"][0]["words"][0]["end_ms"] == 1500


def test_nightcore_sanity_failure_reverts_to_the_normal_flow(db_session, scratch, monkeypatch):
    job = _nightcore_job(db_session, source_id="ncVid0002")
    # Slowed copy measures 200 s where 240 s was expected → r was wrong.
    _nightcore_stages(monkeypatch, scratch, slow_duration_s=200.0)

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"  # normal flow (fetch_lyrics fake) took over

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["speed_factor"] == 1.0
    assert doc["lines"][0]["start_ms"] == 0  # _happy_stages times, unscaled


def test_explicit_speed_factor_with_lyrics_text_skips_detection(
    db_session, scratch, monkeypatch
):
    job = _nightcore_job(
        db_session,
        title="Song",  # no nightcore marker — explicit option drives the branch
        options={"speed_factor": 1.25, "lyrics_text": "hello world"},
        source_id="ncVid0003",
    )
    _happy_stages(monkeypatch, scratch)

    def fake_decode(src, dest, rate, **kw):
        Path(dest).write_bytes(b"wav")
        return Path(dest)

    monkeypatch.setattr(wp, "_decode", fake_decode)
    monkeypatch.setattr(wp, "_wav_duration_s", lambda p: 250.0)  # 200 × 1.25
    monkeypatch.setattr(
        wp,
        "search_candidates",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("search_candidates called")),
    )

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["speed_factor"] == 1.25
    # Honest provenance: caller text is NOT an lrclib record (reviewer, Faz 4).
    assert doc["alignment"]["lyrics_source"] == "caller"
    assert "lyrics_source_id" not in doc["alignment"]
    # _happy_stages align times (0..1000) rescaled by 1/1.25 → 0..800.
    assert doc["lines"][0]["end_ms"] == 800


def test_nightcore_wrong_song_gate_fails_honest(db_session, scratch, monkeypatch):
    """Field failure 2026-07-13: 'Come On Now' aligned against 'Come On
    Eileen' and PERSISTED at anchor-agreement 0.54. Low CTC probs (the honest
    lyrics-identity signal) must now fail the job instead."""
    job = _nightcore_job(db_session, source_id="ncVid0006")
    _nightcore_stages(monkeypatch, scratch, slow_duration_s=240.0)

    def garbage_align(s, j, tmp, audio, lyrics, **kw):
        result = AlignResult(
            sync="word",
            lines=[LineTiming(1200, 2400, "hello world", 0.0)],
            words_per_line=[
                # Wrong-lyrics probs (calibrated wrong ≈ 0.185 raw CTC — use
                # tiny values; quality_from_probs maps them near zero).
                [AlignedWord(1200, 1800, "hello", 1e-6), AlignedWord(1800, 2400, "world", 1e-6)]
            ],
            quality_score=0.1,
        )
        return result, False

    monkeypatch.setattr(wp, "_align_stage", garbage_align)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "lyrics_not_found"

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    # The wrong-song document must never persist.
    docs = db_session.scalars(
        select(ProcessedTrack).where(ProcessedTrack.source_id == "ncVid0006")
    ).all()
    assert docs == []


def test_nightcore_lyrics_text_skips_the_wrong_song_gate(db_session, scratch, monkeypatch):
    """Caller-supplied lyrics are trusted; stretch artifacts alone must not
    fail them (the gate is for DETECTED records only)."""
    job = _nightcore_job(
        db_session,
        title="Song",
        options={"speed_factor": 1.2, "lyrics_text": "hello world"},
        source_id="ncVid0007",
    )
    _happy_stages(monkeypatch, scratch)

    def fake_decode(src, dest, rate, **kw):
        Path(dest).write_bytes(b"wav")
        return Path(dest)

    monkeypatch.setattr(wp, "_decode", fake_decode)
    monkeypatch.setattr(wp, "_wav_duration_s", lambda p: 240.0)

    def lowprob_align(s, j, tmp, audio, lyrics, **kw):
        result = AlignResult(
            sync="word",
            lines=[LineTiming(1200, 2400, "hello world", 0.0)],
            words_per_line=[
                [AlignedWord(1200, 1800, "hello", 1e-6), AlignedWord(1800, 2400, "world", 1e-6)]
            ],
            quality_score=0.1,
        )
        return result, False

    monkeypatch.setattr(wp, "_align_stage", lowprob_align)
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"  # gate skipped for source="caller"


def test_nightcore_detection_off_keeps_the_plain_flow(db_session, scratch, monkeypatch):
    from kashi_server.config import settings

    job = _nightcore_job(db_session, source_id="ncVid0004")
    _happy_stages(monkeypatch, scratch)
    monkeypatch.setattr(settings, "nightcore_detection", False)
    monkeypatch.setattr(
        wp,
        "search_candidates",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("search_candidates called")),
    )

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["speed_factor"] == 1.0


def test_nightcore_channel_artist_detects_via_title_only_retry(db_session, scratch, monkeypatch):
    """Field case (Syrex uploads): the hint artist is a CHANNEL name that can
    never token-match the original artist. Detection must (a) not require
    artist overlap and (b) retry with a title-only query when the channel-
    polluted query yields nothing plausible."""
    job = _nightcore_job(
        db_session,
        title="Nightcore - We Don't Sleep At Night - (Lyrics)",
        source_id="ncVid0005",
    )
    _happy_stages(monkeypatch, scratch)

    def fake_decode(src, dest, rate, **kw):
        Path(dest).write_bytes(b"wav")
        return Path(dest)

    monkeypatch.setattr(wp, "_decode", fake_decode)
    monkeypatch.setattr(wp, "_wav_duration_s", lambda p: 240.0)
    record = {
        "id": 7,
        "trackName": "We Don't Sleep at Night",
        "artistName": "Original Artist",  # ≠ channel "Chan" — must still pass
        "duration": 240.0,
        "syncedLyrics": "[00:01.00] hello world",
    }
    queries: list[str] = []

    def fake_search(query, *, base_url):
        queries.append(query)
        # Channel-polluted query finds nothing; the title-only retry hits.
        return [] if query.startswith("Chan ") else [record]

    monkeypatch.setattr(wp, "search_candidates", fake_search)

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert queries == [
        "Chan We Don't Sleep At Night",  # noise tokens stripped from the query
        "We Don't Sleep At Night",
    ]

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["speed_factor"] == 1.2
    assert doc["alignment"]["lyrics_source_id"] == 7


# --- escape hatches on the r=1 flow + honest explicit-r (pipeline 2.2.4) ---


def test_plain_flow_honors_lyrics_text(db_session, scratch, monkeypatch):
    """Retro finding: the escape hatch was dead exactly where it is needed —
    detection failed/never ran, yet caller lyrics were silently ignored."""
    job = _nightcore_job(
        db_session,
        title="Song",  # no marker, no explicit factor → plain r=1 flow
        options={"lyrics_text": "hello world"},
        source_id="ncVid0008",
    )
    _happy_stages(monkeypatch, scratch)
    monkeypatch.setattr(
        wp,
        "fetch_lyrics",
        lambda hints, base_url: (_ for _ in ()).throw(AssertionError("fetch_lyrics called")),
    )

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["speed_factor"] == 1.0
    assert doc["alignment"]["lyrics_source"] == "caller"
    assert "lyrics_source_id" not in doc["alignment"]


def test_plain_flow_original_title_repairs_the_lookup(db_session, scratch, monkeypatch):
    job = _nightcore_job(
        db_session,
        title="S0ng (broken upl0ad title)",
        options={"original_title": "Real Song"},
        source_id="ncVid0009",
    )
    _happy_stages(monkeypatch, scratch)
    # original_title also arms the detection probe (by design); nothing
    # plausible comes back here, so the flow lands on r=1 + repaired lookup.
    monkeypatch.setattr(wp, "search_candidates", lambda query, *, base_url: [])
    seen: dict = {}

    def spy_fetch(hints, base_url):
        seen.update(hints)
        return LyricsText(["hello world"], "hello world", 5, True)

    monkeypatch.setattr(wp, "fetch_lyrics", spy_fetch)

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"
    assert seen["title"] == "Real Song"  # override reached the lookup
    assert seen["artist"] == "Chan"  # the rest of the hints survive


def test_explicit_r_sanity_miss_fails_honest(db_session, scratch, monkeypatch):
    """The caller STATED the factor; producing a silently-reverted r=1
    document would be wrong in a way they cannot see (retro finding)."""
    job = _nightcore_job(
        db_session,
        title="Song",
        options={"speed_factor": 1.25, "lyrics_text": "hello world"},
        source_id="ncVid0010",
    )
    _happy_stages(monkeypatch, scratch)

    def fake_decode(src, dest, rate, **kw):
        Path(dest).write_bytes(b"wav")
        return Path(dest)

    monkeypatch.setattr(wp, "_decode", fake_decode)
    monkeypatch.setattr(wp, "_wav_duration_s", lambda p: 200.0)  # ≠ 200 × 1.25

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "alignment_failed"

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    docs = db_session.scalars(
        select(ProcessedTrack).where(ProcessedTrack.source_id == "ncVid0010")
    ).all()
    assert docs == []


def test_nightcore_lyrics_resolve_before_the_stretch(db_session, scratch, monkeypatch):
    """A doomed lyrics_not_found must not cost the near-realtime rubberband
    decode first (retro finding — the explicit path decoded, then searched)."""
    job = _nightcore_job(
        db_session,
        title="Song",
        options={"speed_factor": 1.25},  # no lyrics_text → lrclib pick must run
        source_id="ncVid0011",
    )
    _happy_stages(monkeypatch, scratch)
    monkeypatch.setattr(wp, "search_candidates", lambda query, *, base_url: [])
    monkeypatch.setattr(
        wp,
        "_decode",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("decode ran before lyrics")),
    )

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "failed" and job.error_type == "lyrics_not_found"


def test_lyricsfile_fast_path_skips_ctc_and_separation(db_session, job, scratch, monkeypatch):
    # Human word sync on the chosen record: separation and alignment must
    # never run (even under separation_mode=always), the document rides the
    # human clock with method/provenance to match and no qa block.
    from pathlib import Path as _P

    from kashi_server.config import settings

    _happy_stages(monkeypatch, scratch)
    monkeypatch.setattr(settings, "separation_mode", "always")
    raw = (_P(__file__).parent / "fixtures" / "lyricsfile" / "valid.yaml").read_text()
    monkeypatch.setattr(
        wp,
        "fetch_lyrics",
        lambda hints, base_url: LyricsText(
            ["Meet me at the hotel", "Second line"],
            "Meet me at the hotel Second line",
            5,
            True,
            lyricsfile_raw=raw,
        ),
    )

    def never(*args, **kwargs):
        raise AssertionError("CTC/separation must not run on the lyricsfile fast path")

    monkeypatch.setattr(wp, "_align_stage", never)
    monkeypatch.setattr(wp, "_separate_vocals", never)

    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    row = db_session.scalars(select(ProcessedTrack)).one()
    doc = row.document
    assert doc["alignment"]["method"] == "lrclib-lyricsfile/1.0"
    assert doc["alignment"]["lyrics_source"] == "lyricsfile"
    assert doc["alignment"]["lyrics_source_id"] == 5
    assert doc["alignment"]["quality_score"] == 1.0
    assert doc["alignment"]["vocals_separated"] is False
    assert "qa" not in doc["alignment"]  # no repair ran, none is claimed
    assert doc["lines"][0]["words"][0]["text"] == "Meet"  # trailing space stripped
    assert list(scratch.glob("job-*")) == []  # deletion guarantee holds


def test_broken_lyricsfile_falls_back_to_the_normal_path(db_session, job, scratch, monkeypatch):
    _happy_stages(monkeypatch, scratch)
    monkeypatch.setattr(
        wp,
        "fetch_lyrics",
        lambda hints, base_url: LyricsText(
            ["hello world"], "hello world", 5, True, lyricsfile_raw="version: '9.9'\n"
        ),
    )
    wp.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == "completed"

    from sqlalchemy import select

    from kashi_server.db.models import ProcessedTrack

    doc = db_session.scalars(select(ProcessedTrack)).one().document
    assert doc["alignment"]["method"] == "ctc-forced-aligner/mms-300m"
    assert doc["alignment"]["lyrics_source"] == "lrclib"


def test_different_edit_disables_windowed_anchors(db_session, job, scratch, monkeypatch):
    # Field case (2026-07-14): a "video" upload carries a lyricless intro —
    # minutes longer than the lrclib record's edit. Anchors from that record
    # live on a shifted clock; windows would search the wrong places. Past
    # the tolerance the anchors must drop (whole-audio alignment absorbs a
    # global offset); within it they must pass through untouched.
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "windowed_alignment", True)
    monkeypatch.setattr(wp, "_decode", lambda src, dest, rate, **kw: dest)
    monkeypatch.setattr(wp, "detect_language", lambda text: "eng")
    captured: list = []

    def spy_align(wav, texts, lang, **kw):
        captured.append(kw.get("synced_starts_ms"))
        return AlignResult(
            sync="word",
            lines=[LineTiming(0, 1000, "hello world", 0.8)],
            words_per_line=[
                [AlignedWord(0, 400, "hello", 0.8), AlignedWord(500, 1000, "world", 0.8)]
            ],
            quality_score=0.8,
        )

    monkeypatch.setattr(wp, "align", spy_align)
    monkeypatch.setattr(wp, "_wav_duration_s", lambda p: 245.0)  # video edit

    stamps = [0, 5000]
    mismatched = LyricsText(
        ["hello world"], "hello world", 5, True,
        synced_starts_ms=stamps, record_duration_s=180.0,  # song edit: 65s shorter
    )
    wp._align_stage(db_session, job, scratch, scratch / "a.webm", mismatched)
    assert captured[-1] is None  # anchors dropped

    agreeing = LyricsText(
        ["hello world"], "hello world", 5, True,
        synced_starts_ms=stamps, record_duration_s=243.0,  # within ±5s
    )
    wp._align_stage(db_session, job, scratch, scratch / "a.webm", agreeing)
    assert captured[-1] == stamps  # anchors kept


def test_client_edit_mismatch_fails_honest(db_session, scratch, monkeypatch):
    # Field (Sinsirella video id): the browser played a 451s VIDEO while the
    # downloadable audio was the 216s song. A doc timed to audio the client
    # never hears must not exist — fail with both numbers in the message.
    from kashi_server.version import PIPELINE_MAJOR

    _happy_stages(monkeypatch, scratch)  # download stub returns 200s audio
    queue.enqueue(
        db_session,
        source_type="youtube",
        source_id="videoEdit01",
        pipeline_major=PIPELINE_MAJOR,
        hints={"title": "T", "artist": "A", "duration_ms": 451_000},
        options={},
        requested_by=None,
    )
    db_session.commit()
    claimed = queue.claim_next(db_session)
    assert claimed is not None
    wp.process_job(db_session, claimed)
    db_session.refresh(claimed)
    assert claimed.status == "failed"
    assert claimed.error_type == "alignment_failed"
    assert "different" in (claimed.error_message or "")
    # A few seconds of stale-hint jitter must NOT trip the gate: the standard
    # fixture (200s hint vs 200s download) still completes end to end.
