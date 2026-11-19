"""What a preview measures between a stored document and the one the current code builds, and the
verdict it draws (worker/preview.py). Pure: documents and the stem's level envelope in, numbers out.

The verdict takes the archive rig's per-song vetoes as they were written before the 2.34.0 and
2.35.0 runs (outside the repository: the timing release's gates and the 2.35.0 archive appendix),
not tuned here:

- W1 a new pile: a run of >= 4 consecutive words of one line, each starting < 60 ms after the one
  before it, that no run of the stored line contains;
- W2 away from the voice: a line start that moves more than 1 500 ms farther from where the voice
  returns (below);
- W3 lost words: a line with text that had word timings and has none (a build with
  `letterless_lines_wordless` on, by default or by the preview's `--set`, never gives a line with
  no letter and no digit, a '♪', to the aligner: W3 and W2 below then judge no such line, whose
  move is still listed; `alignment.sung_text`);
- W4 rushed first words, counted: the built document has MORE lines of >= 2 words whose first word
  lights < 120 ms before its second word than the stored one (each document counted on its own, as
  registered: a line that gains its word timings counts on the built side only, so a line-to-word
  upgrade can be kept; the row says how many of the built side's lines had no word timings);
- W5 order: the built document has MORE words starting before the word before them (document
  order, across lines) than the stored one.

W4 was first written for the lines one rule moves (a moved line whose first word lost its dwell);
over a jump of several releases hundreds of lines move a little, and one new flash kept songs whose
build removes several, so it counts, as the owner's priority reads: fewer rushed moments
(2026-11-19, adopted with his blind clips; docs/research/refresh-from-evidence-2026-11.md).

W2's witness is the stem's level envelope (pipeline/level.py, kept in the evidence): the voice
returns where a frame reaches the audit voice witness's silence line after at least its shortest
silence (alignment.FIRST_HEARD_SILENCE_DB, FIRST_HEARD_MIN_SILENCE_MS, the constants the 2.35.0 rule
reads), and a line is placed on that return exactly as the audit voice witness places it
(audit.score.line_timings: after >= LINE_GAP_MS of document silence, the voice's return within
LINE_VOICE_MAX_MS of its first word). The rig read the same line off another contour of the same
stem (librosa RMS over 128 ms windows, 16 ms apart); this one reads 20 ms frames. Where the 2.35.0
rule moved a word, it moved it out of silence on this same envelope, so this witness cannot argue
with that rule's own moves; it watches every other move (carries, sheet shifts, regroups).

W2 judges only the lines the STORED document lets the witness place: the reference taken on the new
document lies within LINE_VOICE_MAX_MS of the new start, so it can never put the new start more
than VETO_AWAY_MS farther away; the first line, and every line inside continuous singing, are never
placed. A line start that moves more than VETO_AWAY_MS without such a reference is therefore
`review`: neither vetoed nor refreshed, listed for a person.

A sheet that changed (another lyrics record, source or clock, another line count, any line's text)
is reported and never judged by these measures: they pair lines by index. A document that predates
`alignment.speed_factor` reads as 1.0 (what every document without it was aligned on), and a line
whose stored text, with its inline time tags stripped (pipeline 2.34.0), equals the new text is the
same line.

Every list in the output carries line indexes and times only, never lyric text.
"""

from __future__ import annotations

import bisect
from typing import Any

from kashi_server.pipeline.alignment import (
    FIRST_HEARD_MIN_SILENCE_MS,
    FIRST_HEARD_SILENCE_DB,
    sung_text,
)
from kashi_server.pipeline.level import Envelope
from kashi_server.pipeline.lrclib import strip_inline_tags

