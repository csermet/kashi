"""Where the audit keeps its files: ``<repo>/scratch/audit`` (git-excluded).

Nothing under this tree may be committed: stems are separated copyrighted audio
and every picture carries lyric text.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_ROOT = Path(os.environ.get("KASHI_AUDIT_ROOT", REPO_ROOT / "scratch" / "audit"))
STEMS_DIR = AUDIT_ROOT / "stems"
# Transient downloads only. A manually supplied file (the 403 fallback) is
# dropped here as `<video id>.<ext>` and deleted once its stem exists.
RAW_DIR = AUDIT_ROOT / "raw"
EAR_DIR = AUDIT_ROOT / "ear"
RUNS_DIR = AUDIT_ROOT / "runs"


def stem_path(video_id: str) -> Path:
    """The separator's own output, kept as produced (44.1 kHz)."""
    return STEMS_DIR / f"{video_id}.wav"


def stem_16k_path(video_id: str) -> Path:
    """The stem decoded exactly as the worker decodes it for alignment."""
    return STEMS_DIR / f"{video_id}.16k.wav"


def stem_meta_path(video_id: str) -> Path:
    return STEMS_DIR / f"{video_id}.json"
