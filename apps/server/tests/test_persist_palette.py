"""A reprocess must not lose an album-art palette (2026-11-19).

The palette is fetched live on every build (it is not evidence); when the artwork fetch fails the
build carries the default palette. Before this change a reprocess stored those default colours over
the album's. `document.carried_palette` keeps the stored album palette in that one case, and
persist computes the etag (and the override's applied_etag) on what it stores.
"""

import io
import json
import random
import uuid

import pytest
from sqlalchemy import select

from kashi_server import overrides
from kashi_server.db.models import Job, ProcessedTrack, SongOverride
from kashi_server.pipeline import document
from kashi_server.pipeline.palette import DEFAULT_PALETTE, palette_from_image_bytes
from kashi_server.version import PIPELINE_MAJOR

SOURCE = "paletteKeep1"
ALBUM = {
    "source": "album_art",
    "primary": "#102030",
    "secondary": "#203040",
    "background": "#000000",
    "text": "#ffffff",
    "accent": "#405060",
}
OTHER_ALBUM = dict(ALBUM, primary="#aabbcc")


def _doc(palette: dict | None, start: int = 1000) -> dict:
    doc = {
        "schema_version": 1,
        "pipeline_version": "2.35.0",
        "generated_at": "t",
        "sync": "word",
        "alignment": {"lyrics_source": "lrclib", "lyrics_source_id": 7, "quality_score": 0.9},
        "lines": [
            {
                "start_ms": start,
                "end_ms": start + 800,
                "text": "la la",
                "words": [
                    {"start_ms": start, "end_ms": start + 400, "text": "la"},
                    {"start_ms": start + 400, "end_ms": start + 800, "text": "la"},
                ],
            }
        ],
    }
    if palette is not None:
        doc["palette"] = dict(palette)
    return doc


# --- the pure helper -----------------------------------------------------------------------------


def test_the_sources_are_the_palette_module_s_own():
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(image, format="PNG")
    assert palette_from_image_bytes(image.getvalue())["source"] == document.ARTWORK_PALETTE_SOURCE
    assert DEFAULT_PALETTE["source"] == document.FALLBACK_PALETTE_SOURCE


@pytest.mark.parametrize(
    "stored,new,carried",
    [
        (ALBUM, DEFAULT_PALETTE, True),
        (ALBUM, OTHER_ALBUM, False),  # a fresh read of the artwork wins
        (DEFAULT_PALETTE, DEFAULT_PALETTE, False),
        (None, DEFAULT_PALETTE, False),  # nothing to keep
        (ALBUM, None, False),  # a document without a palette is left as built
        (dict(ALBUM, source="default"), DEFAULT_PALETTE, False),
        (ALBUM, dict(DEFAULT_PALETTE, source="album_art"), False),
    ],
)
def test_only_an_album_palette_over_the_fallback_is_carried(stored, new, carried):
    built = _doc(new, start=2000)
    before = json.loads(json.dumps(built))
    stored_doc = _doc(stored)
    out = document.carried_palette(stored_doc, built)
    assert built == before  # the built document is not changed
    if carried:
        assert out is not built and out["palette"] == stored
        assert out["palette"] is not stored_doc["palette"]  # a copy: the stored row stays apart
        assert {k: v for k, v in out.items() if k != "palette"} == {
            k: v for k, v in built.items() if k != "palette"
        }
    else:
        assert out is built


def test_no_stored_document_carries_nothing():
    built = _doc(DEFAULT_PALETTE)
    assert document.carried_palette(None, built) is built
    assert document.carried_palette({}, built) is built


def test_nothing_but_the_palette_can_change():
    """Seeded: random documents and palettes; every key but `palette` comes out identical (the
    same objects), and the palette is the stored one exactly when the rule says so."""
    rng = random.Random(20261119)
    palettes = [ALBUM, OTHER_ALBUM, DEFAULT_PALETTE, None, {"source": "album_art"}, {}]
    for _ in range(500):
        stored = _doc(rng.choice(palettes), start=rng.randint(0, 9000))
        built = _doc(rng.choice(palettes), start=rng.randint(0, 9000))
        built["beats"] = {"bpm": rng.randint(60, 200)}
        out = document.carried_palette(stored, built)
        assert out.keys() == built.keys()
        assert all(out[k] is built[k] for k in built if k != "palette")
        rule = (stored.get("palette") or {}).get("source") == "album_art" and (
            built.get("palette") or {}
        ).get("source") == "default"
        assert out.get("palette") == (stored["palette"] if rule else built.get("palette"))


# --- persist, against a real database ------------------------------------------------------------


