"""Fetch the lyrics TEXT the aligner needs (server side).

This is not the overlay's lrclib client and never becomes one: the server does
not proxy lyrics to clients (plan R-5). It fetches the words once, aligns them
against the audio, and only the derived timings ever leave the pipeline.

The `syncedLyrics` variant is preferred — its line breaks are exactly what a
line-mode client displays, so word-level output regroups into the same lines.
Every time in the OUTPUT still comes from the aligner; the lrclib timestamps
are kept only as a QA reference (`synced_starts_ms`) so line_qa can catch
sections where the aligner lost lock (drifted far from where the line is
actually sung).
"""

import logging
import re
import unicodedata
from dataclasses import dataclass

import httpx

from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.version import PIPELINE_VERSION

logger = logging.getLogger(__name__)

USER_AGENT = f"kashi-server/{PIPELINE_VERSION} (+https://github.com/csermet/kashi)"
SEARCH_DURATION_TOLERANCE_S = 3
_TIMESTAMP = re.compile(r"^\[(\d{2}):(\d{2})[.:](\d{2,3})\]\s*")
_TOPIC_SUFFIX = re.compile(r"\s*-\s*Topic$", re.IGNORECASE)
_WORD_TOKEN = re.compile(r"\w+")


@dataclass(frozen=True)
class LyricsText:
    line_texts: list[str]
    full_text: str
    source_id: int
    had_synced: bool
    # Parallel to line_texts when the lyrics came from syncedLyrics: the [mm:ss.xx]
    # start of each line (None for a rare stampless line). None entirely for plain
    # lyrics. QA reference only — never copied into the output document.
    synced_starts_ms: list[int | None] | None = None
    # Document provenance: "lrclib" (an lrclib record) or "caller"
    # (ingest options.lyrics_text) — the document must never claim lrclib
    # served text it did not (reviewer, Faz 4). The worker rewrites it to
    # "lyricsfile" when the fast path consumes human word sync (Faz 5 P3).
    source: str = "lrclib"
    # Raw Lyricsfile YAML from the chosen record (null on most records today).
    # Carried OPAQUE — pipeline/lyricsfile.py parses it lazily on the worker,
    # never here (a broken lyricsfile must not break the lyrics fetch).
    lyricsfile_raw: str | None = None


def normalize_artist(artist: str) -> str:
    """YouTube's auto-generated channels append " - Topic" (plan R-2)."""
    return _TOPIC_SUFFIX.sub("", artist).strip()


# YTM joins collaborators with a LOCALE conjunction (" ve " on Turkish UIs,
# " & "/" and " elsewhere) plus commas and feat./ft./x credits. lrclib records
# credit "blueberry, PiNKII" or just the primary — the joined string matches
# nothing structurally (field failure class: Drift Barbie, Señorita, The
# Storm... most of the no-lyrics backlog).
_ARTIST_SEPARATORS = re.compile(r"\s+(?:ve|and|x|feat\.?|ft\.?|&)\s+|\s*,\s*", re.IGNORECASE)


def split_artists(artist: str) -> list[str]:
    """Individual artists from a multi-artist hint, primary first; [] when
    the hint names a single artist (callers gate the retry rung on that)."""
    parts = [p.strip() for p in _ARTIST_SEPARATORS.split(artist) if p and p.strip()]
    return parts if len(parts) > 1 else []


# lrclib 400/422 = the request itself is bad (field: a 61-minute mix's
# duration=3679 earned a 400 — and three identical retries). Permanent: the
# same request cannot succeed later. 429 is the free service asking for
# patience — transient under its own type so logs/metrics show the pressure.
# 403 stays TRANSIENT on purpose: reads need no auth, so a 403 is edge/WAF
# weather that would otherwise stamp whole batches lyrics_not_found behind
# the 7-day block (reviewer catch). 5xx/timeouts/DNS stay plain network.
_PERMANENT_HTTP_STATUS = frozenset({400, 422})


