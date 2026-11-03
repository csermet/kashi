"""Before / after a timing change: two document dumps against the moment verdicts.

    python -m audit.compare <before.json> <after.json> [--verdicts review.jsonl]
        [--audio] [--json out.json] [--same-within-ms 20]

Per song: pipeline version, quality score, worded lines, seam piles
(``benchmarks.seam_piles``), the line-QA counters, duration hygiene (0 ms and
< 60 ms word shares, bunched starts: ``benchmarks.metrics.duration_hygiene``)
and how many words moved between the two.

Per reviewed moment (the Faz 1-3 verdicts): inside the moment's picture window
(+-2.5 s) did the document change, and how - word and line start shifts, piles,
0 ms words, wordless lines. A known-bad moment whose words moved the way the
verdict asks for is ``fixed?``, never "fixed": without audio nobody knows
whether a late line moved onto its voice or past it. ``--audio`` adds
``audit.score``'s two witnesses (ear-text match, the voice's return) where the
stem and the whole-song ear exist under ``KASHI_AUDIT_ROOT``; that is evidence
for the candidate, still not a listen.

Verdicts describe the version they were read on (``--verdicts-describe``,
default 2.29: the pilot set's claims read "2.29 puts..."). When the BEFORE
document is another version, only verdicts whose versions agreed at the moment
(``better_version`` none / either) apply; the rest are counted as ``n/a``.

The owner's listen enters through ``agreement`` only (``caner`` is free text
and is never parsed): agree and partial keep the verdict (partial is
labelled), disagree takes it out of every set (``n/a:owner-disputed``, listed
apart), "screen-right, not-perceived" keeps a flaw that is on screen but was
not heard. Verdicts taken from the numbers alone ("picture not read" in the
note) are counted separately.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from audit.docs import load_documents
from audit.ear import HALF_WINDOW_MS
from audit.finder import SHORT_WORD_MS, flat_words, pile_runs
from audit.score import NOTICE_EARLY_MS, NOTICE_LATE_MS
from benchmarks.metrics import duration_hygiene
from benchmarks.seam_piles import seam_piles

# One model frame (320 samples at 16 kHz). A start or end that moved this much
# or less did not move: CPU/GPU emissions and re-runs differ by a frame.
UNCHANGED_MS = 20
WINDOW_MS = HALF_WINDOW_MS  # the picture the verdict was read on
BAD = frozenset({"pile", "late", "early", "missing", "wrong_text"})
GOOD = frozenset({"ok"})
# better_version values that say both reviewed versions looked the same there:
# either = equivalent (both fine, or the same flaw in both), none = both bad and
# neither better (mostly a lyrics-source flaw). mixed and a version name do not.
AGREEING = frozenset({"none", "either"})
# `agreement`: how the agent's blind verdict compared with the owner's listen.
# `caner` itself is the owner's free-text answer and is never parsed.
OWNER_STATES = {
    "agree": "agree",
    "partial": "owner-partial",               # verdict used, labelled
    "disagree": "owner-disputed",             # verdict NOT used, listed apart
    "screen-right, not-perceived": "not-perceived",  # flaw on screen, not heard: stays bad
}
# A verdict taken from the numbers alone says so in its note.
NUMBERS_ONLY_NOTE = "picture not read"
VERDICTS_DESCRIBE = "2.29"
QA_KEYS = ("flagged", "density_dropped", "nudged", "uncertain")

# Moment classes, in report order.
CLASSES = (
    "bad:fixed?", "bad:worse?", "bad:changed", "bad:unchanged",
    "good:broken?", "good:moved", "good:kept",
    "unsure:changed", "unsure:unchanged", "other",
    "n/a:owner-disputed", "n/a:version", "n/a:no_doc",
)


# --- pairing ------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(text.lower().split())


# Inside a replaced run of unequal length, two items pair when their texts are
# at least this alike (a reworded line next to an inserted one).
REWORDED_SIMILARITY = 0.6


def _pair(a: list[str], b: list[str]) -> list[tuple[int | None, int | None]]:
    """Index pairs of two sequences, in order: equal runs pair up, so does a
    replaced run of the same length (a reworded line keeps its slot); in a
    replaced run of another length, items pair in order when alike; the rest
    is unpaired."""
    out: list[tuple[int | None, int | None]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            out += list(zip(range(i1, i2), range(j1, j2), strict=True))
            continue
        j = j1
        for i in range(i1, i2):
            match = next((k for k in range(j, j2) if SequenceMatcher(
                None, a[i], b[k], autojunk=False).ratio() >= REWORDED_SIMILARITY), None)
            if match is None:
                out.append((i, None))
                continue
            out += [(None, k) for k in range(j, match)] + [(i, match)]
            j = match + 1
        out += [(None, k) for k in range(j, j2)]
    return out


@dataclass(frozen=True)
class LinePair:
    before: dict | None
    after: dict | None

    @property
    def text_changed(self) -> bool:
        return (self.before is not None and self.after is not None
                and _norm(self.before.get("text", "")) != _norm(self.after.get("text", "")))


@dataclass(frozen=True)
class WordPair:
    before: dict | None
    after: dict | None


def pair_documents(before: dict, after: dict) -> tuple[list[LinePair], list[WordPair]]:
    """Lines paired by text, then each paired line's words by text."""
    a_lines, b_lines = before.get("lines", []), after.get("lines", [])
    lines: list[LinePair] = []
    words: list[WordPair] = []
    for i, j in _pair([_norm(ln.get("text", "")) for ln in a_lines],
                      [_norm(ln.get("text", "")) for ln in b_lines]):
        la = a_lines[i] if i is not None else None
        lb = b_lines[j] if j is not None else None
        lines.append(LinePair(la, lb))
        wa = (la or {}).get("words") or []
        wb = (lb or {}).get("words") or []
        for x, y in _pair([_norm(w["text"]) for w in wa], [_norm(w["text"]) for w in wb]):
            words.append(WordPair(wa[x] if x is not None else None,
                                  wb[y] if y is not None else None))
    return lines, words


