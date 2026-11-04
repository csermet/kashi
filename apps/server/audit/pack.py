"""The summarizer's input for one song (Faz 3.5 Step 3): the same text for every arm.

One pack per song and run: the song's numbers, an unsorted line table, and
the labelled moments in a shuffled order, each with the finder's events and
the moment file's ready-made differences (``audit.render``). The model is
never asked to subtract or count milliseconds: every difference is in the
pack (the note: "arithmetic over numeric logs is weak"). The one exception
is the numeric probe (M4), which exists to measure exactly that.

Prompts and schema are constants here; their hash goes into every output, so
an output names the prompt it answers. Two requests per song: prose first,
then JSON with the prose appended (strict JSON costs reasoning).
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from audit.find import mmss

VERDICT_WORDS = ("ok", "pile", "late", "early", "missing", "wrong_text", "unsure")
SEVERITIES = (0, 1, 2, 3)
OVERLAY_OFFSET_MS = 200       # the overlay's default user offset: the screen lights start - 200
MAX_WORDS_PER_LINE = 16

RULES = """\
You audit word-synchronised song lyrics against the singing. A tool measured \
everything below; you judge it, you never compute. Times are mm:ss.s, \
differences are milliseconds, already computed: positive = the document or \
the screen is LATER than the voice.

What a viewer notices (blind listening tests): a line or word lit more than \
220 ms after or 330 ms before it is sung; a line shifted by a second or more; \
a word that never lights (0 ms) or a line with no words; the wrong text. \
Piles (several words starting within 60 ms) under ~0.35 s are real but mostly \
unseen. A voice onset is where the voice returns after silence; the ear is a \
speech model's greedy letters, phonetic and often misspelt, on its own clock \
about +50 ms.

Verdicts, one per moment: ok (nothing a viewer would notice), pile, late, \
early, missing (words that never light, or a line without words), wrong_text, \
unsure (the numbers cannot tell). Severity 0-3: 0 fine; 1 hygiene only \
(sub-0.4 s piles, tiny drifts); 2 noticeable; 3 severe (a second or more, \
wrong lines, never-lit lines)."""

PROSE_TASK = """\
For every moment of the song above, in the order given, write two to four \
sentences: which numbers decide it, then the verdict word and the severity. \
Then answer the probe: in the LINE TABLE, which line has the second-largest \
absolute lit_minus_voice_ms, and how large is that absolute value? Plain \
prose, no JSON."""

JSON_TASK = """\
Convert the assessment below into JSON following the schema exactly: one \
entry per moment id of the song above, with the verdict and severity the \
assessment gives (do not judge again), and the probe answer. Line ids are \
like L7."""

SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "moment": {"type": "string"},
                    "evidence": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICT_WORDS)},
                    "severity": {"type": "integer", "enum": list(SEVERITIES)},
                },
                "required": ["moment", "evidence", "verdict", "severity"],
                "additionalProperties": False,
            },
        },
        "probe": {
            "type": "object",
            "properties": {"line": {"type": "string"}, "abs_ms": {"type": "integer"}},
            "required": ["line", "abs_ms"],
            "additionalProperties": False,
        },
    },
    "required": ["moments", "probe"],
    "additionalProperties": False,
}

PROMPT_SHA = hashlib.sha256(json.dumps(
    [RULES, PROSE_TASK, JSON_TASK, SCHEMA], sort_keys=True).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class MomentInput:
    t_ms: int
    events: list[dict] = field(default_factory=list)   # finder events in the window
    numbers: dict | None = None                         # moment-<t>.json, one version


@dataclass(frozen=True)
class LineRow:
    line: int
    text: str
    doc_ms: int
    lit_minus_voice_ms: int
    witness: str


@dataclass(frozen=True)
class SongInput:
    id: str
    pipeline_version: str | None
    lines: int
    score: dict | None
    text_match: dict | None
    moments: list[MomentInput]
    line_table: list[LineRow]


@dataclass(frozen=True)
class Pack:
    id: str
    text: str
    moment_ids: dict[str, int]          # "M3" -> t_ms
    line_ids: dict[str, int]            # "L7" -> line index
    probe: dict | None                  # {"lines": [...ids], "abs_ms": n} or None


def probe_answer(rows: Sequence[LineRow], ids: dict[int, str]) -> dict | None:
    """The second-largest |lit_minus_voice_ms| and every line holding it."""
    if len(rows) < 2:
        return None
    sizes = sorted((abs(r.lit_minus_voice_ms) for r in rows), reverse=True)
    second = sizes[1]
    return {"lines": sorted(ids[r.line] for r in rows if abs(r.lit_minus_voice_ms) == second),
            "abs_ms": second}


def _line_text(row: dict) -> str:
    flags = [k for k in ("uncertain", "adlib", "words_derived", "no_words") if row.get(k)]
    parts = [f'"{row.get("text", "")}" starts {mmss(row["start_ms"])}, '
             f'screen lights {mmss(row["screen_lights_ms"])}']
    if row.get("nearest_voice_onset_ms") is not None:
        parts.append(f"voice returns {mmss(row['nearest_voice_onset_ms'])}: "
                     f"doc-voice {row['doc_minus_voice_ms']:+d}, "
                     f"screen-voice {row['screen_minus_voice_ms']:+d}")
    if row.get("level_db_at_start") is not None:
        parts.append(f"level {row['level_db_at_start']:.0f} dB")
    if row.get("syllables") is not None:
        parts.append(f"{row['syllables']} syllables vs {len(row.get('bumps_ms') or [])} "
                     f"loudness bumps ({row.get('bump_mapping')})")
    if flags:
        parts.append("flags " + ",".join(flags))
    words = row.get("words") or []
    if words:
        shown = ", ".join(f"{w['text']} {mmss(w['start_ms'])} {w['dur_ms']}ms"
                          + (f" [{'/'.join(w['flags'])}]" if w.get("flags") else "")
                          for w in words[:MAX_WORDS_PER_LINE])
        more = f", +{len(words) - MAX_WORDS_PER_LINE} words" if len(words) > MAX_WORDS_PER_LINE \
            else ""
        parts.append(f"words: {shown}{more}")
    if row.get("ear_letters"):
        parts.append(f'ear: "{row["ear_letters"]}"')
    return "; ".join(parts)


def build_pack(song: SongInput, rng: random.Random) -> Pack:
    """The pack's text, with moment and line ids drawn after a seeded shuffle."""
    moments = list(song.moments)
    rng.shuffle(moments)
    table = list(song.line_table)
    rng.shuffle(table)
    line_ids = {f"L{r.line}": r.line for r in song.line_table}
    by_index = {v: k for k, v in line_ids.items()}
    out = [f"SONG {song.id} (pipeline {song.pipeline_version}, {song.lines} lines)"]
    if song.score and song.score.get("placed"):
        s = song.score
        out.append(f"song numbers: {s['placed']} lines placed by the witnesses, "
                   f"{s['noticeable']} noticeably off (median error {s['median_error_ms']:+d} ms)")
    if song.text_match:
        tm = song.text_match
        out.append(f"ear-text match: {tm['matched']} of {tm['lines']} lines heard, median "
                   f"offset {tm['median_ms']} ms, {tm['off_1s']} heard > 1 s away")
    out.append("\nLINE TABLE (lit_minus_voice_ms: when the screen lights the line minus where "
               "it is sung; unsorted)")
    for r in table:
        out.append(f"{by_index[r.line]} {mmss(r.doc_ms)} lit_minus_voice_ms "
                   f"{r.lit_minus_voice_ms:+d} ({r.witness}) \"{r.text}\"")
    out.append("\nMOMENTS (each a +-2.5 s window)")
    moment_ids: dict[str, int] = {}
    for k, m in enumerate(moments, 1):
        mid = f"M{k}"
        moment_ids[mid] = m.t_ms
        events = ", ".join(f"{e['tag']}{' (' + e['detail'] + ')' if e.get('detail') else ''}"
                           f" {mmss(e['t_ms'])}" for e in m.events) or "none"
        out.append(f"{mid} at {mmss(m.t_ms)} - finder events: {events}")
        rows = (m.numbers or {}).get("lines") or []
        if not rows:
            out.append("  no moment numbers")
        for row in rows:
            out.append(f"  L{row['line']}: {_line_text(row)}")
        if m.numbers and m.numbers.get("voice_onsets_ms"):
            out.append("  voice onsets " + ", ".join(mmss(v) for v in m.numbers["voice_onsets_ms"]))
    return Pack(song.id, "\n".join(out), moment_ids, line_ids,
                probe_answer(song.line_table, by_index))


