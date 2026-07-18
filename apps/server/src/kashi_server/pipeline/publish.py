"""lrclib contribute-back (Faz 5 P6): quality gate, PoW, publish call.

The whole feature ships default-OFF (settings.lrclib_publish_enabled) and
every publish is operator-approved — nothing here runs automatically. The
gate is deliberately conservative: publishing wrong or synthetic data to a
free community service is the one mistake this project must never make.

PoW: lrclib's POST /api/request-challenge returns {prefix, target}; the
token is "{prefix}:{nonce}" where sha256(prefix + nonce) must compare <=
target byte-wise (the lrcget reference implementation's verify_nonce).
Challenges expire in ~5 minutes — solve IMMEDIATELY before publishing,
never ahead of time.
"""

import hashlib
import logging

import httpx
import yaml

from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.version import PIPELINE_VERSION

logger = logging.getLogger(__name__)

USER_AGENT = f"kashi-server/{PIPELINE_VERSION} (+https://github.com/csermet/kashi)"
# Publishing floor mirrors the client word-mode gate; below it we would be
# contributing timings we ourselves refuse to render as word karaoke.
PUBLISH_QUALITY_FLOOR = 0.5
MAX_POW_ATTEMPTS = 50_000_000  # minutes-of-CPU ceiling; the worker owns it


# Stable reason codes for the coded gate — a FIXED enum (metric label safety:
# kashi_lrclib_publish_gate_total{reason} must stay low-cardinality).
GATE_REASON_CODES = (
    "lyrics_source",
    "not_word_sync",
    "nightcore_clock",
    "quality_floor",
    "qa_missing",
    "qa_flagged",
    "qa_density_dropped",
    "no_measured_words",
    "track_metadata",
)


def publish_gate_coded(doc: dict) -> list[tuple[str, str]]:
    """(code, reason) pairs this document must NOT be published (empty = eligible).

    Every rule reads document provenance (alignment + alignment.qa — the
    Faz 5 P1 block), never live state:
    - lyrics_source must be "lrclib": caller text is a copyright question,
      and "lyricsfile" documents ARE human data — republishing them adds
      noise, not signal.
    - word sync only; a nightcore clock (speed_factor != 1) must never be
      written onto the original track's record.
    - line QA must not have repaired anything (flagged/density drops mean
      the aligner lost lock somewhere — not contribution material).

    Codes come from GATE_REASON_CODES; messages stay human-readable.
    """
    reasons: list[tuple[str, str]] = []
    alignment = doc.get("alignment") or {}
    if alignment.get("lyrics_source") != "lrclib":
        reasons.append(
            ("lyrics_source", f"lyrics_source is {alignment.get('lyrics_source')!r}, not 'lrclib'")
        )
    if doc.get("sync") != "word":
        reasons.append(("not_word_sync", "document is not word-sync"))
    if alignment.get("speed_factor", 1.0) != 1.0:
        reasons.append(
            ("nightcore_clock", "nightcore clock (speed_factor != 1) cannot be published")
        )
    quality = alignment.get("quality_score")
    if not isinstance(quality, (int, float)) or quality < PUBLISH_QUALITY_FLOOR:
        reasons.append(
            (
                "quality_floor",
                f"quality {quality!r} under the {PUBLISH_QUALITY_FLOOR} publish floor",
            )
        )
    qa = alignment.get("qa")
    if not isinstance(qa, dict):
        reasons.append(
            ("qa_missing", "no alignment.qa provenance (pre-2.3.0 document — reprocess first)")
        )
    else:
        if qa.get("flagged", 1) != 0:
            reasons.append(("qa_flagged", "line QA snapped lines (aligner lost lock)"))
        if qa.get("density_dropped", 1) != 0:
            reasons.append(("qa_density_dropped", "line QA dropped damaged word runs"))
    if not any(
        line.get("words") and not line.get("words_derived") for line in doc.get("lines") or []
    ):
        # All word content synthetic/absent: the generated lyricsfile would
        # carry no measured words — noise, not signal (reviewer catch).
        reasons.append(
            ("no_measured_words", "no measured word lines (only rederived/wordless content)")
        )
    track = doc.get("track") or {}
    if not track.get("title") or not track.get("artist") or not track.get("duration_ms"):
        reasons.append(("track_metadata", "track metadata incomplete"))
    return reasons


def publish_gate(doc: dict) -> list[str]:
    """Human-readable reasons this document must NOT be published (empty =
    eligible). Thin view over publish_gate_coded — API responses keep their
    message-only contract."""
    return [message for _, message in publish_gate_coded(doc)]