def _pipeline_error(exc: httpx.HTTPError, context: str) -> PipelineError:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _PERMANENT_HTTP_STATUS:
            return PipelineError(
                "lyrics_not_found", f"lrclib rejected the request ({status}): {context}"
            )
        if status == 429:
            return PipelineError("rate_limited", f"lrclib rate-limited: {context}")
    return PipelineError("network", f"lrclib unreachable: {exc}")


def _parse_synced(synced: str) -> list[tuple[int | None, str]]:
    """One (start_ms, text) entry per non-empty lyric line.

    Strips EVERY leading [mm:ss.xx] stamp (multi-stamp LRC lines repeat one text
    at several times; the first stamp wins, none may leak into the text). Empty
    lines drop time and text together, so the result stays parallel to the
    line_texts the aligner sees.
    """
    entries: list[tuple[int | None, str]] = []
    for raw in synced.splitlines():
        rest = raw
        start_ms: int | None = None
        while (match := _TIMESTAMP.match(rest)) is not None:
            if start_ms is None:
                mm, ss, frac = match.groups()
                frac_ms = int(frac) if len(frac) == 3 else int(frac) * 10
                start_ms = int(mm) * 60_000 + int(ss) * 1_000 + frac_ms
            rest = rest[match.end() :]
        text = rest.strip()
        if text:
            entries.append((start_ms, text))
    return entries


def _lines_from_plain(plain: str) -> list[str]:
    return [line.strip() for line in plain.splitlines() if line.strip()]


def _extract(record: dict) -> tuple[list[str], list[int | None] | None, bool] | None:
    if record.get("instrumental"):
        return None
    synced = record.get("syncedLyrics")
    if synced:
        entries = _parse_synced(synced)
        if entries:
            return [text for _, text in entries], [start for start, _ in entries], True
    plain = record.get("plainLyrics")
    if plain:
        lines = _lines_from_plain(plain)
        if lines:
            return lines, None, False
    return None


def _duration_matches(record: dict, wanted_s: float | None) -> bool:
    if wanted_s is None:
        return True
    duration = record.get("duration")
    if duration is None:
        return True
    return abs(float(duration) - wanted_s) <= SEARCH_DURATION_TOLERANCE_S


# Tokens that carry no identity: shared ones must not satisfy a plausibility
# overlap on their own ("Come On Now" vs "Come On Eileen" share come/on).
_STOPWORDS = frozenset(
    "the a an and of to in on at for with feat ft x remix version edit "
    "official video audio lyrics ve ile".split()
)


def _tokens(text: str) -> set[str]:
    # casefold + NFKD + combining-mark drop: Turkish dotted I ("İstanbul")
    # casefolds to "i" + combining dot; without stripping it, "İstanbul" and
    # "Istanbul" share zero tokens (field failure class — retro finding).
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return set(_WORD_TOKEN.findall(folded))


def _significant(tokens: set[str]) -> set[str]:
    sig = tokens - _STOPWORDS
    return sig or tokens  # an all-stopword title still needs SOME signal


def title_covers(record_title: str, query_title: str) -> bool:
    """Every significant token of the QUERY title appears in the record's
    title. The overlap rule is too weak for nightcore detection (the artist
    axis is a channel name there): "Come On Now" vs "Come On Eileen" share
    come/on — containment requires "now" too and rejects it. Identical-title
    strangers still pass; the worker's wrong-song prob gate owns those."""
    return _significant(_tokens(query_title)) <= _tokens(record_title)


def has_usable_lyrics(record: dict) -> bool:
    """THE definition of a lyrics-bearing record (same parse the pipeline
    uses) — truthiness lookalikes drift (a syncedLyrics of "\n\n" is truthy
    but parses to nothing; reviewer finding)."""
    return _extract(record) is not None