# --- one song -----------------------------------------------------------------

def song_numbers(doc: dict) -> dict:
    lines = doc.get("lines", [])
    words = [w for _, _, w in flat_words(doc)]
    alignment = doc.get("alignment") or {}
    qa = alignment.get("qa") or {}
    hygiene = duration_hygiene([w["start_ms"] for w in words], [w["end_ms"] for w in words])
    return {
        "pipeline_version": doc.get("pipeline_version"),
        "method": alignment.get("method"),
        "quality_score": alignment.get("quality_score"),
        "lines": len(lines),
        "worded": sum(1 for ln in lines if ln.get("words")),
        "words": len(words),
        "seam_piles": len(seam_piles(lines)),
        "qa": {k: int(qa.get(k, 0)) for k in QA_KEYS},
        "zero": sum(1 for w in words if w["end_ms"] <= w["start_ms"]),
        "zero_frac": hygiene["zero_len_frac"],
        "short_frac": hygiene["short_frac"],
        "bunched": hygiene["bunched_runs"],
    }


def identity(lines: list[LinePair], words: list[WordPair]) -> dict:
    """How far the two documents are from being the same one."""
    starts = [w.after["start_ms"] - w.before["start_ms"] for w in words
              if w.before is not None and w.after is not None]
    ends = [w.after["end_ms"] - w.before["end_ms"] for w in words
            if w.before is not None and w.after is not None]
    line_moves = [abs(p.after[k] - p.before[k]) for p in lines
                  if p.before is not None and p.after is not None for k in ("start_ms", "end_ms")]
    return {
        "paired_words": len(starts),
        "moved_words": sum(1 for s, e in zip(starts, ends, strict=True)
                           if abs(s) > UNCHANGED_MS or abs(e) > UNCHANGED_MS),
        "max_start_shift_ms": max(starts, key=abs, default=0),
        "max_end_shift_ms": max(ends, key=abs, default=0),
        "max_line_shift_ms": max(line_moves, default=0),
        "unpaired_words": sum(1 for w in words if w.before is None or w.after is None),
        "unpaired_lines": sum(1 for p in lines if p.before is None or p.after is None),
        "text_changed_lines": sum(1 for p in lines if p.text_changed),
    }


def same_within(ident: dict, tolerance_ms: int) -> bool:
    return (ident["unpaired_words"] == 0 and ident["unpaired_lines"] == 0
            and ident["text_changed_lines"] == 0
            and abs(ident["max_start_shift_ms"]) <= tolerance_ms
            and abs(ident["max_end_shift_ms"]) <= tolerance_ms
            and ident["max_line_shift_ms"] <= tolerance_ms)