#: W1: words of one line form a run while each starts less than this after the one before it.
RUN_STEP_MS = 60
#: W1: a new run of at least this many words vetoes the song.
VETO_RUN_WORDS = 4
#: W2: a line start this much farther from the voice's return (strictly more) vetoes the song.
VETO_AWAY_MS = 1500
#: W4 and the counts: a word whose successor in its line starts less than this after it has no
#: visible dwell (W4 counts the lines whose FIRST word has none).
SHORT_DWELL_MS = 120
#: The counts: ... and less than this, a word that only flashes.
FLASH_DWELL_MS = 60
#: A line start that moved more than this (strictly) is a moved line; the counts also report
#: moves past the other two.
MOVED_MS = 100
MOVED_FAR_MS = (500, 1500)
#: The voice witness (audit.finder.LINE_GAP_MS / LINE_VOICE_MAX_MS, copies: the worker image does
#: not ship apps/server/audit; tests pin them to the witness's): a line is placed only after this
#: much document silence ...
LINE_GAP_MS = 500
#: ... on the voice's return nearest its first word, when that return is this close.
LINE_VOICE_MAX_MS = 1500
#: The silence line and the shortest silence (the 2.35.0 rule's, the audit voice witness's own).
SILENCE_DB = FIRST_HEARD_SILENCE_DB
MIN_SILENCE_MS = FIRST_HEARD_MIN_SILENCE_MS

#: Verdicts, in the order they are decided.
FAILED = "failed"
FULL_JOB = "needs a full job"
GUARDED = "keep"  # the persist guard would keep the stored document (detail: "guard: ...")
SHEET = "sheet changed"
VETOED = "keep"  # detail: "veto: W1, ..."
REVIEW = "review"  # a move W2 cannot judge: for a person, not refreshed
UNCHANGED = "unchanged"
REFRESH = "refresh"

#: Document fields a rebuild always changes: they say when and by which version, not what.
STAMP_FIELDS = ("generated_at", "pipeline_version")


def _lines(doc: dict) -> list[dict]:
    return list(doc.get("lines") or [])


def _words(line: dict) -> list[dict]:
    return list(line.get("words") or [])


def shape(doc: dict) -> dict:
    """Counts over one document (the 2.34.0 refresh's definitions): lines, words, worded and
    wordless lines; `dwell_under_120` / `dwell_under_60` / `dwell_0`: words whose successor in
    the same line starts < SHORT_DWELL_MS / < FLASH_DWELL_MS / <= 0 ms after them;
    `first_dwell_under_120`: lines whose first word is such a word (W4, `first_word_flashes`);
    `disorder`: words starting before the word before them (across lines; W5); `overlap`: lines
    ending after the next line starts."""
    lines = _lines(doc)
    out = {
        "lines": len(lines),
        "words": 0,
        "worded": 0,
        "wordless": 0,
        "dwell_under_120": 0,
        "dwell_under_60": 0,
        "dwell_0": 0,
        "first_dwell_under_120": len(first_word_flashes(doc)),
        "disorder": 0,
        "overlap": 0,
    }
    previous: int | None = None
    for index, line in enumerate(lines):
        words = _words(line)
        out["worded" if words else "wordless"] += 1
        if index + 1 < len(lines) and line["end_ms"] > lines[index + 1]["start_ms"]:
            out["overlap"] += 1
        for k, word in enumerate(words):
            out["words"] += 1
            if previous is not None and word["start_ms"] < previous:
                out["disorder"] += 1
            previous = word["start_ms"]
            if k + 1 < len(words):
                dwell = words[k + 1]["start_ms"] - word["start_ms"]
                out["dwell_under_120"] += dwell < SHORT_DWELL_MS
                out["dwell_under_60"] += dwell < FLASH_DWELL_MS
                out["dwell_0"] += dwell <= 0
    return out


def first_dwell(line: dict) -> int | None:
    """The line's first word's dwell: its second word's start minus its first's (None under two
    words)."""
    words = _words(line)
    return words[1]["start_ms"] - words[0]["start_ms"] if len(words) >= 2 else None


def first_word_flashes(doc: dict) -> list[int]:
    """W4's lines: the lines of >= 2 words whose first word's dwell is < SHORT_DWELL_MS."""
    return [
        index
        for index, line in enumerate(_lines(doc))
        if (dwell := first_dwell(line)) is not None and dwell < SHORT_DWELL_MS
    ]


#: What an absent alignment field means (a document older than the field).
FIELD_DEFAULTS = {"speed_factor": 1.0}


def _same_text(stored: str | None, new: str | None) -> bool:
    """The same line: equal, or equal once the stored text's inline time tags are stripped."""
    return stored == new or (stored is not None and strip_inline_tags(stored) == new)