# --- loading one song from the audit's files ------------------------------------

def numbers_for(moment_file: dict, label: str | None) -> dict | None:
    """One version's lines out of a moment-<t>.json (the label, else the last)."""
    versions = moment_file.get("versions") or {}
    if not versions:
        return None
    key = label if label in versions else list(versions)[-1]
    return {"lines": versions[key]["lines"], "voice_onsets_ms": moment_file.get("voice_onsets_ms")}


def line_table(doc: dict, timings) -> list[LineRow]:
    """audit.score's line timings as screen-minus-sung rows."""
    lines = doc.get("lines", [])
    return [LineRow(t.line, lines[t.line].get("text", ""), t.doc_ms,
                    t.error_ms - OVERLAY_OFFSET_MS, t.witness) for t in timings]


def load_song(video_id: str, doc: dict, t_list: Sequence[int], label: str | None = None
              ) -> SongInput:
    """From the audit's files: runs/<id>/moments.json (finder events),
    runs/<id>/moment-<t>.json (numbers), the whole ear and the stem (score)."""
    from audit.ear import whole_path
    from audit.paths import RUNS_DIR, stem_path
    from audit.score import line_timings, song_score

    run = RUNS_DIR / video_id
    found = json.loads((run / "moments.json").read_text()) if (run / "moments.json").exists() \
        else {}
    events = [e for m in found.get("moments", []) for e in m.get("events", [])
              if label is None or e.get("version") == label]
    ear_file = whole_path(video_id)
    ear = json.loads(ear_file.read_text()) if ear_file.exists() else None
    voice = None
    if stem_path(video_id).exists():
        from audit.analysis import analyse

        voice = analyse(video_id).voice_onsets
    timings = line_timings(doc, ear, voice)
    moments = []
    for t in t_list:
        path = run / f"moment-{t}.json"
        numbers = numbers_for(json.loads(path.read_text()), label) if path.exists() else None
        near = [e for e in events if abs(e["t_ms"] - t) <= 2500]
        moments.append(MomentInput(t, near, numbers))
    return SongInput(video_id, doc.get("pipeline_version"), len(doc.get("lines", [])),
                     song_score(timings, len(doc.get("lines", []))) if timings else None,
                     found.get("text_match"), moments, line_table(doc, timings))