# --- one moment ---------------------------------------------------------------

@dataclass(frozen=True)
class WindowStats:
    """One document version inside a moment's window."""

    words: int = 0      # words starting in the window
    lit: int = 0        # ... that last > 0 ms (a 0 ms word never lights)
    zero: int = 0
    short: int = 0      # < SHORT_WORD_MS, zeros included (the hygiene definition)
    piles: int = 0      # finder piles whose first word starts in the window
    piled: int = 0      # words in those piles
    wordless: int = 0   # lines with text and no words, overlapping the window
    flagged: int = 0    # lines marked uncertain / words_derived, overlapping it


def window_stats(doc: dict, t0: int, t1: int) -> WindowStats:
    words = flat_words(doc)
    inside = [w for _, _, w in words if t0 <= w["start_ms"] <= t1]
    runs = [(i, j) for i, j in pile_runs(words) if t0 <= words[i][2]["start_ms"] <= t1]
    over = [ln for ln in doc.get("lines", []) if ln["start_ms"] <= t1 and ln["end_ms"] >= t0]
    return WindowStats(
        words=len(inside),
        lit=sum(1 for w in inside if w["end_ms"] > w["start_ms"]),
        zero=sum(1 for w in inside if w["end_ms"] <= w["start_ms"]),
        short=sum(1 for w in inside if w["end_ms"] - w["start_ms"] < SHORT_WORD_MS),
        piles=len(runs),
        piled=sum(j - i + 1 for i, j in runs),
        wordless=sum(1 for ln in over if doc.get("sync") == "word" and not ln.get("words")
                     and ln.get("text", "").strip()),
        flagged=sum(1 for ln in over if ln.get("uncertain") or ln.get("words_derived")),
    )


@dataclass
class MomentDiff:
    before: WindowStats
    after: WindowStats
    start_shifts: list[int] = field(default_factory=list)  # paired words in the window
    end_shifts: list[int] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)        # per paired line over the window
    lost_words: int = 0
    gained_words: int = 0
    text_changed: int = 0
    unpaired_lines: int = 0

    @property
    def moved(self) -> list[int]:
        return [d for d in self.start_shifts if abs(d) > UNCHANGED_MS]

    @property
    def median_moved_ms(self) -> int | None:
        """Median start shift of the words that moved (unmoved words left out:
        a window holds two or three lines, the verdict is usually about one)."""
        moved = self.moved
        return round(statistics.median(moved)) if moved else None

    @property
    def changed(self) -> bool:
        return bool(
            self.moved
            or any(abs(d) > UNCHANGED_MS for d in self.end_shifts)
            or any(abs(ln["start_shift_ms"]) > UNCHANGED_MS for ln in self.lines
                   if ln["start_shift_ms"] is not None)
            or self.before != self.after
            or self.lost_words or self.gained_words or self.text_changed or self.unpaired_lines
        )


def _in(t: int | None, t0: int, t1: int) -> bool:
    return t is not None and t0 <= t <= t1


def moment_diff(before: dict, after: dict, t_ms: int, half_ms: int = WINDOW_MS,
                pairs: tuple[list[LinePair], list[WordPair]] | None = None) -> MomentDiff:
    t0, t1 = t_ms - half_ms, t_ms + half_ms
    lines, words = pairs if pairs is not None else pair_documents(before, after)
    diff = MomentDiff(window_stats(before, t0, t1), window_stats(after, t0, t1))
    for w in words:
        a = w.before["start_ms"] if w.before is not None else None
        b = w.after["start_ms"] if w.after is not None else None
        if not (_in(a, t0, t1) or _in(b, t0, t1)):
            continue
        if w.before is None:
            diff.gained_words += 1
        elif w.after is None:
            diff.lost_words += 1
        else:
            diff.start_shifts.append(w.after["start_ms"] - w.before["start_ms"])
            diff.end_shifts.append(w.after["end_ms"] - w.before["end_ms"])
    for p in lines:
        spans = [ln for ln in (p.before, p.after) if ln is not None]
        if not any(ln["start_ms"] <= t1 and ln["end_ms"] >= t0 for ln in spans):
            continue
        if p.before is None or p.after is None:
            diff.unpaired_lines += 1
            continue
        diff.text_changed += p.text_changed
        wa, wb = p.before.get("words") or [], p.after.get("words") or []
        diff.lines.append({
            "start_ms": p.before["start_ms"],
            "start_shift_ms": p.after["start_ms"] - p.before["start_ms"],
            "first_word_shift_ms": wb[0]["start_ms"] - wa[0]["start_ms"] if wa and wb else None,
            "words": [len(wa), len(wb)],
        })
    return diff


