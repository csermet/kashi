"""Song overrides through a real process_job (C.1): the worker acts on what the
enqueue merge wrote, the persist guard keeps an override build, and a song
without an override sees exactly the request's options at every seam."""

from pathlib import Path

import pytest
from sqlalchemy import select, text

from kashi_server import queue
from kashi_server.db.models import ProcessedTrack, SongOverride
from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
from kashi_server.pipeline.download import DownloadResult
from kashi_server.pipeline.lrclib import LyricsText
from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.worker import process as wp

PINNED = {
    "id": 4242,
    "trackName": "Song",
    "artistName": "Artist",
    "duration": 240.0,
    "syncedLyrics": "[00:20.00] hello world\n[00:40.00] second line",
    "plainLyrics": "hello world\nsecond line",
}


@pytest.fixture
def scratch(tmp_path, monkeypatch, lrclib_has_no_record):
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "retry_delays_s", [0, 0, 0])
    return tmp_path


class Seams:
    """Fakes for every stage, recording what reaches them."""

    def __init__(self, monkeypatch, *, audio_s=200.0, slow_s=None, prob=0.8):
        self.calls: dict[str, list] = {"fetch": [], "search": [], "record": [], "align": []}
        self.downloads = 0
        monkeypatch.setattr(wp, "heard_tokens", lambda wav, language: [])
        monkeypatch.setattr(wp, "extract_beats", lambda p: None)
        monkeypatch.setattr(wp, "extract_palette", lambda url: _palette())

        def download(source_id, dest_dir, **kw):
            self.downloads += 1
            path = Path(dest_dir) / "audio.webm"
            path.write_bytes(b"x" * 1024)
            return DownloadResult(path=path, abr=128.0, acodec="opus", duration_s=audio_s, info={})

        def decode(src, dest, rate, **kw):
            Path(dest).write_bytes(b"wav")
            return Path(dest)

        def align(s, j, tmp, audio, lyrics, **kw):
            self.calls["align"].append(
                {
                    "texts": list(lyrics.line_texts),
                    "starts": lyrics.synced_starts_ms,
                    "source": lyrics.source,
                    "source_id": lyrics.source_id,
                    "pinned": lyrics.pinned,
                    "tempo": kw.get("tempo"),
                }
            )
            words = [
                [AlignedWord(1000 * i, 1000 * i + 400, t.split()[0], prob)]
                + [AlignedWord(1000 * i + 400, 1000 * i + 800, t.split()[-1], prob)]
                for i, t in enumerate(lyrics.line_texts)
            ]
            result = AlignResult(
                sync="word",
                lines=[
                    LineTiming(1000 * i, 1000 * i + 800, t, prob)
                    for i, t in enumerate(lyrics.line_texts)
                ],
                words_per_line=words,
                quality_score=0.8,
            )
            return result, False, Path(audio)

        def fetch(hints, base_url):
            self.calls["fetch"].append(dict(hints))
            return LyricsText(["auto line"], "auto line", 5, True, synced_starts_ms=[1000])

        def search(query, *, base_url):
            self.calls["search"].append(query)
            return []

        def record(record_id, *, base_url):
            self.calls["record"].append(record_id)
            return PINNED if record_id == PINNED["id"] else None

        monkeypatch.setattr(wp, "download_audio", download)
        monkeypatch.setattr(wp, "_decode", decode)
        monkeypatch.setattr(wp, "_wav_duration_s", lambda p: slow_s or audio_s)
        monkeypatch.setattr(wp, "_align_stage", align)
        monkeypatch.setattr(wp, "fetch_lyrics", fetch)
        monkeypatch.setattr(wp, "search_candidates", search)
        monkeypatch.setattr(wp, "fetch_record", record)
        monkeypatch.setattr(wp, "LRCLIB", wp.build_gateway())  # sees the fakes above
        monkeypatch.setattr(
            wp,
            "_lyrics_by_ear",
            lambda *a: (_ for _ in ()).throw(AssertionError("by-ear rung asked")),
        )


def _palette():
    return {
        "source": "default",
        "primary": "#e84545",
        "secondary": "#f5d76e",
        "background": "#1a1a2e",
        "text": "#ffffff",
        "accent": "#903749",
    }


def _override(db_session, source_id, **fields):
    db_session.add(SongOverride(source_type="youtube", source_id=source_id, **fields))
    db_session.commit()


def _run(db_session, source_id, *, title="Song", options=None, reprocess=True):
    kwargs = dict(
        source_type="youtube",
        source_id=source_id,
        pipeline_major=2,
        hints={"title": title, "artist": "Artist", "duration_ms": 200_000},
        options=dict(options or {}),
        requested_by=None,
    )
    if reprocess:
        queue.enqueue_reprocess(db_session, **kwargs)
    else:
        queue.enqueue(db_session, **kwargs)
    db_session.commit()
    job = queue.claim_next(db_session)
    db_session.commit()
    assert job is not None
    wp.process_job(db_session, job)
    db_session.refresh(job)
    return job


