"""Parse lrclib's `lyricsfile` field (HUMAN word sync) into an AlignResult.

Spec vendored at docs/vendor/lrclib-LYRICSFILE_*.md (upstream 3f68e7cda086,
2026-06-24). The field is served on every record (null when absent) and, when
it carries word-level lines, it is strictly better than anything CTC can
derive — human-timed word boundaries are the single biggest lever on the
"words flow fast/slow" ear-test complaint.

Philosophy: a job must NEVER fail over a lyricsfile. The YAML is untrusted
crowd data behind an "expect breaking changes" upstream warning — every
problem here returns None and the caller falls back to the proven
syncedLyrics/CTC path. Pure module: no I/O, unit-tested against golden
fixtures (tests/fixtures/lyricsfile/).
"""

import logging

import yaml

from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming

logger = logging.getLogger(__name__)

# Untrusted YAML guards: size before parse (a 100 MB blob must not reach the
# loader), safe_load always (no object construction).
MAX_LYRICSFILE_BYTES = 256 * 1024
# The last stamp may run slightly past the downloaded audio (edits, padding);
# beyond this slack the file is timed to a DIFFERENT edit — unusable.
DURATION_SLACK_S = 5.0


def _ms(value) -> int | None:
    """Integer milliseconds per spec; bool is an int in Python — reject it."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _texts_match(joined: str, line_text: str) -> bool:
    """Spec: sequential word texts 'approximate' the line text (trailing
    spaces on all but the last word). Whitespace-normalized equality — CJK
    lines carry no separators, so plain concatenation is compared too."""
    norm = " ".join(joined.split())
    want = " ".join(line_text.split())
    return norm == want or joined.replace(" ", "") == line_text.replace(" ", "")


def alignresult_from_lyricsfile(raw: str | None, duration_s: float) -> AlignResult | None:
    """AlignResult (sync=word, quality 1.0) from a record's lyricsfile, or
    None for ANY reason to fall back: absent/oversized/broken YAML, version
    major != 1, instrumental, no word-level content, non-monotonic times, or
    stamps timed to a different edit than the downloaded audio."""
    if not raw or not isinstance(raw, str):
        return None
    if len(raw.encode("utf-8", errors="ignore")) > MAX_LYRICSFILE_BYTES:
        logger.info("lyricsfile rejected: oversized (%d bytes)", len(raw))
        return None
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.info("lyricsfile rejected: yaml error (%s)", str(exc)[:120])
        return None
    if not isinstance(doc, dict):
        return None
    version = doc.get("version")
    if not isinstance(version, str) or version.split(".")[0] != "1":
        logger.info("lyricsfile rejected: unsupported version %r", version)
        return None
    metadata = doc.get("metadata") or {}
    if not isinstance(metadata, dict) or metadata.get("instrumental") is True:
        return None
    offset = metadata.get("offset_ms")
    offset_ms = offset if isinstance(offset, int) and not isinstance(offset, bool) else 0
    # Two-sided edit gate (reviewer): the file DECLARES its edit's length —
    # a lyricsfile timed to a radio edit must not sweep over the extended
    # mix (the one-sided last-stamp gate below only catches LONGER edits;
    # legitimate songs end their lyrics well before an instrumental outro,
    # so the last stamp cannot be checked two-sided).
    declared = metadata.get("duration_ms")
    if (
        isinstance(declared, int)
        and not isinstance(declared, bool)
        and declared > 0
        and duration_s > 0
        and abs(declared / 1000 - duration_s) > DURATION_SLACK_S
    ):
        logger.info(
            "lyricsfile rejected: declares %dms but the audio is %.0fs "
            "(timed to a different edit)",
            declared,
            duration_s,
        )
        return None
    entries = doc.get("lines")
    if not isinstance(entries, list) or not entries:
        return None

    starts: list[int] = []
    ends: list[int | None] = []
    texts: list[str] = []
    raw_words: list[list[tuple[str, int, int | None]] | None] = []
    prev_start = 0
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        text = entry.get("text")
        start = _ms(entry.get("start_ms"))
        if not isinstance(text, str) or not text.strip() or start is None:
            return None
        start = max(0, start + offset_ms)
        if start < prev_start:  # spec: monotonically increasing
            logger.info("lyricsfile rejected: non-monotonic line starts")
            return None
        prev_start = start
        end = _ms(entry.get("end_ms"))
        starts.append(start)
        ends.append(max(start, end + offset_ms) if end is not None else None)
        texts.append(text.strip())
        raw_words.append(_parse_words(entry.get("words"), text.strip(), start, offset_ms))

    if not any(raw_words):
        # Line-only lyricsfile adds nothing over syncedLyrics — out of scope
        # (Faz 5 discipline); the caller's normal path handles those.
        return None

    # Resolve open LINE ends first: next line's start, else the line's last
    # explicit word end, else zero width (display-hold semantics).
    lines: list[LineTiming] = []
    for i, start in enumerate(starts):
        end = ends[i]
        if end is None:
            nxt = starts[i + 1] if i + 1 < len(starts) else None
            word_ends = [e for _, _, e in (raw_words[i] or []) if e is not None]
            candidates = [v for v in (nxt, max(word_ends, default=None)) if v is not None]
            end = max(candidates) if candidates else start
        lines.append(LineTiming(start_ms=start, end_ms=max(start, end), text=texts[i], score=1.0))

    # Then WORD ends: next word's start, else the (now resolved) line end;
    # explicit ends are clamped to the next LINE's start — garbage data must
    # not sweep across a line boundary.
    words_per_line: list[list[AlignedWord]] = []
    for i, parsed in enumerate(raw_words):
        if not parsed:
            words_per_line.append([])
            continue
        boundary = starts[i + 1] if i + 1 < len(starts) else None
        chunk: list[AlignedWord] = []
        for k, (text, start, end) in enumerate(parsed):
            if end is None:
                end = parsed[k + 1][1] if k + 1 < len(parsed) else lines[i].end_ms
            if boundary is not None:
                end = min(end, boundary)
            chunk.append(
                AlignedWord(start_ms=start, end_ms=max(start, end), text=text, prob=1.0)
            )
        words_per_line.append(chunk)

    last_ms = max(
        max((w.end_ms for chunk in words_per_line for w in chunk), default=0),
        max(line.end_ms for line in lines),
    )
    if duration_s > 0 and last_ms > (duration_s + DURATION_SLACK_S) * 1000:
        logger.info(
            "lyricsfile rejected: stamps run to %dms but the audio is %.0fs "
            "(timed to a different edit)",
            last_ms,
            duration_s,
        )
        return None

    return AlignResult(
        sync="word",
        lines=lines,
        words_per_line=words_per_line,
        quality_score=1.0,  # no aligner uncertainty to model — human data
        windowed=False,
    )


def _parse_words(
    raw_words, line_text: str, line_start: int, offset_ms: int
) -> list[tuple[str, int, int | None]] | None:
    """Validated (stripped_text, start, end|None) tuples for one line, or
    None when the line has no usable word data (the line stays, wordless —
    mixed documents are a supported shape). End resolution happens in the
    caller once line ends are known."""
    if not isinstance(raw_words, list) or not raw_words:
        return None
    parsed: list[tuple[str, int, int | None]] = []
    for w in raw_words:
        if not isinstance(w, dict):
            return None
        text = w.get("text")
        start = _ms(w.get("start_ms"))
        if not isinstance(text, str) or not text or start is None:
            return None
        end = _ms(w.get("end_ms"))
        shifted_end = end + offset_ms if end is not None else None
        parsed.append((text, max(0, start + offset_ms), shifted_end))

    if not _texts_match("".join(t for t, _, _ in parsed), line_text):
        # The words don't spell the display text — keep the line, drop the
        # word data (never render a sweep over mismatched spans).
        return None

    out: list[tuple[str, int, int | None]] = []
    prev = line_start
    for text, start, end in parsed:
        if start < prev:  # non-monotonic word starts: drop the line's words
            return None
        prev = start
        stripped = text.strip()
        if not stripped:
            return None
        out.append((stripped, start, end))
    return out