def _net(*gains: int) -> int:
    """+1 when something improved and nothing got worse, -1 the other way round."""
    if any(g > 0 for g in gains) and not any(g < 0 for g in gains):
        return 1
    if any(g < 0 for g in gains) and not any(g > 0 for g in gains):
        return -1
    return 0


def direction(verdict: str, diff: MomentDiff) -> int:
    """+1: changed the way the verdict asks for; -1: the other way; 0: unclear."""
    b, a = diff.before, diff.after
    if verdict == "pile":
        return _net(b.piled - a.piled, b.zero - a.zero)
    if verdict in ("late", "early"):
        m = diff.median_moved_ms
        if not m:
            return 0
        wanted = -1 if verdict == "late" else 1  # a late line has to come earlier
        return 1 if m * wanted > 0 else -1
    if verdict == "missing":
        # Lit words only: a wordless line and one with only 0 ms words are
        # equally dark on screen, so trading one for the other fixes nothing.
        return _net(a.lit - b.lit)
    if verdict == "wrong_text":
        return 1 if diff.text_changed or diff.unpaired_lines else 0
    return 0


def breaks(diff: MomentDiff) -> bool:
    """A change a viewer could see at a moment that was fine: a word moved past
    the noticeable thresholds (audit.score), more piled or 0 ms words, fewer
    lit words, a line lost its words, words or lines vanished, text changed."""
    b, a = diff.before, diff.after
    if a.piled > b.piled or a.zero > b.zero or a.lit < b.lit or a.wordless > b.wordless:
        return True
    if diff.lost_words or diff.text_changed or diff.unpaired_lines:
        return True
    return any(d > NOTICE_LATE_MS or d < NOTICE_EARLY_MS for d in diff.start_shifts)


def classify(verdict: str, diff: MomentDiff) -> str:
    if verdict in BAD:
        if not diff.changed:
            return "bad:unchanged"
        return {1: "bad:fixed?", -1: "bad:worse?", 0: "bad:changed"}[direction(verdict, diff)]
    if verdict in GOOD:
        if not diff.changed:
            return "good:kept"
        return "good:broken?" if breaks(diff) else "good:moved"
    if verdict == "unsure":
        return "unsure:changed" if diff.changed else "unsure:unchanged"
    return "other"


# --- verdicts -----------------------------------------------------------------

def load_verdicts(path: Path) -> list[dict]:
    out = []
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        if not raw.strip():
            continue
        rec = json.loads(raw)
        if "id" not in rec or "t_ms" not in rec or "verdict" not in rec:
            raise ValueError(f"{path}:{n}: a verdict needs id, t_ms and verdict")
        try:
            owner_state(rec)
        except ValueError as exc:
            raise ValueError(f"{path}:{n}: {exc}") from None
        out.append(rec)
    return out


def owner_state(rec: dict) -> str | None:
    """What the owner's listen says about the verdict: None when nobody listened,
    else one of OWNER_STATES' values. An unknown `agreement` is refused: a typo
    must not quietly turn a disputed verdict into a counted one."""
    raw = rec.get("agreement")
    if raw is None or not str(raw).strip():
        return None
    key = " ".join(str(raw).lower().split())
    if key not in OWNER_STATES:
        raise ValueError(f"unknown agreement {raw!r} (known: {', '.join(OWNER_STATES)})")
    return OWNER_STATES[key]


def numbers_only(rec: dict) -> bool:
    return NUMBERS_ONLY_NOTE in str(rec.get("note") or "").lower()


def _minor(version: str | None) -> str:
    return ".".join((version or "").split(".")[:2])


def applies(rec: dict, before_doc: dict, describes: str = VERDICTS_DESCRIBE) -> bool:
    if _minor(before_doc.get("pipeline_version")) == describes:
        return True
    return rec.get("better_version") in AGREEING


# --- audio evidence (optional) -------------------------------------------------

