"""Fetch the lyrics TEXT the aligner needs (server side).

This is not the overlay's lrclib client and never becomes one: the server does
not proxy lyrics to clients (plan R-5). It fetches the words once, aligns them
against the audio, and only the derived timings ever leave the pipeline.

The `syncedLyrics` variant is preferred and its timestamps are STRIPPED — its
line breaks are exactly what a line-mode client displays, so word-level output
regroups into the same lines. The timestamps themselves are discarded; every
time in the output comes from the aligner.
"""

import logging
import re
from dataclasses import dataclass

import httpx

from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.version import PIPELINE_VERSION

logger = logging.getLogger(__name__)

USER_AGENT = f"kashi-server/{PIPELINE_VERSION} (+https://github.com/csermet/kashi)"
SEARCH_DURATION_TOLERANCE_S = 3
_TIMESTAMP = re.compile(r"^\[\d{2}:\d{2}[.:]\d{2,3}\]\s*")
_TOPIC_SUFFIX = re.compile(r"\s*-\s*Topic$", re.IGNORECASE)


@dataclass(frozen=True)
class LyricsText:
    line_texts: list[str]
    full_text: str
    source_id: int
    had_synced: bool


def normalize_artist(artist: str) -> str:
    """YouTube's auto-generated channels append " - Topic" (plan R-2)."""
    return _TOPIC_SUFFIX.sub("", artist).strip()


def _lines_from_synced(synced: str) -> list[str]:
    lines = [_TIMESTAMP.sub("", raw).strip() for raw in synced.splitlines()]
    return [line for line in lines if line]


def _lines_from_plain(plain: str) -> list[str]:
    return [line.strip() for line in plain.splitlines() if line.strip()]


def _extract(record: dict) -> tuple[list[str], bool] | None:
    if record.get("instrumental"):
        return None
    synced = record.get("syncedLyrics")
    if synced:
        lines = _lines_from_synced(synced)
        if lines:
            return lines, True
    plain = record.get("plainLyrics")
    if plain:
        lines = _lines_from_plain(plain)
        if lines:
            return lines, False
    return None


def _duration_matches(record: dict, wanted_s: float | None) -> bool:
    if wanted_s is None:
        return True
    duration = record.get("duration")
    if duration is None:
        return True
    return abs(float(duration) - wanted_s) <= SEARCH_DURATION_TOLERANCE_S


def fetch_lyrics(
    hints: dict,
    *,
    base_url: str,
    timeout_s: float = 15.0,
    client: httpx.Client | None = None,
) -> LyricsText:
    """Exact `/api/get` first, then `/api/search`. Sequential on purpose: two
    parallel requests would double the load on a free service for one track."""
    title = (hints.get("title") or "").strip()
    artist = normalize_artist(hints.get("artist") or "")
    if not title or not artist:
        raise PipelineError("lyrics_not_found", "hints lack a title/artist to search with")
    duration_ms = hints.get("duration_ms")
    duration_s = float(duration_ms) / 1000 if duration_ms else None

    owns_client = client is None
    http = client or httpx.Client(
        base_url=base_url, timeout=timeout_s, headers={"User-Agent": USER_AGENT}
    )
    try:
        record = _get_exact(http, title, artist, hints.get("album"), duration_s)
        extracted = _extract(record) if record else None
        if extracted is None:
            record = _search(http, title, artist, duration_s)
            extracted = _extract(record) if record else None
        if extracted is None:
            raise PipelineError("lyrics_not_found", f"no lyrics for {artist} - {title}")
    except httpx.HTTPError as exc:
        raise PipelineError("network", f"lrclib unreachable: {exc}") from exc
    finally:
        if owns_client:
            http.close()

    line_texts, had_synced = extracted
    logger.info(
        "lyrics for %s - %s: %d lines (%s)",
        artist,
        title,
        len(line_texts),
        "synced" if had_synced else "plain",
    )
    return LyricsText(
        line_texts=line_texts,
        full_text=" ".join(line_texts),
        source_id=int(record.get("id") or 0),
        had_synced=had_synced,
    )


def _get_exact(
    http: httpx.Client, title: str, artist: str, album: str | None, duration_s: float | None
) -> dict | None:
    params: dict[str, str | int] = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration_s:
        params["duration"] = round(duration_s)
    response = http.get("/api/get", params=params)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _search(http: httpx.Client, title: str, artist: str, duration_s: float | None) -> dict | None:
    response = http.get("/api/search", params={"track_name": title, "artist_name": artist})
    response.raise_for_status()
    candidates = [r for r in response.json() if _extract(r) is not None]
    if not candidates:
        return None
    if duration_s is None:
        return candidates[0]
    scored = [
        (abs(float(r.get("duration") or 0) - duration_s), r)
        for r in candidates
        if _duration_matches(r, duration_s)
    ]
    if not scored:
        return None
    return min(scored, key=lambda pair: pair[0])[1]
