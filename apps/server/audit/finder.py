"""Suspicious-moment finder: where a document and its audio disagree.

Pure: documents, the song analysis and (optionally) the whole-song ear go in,
events come out. One event is one observation at one time; ``cluster`` merges
events into moments and ranks them. Thresholds are named and pinned by tests;
their values were set on the Faz 1 proof moments (2026-10-31).

Event tags:
  pile           >= 2 word starts within PILE_SPAN_MS (across line seams too)
  short_run      >= SHORT_RUN words in a row, each < SHORT_WORD_MS
  orphan_voice   the voice returns after silence and no word starts near it
  line_vs_voice  a line after a silence starts far from the voice's return
  sung_unboxed   the model hears letters where no word box is
  boxed_silent   a long word box where the model hears nothing and it is quiet
  qa             line QA marked the line (uncertain, derived words, no words)
  moved          a word moved between two document versions
"""

from __future__ import annotations

import bisect
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

PILE_SPAN_MS = 60
SHORT_WORD_MS = 60
SHORT_RUN = 3
ORPHAN_MS = 250
LINE_GAP_MS = 500
LINE_VOICE_MIN_MS = 250
LINE_VOICE_MAX_MS = 1500
EAR_BOX_SLACK_MS = 150
EAR_CLUSTER_MS = 300
EAR_MIN_LETTERS = 4
BOXED_SILENT_MIN_MS = 250
BOXED_SILENT_PROB = 0.2
SILENCE_DB = -25.0
MOVED_MS = 250
CLUSTER_MS = 1500
# A moment is one picture (+-2.5 s): chained events stop at this span.
CLUSTER_MAX_SPAN_MS = 3000
# One tag counts at most this often toward a moment's score, so twenty zero
# words in a dense hook do not outrank one line sung a second off.
TAG_COUNT_CAP = 3
CONTROL_CLEARANCE_MS = (3000, 2000, 1000)
CONTROL_MIN_GAP_MS = 5000
CONTROLS_PER_SONG = 2

# pile: per extra word in the pile (a 4-word pile weighs 3 x 0.6), see Event.size
WEIGHTS = {
    "pile": 0.6, "short_run": 1.0, "zero": 0.3, "orphan_voice": 1.0, "line_vs_voice": 1.0,
    "sung_unboxed": 1.0, "boxed_silent": 0.7, "qa": 0.6, "moved": 0.5,
}


PILE_SIZE_CAP = 3


@dataclass(frozen=True)
class Event:
    t_ms: int
    tag: str
    version: str
    detail: str = ""
    size: int = 1

    @property
    def weight(self) -> float:
        return WEIGHTS[self.tag] * self.size


@dataclass
class Moment:
    t_ms: int
    score: float
    tags: dict[str, int]
    versions: list[str]
    events: list[Event] = field(default_factory=list)
    control: bool = False


def flat_words(doc: dict) -> list[tuple[int, int, dict]]:
    """(line index, word index, word) in document order."""
    return [(li, wi, w) for li, ln in enumerate(doc.get("lines", []))
            for wi, w in enumerate(ln.get("words") or [])]


def word_events(doc: dict, version: str) -> list[Event]:
    """Piles, runs of short words and lone zero-length words."""
    out: list[Event] = []
    words = flat_words(doc)
    i = 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and words[j + 1][2]["start_ms"] - words[i][2]["start_ms"] \
                <= PILE_SPAN_MS:
            j += 1
        if j > i:
            texts = " ".join(w["text"] for _, _, w in words[i : j + 1])
            out.append(Event(words[i][2]["start_ms"], "pile", version, f"{j - i + 1}: {texts}",
                             size=min(j - i, PILE_SIZE_CAP)))
        i = j + 1
    run: list[dict] = []
    for _, _, w in words + [(0, 0, {"start_ms": 0, "end_ms": 10**9, "text": ""})]:
        if w["end_ms"] - w["start_ms"] < SHORT_WORD_MS:
            run.append(w)
            continue
        if len(run) >= SHORT_RUN:
            out.append(Event(run[0]["start_ms"], "short_run", version,
                             " ".join(x["text"] for x in run)))
        elif len(run) == 1 and run[0]["end_ms"] <= run[0]["start_ms"]:
            out.append(Event(run[0]["start_ms"], "zero", version, run[0]["text"]))
        run = []
    return out