def plausible_match(record: dict, title: str, artist: str, *, require_artist: bool = True) -> bool:
    """Free-text `q=` results are loose, and a wrong record with a matching
    duration is invisible to line QA on the windowed path — so every q=-fed
    consumer requires the candidate to still look like the requested track.

    The fallback rung keeps BOTH axes (its hints carry the real artist).
    Nightcore detection sets require_artist=False: uploads live on channel
    "artists" ("Syrex") that can never token-match the original artist —
    there, title overlap + the duration-ratio band carry the signal."""
    if not _significant(_tokens(record.get("trackName") or "")) & _significant(_tokens(title)):
        return False
    if not require_artist:
        return True
    return bool(
        _significant(_tokens(record.get("artistName") or "")) & _significant(_tokens(artist))
    )


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
        rung = "get"
        record = _get_exact(http, title, artist, hints.get("album"), duration_s)
        extracted = _extract(record) if record else None
        if extracted is None:
            rung = "search"
            record = _search(http, title, artist, duration_s)
            extracted = _extract(record) if record else None
        if extracted is None:
            rung = "search-q"
            record = _search_freetext(http, title, artist, duration_s)
            extracted = _extract(record) if record else None
        if extracted is None and (parts := split_artists(artist)):
            # Multi-artist retry: the joined hint matched nothing — try the
            # primary artist alone. Plausibility accepts ANY credited part so
            # a record naming only the featured artist still qualifies; the
            # structured rung gets the same gate (a split-derived short token
            # like "Tyler" is the loosest query in the ladder — reviewer). Two
            # extra sequential requests at most (etiquette budget: ≤5 total).
            rung = "search-primary"
            record = _search(http, title, parts[0], duration_s, plausible_artists=parts)
            extracted = _extract(record) if record else None
            if extracted is None:
                rung = "search-q-primary"
                record = _search_freetext(
                    http, title, parts[0], duration_s, plausible_artists=parts
                )
                extracted = _extract(record) if record else None
        if extracted is None:
            raise PipelineError("lyrics_not_found", f"no lyrics for {artist} - {title}")
    except httpx.HTTPError as exc:
        raise _pipeline_error(exc, f"{artist} - {title}") from exc
    finally:
        if owns_client:
            http.close()

    assert record is not None  # extracted != None implies a record
    line_texts, synced_starts_ms, had_synced = extracted
    logger.info(
        "lyrics for %s - %s: %d lines (%s, via %s)",
        artist,
        title,
        len(line_texts),
        "synced" if had_synced else "plain",
        rung,
    )
    return LyricsText(
        line_texts=line_texts,
        full_text=" ".join(line_texts),
        source_id=int(record.get("id") or 0),
        had_synced=had_synced,
        synced_starts_ms=synced_starts_ms,
        lyricsfile_raw=_lyricsfile_raw(record),
    )


def _lyricsfile_raw(record: dict) -> str | None:
    raw = record.get("lyricsfile")
    return raw if isinstance(raw, str) and raw.strip() else None


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


# Cheap word-level Lyricsfile probe for record SELECTION only (a full YAML
# parse per candidate would be waste; the worker's real parse still guards
# correctness — a probe hit that later fails to parse just falls back).
_LYRICSFILE_WORDS_PROBE = re.compile(r"^\s+words:", re.MULTILINE)


def _has_lyricsfile_words(record: dict) -> bool:
    raw = record.get("lyricsfile")
    return isinstance(raw, str) and bool(_LYRICSFILE_WORDS_PROBE.search(raw))


def choose_record(
    records: list[dict],
    *,
    duration_s: float | None,
    tolerance_s: float = SEARCH_DURATION_TOLERANCE_S,
) -> dict | None:
    """THE record-selection policy (Faz 5 P3 — one policy instead of three).

    Usable lyrics required (the pipeline's own parse, not truthiness);
    records whose lyrics PARSE as synced outrank plain — a synced pick
    doubles as the QA/windowing reference; WITHIN the synced class, a
    record carrying word-level Lyricsfile data (HUMAN word sync) wins.
    The lyricsfile probe deliberately ranks BELOW synced, not above it: a
    probe hit that later fails the real parse falls back to CTC, and that
    fallback must not have traded away its QA reference for the probe
    (reviewer catch). Duration proximity breaks remaining ties.
    duration_s=None skips the duration axis entirely (callers that
    pre-filtered their own band, e.g. the nightcore r-cluster vote); a
    record without a duration field is never excluded, it just sorts last
    on the distance axis.
    """
    scored: list[tuple[bool, bool, float, int, dict]] = []
    for rec in records:
        extracted = _extract(rec)
        if extracted is None:
            continue
        if duration_s is None:
            distance = 0.0
        else:
            rec_duration = rec.get("duration")
            if rec_duration is not None and abs(float(rec_duration) - duration_s) > tolerance_s:
                continue
            distance = abs(float(rec_duration or 0) - duration_s)
        had_synced = extracted[2]
        scored.append(
            (not had_synced, not _has_lyricsfile_words(rec), distance, len(scored), rec)
        )
    if not scored:
        return None
    return min(scored, key=lambda item: item[:4])[4]


