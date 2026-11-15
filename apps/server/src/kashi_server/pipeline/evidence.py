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
separation, no model forward pass. Whatever a replay cannot serve exactly (a slice another
window plan cuts differently, another checkpoint or revision, a nightcore clock, the by-ear rung,
another separator, another library that computed a value, a value never recorded) raises
`EvidenceMissing`, always before anything is persisted, and the job runs in full instead.

The hooks are the module-level functions below (`emissions`, `track`, `download`, ...). Each
takes the real computation as `compute` and calls it as given when no session is active, so with
the mode off the pipeline does exactly what it did (the callers look `compute` up at call time,
which keeps the test and harness seams working).

Design note: `docs/research` (indirmesiz-yenileme, 2026-11-15).
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import zlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Layout of a stored record (key, meta, blob). A reader refuses any other value.
EVIDENCE_FORMAT = 1

#: What OUR code that computes each recorded value looks like, by version. Bump the matching
#: entry whenever that code changes: `alignment._emit` / `_emissions` (emissions), `onsets.
#: detect_onsets` (onsets), `process._mix_features` and the beats / energy / structure modules
#: (mix). Stored evidence from another version is refused, so the next job runs in full.
#: tests/test_evidence.py pins a hash of those functions' source to these numbers.
EVIDENCE_CODE: dict[str, int] = {"emissions": 1, "onsets": 1, "mix": 1}

#: The aligner input's sample rate (alignment.SAMPLE_RATE, which load_audio produces).
SAMPLE_RATE = 16_000


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
    import numpy as np

    item = np.dtype(dtype).itemsize
    planes = np.frombuffer(zlib.decompress(blob), dtype=np.uint8).reshape(item, -1)
    return np.ascontiguousarray(planes.T).reshape(-1).view(np.dtype(dtype)).reshape(shape)


def _numpy(array: Any) -> Any:
    """A float array on the CPU, whatever produced it (torch on CPU or CUDA, or numpy)."""
    import numpy as np

    if hasattr(array, "detach"):
        array = array.detach().cpu().numpy()
    return np.array(array, copy=True)


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


def runtime_versions() -> dict[str, str | None]:
    """Libraries whose code computes a recorded value. `ctc-forced-aligner` (generate_emissions)
    and `librosa` (onsets, beats, energy, structure) are part of the key; torch and transformers
    are provenance only — the cluster worker (CPU build) and the GPU helper (CUDA build) already
    differ in the last bits of every emission, and replaying the recorder's own emissions is
    exactly what the replay is for."""
    return {
        name: _distribution_version(name)
        for name in ("ctc-forced-aligner", "librosa", "torch", "transformers", "numpy")
    }


KEY_LIBRARIES = ("ctc-forced-aligner", "librosa")


