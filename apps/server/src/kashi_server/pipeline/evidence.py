"""Evidence for a refresh without download (prototype; `settings.evidence_mode`, off by default).

A job reads the audio at a handful of places, and everything after them is code over what those
places returned: the aligner's emissions over the 16 kHz stem (the whole track for the sheet
shift and the vote, one slice per window), the stem's length, its onsets (line QA), the mix's
beats / energy / sections, and the download's duration. Emissions are text-independent: a slice's
emissions depend on the stem, the checkpoint and its revision, and the slice's sample range,
never on the lyrics.

With `evidence_mode` on, a full job runs under a `Recorder`, which keeps what those places
returned, and stores it per song (`track_evidence`, `DbStore`). With `"replay"`, a later job for
the song runs under a `Replayer`: the SAME pipeline code from the lyrics lookup to the persisted
document, with only its audio boundaries served from the stored values — no download, no
separation, no model forward pass. A record whose song-wide key differs (what the aligner heard:
the separator and whether this job separates first; the evidence code and the libraries and
ffmpeg that computed a value) or that lacks a value a replay always reads is not replayed at all;
whatever a replay then cannot serve exactly (a slice another window plan cuts differently,
another checkpoint, an unpinned or other revision, a nightcore clock, the by-ear rung) raises
`EvidenceMissing`, always before anything is persisted, and the job runs in full instead.

A recording is never stored when it cannot be keyed or is not what a healthy job reads: two
different tracks in one job (a second-pass separation), a nightcore clock, an unpinned
checkpoint, or an extractor that FAILED (`extractor_failed`; "found nothing" is a value, a
failure is not).

The hooks are the module-level functions below (`emissions`, `track`, `download`, ...). Each
takes the real computation as `compute` and calls it as given when no session is active, so with
the mode off the pipeline does exactly what it did (the callers look `compute` up at call time,
which keeps the test and harness seams working).

Design note: docs/research/refresh-from-evidence-2026-11.md.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import zlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Layout of a stored record (key, meta, blob). A reader refuses any other value.
#: 2 (review round, 2026-11-16): meta names the track's digest, the download's format facts and
#: the recorder's provenance; the key names the separation this job did and ffmpeg.
EVIDENCE_FORMAT = 2

#: What OUR code that computes each recorded value looks like, by version. Bump the matching
#: entry whenever that code changes: `alignment._emit`, `_emissions`, `_load_track` (emissions);
#: `onsets` (onsets); `process._mix_features` and the beats / energy / structure modules (mix);
#: `process._decode`, `_separate_vocals`, `_mix_back`, `_aligner_decode` and the download's
#: format choice and duration (stem). Stored evidence from another version is refused, so the
#: next job runs in full. tests/test_evidence.py pins a hash of that source to these numbers.
EVIDENCE_CODE: dict[str, int] = {"emissions": 1, "onsets": 1, "mix": 1, "stem": 1}

#: The aligner input's sample rate (alignment.SAMPLE_RATE, which load_audio produces).
SAMPLE_RATE = 16_000

#: How far a job's credible duration hint (what the player plays) may sit from the recorded
#: download's duration before the replay refuses: the audio behind the id may have changed. The
#: lrclib ladder's own duration tolerance; client hints are whole seconds, probes are exact.
DURATION_TOLERANCE_S = 3.0

#: What every aligned job reads, so what every replay reads: a record without one of them is never
#: stored, and one without them is refused before a replay starts (it cannot stop after line QA
#: has counted the job). The aligner wav's duration is not among them: a job reads it only for
#: stamped lyrics with a record length (plain lyrics and LRC-text overrides have none), and a
#: replay that needs it and lacks it stops before alignment.
REQUIRED_META = ("download", "track_samples", "track_sha256", "onsets", "mix")

#: A merged record keeps the stored slices of earlier window plans up to this multiple of the new
#: record's own size (a row must not grow by one plan per stamp change forever).
MERGE_CAP_FACTOR = 2.0


class EvidenceMissing(Exception):  # noqa: N818 - a reason to run the job in full, not an error
    """The replay cannot serve this exactly; the job must run in full."""


# --- arrays ---------------------------------------------------------------------------------


def encode_array(array: Any) -> bytes:
    """Lossless: the array's bytes split into byte planes (all first bytes, then all second
    bytes, ...), then zlib. Float32 log-probabilities share their sign and exponent bytes, so the
    planes compress far better than the interleaved bytes (39 songs: 1.99 MB a song against 2.29
    with plain zlib and 2.74 raw)."""
    import numpy as np

    contiguous = np.ascontiguousarray(array)
    planes = contiguous.reshape(-1).view(np.uint8).reshape(-1, contiguous.dtype.itemsize)
    return zlib.compress(np.ascontiguousarray(planes.T).tobytes(), 6)


def decode_array(blob: bytes, dtype: str, shape: tuple[int, ...]) -> Any:
    """The array `encode_array` wrote, or EvidenceMissing when the bytes are not exactly one such
    stream of exactly this size (zlib itself ignores trailing bytes: an index applied to another
    record's blob could otherwise decode)."""
    import numpy as np

    item = np.dtype(dtype).itemsize
    size = item * math.prod(shape)
    stream = zlib.decompressobj()
    try:
        raw = stream.decompress(blob)
    except zlib.error as exc:
        raise EvidenceMissing(f"an encoded array does not decode ({exc})") from None
    if not stream.eof or stream.unused_data or len(raw) != size:
        raise EvidenceMissing("an encoded array does not match its index")
    planes = np.frombuffer(raw, dtype=np.uint8).reshape(item, -1)
    return np.ascontiguousarray(planes.T).reshape(-1).view(np.dtype(dtype)).reshape(shape)


def _numpy(array: Any) -> Any:
    """A float array on the CPU, whatever produced it (torch on CPU or CUDA, or numpy)."""
    import numpy as np

    if hasattr(array, "detach"):
        array = array.detach().cpu().numpy()
    return np.array(array, copy=True)


def _digest(audio: Any) -> str:
    """sha256 of a loaded track's samples (what the aligner heard), on the CPU."""
    import numpy as np

    values = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
    return hashlib.sha256(np.ascontiguousarray(values).tobytes()).hexdigest()


def _tensor(array: Any) -> Any:
    """What `_emit` returns: a torch tensor when torch is there (the worker), else the array."""
    try:
        import torch  # pyright: ignore[reportMissingImports]
    except ImportError:
        return array
    return torch.from_numpy(array)


def library_stride(n_samples: int, frames: int) -> int:
    """generate_emissions' own frame step (ctc_forced_aligner): ceil(n * 1000 / frames / rate)."""
    return math.ceil(float(n_samples * 1000 / frames / SAMPLE_RATE))


# --- the audio as the replay sees it --------------------------------------------------------


@dataclass(frozen=True)
class VirtualTrack:
    """The 16 kHz track during a replay: its length and, sliced, the sample range a window
    reads. Supports exactly what the aligner does with the loaded audio: `.shape[-1]` and
    `audio[..., a:b]`."""

    n: int
    lo: int = 0

    @property
    def shape(self) -> tuple[int]:
        return (self.n,)

    def __getitem__(self, index: Any) -> VirtualTrack:
        if isinstance(index, tuple):
            if len(index) != 2 or index[0] is not Ellipsis:
                raise EvidenceMissing(f"unsupported index {index!r} on the replayed track")
            index = index[1]
        if not isinstance(index, slice) or index.step not in (None, 1):
            raise EvidenceMissing(f"unsupported index {index!r} on the replayed track")
        start, stop, _ = index.indices(self.n)
        return VirtualTrack(max(0, stop - start), self.lo + start)


class StandInModel:
    """The aligner during a replay: its name, no weights (the emissions come from evidence)."""

    def __init__(self, name: str) -> None:
        self.config = type("Config", (), {"_name_or_path": name})()
        self.dtype = None
        self.device = "cpu"


def checkpoint_name(model: Any) -> str:
    return str(getattr(getattr(model, "config", None), "_name_or_path", "") or "")


# --- what a key is made of -------------------------------------------------------------------


def _distribution_version(name: str) -> str | None:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    try:  # a VCS install (ctc-forced-aligner is pinned to a git commit) names its commit
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        commit = (direct.get("vcs_info") or {}).get("commit_id")
    except (OSError, ValueError):
        commit = None
    return f"{dist.version}+{commit}" if commit else dist.version


@functools.lru_cache(maxsize=1)
def ffmpeg_version() -> str | None:
    """The ffmpeg that decodes the download and the stem ("8.1.2" in the image: a pinned static
    build, the same in the cluster worker and the GPU helper, both built from one Dockerfile)."""
    binary = shutil.which("ffmpeg")
    if binary is None:
        return None
    try:
        first = subprocess.run([binary, "-version"], capture_output=True, text=True,
                               timeout=20).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None
    found = re.match(r"ffmpeg version (\S+)", first)
    return found.group(1) if found else first[:80]


def runtime_versions() -> dict[str, str | None]:
    """Libraries and the ffmpeg build behind the recorded values. `KEY_LIBRARIES` are key: the
    aligner library's emission pass, librosa (onsets, beats, energy, structure), the separator
    and ffmpeg (the stem and the decode) — each is pinned and changes only by a deliberate bump.
    torch / transformers / numpy and yt-dlp are provenance only: the cluster worker (CPU build)
    and the GPU helper (CUDA build) already differ in the last bits of every emission and a replay
    serves the recorder's own arrays; yt-dlp is bumped whenever YouTube breaks it, which must not
    void the archive's evidence."""
    versions = {
        name: _distribution_version(name)
        for name in ("ctc-forced-aligner", "librosa", "audio-separator", "torch",
                     "transformers", "numpy", "yt-dlp")
    }
    versions["ffmpeg"] = ffmpeg_version()
    return versions


KEY_LIBRARIES = ("ctc-forced-aligner", "librosa", "audio-separator", "ffmpeg")


def expected_key(settings: Any, versions: dict[str, str | None] | None = None, *,
                 separate_first: bool) -> dict:
    """The song-wide key a record must carry to be replayed by THIS worker for THIS job.
    `separate_first`: the job aligns the separated stem from the start (process.
    _separates_first: the mode `always` or the job's `separate` option), so a record made over
    the mix never serves a stem job, nor the reverse. Per-slice keys (checkpoint, pinned
    revision, sample range) are checked when the aligner asks for each slice."""
    versions = runtime_versions() if versions is None else versions
    return {
        "format": EVIDENCE_FORMAT,
        "code": dict(EVIDENCE_CODE),
        "separation": {
            "mode": settings.separation_mode,
            "model": settings.separation_model_filename,
            "mixback": float(settings.separation_mixback),
            "first": bool(separate_first),
        },
        "structure_sections": bool(settings.structure_sections),
        "libraries": {name: versions.get(name) for name in KEY_LIBRARIES},
    }


def key_difference(stored: dict, expected: dict) -> str | None:
    """The first key field that differs, as 'path: stored -> expected', or None when equal."""

    def walk(a: Any, b: Any, path: str) -> str | None:
        if isinstance(a, dict) and isinstance(b, dict):
            for name in sorted(set(a) | set(b)):
                where = f"{path}.{name}" if path else name
                if (name in a) != (name in b):  # a field only one side has is a difference
                    return f"{where}: {'present' if name in a else 'absent'} -> " + (
                        "present" if name in b else "absent"
                    )
                found = walk(a[name], b[name], where)
                if found:
                    return found
            return None
        same = a == b and isinstance(a, bool) == isinstance(b, bool)
        return None if same else f"{path}: {a!r} -> {b!r}"

    return walk(stored, expected, "")


def provenance(settings: Any, pipeline_version: str) -> dict:
    """Who recorded a record and with what (stored, never compared)."""
    return {
        "worker": settings.worker_name,
        "pipeline_version": pipeline_version,
        "device": os.environ.get("KASHI_ALIGN_DEVICE", "cpu"),
        "versions": runtime_versions(),
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def duration_conflict(evidence: Evidence, hint_ms: int | None) -> str | None:
    """Why the record may not be the audio this job's player plays, or None: the one identity a
    replay can check without fetching. The job's credible duration hint is compared with the hint
    the record was made under (`hint_duration_ms`) — the player's own number on both sides, so a
    song whose player and download always disagreed by a few seconds is not refused forever — and,
    when the record was made without a hint, with the recorded download's duration. Beyond
    `DURATION_TOLERANCE_S` the job runs in full (and records again). No hint: nothing to compare.
    What it catches: a job whose player reports another length than the record's (an admin
    reprocess with corrected hints, a re-ingest after a failure). What it cannot: audio swapped
    behind the id while the hints stay as they were (a reprocess reuses the latest job's hints) —
    only `fresh_audio` covers that."""
    if hint_ms is None:
        return None
    recorded_hint = _number(evidence.meta.get("hint_duration_ms"))
    if recorded_hint is not None:
        reference, what = recorded_hint / 1000, "the recorded job's player"
    else:
        downloaded = _number((evidence.meta.get("download") or {}).get("duration_s"))
        if downloaded is None:
            return None
        reference, what = downloaded, "the recorded download"
    if abs(hint_ms / 1000 - reference) > DURATION_TOLERANCE_S:
        return f"the player's {hint_ms / 1000:.1f} s is not {what}'s {reference:.1f} s"
    return None


# --- the stored record -----------------------------------------------------------------------


@dataclass
class Evidence:
    """One song's stored evidence: `key` (song-wide, compared whole), `meta` (the recorded values
    and the emission index), the encoded emission arrays (concatenated) — given, or read on first
    use through `load_blob` (a store reads the key before the megabytes)."""

    source_type: str
    source_id: str
    key: dict
    meta: dict
    blob_bytes: bytes | None = None
    load_blob: Callable[[], bytes] | None = None

    @property
    def blob(self) -> bytes:
        if self.blob_bytes is None:
            self.blob_bytes = self.load_blob() if self.load_blob is not None else b""
        return self.blob_bytes

    def array(self, entry: dict) -> Any:
        part = self.blob[entry["offset"] : entry["offset"] + entry["length"]]
        return decode_array(part, entry["dtype"], (entry["frames"], entry["cols"]))


def entry_key(entry: dict) -> tuple:
    return entry["checkpoint"], entry["revision"], entry["lo"], entry["hi"]


def merged(stored: Evidence | None, new: Evidence) -> Evidence:
    """What to store after a full job: `new`, plus the stored slices `new` did not read when both
    describe the same audio under the same key (the same track digest) and come from a checkpoint
    and revision `new` also read: a job whose plan cut fewer or other windows must not throw away
    slices a later plan may ask for again, while another checkpoint's or a superseded revision's
    slices could never be served and are dropped."""
    if (
        stored is None
        or stored.key != new.key
        or stored.meta.get("track_sha256") is None
        or stored.meta.get("track_sha256") != new.meta.get("track_sha256")
    ):
        return new
    have = {entry_key(e) for e in new.meta.get("emissions", [])}
    models = {(e["checkpoint"], e["revision"]) for e in new.meta.get("emissions", [])}
    index = [dict(e) for e in new.meta.get("emissions", [])]
    parts = [new.blob]
    offset = len(new.blob)
    cap = MERGE_CAP_FACTOR * len(new.blob)
    try:
        stored_blob = stored.blob
    except EvidenceMissing:  # replaced while read: nothing of it can be trusted
        return new
    for entry in stored.meta.get("emissions", []):  # the most recently recorded first
        if entry_key(entry) in have or (entry["checkpoint"], entry["revision"]) not in models:
            continue
        if offset + entry["length"] > cap:
            break
        part = stored_blob[entry["offset"] : entry["offset"] + entry["length"]]
        index.append({**entry, "offset": offset, "length": len(part)})
        parts.append(part)
        offset += len(part)
    meta = {**new.meta, "emissions": index}
    return Evidence(new.source_type, new.source_id, new.key, meta, b"".join(parts))


class EvidenceStore(Protocol):
    def load(self, source_type: str, source_id: str) -> Evidence | None: ...
    def save(self, evidence: Evidence) -> None: ...


class DirStore:
    """Evidence as files (<dir>/<type>/<id>.json + .bin): the offline harness, or a volume."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _paths(self, source_type: str, source_id: str) -> tuple[Path, Path]:
        base = self.root / source_type / source_id
        return base.with_suffix(".json"), base.with_suffix(".bin")

    def load(self, source_type: str, source_id: str) -> Evidence | None:
        head, body = self._paths(source_type, source_id)
        if not head.exists() or not body.exists():
            return None
        data = json.loads(head.read_text())
        return Evidence(source_type, source_id, data["key"], data["meta"],
                        load_blob=body.read_bytes)

    def save(self, evidence: Evidence) -> None:
        head, body = self._paths(evidence.source_type, evidence.source_id)
        head.parent.mkdir(parents=True, exist_ok=True)
        tmp_body, tmp_head = body.with_suffix(".bin.tmp"), head.with_suffix(".json.tmp")
        tmp_body.write_bytes(evidence.blob)
        tmp_head.write_text(json.dumps({"key": evidence.key, "meta": evidence.meta}))
        tmp_body.replace(body)
        tmp_head.replace(head)


class DbStore:
    """Evidence in Postgres (`track_evidence`, one row per song). `load` reads the key and the
    meta; the arrays only when a replay first asks for one."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def load(self, source_type: str, source_id: str) -> Evidence | None:
        from sqlalchemy import select

        from kashi_server.db.models import TrackEvidence

        row = self.session.execute(
            select(TrackEvidence.key, TrackEvidence.meta, TrackEvidence.updated_at).where(
                TrackEvidence.source_type == source_type, TrackEvidence.source_id == source_id
            )
        ).first()
        if row is None:
            return None
        token = row.updated_at

        def blob() -> bytes:
            # The arrays of THIS version of the row: another writer (a back-fill beside a live
            # job) may have replaced it since the key and the index were read.
            value = self.session.execute(
                select(TrackEvidence.emissions).where(
                    TrackEvidence.source_type == source_type,
                    TrackEvidence.source_id == source_id,
                    TrackEvidence.updated_at == token,
                )
            ).scalar()
            if value is None:
                raise EvidenceMissing("the stored record changed while it was read")
            return bytes(value)

        return Evidence(source_type, source_id, row.key, row.meta, load_blob=blob)

    def save(self, evidence: Evidence) -> None:
        from sqlalchemy.dialects.postgresql import insert

        from kashi_server.db.models import TrackEvidence

        values = {
            "source_type": evidence.source_type,
            "source_id": evidence.source_id,
            "format": int(evidence.key.get("format", 0)),
            "key": evidence.key,
            "meta": evidence.meta,
            "emissions": evidence.blob,
            "size_bytes": len(evidence.blob),
        }
        stmt = insert(TrackEvidence).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_track_evidence_source",
            set_={**{k: stmt.excluded[k] for k in values if k not in ("source_type", "source_id")},
                  "updated_at": _clock()},
        )
        self.session.execute(stmt)


def _clock() -> Any:
    from sqlalchemy import func

    return func.clock_timestamp()


# --- sessions --------------------------------------------------------------------------------


class _Session:
    replaying: bool = False


@contextlib.contextmanager
def _guarded(session: Any, what: str) -> Iterator[None]:
    """The recorder's own work (never the computation it wraps): a fault there must cost the
    record, not the job (review finding 6)."""
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        logger.warning("evidence recorder fault in %s", what, exc_info=True)
        session.refuse(f"recorder fault in {what}: {type(exc).__name__}")


@dataclass
class Recorder(_Session):
    """A full job's audio boundaries, kept. `usable` turns False when the job read audio this
    record cannot key (a nightcore clock, a second-pass separation, two different tracks, a slice
    of an unknown track, an unpinned checkpoint) or an extractor failed; such a record is never
    stored."""

    source_type: str
    source_id: str
    key: dict
    made_by: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    arrays: dict[tuple, Any] = field(default_factory=dict)
    order: list[tuple] = field(default_factory=list)
    usable: bool = True
    why_not: str | None = None
    _tracks: list[tuple[Any, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        unknown = unversioned(self.key)
        if unknown:
            self.refuse(f"no version for {', '.join(unknown)}")

    def refuse(self, reason: str) -> None:
        if self.usable:
            self.usable, self.why_not = False, reason

    def missing(self) -> list[str]:
        """What every replay reads that this recording never saw (an unaligned job)."""
        return [name for name in REQUIRED_META if name not in self.meta]

    def span(self, audio: Any) -> tuple[str, int, int] | None:
        """(the track's digest, first sample, end sample) of a view of a loaded track."""
        for track, digest in self._tracks:
            found = _view_span(audio, track)
            if found is not None:
                return digest, found[0], found[1]
        return None

    def add_track(self, audio: Any) -> None:
        digest = _digest(audio)
        seen = self.meta.get("track_sha256")
        if seen is not None and seen != digest:
            self.refuse("two different tracks in one job")
        self.meta.setdefault("track_sha256", digest)
        self.meta.setdefault("track_samples", int(audio.shape[-1]))
        self._tracks.append((audio, digest))

    def pack(self) -> Evidence:
        index, parts, offset = [], [], 0
        for key in self.order:
            array = self.arrays[key]
            blob = encode_array(array)
            checkpoint, revision, _digest_of_track, lo, hi = key
            index.append({
                "checkpoint": checkpoint, "revision": revision, "lo": lo, "hi": hi,
                "frames": int(array.shape[0]), "cols": int(array.shape[1]),
                "dtype": str(array.dtype), "offset": offset, "length": len(blob),
            })
            parts.append(blob)
            offset += len(blob)
        meta = {**self.meta, "emissions": index, "made_by": self.made_by}
        return Evidence(self.source_type, self.source_id, self.key, meta, b"".join(parts))


def _address(array: Any) -> tuple[int, int, int] | None:
    """(first element's address, element size, element stride) of a 1-D array, or None."""
    if hasattr(array, "data_ptr"):  # torch
        if array.dim() != 1:
            return None
        return int(array.data_ptr()), int(array.element_size()), int(array.stride(0))
    interface = getattr(array, "__array_interface__", None)
    if interface is None or len(getattr(array, "shape", ())) != 1:
        return None
    stride = array.strides[0] // array.itemsize if array.strides else 1
    return int(interface["data"][0]), int(array.itemsize), int(stride)


def _view_span(audio: Any, track: Any) -> tuple[int, int] | None:
    """The sample range `audio` covers when it is a contiguous view into `track`, else None."""
    if audio is track:
        return 0, int(track.shape[-1])
    mine, theirs = _address(audio), _address(track)
    if mine is None or theirs is None or mine[1] != theirs[1] or mine[2] != 1 or theirs[2] != 1:
        return None
    delta = mine[0] - theirs[0]
    if delta < 0 or delta % mine[1]:
        return None
    lo = delta // mine[1]
    hi = lo + int(audio.shape[-1])
    if hi > int(track.shape[-1]):
        return None
    return lo, hi


@dataclass
class Replayer(_Session):
    """Serves a stored record. Every request it cannot serve exactly raises EvidenceMissing."""

    evidence: Evidence
    replaying: bool = True
    served: dict[str, int] = field(default_factory=dict)
    _decoded: dict[int, Any] = field(default_factory=dict)

    def _count(self, what: str) -> None:
        self.served[what] = self.served.get(what, 0) + 1

    def meta(self, name: str) -> Any:
        if name not in self.evidence.meta:
            raise EvidenceMissing(f"no recorded {name}")
        self._count(name)
        return self.evidence.meta[name]

    def track(self) -> VirtualTrack:
        return VirtualTrack(int(self.meta("track_samples")))

    def emissions(self, name: str, revision: str | None, audio: Any) -> Any:
        if revision is None:
            raise EvidenceMissing(f"{name} has no pinned revision")
        if not isinstance(audio, VirtualTrack):
            raise EvidenceMissing("emissions asked for audio that is not the replayed track")
        wanted = (name, revision, audio.lo, audio.lo + audio.n)
        for number, entry in enumerate(self.evidence.meta.get("emissions", [])):
            if entry_key(entry) == wanted:
                self._count("emissions")
                if number not in self._decoded:
                    self._decoded[number] = self.evidence.array(entry)
                return self._decoded[number].copy()
        raise EvidenceMissing(
            f"no emissions for {name}@{revision} over samples {wanted[2]}-{wanted[3]}"
        )


def unversioned(key: dict) -> list[str]:
    """Key libraries whose version could not be read: None would equal None on the other side
    after either changed (review finding 8, like an unpinned checkpoint)."""
    libraries = key.get("libraries") if isinstance(key, dict) else None
    return sorted(name for name, value in (libraries or {}).items() if value is None)


def replayer_for(evidence: Evidence | None, settings: Any,
                 versions: dict[str, str | None] | None = None, *,
                 separate_first: bool) -> tuple[Replayer | None, str]:
    """(a Replayer, "") when `evidence` can be replayed by this worker for a job that does (or
    does not) separate first, else (None, why not)."""
    if evidence is None:
        return None, "no evidence stored"
    expected = expected_key(settings, versions, separate_first=separate_first)
    unknown = unversioned(expected)
    if unknown:
        return None, f"no version for {', '.join(unknown)}"
    difference = key_difference(
        evidence.key, expected
    )
    if difference is not None:
        return None, f"key differs at {difference}"
    missing = [name for name in REQUIRED_META if name not in evidence.meta]
    if missing:
        return None, f"evidence lacks {', '.join(missing)}"
    return Replayer(evidence), ""


_SESSION: ContextVar[_Session | None] = ContextVar("evidence_session", default=None)


def current() -> _Session | None:
    return _SESSION.get()


@contextlib.contextmanager
def active(session: _Session) -> Iterator[_Session]:
    token = _SESSION.set(session)
    try:
        yield session
    finally:
        _SESSION.reset(token)


# --- the hooks (each a no-op pass-through without a session) --------------------------------


def emissions(model: Any, audio: Any, exact_stride: bool, compute: Callable[..., Any],
              stride_of: Callable[[int, int, int], float], revision: str | None) -> tuple[Any, Any]:
    """Every emission pass of the aligner (`alignment._emissions`). `stride_of` is
    alignment.exact_stride_ms; a replay derives the frame step from the slice length exactly as
    the library and `_emit` do. `revision`: the checkpoint's pinned hub revision (None: unpinned,
    which no record keys and no replay serves — the hub's `main` moves)."""
    session = current()
    if session is None:
        return compute(model, audio, exact_stride=exact_stride)
    name = checkpoint_name(model)
    if isinstance(session, Replayer):
        array = session.emissions(name, revision, audio)
        n, frames = int(audio.shape[-1]), int(array.shape[0])
        lib = library_stride(n, frames)
        return _tensor(array), (stride_of(n, frames, lib) if exact_stride else lib)
    assert isinstance(session, Recorder)
    if revision is None:
        session.refuse(f"{name} has no pinned revision")
    key = None
    with _guarded(session, "emissions"):
        span = session.span(audio)
        key = None if span is None else (name, revision, *span)
        if key is not None and key in session.arrays:  # the second whole-track pass of a job
            array = session.arrays[key]
            n, frames = int(audio.shape[-1]), int(array.shape[0])
            lib = library_stride(n, frames)
            return _tensor(array.copy()), (stride_of(n, frames, lib) if exact_stride else lib)
    em, stride = compute(model, audio, exact_stride=exact_stride)
    if key is None:
        session.refuse("emissions over audio that is no view of the loaded track")
        return em, stride
    with _guarded(session, "emissions"):
        array = _numpy(em)
        if array.ndim == 3 and array.shape[0] == 1:
            array = array[0]
        n, frames = int(audio.shape[-1]), int(array.shape[0])
        lib = library_stride(n, frames)
        expected = stride_of(n, frames, lib) if exact_stride else lib
        if expected != stride or type(expected) is not type(stride):
            session.refuse(f"frame step {stride!r} is not the derived {expected!r}")
        session.arrays[key] = array
        session.order.append(key)
    return em, stride


def track(compute: Callable[[], Any]) -> Any:
    """The aligner's loaded 16 kHz track (`load_audio` in align() and heard_tokens())."""
    session = current()
    if session is None:
        return compute()
    if isinstance(session, Replayer):
        return session.track()
    assert isinstance(session, Recorder)
    audio = compute()
    with _guarded(session, "the track"):
        session.add_track(audio)
    return audio


def _download_facts(info: Any) -> dict:
    """What yt-dlp said about the file it fetched (provenance: a later job cannot compare it
    without fetching again)."""
    if not isinstance(info, dict):
        return {}
    facts = {name: info.get(name) for name in ("format_id", "ext", "asr", "filesize",
                                               "filesize_approx", "duration")}
    return {k: v for k, v in facts.items() if isinstance(v, str | int | float)}


def download(compute: Callable[[], Any], placeholder: Path) -> Any:
    """The job's audio fetch. A replay hands back a DownloadResult whose path is never read."""
    session = current()
    if session is None:
        return compute()
    if isinstance(session, Replayer):
        from kashi_server.pipeline.download import DownloadResult

        recorded = session.meta("download")
        return DownloadResult(path=placeholder, abr=recorded["abr"], acodec=recorded["acodec"],
                              duration_s=recorded["duration_s"], info={})
    assert isinstance(session, Recorder)
    result = compute()
    with _guarded(session, "the download"):
        session.meta["download"] = {"duration_s": result.duration_s, "abr": result.abr,
                                    "acodec": result.acodec,
                                    "facts": _download_facts(getattr(result, "info", None))}
    return result


def separated(compute: Callable[[], Path], placeholder: Path) -> Path:
    """The vocal stem. The separator is part of the key; a replay never separates."""
    session = current()
    if session is None:
        return compute()
    if isinstance(session, Replayer):
        session._count("separation")
        return placeholder
    return compute()


def aligner_wav(compute: Callable[[], Path], dest: Path) -> Path:
    """The plain 16 kHz decode the aligner, the sheet shift and line QA read (tempo 1)."""
    session = current()
    if session is None:
        return compute()
    if isinstance(session, Replayer):
        session._count("decode")
        return dest
    return compute()


def wav_duration(compute: Callable[[], float]) -> float:
    session = current()
    if session is None:
        return compute()
    if isinstance(session, Replayer):
        return float(session.meta("wav_duration_s"))
    assert isinstance(session, Recorder)
    value = compute()
    with _guarded(session, "the wav duration"):
        if "wav_duration_s" in session.meta and session.meta["wav_duration_s"] != value:
            session.refuse("two different aligner wav durations")
        session.meta["wav_duration_s"] = value
    return value


def onsets(compute: Callable[[], list[int] | None]) -> list[int] | None:
    """The stem's onsets line QA reads. None is recorded only when detection found none: a
    detector that FAILED says so (`extractor_failed`) and the record is not stored."""
    session = current()
    if session is None:
        return compute()
    if isinstance(session, Replayer):
        value = session.meta("onsets")
        return None if value is None else list(value)
    assert isinstance(session, Recorder)
    value = compute()
    with _guarded(session, "the onsets"):
        session.meta["onsets"] = None if value is None else [int(t) for t in value]
    return value


def mix_features(compute: Callable[[], tuple]) -> tuple:
    """Beats, energy and sections off the full mix (process._mix_features)."""
    session = current()
    if session is None:
        return compute()
    if isinstance(session, Replayer):
        from kashi_server.pipeline.beats import Beats
        from kashi_server.pipeline.energy import Energy, Section

        recorded = session.meta("mix")
        beats = None if recorded["beats"] is None else Beats(**recorded["beats"])
        energy = None if recorded["energy"] is None else Energy(**recorded["energy"])
        sections = (None if recorded["sections"] is None
                    else [Section(**s) for s in recorded["sections"]])
        return beats, energy, sections
    assert isinstance(session, Recorder)
    beats, energy, sections = compute()
    with _guarded(session, "the mix features"):
        _record_mix(session, beats, energy, sections)
    return beats, energy, sections


def _record_mix(session: Recorder, beats: Any, energy: Any, sections: Any) -> None:
    session.meta["mix"] = {
        "beats": None if beats is None else {
            "bpm": beats.bpm, "confidence": beats.confidence,
            "times_ms": list(beats.times_ms), "downbeat_indices": list(beats.downbeat_indices)},
        "energy": None if energy is None else {
            "rate_hz": energy.rate_hz, "values": list(energy.values)},
        "sections": None if sections is None else [
            {"type": s.type, "start_ms": s.start_ms, "end_ms": s.end_ms} for s in sections],
    }


def extractor_failed(what: str) -> None:
    """A best-effort extractor (onsets, beats, energy, structure) swallowed an error and returns
    None. The job carries on as before; a recording made in it is not stored, because a healthy
    job would have read a value there (review finding 2, 2026-11-16)."""
    session = current()
    if isinstance(session, Recorder):
        session.refuse(f"{what} failed")


def other_audio(reason: str, *, keyed: bool = True) -> None:
    """A read of the audio the record does not cover. A replay refuses; a recording carries on,
    and stops being storable when the read changes what its keys mean (`keyed=False`: a
    nightcore clock, a second-pass separation)."""
    session = current()
    if session is None:
        return
    if isinstance(session, Replayer):
        raise EvidenceMissing(reason)
    if not keyed:
        assert isinstance(session, Recorder)
        session.refuse(reason)


def replaying() -> bool:
    session = current()
    return session is not None and session.replaying