def _doc(db_session, source_id):
    row = db_session.scalars(
        select(ProcessedTrack)
        .where(ProcessedTrack.source_id == source_id)
        .execution_options(populate_existing=True)
    ).first()
    return row.document if row is not None else None


# --- the worker acts on the override --------------------------------------------


def test_a_pinned_record_replaces_the_ladder(db_session, scratch, monkeypatch):
    seams = Seams(monkeypatch)
    _override(db_session, "pinVid00001", lrclib_id=4242)
    job = _run(db_session, "pinVid00001")
    assert job.status == "completed", job.error_message
    assert seams.calls["fetch"] == [] and seams.calls["search"] == []
    assert seams.calls["record"] == [4242]
    assert seams.calls["align"][0]["starts"] == [20_000, 40_000]
    doc = _doc(db_session, "pinVid00001")
    assert doc["alignment"]["lyrics_source"] == "lrclib"
    assert doc["alignment"]["lyrics_source_id"] == 4242  # recognisable afterwards
    etag = db_session.execute(text("SELECT etag FROM processed_tracks")).scalar_one()
    applied = db_session.execute(text("SELECT applied_etag FROM song_overrides")).scalar_one()
    assert applied == etag


def test_the_sheet_offset_reaches_the_aligner(db_session, scratch, monkeypatch):
    seams = Seams(monkeypatch)
    _override(db_session, "pinVid00002", lrclib_id=4242, offset_ms=-15_200)
    assert _run(db_session, "pinVid00002").status == "completed"
    assert seams.calls["align"][0]["starts"] == [4_800, 24_800]


def test_a_gone_pin_fails_honest_before_the_download_and_never_asks_by_ear(
    db_session, scratch, monkeypatch
):
    seams = Seams(monkeypatch)
    _override(db_session, "pinVid00003", lrclib_id=999)
    job = _run(db_session, "pinVid00003")
    assert job.status == "failed"
    assert job.error_type == "other"
    assert "999" in (job.error_message or "")
    assert seams.downloads == 0
    assert seams.calls["fetch"] == []  # no silent fallback to the ladder


def test_caller_lrc_aligns_on_its_own_stamps(db_session, scratch, monkeypatch):
    seams = Seams(monkeypatch)
    _override(db_session, "lrcVid00001", lyrics_text="[00:03.00] la la\n[00:07.50] da da")
    assert _run(db_session, "lrcVid00001").status == "completed"
    assert seams.calls["fetch"] == []
    assert seams.calls["align"][0] == {
        "texts": ["la la", "da da"],
        "starts": [3_000, 7_500],
        "source": "caller",
        "source_id": 0,
        "pinned": False,
        "tempo": 1.0,
    }
    assert _doc(db_session, "lrcVid00001")["alignment"]["lyrics_source"] == "caller"


def test_nightcore_repair_rides_every_refresh(db_session, scratch, monkeypatch):
    """The 2026-11-10 repair shape (explicit r + caller text) stored once:
    the archive feeder's reprocess (no options) rebuilds it on the r clock."""
    seams = Seams(monkeypatch, slow_s=250.0)
    _override(db_session, "ncVidOvr001", speed_factor=1.25, lyrics_text="hello world")
    for _ in range(2):  # two refreshes, both under the override
        assert _run(db_session, "ncVidOvr001").status == "completed"
    assert [call["tempo"] for call in seams.calls["align"]] == [0.8, 0.8]
    doc = _doc(db_session, "ncVidOvr001")
    assert doc["alignment"]["speed_factor"] == 1.25
    assert doc["alignment"]["lyrics_source"] == "caller"
    assert seams.calls["search"] == []  # detection never ran


def test_a_pinned_nightcore_record_skips_the_wrong_song_gate(db_session, scratch, monkeypatch):
    """Same low CTC probs that fail a DETECTED record (test_worker_process
    test_nightcore_wrong_song_gate_fails_honest): the owner chose this one."""
    Seams(monkeypatch, slow_s=250.0, prob=1e-6)
    _override(db_session, "ncVidOvr002", speed_factor=1.25, lrclib_id=4242)
    job = _run(db_session, "ncVidOvr002")
    assert job.status == "completed", job.error_message
    assert _doc(db_session, "ncVidOvr002")["alignment"]["lyrics_source_id"] == 4242