def expected_key(settings: Any, versions: dict[str, str | None] | None = None) -> dict:
    """The song-wide key a record must carry to be replayed by THIS worker. Per-slice keys
    (checkpoint, revision, sample range) are checked when the aligner asks for each slice."""
    versions = runtime_versions() if versions is None else versions
    return {
        "format": EVIDENCE_FORMAT,
        "code": dict(EVIDENCE_CODE),
        "separation": {
            "mode": settings.separation_mode,
            "model": settings.separation_model_filename,
            "mixback": float(settings.separation_mixback),
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


# --- the stored record -----------------------------------------------------------------------


@dataclass
class Evidence:
    """One song's stored evidence: `key` (song-wide, compared whole), `meta` (the recorded values
    and the emission index), `blob` (the encoded emission arrays, concatenated)."""

    source_type: str
    source_id: str
    key: dict
    meta: dict
    blob: bytes

    def array(self, entry: dict) -> Any:
        part = self.blob[entry["offset"] : entry["offset"] + entry["length"]]
        return decode_array(part, entry["dtype"], (entry["frames"], entry["cols"]))


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
        return Evidence(source_type, source_id, data["key"], data["meta"], body.read_bytes())

    def save(self, evidence: Evidence) -> None:
        head, body = self._paths(evidence.source_type, evidence.source_id)
        head.parent.mkdir(parents=True, exist_ok=True)
        tmp_body, tmp_head = body.with_suffix(".bin.tmp"), head.with_suffix(".json.tmp")
        tmp_body.write_bytes(evidence.blob)
        tmp_head.write_text(json.dumps({"key": evidence.key, "meta": evidence.meta}))
        tmp_body.replace(body)
        tmp_head.replace(head)


class DbStore:
    """Evidence in Postgres (`track_evidence`, one row per song, replaced by each full job)."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def load(self, source_type: str, source_id: str) -> Evidence | None:
        from sqlalchemy import select

        from kashi_server.db.models import TrackEvidence

        row = self.session.scalars(
            select(TrackEvidence).where(
                TrackEvidence.source_type == source_type, TrackEvidence.source_id == source_id
            )
        ).first()
        if row is None:
            return None
        return Evidence(row.source_type, row.source_id, row.key, row.meta, bytes(row.emissions))

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


@dataclass
class Recorder(_Session):
    """A full job's audio boundaries, kept. `usable` turns False when the job read audio this
    record cannot key (a nightcore clock, a second-pass separation, a slice of an unknown track);
    such a record is never stored."""

    source_type: str
    source_id: str
    key: dict
    meta: dict = field(default_factory=dict)
    arrays: dict[tuple, Any] = field(default_factory=dict)
    order: list[tuple] = field(default_factory=list)
    usable: bool = True
    why_not: str | None = None
    _tracks: list[Any] = field(default_factory=list)

    def refuse(self, reason: str) -> None:
        if self.usable:
            self.usable, self.why_not = False, reason

    # Every array view of a registered track maps to its sample range on that track.
    def span(self, audio: Any) -> tuple[int, int] | None:
        for track in self._tracks:
            found = _view_span(audio, track)
            if found is not None:
                return found
        return None

    def add_track(self, audio: Any) -> None:
        n = int(audio.shape[-1])
        seen = self.meta.get("track_samples")
        if seen is not None and seen != n:
            self.refuse(f"two different tracks ({seen} and {n} samples)")
        self.meta["track_samples"] = n
        self._tracks.append(audio)

    def pack(self) -> Evidence:
        index, parts, offset = [], [], 0
        for key in self.order:
            array = self.arrays[key]
            blob = encode_array(array)
            checkpoint, revision, lo, hi = key
            index.append({
                "checkpoint": checkpoint, "revision": revision, "lo": lo, "hi": hi,
                "frames": int(array.shape[0]), "cols": int(array.shape[1]),
                "dtype": str(array.dtype), "offset": offset, "length": len(blob),
            })
            parts.append(blob)
            offset += len(blob)
        meta = {**self.meta, "emissions": index}
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
        if not isinstance(audio, VirtualTrack):
            raise EvidenceMissing("emissions asked for audio that is not the replayed track")
        wanted = (name, revision, audio.lo, audio.lo + audio.n)
        for number, entry in enumerate(self.evidence.meta.get("emissions", [])):
            if (entry["checkpoint"], entry["revision"], entry["lo"], entry["hi"]) == wanted:
                self._count("emissions")
                if number not in self._decoded:
                    self._decoded[number] = self.evidence.array(entry)
                return self._decoded[number].copy()
        raise EvidenceMissing(
            f"no emissions for {name}@{revision} over samples {wanted[2]}-{wanted[3]}"
        )


def replayer_for(evidence: Evidence | None, settings: Any,
                 versions: dict[str, str | None] | None = None) -> tuple[Replayer | None, str]:
    """(a Replayer, "") when `evidence` can be replayed by this worker, else (None, why not)."""
    if evidence is None:
        return None, "no evidence stored"
    difference = key_difference(evidence.key, expected_key(settings, versions))
    if difference is not None:
        return None, f"key differs at {difference}"
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
    the library and `_emit` do."""
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
    span = session.span(audio)
    key = None if span is None else (name, revision, span[0], span[1])
    if key is not None and key in session.arrays:  # the second whole-track pass of a job
        array = session.arrays[key]
        n, frames = int(audio.shape[-1]), int(array.shape[0])
        lib = library_stride(n, frames)
        return _tensor(array.copy()), (stride_of(n, frames, lib) if exact_stride else lib)
    em, stride = compute(model, audio, exact_stride=exact_stride)
    if key is None:
        session.refuse("emissions over audio that is no view of the loaded track")
        return em, stride
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
    session.add_track(audio)
    return audio


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
    session.meta["download"] = {"duration_s": result.duration_s, "abr": result.abr,
                                "acodec": result.acodec}
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
    if "wav_duration_s" in session.meta and session.meta["wav_duration_s"] != value:
        session.refuse("two different aligner wav durations")
    session.meta["wav_duration_s"] = value
    return value


def onsets(compute: Callable[[], list[int] | None]) -> list[int] | None:
    """The stem's onsets line QA reads (None, as recorded, when detection failed)."""
    session = current()
    if session is None:
        return compute()
    if isinstance(session, Replayer):
        value = session.meta("onsets")
        return None if value is None else list(value)
    assert isinstance(session, Recorder)
    value = compute()
    session.meta["onsets"] = None if value is None else list(value)
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
    session.meta["mix"] = {
        "beats": None if beats is None else {
            "bpm": beats.bpm, "confidence": beats.confidence,
            "times_ms": list(beats.times_ms), "downbeat_indices": list(beats.downbeat_indices)},
        "energy": None if energy is None else {
            "rate_hz": energy.rate_hz, "values": list(energy.values)},
        "sections": None if sections is None else [
            {"type": s.type, "start_ms": s.start_ms, "end_ms": s.end_ms} for s in sections],
    }
    return beats, energy, sections


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