def _search(
    http: httpx.Client,
    title: str,
    artist: str,
    duration_s: float | None,
    *,
    plausible_artists: list[str] | None = None,
) -> dict | None:
    response = http.get("/api/search", params={"track_name": title, "artist_name": artist})
    response.raise_for_status()
    records = response.json()
    if plausible_artists:  # split-retry rung: gate the loose primary query
        records = [
            r for r in records if any(plausible_match(r, title, a) for a in plausible_artists)
        ]
    return choose_record(records, duration_s=duration_s)


def search_candidates(
    query: str,
    *,
    base_url: str,
    timeout_s: float = 15.0,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Raw free-text /api/search results — the nightcore-detection seam.

    The caller picks a record and turns it into lyrics with
    `lyrics_from_record`, so the chosen record never costs a second request
    (etiquette). Network errors surface as transient PipelineErrors like the
    main fetch path.
    """
    owns_client = client is None
    http = client or httpx.Client(
        base_url=base_url, timeout=timeout_s, headers={"User-Agent": USER_AGENT}
    )
    try:
        response = http.get("/api/search", params={"q": query})
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        raise _pipeline_error(exc, query) from exc
    finally:
        if owns_client:
            http.close()
    return data if isinstance(data, list) else []


def lyrics_from_record(record: dict) -> LyricsText:
    """LyricsText from an already-fetched lrclib record (no extra request)."""
    extracted = _extract(record)
    if extracted is None:
        raise PipelineError("lyrics_not_found", "lrclib record carries no usable lyrics")
    line_texts, synced_starts_ms, had_synced = extracted
    return LyricsText(
        line_texts=line_texts,
        full_text=" ".join(line_texts),
        source_id=int(record.get("id") or 0),
        had_synced=had_synced,
        synced_starts_ms=synced_starts_ms,
        lyricsfile_raw=_lyricsfile_raw(record),
    )


def lyrics_from_text(text: str) -> LyricsText:
    """Caller-supplied plain lyrics (ingest options.lyrics_text). No stamps →
    whole-audio alignment path; line QA has no references and skips."""
    lines = _lines_from_plain(text)
    if not lines:
        raise PipelineError("lyrics_not_found", "lyrics_text is empty")
    return LyricsText(
        line_texts=lines,
        full_text=" ".join(lines),
        source_id=0,
        had_synced=False,
        source="caller",
    )


def _search_freetext(
    http: httpx.Client,
    title: str,
    artist: str,
    duration_s: float | None,
    *,
    plausible_artists: list[str] | None = None,
) -> dict | None:
    """Free-text rung (pipeline 2.0.3): structured search misses remixes and
    songs with extra credits — the hint title/artist don't literally match the
    record ("Wet" / "Snoop Dogg" vs "Wet (… vs. David Guetta) [Remix]"). One
    free-text pass catches those; `plausible_match` keeps the loose query
    honest. `plausible_artists` widens ONLY the plausibility axis (multi-artist
    retry: the record may credit any collaborator), never the query."""
    response = http.get("/api/search", params={"q": f"{artist} {title}"})
    response.raise_for_status()
    artists = plausible_artists or [artist]
    candidates = [
        r for r in response.json() if any(plausible_match(r, title, a) for a in artists)
    ]
    picked = choose_record(candidates, duration_s=duration_s)
    if picked is None and duration_s is not None:
        # Duration-less last chance (client precedent: its structured lookup
        # retries without duration and finds what we used to miss — the
        # Mor/Gasolina field failures). Plausibility already filtered the
        # pool, so the loose pick still looks like the requested track.
        picked = choose_record(candidates, duration_s=None)
    return picked