def test_the_gate_still_judges_an_unpinned_record_at_the_same_probs(
    db_session, scratch, monkeypatch
):
    """Counterpart of the test above: the gate is not simply off."""
    seams = Seams(monkeypatch, slow_s=250.0, prob=1e-6)
    record = dict(PINNED, trackName="Song", duration=250.0)
    monkeypatch.setattr(wp, "search_candidates", lambda q, *, base_url: [record])
    monkeypatch.setattr(wp, "LRCLIB", wp.build_gateway())
    _override(db_session, "ncVidOvr003", speed_factor=1.25)
    job = _run(db_session, "ncVidOvr003")
    assert job.status == "failed" and job.error_type == "lyrics_not_found"
    assert seams.calls["align"]


def test_an_override_build_survives_a_job_queued_before_it(db_session, scratch, monkeypatch):
    """A job created before the override (its options carry no marker) runs
    after the override build: it must not undo the repair."""
    Seams(monkeypatch)
    # 1. The job queued before the override: plain enqueue, empty table.
    queue.enqueue(
        db_session,
        source_type="youtube",
        source_id="raceVidOvr1",
        pipeline_major=2,
        hints={"title": "Song", "artist": "Artist", "duration_ms": 200_000},
        options={},
        requested_by=None,
    )
    db_session.commit()
    early = queue.claim_next(db_session)
    db_session.commit()
    assert early is not None
    # 2. The owner sets the override; a reprocess builds under it... (the
    # early job is still running, so park it as if it were mid-flight).
    db_session.execute(text("UPDATE jobs SET status = 'completed'"))  # free the live slot
    db_session.commit()
    _override(db_session, "raceVidOvr1", lrclib_id=4242)
    assert _run(db_session, "raceVidOvr1").status == "completed"
    built = db_session.execute(text("SELECT etag FROM processed_tracks")).scalar_one()
    # 3. ...then the early run finishes and tries to persist the automatic sheet.
    db_session.execute(text("UPDATE jobs SET status = 'aligning' WHERE id = :id"), {"id": early.id})
    db_session.commit()
    db_session.refresh(early)
    wp.process_job(db_session, early)
    db_session.refresh(early)
    assert early.status == "completed"  # the work ran; the repair was kept
    assert db_session.execute(text("SELECT etag FROM processed_tracks")).scalar_one() == built
    assert _doc(db_session, "raceVidOvr1")["alignment"]["lyrics_source_id"] == 4242


# --- no override: the request's options reach every seam, unchanged ------------------

MATRIX = [
    ("Song", {}),
    ("Song", {"separate": False}),
    ("Song", {"lyrics_text": "hello world"}),
    ("Song", {"original_title": "Real Song"}),
    ("Song", {"speed_factor": 1.25, "lyrics_text": "hello world"}),
    ("Nightcore - Song", {}),
    ("Song", {"force_persist": True}),
]


def _trace(db_session, monkeypatch, source_id, title, options, *, slow_s):
    seams = Seams(monkeypatch, slow_s=slow_s)
    job = _run(db_session, source_id, title=title, options=options)
    doc = _doc(db_session, source_id)
    return {
        "options": dict(job.options),
        "status": job.status,
        "error": job.error_type,
        "calls": seams.calls,
        "downloads": seams.downloads,
        "alignment": None if doc is None else doc["alignment"],
        "lines": None if doc is None else doc["lines"],
    }


@pytest.mark.parametrize(("title", "options"), MATRIX)
def test_without_an_override_every_seam_sees_the_request(
    db_session, scratch, monkeypatch, title, options
):
    """No-change proof, worker side: the same job with an empty table and
    with override rows for OTHER songs traces identically, and the options
    the worker read are exactly the request's (what main's worker reads)."""
    slow_s = 250.0 if options.get("speed_factor") else None
    empty = _trace(db_session, monkeypatch, "noOvrVid001", title, options, slow_s=slow_s)
    db_session.execute(text("TRUNCATE jobs, processed_tracks CASCADE"))
    db_session.commit()
    _override(db_session, "someOther01", lrclib_id=4242, offset_ms=-3000)
    _override(db_session, "someOther02", speed_factor=1.5, lyrics_text="other song")
    others = _trace(db_session, monkeypatch, "noOvrVid001", title, options, slow_s=slow_s)
    assert empty == others
    assert empty["options"] == options
    assert empty["calls"]["record"] == []  # nobody asked lrclib for a pinned id
    assert all(call["pinned"] is False for call in empty["calls"]["align"])


def test_pinned_failure_is_not_lyrics_not_found_upstream(monkeypatch):
    """Unit view of the r=1 seam: the pin's failure leaves the lookup as an
    error the by-ear rung does not catch."""

    class _Job:
        hints = {"title": "T", "artist": "A"}
        options = {"lrclib_id": 7}

    class _Gw:
        def get_record(self, rid):
            return None

        def fetch_lyrics(self, hints):
            raise AssertionError("ladder asked")

    monkeypatch.setattr(wp, "LRCLIB", _Gw())
    with pytest.raises(PipelineError) as err:
        wp._lookup_before_download(_Job())  # type: ignore[arg-type]
    assert err.value.error_type == "other"