def _near(sorted_times: Sequence[int], t: int, within: int) -> bool:
    i = bisect.bisect_left(sorted_times, t - within)
    return i < len(sorted_times) and sorted_times[i] <= t + within


def voice_events(doc: dict, version: str, voice_onsets: Sequence[int]) -> list[Event]:
    """Voice returns no word explains, and lines that start far from one."""
    words = flat_words(doc)
    if not words or not voice_onsets:
        return []
    starts = sorted(w["start_ms"] for _, _, w in words)
    first, last = starts[0], max(w["end_ms"] for _, _, w in words)
    out: list[Event] = []
    for v in voice_onsets:
        if first - ORPHAN_MS <= v <= last + ORPHAN_MS and not _near(starts, v, ORPHAN_MS):
            out.append(Event(v, "orphan_voice", version))
    prev_end: int | None = None
    for ln in doc.get("lines", []):
        ws = ln.get("words") or []
        if ws and prev_end is not None and ws[0]["start_ms"] - prev_end >= LINE_GAP_MS:
            s = ws[0]["start_ms"]
            nearest = min(voice_onsets, key=lambda x: abs(x - s))
            if LINE_VOICE_MIN_MS <= abs(s - nearest) <= LINE_VOICE_MAX_MS:
                out.append(Event(s, "line_vs_voice", version, f"{s - nearest:+d} ms: {ln['text']}"))
        prev_end = ln["end_ms"] if ws or prev_end is None else max(prev_end, ln["end_ms"])
    return out


