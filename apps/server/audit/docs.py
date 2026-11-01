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


def documents_by_id(raw: list | dict) -> dict[str, dict]:
    """Every document of a dump, by video id. A single document (what the API
    serves and ``audit.run_local`` writes) is keyed by its ``track.source.id``.
    Dump entries without a document (a song the API had nothing for) are
    left out."""
    if isinstance(raw, dict):
        source = ((raw.get("track") or {}).get("source") or {}).get("id")
        if not source:
            raise ValueError("a single document needs track.source.id to be compared")
        return {str(source): raw}
    return {str(entry["id"]): entry["doc"] for entry in raw if entry.get("doc")}


def load_documents(path: Path) -> dict[str, dict]:
    return documents_by_id(json.loads(path.read_text()))
