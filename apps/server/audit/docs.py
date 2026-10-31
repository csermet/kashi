"""Loading documents for the audit: a single document or a ``[{id, doc}]`` dump
(the shape ``scratch/favori-seti/favori.sh`` writes)."""

from __future__ import annotations

import json
from pathlib import Path


def load_document(path: Path, video_id: str | None = None) -> dict:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict):
        return raw
    for entry in raw:
        if entry.get("id") == video_id and entry.get("doc"):
            return entry["doc"]
    raise KeyError(f"no document for {video_id} in {path}")