def song_witnesses(video_id: str) -> tuple[dict | None, list[int] | None]:
    """The whole-song ear and the voice onsets, where they exist."""
    from audit.ear import whole_path
    from audit.paths import stem_path

    ear_file = whole_path(video_id)
    ear = json.loads(ear_file.read_text()) if ear_file.exists() else None
    voice = None
    if stem_path(video_id).exists():
        from audit.analysis import analyse

        voice = analyse(video_id).voice_onsets
    return ear, voice


def ear_reading(before: dict, after: dict, timings_before, timings_after,
                t0: int, t1: int) -> dict:
    """audit.score's line errors in the window, paired by line text. ``reading``:
    better / worse when a placed line crossed the noticeable threshold."""
    lines_b, lines_a = before.get("lines", []), after.get("lines", [])
    by_b = {t.line: t for t in timings_before if t0 <= t.doc_ms <= t1}
    by_a = {t.line: t for t in timings_after if t0 <= t.doc_ms <= t1}
    pairs = _pair([_norm(ln.get("text", "")) for ln in lines_b],
                  [_norm(ln.get("text", "")) for ln in lines_a])
    rows, net = [], 0
    for i, j in pairs:
        tb = by_b.get(i) if i is not None else None
        ta = by_a.get(j) if j is not None else None
        if tb is None and ta is None:
            continue
        rows.append([tb.error_ms if tb else None, ta.error_ms if ta else None])
        if tb is not None and ta is not None:
            net += int(tb.noticeable) - int(ta.noticeable)
    reading = "none" if not rows else ("better" if net > 0 else "worse" if net < 0 else "same")
    return {"reading": reading, "errors_ms": rows}


# --- report -------------------------------------------------------------------

def mmss(t_ms: int) -> str:
    return f"{t_ms // 60000}:{(t_ms // 1000) % 60:02d}.{(t_ms % 1000) // 100}"


def _arrow(a, b) -> str:
    return str(a) if a == b else f"{a}→{b}"


def compare(before: dict[str, dict], after: dict[str, dict], verdicts: list[dict],
            *, describes: str = VERDICTS_DESCRIBE, audio: bool = False,
            half_ms: int = WINDOW_MS) -> dict:
    common = sorted(set(before) & set(after))
    pairs = {vid: pair_documents(before[vid], after[vid]) for vid in common}
    songs = []
    timings: dict[str, tuple[list, list]] = {}
    for vid in common:
        row = {"id": vid, "before": song_numbers(before[vid]), "after": song_numbers(after[vid]),
               "identity": identity(*pairs[vid])}
        if audio:
            from audit.score import line_timings, song_score

            ear, voice = song_witnesses(vid)
            tb = line_timings(before[vid], ear, voice)
            ta = line_timings(after[vid], ear, voice)
            timings[vid] = (tb, ta)
            row["score"] = {"before": song_score(tb, len(before[vid].get("lines", []))),
                            "after": song_score(ta, len(after[vid].get("lines", []))),
                            "ear": ear is not None, "stem": voice is not None}
        songs.append(row)
    moments = []
    for rec in verdicts:
        vid, t_ms = str(rec["id"]), int(rec["t_ms"])
        verdict, owner = str(rec["verdict"]), owner_state(rec)
        out: dict = {"id": vid, "t_ms": t_ms, "verdict": verdict, "owner": owner,
                     "numbers_only": numbers_only(rec), "tags": rec.get("tags", []),
                     "better_version": rec.get("better_version")}
        moments.append(out)
        if vid not in pairs:
            out["class"] = "n/a:no_doc"
            continue
        if owner != "owner-disputed" and not applies(rec, before[vid], describes):
            out["class"] = "n/a:version"
            continue
        diff = moment_diff(before[vid], after[vid], t_ms, half_ms, pairs[vid])
        # The owner heard a disputed moment otherwise: it joins no set; its
        # diff is still listed.
        out["class"] = ("n/a:owner-disputed" if owner == "owner-disputed"
                        else classify(verdict, diff))
        out["diff"] = {
            "moved": len(diff.moved), "paired": len(diff.start_shifts),
            "median_moved_ms": diff.median_moved_ms,
            "max_shift_ms": max(diff.start_shifts, key=abs, default=0),
            "lost_words": diff.lost_words, "gained_words": diff.gained_words,
            "text_changed": diff.text_changed, "unpaired_lines": diff.unpaired_lines,
            "lines": diff.lines,
            "before": asdict(diff.before), "after": asdict(diff.after),
        }
        if audio and vid in timings:
            out["ear"] = ear_reading(before[vid], after[vid], *timings[vid],
                                     t_ms - half_ms, t_ms + half_ms)
    return {"songs": songs, "moments": moments, "totals": totals(songs, moments),
            "only_before": sorted(set(before) - set(after)),
            "only_after": sorted(set(after) - set(before))}


