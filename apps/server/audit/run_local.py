"""One song through the worker's alignment, locally: no database, no download,
no separation, no GPU.

    python -m audit.run_local <id> --lyrics <lrclib record .json | plain .txt> \\
        --out doc.json [--no-anchors] [--record-id N] [--mix <full mix>] \\
        [--title T] [--artist A] [--extras extras.json]

What runs is the worker's own code: ``process._align_with_ctc`` (nightcore
plan, the aligner stage, line QA on the stem's onsets and vocal energy) and
``process._assemble_document`` (fx, palette, ``build_document``), then
``validate_document``, the hard gate ``persist_processed_track`` applies. The
document is written to ``--out`` instead of persisted. The job is an in-memory
``Job`` and the session a no-op, so checkpoints and status changes do nothing.

Separation is never repeated: the worker's separator call returns the cached
``stems/<id>.16k.wav`` (``audit.stems``: the live separator, decoded by the
worker's own ``_decode``). The aligner stage decodes it again at 16 kHz, which
is sample-identical, so the aligner hears what prod's aligner heard.

The pipeline's settings come from the environment, as in the worker: run with
the worker's env (``ALIGN_MODELS`` above all) or the document comes from a
different pipeline. ``--extras`` records what was in force, plus what prod
computes and never stores: the onsets and vocal energy line QA read, each
word's prob, lateness shift and placing pass (``AlignedWord.source``), and
line QA's per-line lists.

Lyrics: an lrclib record (``/api/get`` JSON, or a search list with one record
or ``--record-id``) goes through ``lyrics_from_record``, anchored as in prod.
``--no-anchors`` keeps the record's lines and drops its stamps: whole-audio
alignment, line QA without references (the plain-lyrics experiment). A text
file goes through ``lyrics_from_text``, exactly like ``options.lyrics_text``.

Not reproduced: nightcore (r = 1 only); the lyricsfile fast path (prod skips
CTC when the record carries human word timings - a warning says so); a
separation mode other than the live ``always`` (refused); beats, energy and
sections unless ``--mix`` names the full mix (they never touch word timings;
fx selection reads sections).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import uuid
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from audit.paths import stem_16k_path, stem_meta_path

if TYPE_CHECKING:
    from kashi_server.pipeline.lrclib import LyricsText

logger = logging.getLogger("audit.run_local")

# What _detect_nightcore answers for a plain upload: r = 1, no record, no branch.
NO_NIGHTCORE: tuple[float, dict | None, str | None] = (1.0, None, None)
# The live worker separates every job first (config default, gitops value).
LIVE_SEPARATION_MODE = "always"


class _NoRows:
    rowcount = 1

    def first(self) -> None:
        return None


class NullSession:
    """Just enough of a Session for ``checkpoint`` and ``queue.set_status``:
    every write goes nowhere, the in-memory job is the only state."""

    def flush(self) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def expire(self, *_a: Any, **_k: Any) -> None: ...
    def refresh(self, *_a: Any, **_k: Any) -> None: ...

    def execute(self, *_a: Any, **_k: Any) -> _NoRows:
        return _NoRows()


# --- inputs -------------------------------------------------------------------

def pick_record(raw: dict | list, record_id: int | None = None) -> dict:
    records = [raw] if isinstance(raw, dict) else [r for r in raw if isinstance(r, dict)]
    if record_id is not None:
        records = [r for r in records if int(r.get("id") or 0) == record_id]
    if len(records) != 1:
        raise ValueError(f"{len(records)} lrclib records match: pass --record-id")
    return records[0]


def load_lyrics(path: Path, *, record_id: int | None = None,
                anchors: bool = True) -> tuple[LyricsText, dict]:
    """(lyrics, track hints from the record). JSON is an lrclib record, any
    other text is plain lyrics (always unanchored, like ``options.lyrics_text``)."""
    from kashi_server.pipeline.lrclib import lyrics_from_record, lyrics_from_text

    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = None
    if not isinstance(raw, dict | list):
        return lyrics_from_text(text), {}
    record = pick_record(raw, record_id)
    lyrics = lyrics_from_record(record)
    if not anchors:
        lyrics = replace(lyrics, synced_starts_ms=None, had_synced=False)
    hints: dict = {}
    if record.get("trackName"):
        hints["title"] = record["trackName"]
    if record.get("artistName"):
        hints["artist"] = record["artistName"]
    if record.get("duration"):
        hints["duration_ms"] = round(float(record["duration"]) * 1000)
    return lyrics, hints


def cached_stem(video_id: str) -> Path:
    path = stem_16k_path(video_id)
    if not path.exists():
        raise FileNotFoundError(
            f"no cached stem {path}: make it with `python -m audit.stems {video_id}` "
            "(this run never separates)")
    return path


def audio_duration_s(video_id: str, stem: Path) -> float:
    """What the worker's download reported (kept in the stem's meta by
    audit.stems), else the stem's own length. Only ``track.duration_ms``
    reads it."""
    meta = stem_meta_path(video_id)
    if meta.exists():
        duration = json.loads(meta.read_text()).get("duration_s")
        if duration:
            return float(duration)
    with wave.open(str(stem), "rb") as f:
        return f.getnframes() / f.getframerate()


# --- the run ------------------------------------------------------------------

@contextmanager
def worker_seams(process: Any, stem: Path, seen: dict) -> Iterator[None]:
    """The worker module, for one local run: its separator hands back the
    cached stem, and the onsets / vocal energy line QA reads are recorded on
    their way through. Restored afterwards, also on failure."""
    real = (process._separate_vocals, process.detect_onsets, process.measure_vocal_energy)
    real_separate, real_onsets, real_energy = real

    def separate(audio: Path, tmp: Path, **_kw: Any) -> Path:
        seen["separations"] = seen.get("separations", 0) + 1
        return stem

    def onsets(wav: Path):
        seen["onsets"] = real_onsets(wav)
        return seen["onsets"]

    def energy(wav: Path):
        seen["vocal_energy"] = real_energy(wav)
        return seen["vocal_energy"]

    process._separate_vocals, process.detect_onsets, process.measure_vocal_energy = (
        separate, onsets, energy)
    try:
        yield
    finally:
        process._separate_vocals, process.detect_onsets, process.measure_vocal_energy = real


def run(video_id: str, lyrics: LyricsText, hints: dict, *,
        mix: Path | None = None) -> tuple[dict, dict]:
    """(the validated document, extras)."""
    from kashi_server.config import settings
    from kashi_server.db.models import Job
    from kashi_server.pipeline.document import validate_document
    from kashi_server.pipeline.download import DownloadResult
    from kashi_server.worker import process

    if settings.separation_mode != LIVE_SEPARATION_MODE:
        raise ValueError(
            f"separation_mode={settings.separation_mode!r}: the local run replays the "
            f"stem cache, which is the live {LIVE_SEPARATION_MODE!r} mode only")
    missing = [k for k in ("title", "artist") if not hints.get(k)]
    if missing:
        raise ValueError(f"no {' / '.join(missing)}: pass --{' --'.join(missing)} (the "
                         "document requires them; prod takes them from the player)")
    stem = cached_stem(video_id)
    duration_s = audio_duration_s(video_id, stem)
    _warn_lyricsfile(lyrics, duration_s)
    job = Job(id=uuid.uuid4(), source_type="youtube", source_id=video_id, hints=hints,
              options={}, attempts=1, status="aligning")
    # What the worker downloaded. Only the separator reads its path, and that is
    # replaced here, so it is the mix when there is one for honesty's sake.
    download = DownloadResult(path=mix or stem, abr=0.0, acodec="", duration_s=duration_s,
                              info={})
    seen: dict = {}
    session: Any = NullSession()
    with tempfile.TemporaryDirectory(prefix=f"kashi-local-{video_id}-") as tmp, \
            worker_seams(process, stem, seen):
        lyrics, result, qa, separated, speed = process._align_with_ctc(
            session, job, Path(tmp), download, NO_NIGHTCORE, lyrics)
        beats, energy, sections = (process._mix_features(mix) if mix is not None
                                   else (None, None, None))
        doc = process._assemble_document(
            job, download, lyrics, result, qa, vocals_separated=separated, speed_factor=speed,
            beats=beats, energy=energy, sections=sections)
    validate_document(doc)
    return doc, _extras(video_id, stem, lyrics, result, qa, seen)


def _warn_lyricsfile(lyrics: LyricsText, duration_s: float) -> None:
    if lyrics.source != "lrclib" or not lyrics.lyricsfile_raw:
        return
    from kashi_server.pipeline.lyricsfile import alignresult_from_lyricsfile

    if alignresult_from_lyricsfile(lyrics.lyricsfile_raw, duration_s) is not None:
        logger.warning("the record carries human word timings: prod takes the lyricsfile "
                       "fast path and never aligns it; this run aligns anyway")


def _extras(video_id: str, stem: Path, lyrics: LyricsText, result, qa, seen: dict) -> dict:
    from kashi_server.config import settings
    from kashi_server.pipeline.alignment import resolve_aligner
    from kashi_server.pipeline.langid import detect_language

    language = detect_language(lyrics.full_text)
    energy = seen.get("vocal_energy")
    return {
        "id": video_id,
        "stem": str(stem),
        "anchors": lyrics.synced_starts_ms is not None,
        "separations": seen.get("separations", 0),
        "language": language,
        "aligner": asdict(resolve_aligner(language)),
        "settings": {
            **{k: getattr(settings, k) for k in (
                "separation_mode", "windowed_alignment", "align_model", "align_romanize",
                "align_offset_ms", "separation_model_filename", "structure_sections",
                "fx_embeddings")},
            "align_models": {k: v.model_dump() for k, v in settings.align_models.items()},
        },
        "qa": None if qa is None else {
            k: getattr(qa, k) for k in (
                "flagged", "offset_ms", "degraded_to_line", "density_dropped", "adlib_shifted",
                "adlib_rederived", "trimmed_ends", "uncertain", "response_shifted", "nudged")},
        "words": [[{"text": w.text, "start_ms": w.start_ms, "end_ms": w.end_ms,
                    "prob": round(w.prob, 4), "shift_ms": w.shift_ms, "source": w.source}
                   for w in chunk] for chunk in result.words_per_line],
        "onsets": seen.get("onsets"),
        "vocal_energy": None if energy is None else {
            "hop_ms": energy.hop_ms, "frames": [list(f) for f in energy.frames]},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One song through the worker, locally.")
    parser.add_argument("id")
    parser.add_argument("--lyrics", required=True, type=Path,
                        help="lrclib record JSON (anchored) or plain text (unanchored)")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--no-anchors", action="store_true",
                        help="drop the record's stamps: whole-audio alignment")
    parser.add_argument("--record-id", type=int, help="pick one record of a search list")
    parser.add_argument("--mix", type=Path, help="full mix for beats/energy/sections")
    parser.add_argument("--title")
    parser.add_argument("--artist")
    parser.add_argument("--extras", type=Path, help="write what prod never stores here")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from kashi_server.vdl_kit.errors import PipelineError

    try:
        lyrics, hints = load_lyrics(args.lyrics, record_id=args.record_id,
                                    anchors=not args.no_anchors)
        hints |= {k: v for k, v in (("title", args.title), ("artist", args.artist)) if v}
        doc, extras = run(args.id, lyrics, hints, mix=args.mix)
    except (FileNotFoundError, ValueError) as exc:
        print(f"run_local: {exc}", file=sys.stderr)
        return 2
    except PipelineError as exc:  # what the worker would have filed as the job's error
        print(f"run_local: the pipeline failed ({exc.error_type}): {exc.message}",
              file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    if args.extras:
        args.extras.write_text(json.dumps(extras, ensure_ascii=False) + "\n")
    lines = doc["lines"]
    print(f"{args.id}: {doc['pipeline_version']} {doc['alignment']['method']} "
          f"sync={doc['sync']} quality={doc['alignment']['quality_score']} "
          f"worded {sum(1 for ln in lines if ln.get('words'))}/{len(lines)} "
          f"anchors {'on' if extras['anchors'] else 'off'} "
          f"({extras['aligner']['model_name']}, offset {extras['aligner']['offset_ms']:+d} ms) "
          f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