def ear_events(doc: dict, version: str, ear: dict,
               level_at=None) -> list[Event]:
    """Letters with no box near them; long boxes with no letters and no loudness."""
    words = flat_words(doc)
    if not words:
        return []
    spans = sorted((w["start_ms"] - EAR_BOX_SLACK_MS, w["end_ms"] + EAR_BOX_SLACK_MS)
                   for _, _, w in words)
    span_starts = [s for s, _ in spans]

    def boxed(t: int) -> bool:
        i = bisect.bisect_right(span_starts, t)
        return any(s <= t <= e for s, e in spans[max(0, i - 8) : i])

    first, last = spans[0][0], spans[-1][1]
    out: list[Event] = []
    loose = [t for t in ear["tokens"] if t["text"] != "|" and first <= t["start_ms"] <= last
             and not boxed(t["start_ms"])]
    group: list[dict] = []
    for tok in loose + [{"start_ms": 10**12, "text": ""}]:
        if group and tok["start_ms"] - group[-1]["start_ms"] > EAR_CLUSTER_MS:
            if len(group) >= EAR_MIN_LETTERS:
                out.append(Event(group[0]["start_ms"], "sung_unboxed", version,
                                 "".join(t["text"] for t in group)))
            group = []
        group.append(tok)
    stride, t0, probs = ear["stride_ms"], ear["start_ms"], ear["letter_prob"]
    letter_starts = sorted(t["start_ms"] for t in ear["tokens"] if t["text"] != "|")
    for _, _, w in words:
        s, e = w["start_ms"], w["end_ms"]
        if e - s < BOXED_SILENT_MIN_MS:
            continue
        i0, i1 = max(0, int((s - t0) / stride)), min(len(probs), int((e - t0) / stride) + 1)
        window = probs[i0:i1]
        has_letter = _near(letter_starts, (s + e) // 2, (e - s) // 2)
        loud = level_at is not None and any(
            (level_at(t) or -99) >= SILENCE_DB for t in range(s, e, 50))
        if window and max(window) < BOXED_SILENT_PROB and not has_letter and not loud:
            out.append(Event(s, "boxed_silent", version, w["text"]))
    return out


def qa_events(doc: dict, version: str) -> list[Event]:
    out: list[Event] = []
    word_doc = doc.get("sync") == "word"
    for ln in doc.get("lines", []):
        marks = [k for k in ("uncertain", "words_derived") if ln.get(k)]
        if word_doc and not ln.get("words") and ln.get("text", "").strip():
            marks.append("no_words")
        if marks:
            out.append(Event(ln["start_ms"], "qa", version, ",".join(marks) + ": " + ln["text"]))
    return out


def moved_events(old: dict, new: dict, version: str) -> list[Event]:
    """Words that moved > MOVED_MS between versions (same line, same word, same text)."""
    out: list[Event] = []
    old_lines = old.get("lines", [])
    for li, ln in enumerate(new.get("lines", [])):
        if li >= len(old_lines) or old_lines[li].get("text") != ln.get("text"):
            continue
        for a, b in zip(old_lines[li].get("words") or [], ln.get("words") or [], strict=False):
            if a["text"] == b["text"] and abs(a["start_ms"] - b["start_ms"]) > MOVED_MS:
                out.append(Event(b["start_ms"], "moved", version,
                                 f"{b['text']} {b['start_ms'] - a['start_ms']:+d} ms"))
                break
    return out


def cluster(events: Sequence[Event]) -> list[Moment]:
    """Events within CLUSTER_MS of the previous one form a moment, up to
    CLUSTER_MAX_SPAN_MS long; best first. Score: per tag, the TAG_COUNT_CAP
    heaviest events of that tag."""
    moments: list[Moment] = []
    group: list[Event] = []
    for ev in sorted(events, key=lambda e: e.t_ms) + [None]:
        if group and (ev is None or ev.t_ms - group[-1].t_ms > CLUSTER_MS
                      or ev.t_ms - group[0].t_ms > CLUSTER_MAX_SPAN_MS):
            anchor = max(group, key=lambda e: (e.weight, -e.t_ms))
            counts = Counter(e.tag for e in group)
            score = sum(
                sum(sorted((e.weight for e in group if e.tag == tag), reverse=True)[:TAG_COUNT_CAP])
                for tag in counts
            )
            moments.append(Moment(
                t_ms=anchor.t_ms,
                score=round(score, 2),
                tags=dict(counts),
                versions=sorted({e.version for e in group}),
                events=list(group),
            ))
            group = []
        if ev is not None:
            group.append(ev)
    return sorted(moments, key=lambda m: (-m.score, m.t_ms))


def controls(doc: dict, moments: Sequence[Moment], seed: str,
             n: int = CONTROLS_PER_SONG) -> list[Moment]:
    """Quiet moments at word starts, seeded: no event within the first clearance
    of CONTROL_CLEARANCE_MS that leaves any candidate (dense songs relax it)."""
    busy = sorted(e.t_ms for m in moments for e in m.events)
    starts = [w["start_ms"] for _, _, w in flat_words(doc)]
    candidates: list[int] = []
    for clearance in CONTROL_CLEARANCE_MS:
        candidates = [t for t in starts if not _near(busy, t, clearance)]
        if candidates:
            break
    rng = random.Random(seed)
    rng.shuffle(candidates)
    picks: list[int] = []
    for t in candidates:
        if len(picks) < n and all(abs(t - p) >= CONTROL_MIN_GAP_MS for p in picks):
            picks.append(t)
    picks.sort()
    return [Moment(t_ms=t, score=0.0, tags={}, versions=[], control=True) for t in picks]


def find(versions: Sequence[tuple[str, dict]], voice_onsets: Sequence[int] | None = None,
         ear: dict | None = None, level_at=None) -> list[Event]:
    events: list[Event] = []
    for label, doc in versions:
        events += word_events(doc, label) + qa_events(doc, label)
        if voice_onsets:
            events += voice_events(doc, label, voice_onsets)
        if ear:
            events += ear_events(doc, label, ear, level_at)
    for (_, old), (label, new) in zip(versions, versions[1:], strict=False):
        events += moved_events(old, new, label)
    return events