def totals(songs: list[dict], moments: list[dict]) -> dict:
    def total(side: str, key: str) -> int:
        return sum(s[side][key] for s in songs)

    out: dict = {"songs": len(songs)}
    for side in ("before", "after"):
        words = total(side, "words")
        out[side] = {
            "worded": total(side, "worded"), "words": words,
            "seam_piles": total(side, "seam_piles"), "bunched": total(side, "bunched"),
            "zero_frac": round(total(side, "zero") / words, 4) if words else 0.0,
            "qa": {k: sum(s[side]["qa"][k] for s in songs) for k in QA_KEYS},
        }
    out["moved_words"] = sum(s["identity"]["moved_words"] for s in songs)
    out["classes"] = dict(Counter(m["class"] for m in moments))
    out["owner"] = dict(Counter(m["owner"] or "no listen" for m in moments))
    out["numbers_only"] = sum(1 for m in moments if m["numbers_only"])
    return out


def _qa(numbers: dict) -> str:
    return "/".join(str(numbers["qa"][k]) for k in QA_KEYS)


def print_report(result: dict, describes: str) -> None:
    print(f"{'song':14} {'pipeline':16} {'quality':13} {'worded':9} {'seam':6} "
          f"{'qa f/d/n/u':18} {'0ms %':11} {'<60ms %':11} {'bunched':8} moved")
    for s in result["songs"]:
        b, a, ident = s["before"], s["after"], s["identity"]
        print(f"{s['id'][:14]:14} {_arrow(b['pipeline_version'], a['pipeline_version']):16} "
              f"{_arrow(b['quality_score'], a['quality_score']):13} "
              f"{_arrow(b['worded'], a['worded']):9} {_arrow(b['seam_piles'], a['seam_piles']):6} "
              f"{_arrow(_qa(b), _qa(a)):18} "
              f"{_arrow(round(100 * b['zero_frac'], 1), round(100 * a['zero_frac'], 1)):11} "
              f"{_arrow(round(100 * b['short_frac'], 1), round(100 * a['short_frac'], 1)):11} "
              f"{_arrow(b['bunched'], a['bunched']):8} "
              f"{ident['moved_words']}/{ident['paired_words']} "
              f"(max {ident['max_start_shift_ms']:+d} ms"
              f"{', unpaired ' + str(ident['unpaired_words']) if ident['unpaired_words'] else ''})")
        if "score" in s:
            sb, sa = s["score"]["before"], s["score"]["after"]
            print(f"{'':14} perceived misses {sb.get('noticeable', '-')}/{sb['placed']} → "
                  f"{sa.get('noticeable', '-')}/{sa['placed']} placed lines "
                  f"(ear {'yes' if s['score']['ear'] else 'no'}, "
                  f"stem {'yes' if s['score']['stem'] else 'no'})")
    t = result["totals"]
    tb, ta = t["before"], t["after"]
    print(f"\n{t['songs']} songs: worded {_arrow(tb['worded'], ta['worded'])}, "
          f"seam piles {_arrow(tb['seam_piles'], ta['seam_piles'])}, "
          f"bunched {_arrow(tb['bunched'], ta['bunched'])}, "
          f"0 ms {_arrow(round(100 * tb['zero_frac'], 2), round(100 * ta['zero_frac'], 2))} %, "
          f"qa f/d/n/u {_arrow(_qa(tb), _qa(ta))}, moved words {t['moved_words']}")
    for key in ("only_before", "only_after"):
        if result[key]:
            print(f"{key.replace('_', ' ')}: {', '.join(result[key])}")
    if not result["moments"]:
        return
    print(f"\nmoments (verdicts read on {describes}; window ±{WINDOW_MS / 1000:g} s; "
          f"'?' = candidate, no audio judged it)")
    owner = t["owner"]
    print("owner listens: " + ", ".join(
        f"{state} {owner.get(state, 0)}"
        for state in (*OWNER_STATES.values(), "no listen"))
        + f"; verdicts from numbers only (picture not read): {t['numbers_only']}")
    order = {c: i for i, c in enumerate(CLASSES)}
    for m in sorted(result["moments"], key=lambda m: (order.get(m["class"], 99), m["id"],
                                                       m["t_ms"])):
        line = f"  {m['class']:18} {m['id'][:14]:14} {mmss(m['t_ms']):>8} {m['verdict']:10}"
        d = m.get("diff")
        if d is not None:
            line += (f" moved {d['moved']}/{d['paired']}"
                     + (f" median {d['median_moved_ms']:+d} ms" if d["median_moved_ms"] else "")
                     + f", piled {_arrow(d['before']['piled'], d['after']['piled'])}"
                     + f", 0ms {_arrow(d['before']['zero'], d['after']['zero'])}"
                     + f", wordless {_arrow(d['before']['wordless'], d['after']['wordless'])}")
            if d["lost_words"] or d["gained_words"]:
                line += f", words -{d['lost_words']}/+{d['gained_words']}"
        if "ear" in m:
            line += f" [ear: {m['ear']['reading']}]"
        if m["owner"] not in (None, "agree"):
            line += f" [{m['owner']}]"
        if m["numbers_only"]:
            line += " [numbers-only]"
        print(line)
    classes = t["classes"]
    bad = sum(v for k, v in classes.items() if k.startswith("bad:"))
    good = sum(v for k, v in classes.items() if k.startswith("good:"))
    print(f"\nknown-bad {bad}: fixed? {classes.get('bad:fixed?', 0)}, "
          f"worse? {classes.get('bad:worse?', 0)}, changed {classes.get('bad:changed', 0)}, "
          f"unchanged {classes.get('bad:unchanged', 0)}")
    print(f"known-good {good}: broken? {classes.get('good:broken?', 0)}, "
          f"moved {classes.get('good:moved', 0)}, kept {classes.get('good:kept', 0)}")
    if t["numbers_only"]:
        counted = [m for m in result["moments"] if m["numbers_only"]
                   and m["class"].startswith(("bad:", "good:"))]
        split = ", ".join(f"{k} {v}" for k, v in sorted(Counter(
            m["class"] for m in counted).items()))
        print(f"  of these, from numbers only: {len(counted)}" + (f" ({split})" if split else ""))
    rest = {k: v for k, v in classes.items() if not k.startswith(("bad:", "good:"))}
    if rest:
        print("other:", ", ".join(f"{k} {v}" for k, v in sorted(rest.items())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Before/after documents against verdicts.")
    parser.add_argument("before", type=Path, help="[{id, doc}] dump or one document")
    parser.add_argument("after", type=Path, help="[{id, doc}] dump or one document")
    parser.add_argument("--verdicts", type=Path, help="review.jsonl (one moment per line)")
    parser.add_argument("--verdicts-describe", default=VERDICTS_DESCRIBE,
                        help="pipeline minor the verdicts were read on (default %(default)s)")
    parser.add_argument("--audio", action="store_true",
                        help="add audit.score's witnesses where stem / whole ear exist")
    parser.add_argument("--json", type=Path, help="write the full result here")
    parser.add_argument("--same-within-ms", type=int,
                        help="exit 1 unless every song's words and lines match within N ms")
    args = parser.parse_args(argv)
    before, after = load_documents(args.before), load_documents(args.after)
    verdicts = load_verdicts(args.verdicts) if args.verdicts else []
    result = compare(before, after, verdicts, describes=args.verdicts_describe, audio=args.audio)
    print_report(result, args.verdicts_describe)
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    if args.same_within_ms is not None:
        failed = [s["id"] for s in result["songs"]
                  if not same_within(s["identity"], args.same_within_ms)]
        if not result["songs"]:
            print("same-within: no song is in both inputs")
            return 1
        # A song one side lacks (its local run failed) is a miss, not a pass.
        unpaired = result["only_before"] + result["only_after"]
        print(f"same within {args.same_within_ms} ms: "
              f"{len(result['songs']) - len(failed)}/{len(result['songs'])} songs"
              + (f" (differ: {', '.join(failed)})" if failed else "")
              + (f"; on one side only: {', '.join(unpaired)}" if unpaired else ""))
        return 1 if failed or unpaired else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