def sheet_changes(old: dict, new: dict) -> list[str]:
    """Why the two documents are not on the same sheet: the lyrics record, its source, the clock
    (speed factor; absent = 1.0), the line count, and the index of the first line whose text
    differs (inline tags aside). []: the same sheet, so lines pair by index."""
    a, b = old.get("alignment") or {}, new.get("alignment") or {}
    out = []
    for name, label in (
        ("lyrics_source", "lyrics source"),
        ("lyrics_source_id", "lyrics record"),
        ("speed_factor", "speed factor"),
    ):
        default = FIELD_DEFAULTS.get(name)
        if a.get(name, default) != b.get(name, default):
            out.append(f"{label} {a.get(name)} -> {b.get(name)}")
    la, lb = _lines(old), _lines(new)
    if len(la) != len(lb):
        out.append(f"line count {len(la)} -> {len(lb)}")
    else:
        texts = [
            i
            for i, (x, y) in enumerate(zip(la, lb, strict=True))
            if not _same_text(x.get("text"), y.get("text"))
        ]
        if texts:
            out.append(f"text of {len(texts)} line(s), first line {texts[0]}")
    return out


def runs(words: list[dict]) -> list[tuple[int, int]]:
    """Maximal runs (first, last word index) of >= 2 consecutive words, each starting less than
    RUN_STEP_MS after the word before it."""
    out: list[tuple[int, int]] = []
    k = 0
    while k < len(words):
        j = k
        while j + 1 < len(words) and words[j + 1]["start_ms"] - words[j]["start_ms"] < RUN_STEP_MS:
            j += 1
        if j > k:
            out.append((k, j))
        k = j + 1
    return out


def new_runs(old_words: list[dict], new_words: list[dict]) -> list[tuple[int, int]]:
    """The runs of the new line that no run of the stored line contains; the stored line's runs
    count only when it had words and the same number of them (a word pairing)."""
    paired = bool(old_words) and len(old_words) == len(new_words)
    before = runs(old_words) if paired else []
    return [
        (k0, k1)
        for k0, k1 in runs(new_words)
        if not any(x0 <= k0 and k1 <= x1 for x0, x1 in before)
    ]


def voiced(envelope: Envelope, frame: int) -> bool:
    """The frame is at or above the silence line."""
    return envelope.db(frame) >= SILENCE_DB


