"""Separated vocal stems for the audit, made the way the worker makes them.

Production chain (``worker/process.py``): the downloaded file is separated
directly (``_separation_source``), the stem is decoded to 16 kHz mono
(``_align_stage``), and the arbiter's onsets and vocal energy are measured on
that 16 kHz file. This module runs the same three calls with the live
separator (``kim-base``, mixback 0) and keeps the stem; the downloaded audio is
deleted as soon as the stem exists, whatever happens.

Run inside the server image (see README):

    python -m audit.stems <video id> [<video id> ...]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from audit.paths import RAW_DIR, STEMS_DIR, stem_16k_path, stem_meta_path, stem_path

logger = logging.getLogger("audit.stems")

# The live worker's separator (gitops deployment-worker.yaml,
# SEPARATION_MODEL_FILENAME); the settings default is a different checkpoint,
# so the name is always passed explicitly. Same file as the bench's "kim-base".
SEPARATION_MODEL = "vocals_mel_band_roformer.ckpt"
MAX_DURATION_S = 1200  # settings.max_track_duration_s


def _manual_raw(video_id: str) -> Path | None:
    found = sorted(p for p in RAW_DIR.glob(f"{video_id}.*") if p.is_file())
    return found[0] if found else None


def decode_16k(video_id: str) -> Path:
    """16 kHz mono copy of the stem, decoded by the worker's own ``_decode``."""
    from kashi_server.worker.process import _decode

    dest = stem_16k_path(video_id)
    if not dest.exists():
        _decode(stem_path(video_id), dest, rate=16000)
    return dest


def make_stem(video_id: str) -> Path:
    """Download (or take the manual file), separate, keep the stem, delete the audio."""
    from kashi_server.pipeline.download import download_audio
    from kashi_server.worker.process import _separate_vocals

    out = stem_path(video_id)
    if out.exists():
        logger.info("%s: stem already there", video_id)
        decode_16k(video_id)
        return out
    STEMS_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manual = _manual_raw(video_id)
    meta: dict = {"id": video_id, "model": SEPARATION_MODEL, "mixback": 0}
    # The temp dir lives next to the stems (same disk, so the final move is a
    # rename) and is removed in `finally`: the audio-deletion guarantee.
    tmp = Path(tempfile.mkdtemp(prefix=f"{video_id}-", dir=RAW_DIR))
    try:
        started = time.monotonic()
        if manual is not None:
            audio = manual
            meta["source"] = "manual"
        else:
            result = download_audio(video_id, tmp / "dl", max_duration_s=MAX_DURATION_S)
            audio = result.path
            meta |= {"source": "youtube", "abr": result.abr, "acodec": result.acodec,
                     "duration_s": result.duration_s}
        meta["download_s"] = round(time.monotonic() - started, 1)
        started = time.monotonic()
        vocals = _separate_vocals(audio, tmp, model_filename=SEPARATION_MODEL, mixback=0)
        meta["separate_s"] = round(time.monotonic() - started, 1)
        shutil.move(str(vocals), out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if manual is not None and out.exists():
            manual.unlink(missing_ok=True)
    decode_16k(video_id)
    meta["created"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    stem_meta_path(video_id).write_text(json.dumps(meta, indent=1) + "\n")
    logger.info("%s: stem in %.0f s (download %.0f s)", video_id, meta["separate_s"],
                meta["download_s"])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Separated vocal stems for the perceptual audit.")
    parser.add_argument("ids", nargs="+", help="YouTube video ids")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    failed = 0
    for video_id in args.ids:
        try:
            make_stem(video_id)
        except Exception:  # noqa: BLE001 - one bad id must not stop the batch
            logger.exception("%s: failed", video_id)
            failed += 1
    stray = [p for p in RAW_DIR.rglob("*") if p.is_file() and p.parent != RAW_DIR]
    if stray:  # a temp dir survived: the deletion guarantee is broken
        logger.error("audio left behind: %s", ", ".join(map(str, stray)))
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