def _job(s, options: dict | None = None) -> Job:
    job = Job(
        source_type="youtube",
        source_id=SOURCE,
        pipeline_major=PIPELINE_MAJOR,
        hints={"title": "Song", "artist": "Artist", "duration_ms": 200_000},
        options=options or {},
        status="completed",
    )
    job.id = uuid.uuid4()
    s.add(job)
    s.flush()
    return job


def _real_doc(job: Job, palette: dict, shift: int = 0) -> dict:
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming
    from kashi_server.pipeline.lrclib import LyricsText

    texts = [f"line {i} words" for i in range(6)]
    result = AlignResult(
        sync="word",
        lines=[LineTiming(1000 * i + shift, 1000 * i + 800, t, 0.8) for i, t in enumerate(texts)],
        words_per_line=[
            [AlignedWord(1000 * i + shift, 1000 * i + 400, "line", 0.8)]
            + [AlignedWord(1000 * i + 400, 1000 * i + 800, "words", 0.8)]
            for i in range(6)
        ],
        quality_score=0.9,
        windowed=True,
        quality_basis="anchors",
    )
    lyrics = LyricsText(line_texts=texts, full_text=" ".join(texts), source_id=7, had_synced=True)
    return document.build_document(job, lyrics, result, None, dict(palette), vocals_separated=True)


def _stored(s) -> ProcessedTrack:
    s.expire_all()
    return s.scalars(select(ProcessedTrack).where(ProcessedTrack.source_id == SOURCE)).one()


def test_a_reprocess_whose_artwork_fetch_failed_keeps_the_album_palette(db_session):
    first = _job(db_session)
    document.persist_processed_track(db_session, first, _real_doc(first, ALBUM))
    job = _job(db_session)
    built = _real_doc(job, DEFAULT_PALETTE, shift=100)
    etag = document.persist_processed_track(db_session, job, built)
    stored = _stored(db_session)
    assert stored.document["palette"] == ALBUM
    assert stored.document["lines"] == built["lines"]  # the new timing is stored
    assert etag == stored.etag == document.compute_etag(stored.document)
    assert built["palette"] == DEFAULT_PALETTE  # the caller's document is not changed


@pytest.mark.parametrize(
    "before,after,stored_palette",
    [
        (ALBUM, OTHER_ALBUM, OTHER_ALBUM),
        (DEFAULT_PALETTE, DEFAULT_PALETTE, DEFAULT_PALETTE),
        (DEFAULT_PALETTE, ALBUM, ALBUM),
    ],
)
def test_any_other_palette_is_stored_as_built(db_session, before, after, stored_palette):
    first = _job(db_session)
    document.persist_processed_track(db_session, first, _real_doc(first, before))
    job = _job(db_session)
    built = _real_doc(job, after, shift=100)
    etag = document.persist_processed_track(db_session, job, built)
    stored = _stored(db_session)
    assert stored.document["palette"] == stored_palette
    assert etag == stored.etag == document.compute_etag(built)


def test_the_first_document_of_a_song_is_stored_as_built(db_session):
    job = _job(db_session)
    built = _real_doc(job, DEFAULT_PALETTE)
    etag = document.persist_processed_track(db_session, job, built)
    assert _stored(db_session).document["palette"] == DEFAULT_PALETTE
    assert etag == document.compute_etag(built)


def test_an_override_s_applied_etag_is_the_etag_of_the_carried_document(db_session):
    first = _job(db_session)
    document.persist_processed_track(db_session, first, _real_doc(first, ALBUM))
    db_session.add(SongOverride(source_type="youtube", source_id=SOURCE, lrclib_id=7))
    db_session.flush()
    job = _job(db_session, overrides.apply(db_session, "youtube", SOURCE, {}))
    etag = document.persist_processed_track(db_session, job, _real_doc(job, DEFAULT_PALETTE, 100))
    stored = _stored(db_session)
    override = db_session.scalars(select(SongOverride)).one()
    db_session.refresh(override)
    assert stored.document["palette"] == ALBUM
    assert override.applied_etag == etag == stored.etag
    # G1 then recognises the stored document as the override's build
    rerun = _job(db_session)  # no override applied
    kept = document.persist_processed_track(db_session, rerun, _real_doc(rerun, ALBUM, 200))
    assert kept == etag and _stored(db_session).etag == etag


def test_a_kept_document_stays_as_it_was(db_session):
    first = _job(db_session)
    good = document.persist_processed_track(db_session, first, _real_doc(first, ALBUM))
    job = _job(db_session)
    worse = _real_doc(job, DEFAULT_PALETTE)
    worse["lines"] = (
        worse["lines"][:1]
        + [  # worded lines 6 -> 1: a regression
            {k: v for k, v in line.items() if k != "words"} for line in worse["lines"][1:]
        ]
    )
    assert document.persist_processed_track(db_session, job, worse) == good
    stored = _stored(db_session)
    assert stored.etag == good and stored.document["palette"] == ALBUM