def silent_lead(envelope: Envelope, t_ms: int) -> int | None:
    """Ms from `t_ms` to the start of the first frame starting at or after it that is voiced;
    None when no such frame follows."""
    step = envelope.step_ms
    for frame in range(max(0, -(-t_ms // step)), len(envelope.levels)):
        if voiced(envelope, frame):
            return frame * step - t_ms
    return None


def voice_returns(envelope: Envelope) -> list[int]:
    """Where the voice returns: the start of a voiced frame after at least MIN_SILENCE_MS of
    frames under the silence line (the track starts in silence), as audit.analysis.voice_onsets
    reads its contour."""
    out: list[int] = []
    quiet_since: int | None = 0
    for frame in range(len(envelope.levels)):
        t = frame * envelope.step_ms
        if not voiced(envelope, frame):
            if quiet_since is None:
                quiet_since = t
        else:
            if quiet_since is not None and t - quiet_since >= MIN_SILENCE_MS:
                out.append(t)
            quiet_since = None
    return out


def placed_on_voice(doc: dict, returns: list[int]) -> dict[int, int]:
    """Line index -> the voice's return the witness places it on (audit.score.line_timings, the
    voice part): a worded line whose first word starts >= LINE_GAP_MS after the end of the
    document's previous worded stretch, on the nearest return (the earlier one on a tie) when that
    return is within LINE_VOICE_MAX_MS of the word."""
    out: dict[int, int] = {}
    if not returns:
        return out
    previous_end: int | None = None
    for index, line in enumerate(_lines(doc)):
        words = _words(line)
        start = words[0]["start_ms"] if words else None
        if start is not None and previous_end is not None and start - previous_end >= LINE_GAP_MS:
            at = bisect.bisect_left(returns, start)
            near = min(returns[max(0, at - 1) : at + 1], key=lambda v, t=start: abs(v - t))
            if abs(start - near) <= LINE_VOICE_MAX_MS:
                out[index] = near
        if words or previous_end is None:
            previous_end = line["end_ms"]
        else:
            previous_end = max(previous_end, line["end_ms"])
    return out


def qa_exit(doc: dict) -> str:
    """Which way out of line QA a document took, as the document shows it: `line` (degraded, or
    the aligner's line fallback), `snapped` (lines flagged), `clean` (references judged, none
    flagged) or `unreferenced` (no references to judge against)."""
    alignment = doc.get("alignment") or {}
    if doc.get("sync") == "line":
        return "line"
    if (alignment.get("qa") or {}).get("flagged"):
        return "snapped"
    if alignment.get("quality_basis") in ("anchors", "probs+anchors"):
        return "clean"
    return "unreferenced"


def compare(
    old: dict, new: dict, envelope: Envelope | None, *, letterless_wordless: bool = False
) -> dict:
    """Everything the verdict reads, and the report around it (numbers only).
    `letterless_wordless`: the build ran with `letterless_lines_wordless` on, so a line with no
    letter and no digit was never given to the aligner; W2 and W3 then judge no such line. Off
    (the production setting through 2.35.0) they judge every line, as before it existed. `qa`
    (report only): line QA's median offset and its way out, stored -> built."""
    out: dict[str, Any] = {
        "pipeline": [old.get("pipeline_version"), new.get("pipeline_version")],
        "differs": sorted(
            k for k in set(old) | set(new) if k not in STAMP_FIELDS and old.get(k) != new.get(k)
        ),
        "old": shape(old),
        "new": shape(new),
        "sheet": sheet_changes(old, new),
        "qa": {
            "offset_ms": [
                ((d.get("alignment") or {}).get("qa") or {}).get("offset_ms") for d in (old, new)
            ],
            "exit": [qa_exit(old), qa_exit(new)],
        },
    }
    if out["sheet"]:
        return out
    la, lb = _lines(old), _lines(new)
    returns = voice_returns(envelope) if envelope is not None else []
    on_old, on_new = placed_on_voice(old, returns), placed_on_voice(new, returns)
    moved: list[dict] = []
    w1: dict[str, Any] = {"words": 0}
    w2: dict[str, Any] = {"away_ms": 0}
    lost: list[int] = []
    gained: list[int] = []
    unjudged: list[int] = []
    for i, (a, b) in enumerate(zip(la, lb, strict=True)):
        wa, wb = _words(a), _words(b)
        for k0, k1 in new_runs(wa, wb):
            if k1 - k0 + 1 > w1["words"]:
                w1 = {"words": k1 - k0 + 1, "line": i, "word": k0, "t_ms": wb[k0]["start_ms"]}
        # Built with letterless_lines_wordless: a line with no letter and no digit ('♪') is no
        # one's voice returning and holds no lyric word, so neither W2 nor W3 judges it (its
        # move is still listed below). Without it every line is judged, as before.
        lyric = not letterless_wordless or sung_text(a.get("text") or "")
        sung = on_old.get(i, on_new.get(i)) if lyric else None
        if sung is not None:
            away = abs(b["start_ms"] - sung) - abs(a["start_ms"] - sung)
            if away > w2["away_ms"]:
                w2 = {
                    "away_ms": away,
                    "line": i,
                    "voice_ms": sung,
                    "old_ms": a["start_ms"],
                    "new_ms": b["start_ms"],
                }
        if wa and not wb:
            if lyric and (a.get("text") or "").strip():
                lost.append(i)
        elif wb and not wa:
            gained.append(i)
        d = b["start_ms"] - a["start_ms"]
        if abs(d) > MOVED_MS:
            row: dict[str, Any] = {
                "line": i,
                "old_ms": a["start_ms"],
                "new_ms": b["start_ms"],
                "d_ms": d,
            }
            if envelope is not None:
                row["lead_old_ms"] = silent_lead(envelope, a["start_ms"])
                row["lead_new_ms"] = silent_lead(envelope, b["start_ms"])
            moved.append(row)
            if abs(d) > VETO_AWAY_MS and i not in on_old and lyric:
                unjudged.append(i)
    flashed = set(first_word_flashes(old))
    flashing = first_word_flashes(new)
    # Each document is counted as registered: a line that gains its word timings counts on the
    # built side only, so the built side's flashing lines are split by what the stored line had.
    from_wordless = sum(1 for i in flashing if not _words(la[i]))
    w4 = {
        "old": out["old"]["first_dwell_under_120"],
        "new": out["new"]["first_dwell_under_120"],
        "new_from_worded": len(flashing) - from_wordless,
        "new_from_wordless": from_wordless,
        "lines": [
            {"line": i, "dwell_old_ms": first_dwell(la[i]), "dwell_new_ms": first_dwell(lb[i])}
            for i in flashing
            if i not in flashed
        ],
    }
    w5 = {"old": out["old"]["disorder"], "new": out["new"]["disorder"]}
    largest = max(moved, key=lambda r: abs(r["d_ms"]), default=None)
    out["moved"] = {
        f"over_{MOVED_MS}": len(moved),
        **{f"over_{far}": sum(abs(r["d_ms"]) > far for r in moved) for far in MOVED_FAR_MS},
        "largest": largest,
    }
    out["moved_lines"] = moved
    out["gained_words"] = gained
    out["voice"] = {
        "returns": len(returns),
        "placed_old": len(on_old),
        "placed_new": len(on_new),
        "envelope": envelope is not None,
    }
    out["w1"], out["w2"], out["w3"], out["w4"], out["w5"] = w1, w2, lost, w4, w5
    out["w2_unjudged"] = unjudged
    return out


def vetoes(comparison: dict) -> list[str]:
    """The vetoes that fire (W1-W5), on a comparison of one sheet."""
    out = []
    if comparison["w1"]["words"] >= VETO_RUN_WORDS:
        out.append("W1")
    if comparison["w2"]["away_ms"] > VETO_AWAY_MS:
        out.append("W2")
    if comparison["w3"]:
        out.append("W3")
    if comparison["w4"]["new"] > comparison["w4"]["old"]:
        out.append("W4")
    if comparison["w5"]["new"] > comparison["w5"]["old"]:
        out.append("W5")
    return out


def reviews(comparison: dict) -> list[str]:
    """What a person must look at before the song is refreshed (none of it a veto): moves W2
    cannot judge. Two earlier reasons are gone: a palette that falls back to the default
    (persist keeps the stored album palette, `document.carried_palette`, and the preview judges
    the document with it) and a pass in the aligner library's overrun region (since 2026-11-19
    the job's Viterbi is `trellis.forced_align`, defined on every input, so a reprocess stores
    the document previewed)."""
    out = []
    unjudged = comparison.get("w2_unjudged") or []
    if unjudged:
        lines = ", ".join(str(i) for i in unjudged)
        out.append(f"line(s) {lines} moved > {VETO_AWAY_MS} ms where W2 cannot judge")
    return out


def reasons(comparison: dict) -> dict:
    """Every veto that fires and every review reason, whichever one the verdict names (the first
    match hides the rest: a guard its vetoes, a veto its review reasons); a changed sheet has
    neither (its lines do not pair)."""
    return {
        "vetoes": [] if comparison["sheet"] else vetoes(comparison),
        "reviews": reviews(comparison),
    }


def verdict(comparison: dict, guard: tuple[str, str] | None = None) -> tuple[str, str]:
    """(verdict, detail) for a built document: the persist guard first (a reprocess would keep the
    stored document whatever it measures), then a changed sheet, then the vetoes, then what needs a
    person (`review`); a document that differs only in when and by which version it was made is
    `unchanged`."""
    if guard is not None:
        return GUARDED, f"guard: {guard[0]}: {guard[1]}"
    if comparison["sheet"]:
        return SHEET, "; ".join(comparison["sheet"])
    fired = vetoes(comparison)
    if fired:
        return VETOED, "veto: " + ", ".join(fired)
    review = reviews(comparison)
    if review:
        return REVIEW, "; ".join(review)
    if not comparison["differs"]:
        return UNCHANGED, ""
    return REFRESH, ""