def generate_lyricsfile(doc: dict) -> str:
    """Document → Lyricsfile 1.0 YAML (docs/vendor spec).

    words_derived lines are written WITHOUT words: their boundaries are
    synthetic presentation data (rederive) and must not masquerade as
    measured timings. The trailing-space rule applies to every word but a
    line's last.
    """
    track = doc.get("track") or {}
    lines_out: list[dict] = []
    for line in doc.get("lines") or []:
        entry: dict = {"text": line["text"], "start_ms": line["start_ms"]}
        if isinstance(line.get("end_ms"), int):
            entry["end_ms"] = line["end_ms"]
        words = line.get("words")
        if words and not line.get("words_derived"):
            entry["words"] = [
                {
                    "text": word["text"] + ("" if index == len(words) - 1 else " "),
                    "start_ms": word["start_ms"],
                    "end_ms": word["end_ms"],
                }
                for index, word in enumerate(words)
            ]
        lines_out.append(entry)

    payload: dict = {
        "version": "1.0",
        "metadata": {
            "title": track.get("title") or "",
            "artist": track.get("artist") or "",
            **({"album": track["album"]} if track.get("album") else {}),
            "duration_ms": track.get("duration_ms") or 0,
            "offset_ms": 0,
        },
        "lines": lines_out,
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=1000)


def derive_legacy_fields(doc: dict) -> tuple[str, str]:
    """(plainLyrics, syncedLyrics) from the document — lrclib derives them
    from the lyricsfile server-side, but sending honest values keeps the
    request self-contained for older instances."""
    lines = doc.get("lines") or []
    plain = "\n".join(line["text"] for line in lines)
    synced_parts = []
    for line in lines:
        ms = int(line["start_ms"])
        minutes, rest = divmod(ms, 60_000)
        seconds, centis = divmod(rest, 1000)
        synced_parts.append(f"[{minutes:02d}:{seconds:02d}.{centis // 10:02d}] {line['text']}")
    return plain, "\n".join(synced_parts)


def _nonce_ok(digest: bytes, target: bytes) -> bool:
    """lrcget's verify_nonce: byte-wise digest <= target."""
    if len(digest) != len(target):
        return False
    for d, t in zip(digest, target, strict=True):
        if d > t:
            return False
        if d < t:
            return True
    return True


def solve_challenge(
    prefix: str,
    target_hex: str,
    *,
    max_attempts: int = MAX_POW_ATTEMPTS,
    should_stop: object = None,
) -> str:
    try:
        target = bytes.fromhex(target_hex)
    except ValueError as exc:
        raise PipelineError("other", f"lrclib challenge target is not hex: {target_hex!r}") from exc
    nonce = 0
    while nonce < max_attempts:
        if callable(should_stop) and nonce % 100_000 == 0 and should_stop():
            # Graceful-shutdown hook: minutes of sha256 must not block SIGTERM
            # (the request stays queued and drains on the next boot).
            raise PipelineError("other", "PoW interrupted by shutdown")
        digest = hashlib.sha256(f"{prefix}{nonce}".encode()).digest()
        if _nonce_ok(digest, target):
            return str(nonce)
        nonce += 1
    raise PipelineError("other", f"PoW unsolved after {max_attempts} attempts")


def publish_document(
    doc: dict,
    *,
    base_url: str,
    timeout_s: float = 30.0,
    client: httpx.Client | None = None,
    should_stop: object = None,
) -> None:
    """Challenge → PoW → POST /api/publish. Raises PipelineError on any
    failure; success returns silently (lrclib answers 201 with no body of
    interest). Etiquette: one sequential pass, meaningful User-Agent, and
    the caller guarantees operator approval + the quality gate."""
    track = doc.get("track") or {}
    plain, synced = derive_legacy_fields(doc)
    body = {
        "trackName": track.get("title") or "",
        "artistName": track.get("artist") or "",
        "albumName": track.get("album") or "",
        # Hint-sourced (YTM player) duration — timings live on the download
        # clock; same video keeps the delta sub-second, inside lrclib matching.
        "duration": round((track.get("duration_ms") or 0) / 1000),
        "plainLyrics": plain,
        "syncedLyrics": synced,
        "lyricsfile": generate_lyricsfile(doc),
    }
    owns_client = client is None
    http = client or httpx.Client(
        base_url=base_url, timeout=timeout_s, headers={"User-Agent": USER_AGENT}
    )
    try:
        challenge = http.post("/api/request-challenge")
        challenge.raise_for_status()
        data = challenge.json()
        prefix, target = data.get("prefix"), data.get("target")
        if not isinstance(prefix, str) or not isinstance(target, str):
            raise PipelineError("other", f"malformed lrclib challenge: {data!r}")
        logger.info("solving lrclib publish challenge (target %s…)", target[:12])
        nonce = solve_challenge(prefix, target, should_stop=should_stop)
        response = http.post(
            "/api/publish", json=body, headers={"X-Publish-Token": f"{prefix}:{nonce}"}
        )
        response.raise_for_status()
        logger.info(
            "published to lrclib: %s - %s (%d lines)",
            body["artistName"],
            body["trackName"],
            len(doc.get("lines") or []),
        )
    except httpx.HTTPError as exc:
        raise PipelineError("network", f"lrclib publish failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()
