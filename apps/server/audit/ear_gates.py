"""Does the second ear earn a place? The Faz 3.5 step 2 gates.

Per song, the current ear (the production CTC checkpoint, ``ear/<id>/whole.json``)
and an external one (``audit.ext_ear``) against the same document:

- placed lines: the ear-text match of every line (``audit.textmatch.locate``,
  look-alike neighbours' placements left out as in ``audit.finder``); the
  external ear must place at least the current ear's share;
- median line offset (heard minus document): on a song read as healthy,
  |median| <= HEALTHY_MAX_MS (segment-timed); on GOSSIP and Got 2 Luv U,
  |median| > SHIFTED_MIN_MS with the Faz 3 sign (``--expect sheet-late``: the
  sheet is late, the median negative);
- hallucination: the external transcript's lines - its finest timed spans:
  Whisper's own segments, the forced aligner's words, else the piece - lying
  mostly (>= INSIDE_SHARE of their length) inside a region where the CTC
  letter curve stays below SILENT_PROB for more than SILENT_MIN_MS; at most
  MAX_SILENT_SEGMENTS of the lines with text.

Japanese and Turkish songs are reported, never gated (n = 1 Japanese song;
Turkish is segment-level only). ``arm`` pools the per-song reports into the
arm's verdict. Thresholds are pre-registered and pinned by
``tests/test_audit_ext_ear.py``.

    python -m audit.ear_gates <id> --doc D --ear whole.json --ext whole-qwen3-asr.json \
        [--expect healthy|sheet-late|sheet-early] [--search-ms 8000] [--json out]
    python -m audit.ear_gates --manifest songs.json [--json out]   # [{id, doc, ear, ext, expect}]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path

from audit.finder import LINE_OFFSET_MS, heard_on_owned_neighbour
from audit.textmatch import SEARCH_MS, locate, matcher_for, sung_span

HEALTHY_MAX_MS = 500
SHIFTED_MIN_MS = 1000
SILENT_PROB = 0.2          # finder.BOXED_SILENT_PROB: the model hears no letter
SILENT_MIN_MS = 2000       # ... for longer than this
INSIDE_SHARE = 0.5         # a segment at least this much inside such a region
MAX_SILENT_SEGMENTS = 0.05
EXPECTATIONS = ("healthy", "sheet-late", "sheet-early")
REPORTED_ONLY = frozenset({"jpn", "tur"})


def placements(doc: dict, ear: dict, search_ms: int = SEARCH_MS,
               language: str | None = None) -> tuple[dict[int, int], set[int]]:
    """``finder.line_offsets`` with a chosen search window and the language's
    matcher (``textmatch.matcher_for``: Japanese is script-aware): (line index
    -> heard minus document start, the indices whose placement belongs to a
    look-alike neighbour)."""
    normalize, build = matcher_for(language)
    stream, times = build(ear["tokens"])
    lines = doc.get("lines", [])
    offsets: dict[int, int] = {}
    for li, ln in enumerate(lines):
        start = (ln.get("words") or [ln])[0]["start_ms"]
        match = locate(ln.get("text", ""), start, stream, times, search_ms=search_ms,
                       line_ms=sung_span(ln), normalize=normalize)
        if match is not None:
            offsets[li] = match.heard_ms - start
    borrowed = {
        li for li, off in offsets.items()
        if abs(off) > LINE_OFFSET_MS and heard_on_owned_neighbour(
            lines, offsets, li, (lines[li].get("words") or [lines[li]])[0]["start_ms"] + off)
    }
    return offsets, borrowed


def ear_reading(doc: dict, ear: dict, search_ms: int = SEARCH_MS,
                language: str | None = None) -> dict:
    offsets, borrowed = placements(doc, ear, search_ms, language)
    kept = [o for li, o in offsets.items() if li not in borrowed]
    n = len(doc.get("lines", []))
    return {"lines": n, "placed": len(kept), "placed_share": round(len(kept) / n, 3) if n else 0.0,
            "median_ms": round(statistics.median(kept)) if kept else None}


def silent_regions(ear: dict, prob: float = SILENT_PROB,
                   min_ms: int = SILENT_MIN_MS) -> list[tuple[int, int]]:
    """Runs of frames where the CTC letter curve stays below ``prob``, longer
    than ``min_ms``."""
    stride, t0 = float(ear["stride_ms"]), float(ear.get("start_ms", 0))
    out: list[tuple[int, int]] = []
    start: int | None = None
    curve = list(ear.get("letter_prob") or [])
    for i, p in enumerate([*curve, 1.0]):
        if p < prob and i < len(curve):
            if start is None:
                start = i
            continue
        if start is not None:
            s, e = round(t0 + start * stride), round(t0 + i * stride)
            if e - s > min_ms:
                out.append((s, e))
            start = None
    return out


def silent_segments(ext: dict, regions: Sequence[tuple[int, int]]) -> dict:
    """External transcript lines with text that lie mostly inside the CTC's
    silences. Lines are the file's ``lines`` (timed sub-spans) when it has
    them, else its ``segments`` (the pieces). The share is exact."""
    unit = "lines" if ext.get("lines") is not None else "segments"
    with_text = [x for x in ext.get(unit) or [] if x.get("text", "").strip()]
    inside = 0
    for seg in with_text:
        length = max(1, seg["end_ms"] - seg["start_ms"])
        overlap = sum(max(0, min(seg["end_ms"], e) - max(seg["start_ms"], s)) for s, e in regions)
        inside += overlap / length >= INSIDE_SHARE
    return {"unit": unit, "segments": len(with_text), "inside_silence": inside,
            "share": inside / len(with_text) if with_text else 0.0}


def song_language(doc: dict) -> str:
    from audit.syllables import doc_language
    from kashi_server.pipeline.japanese import looks_japanese

    if any(looks_japanese(ln.get("text", "")) for ln in doc.get("lines", [])):
        return "jpn"
    return doc_language(doc)


def report(doc: dict, current: dict, ext: dict, expect: str | None = None,
           search_ms: int = SEARCH_MS, language: str | None = None) -> dict:
    """The step-2 numbers and gates for one song."""
    if expect is not None and expect not in EXPECTATIONS:
        raise ValueError(f"unknown expectation {expect!r}")
    lang = language or song_language(doc)
    cur = ear_reading(doc, current, search_ms, lang)
    new = ear_reading(doc, ext, search_ms, lang)
    silence = silent_segments(ext, silent_regions(current))
    gates: dict[str, bool] = {"placed_share": new["placed_share"] >= cur["placed_share"],
                              "silence": silence["share"] <= MAX_SILENT_SEGMENTS}
    m = new["median_ms"]
    if expect == "healthy":
        gates["offset"] = m is not None and abs(m) <= HEALTHY_MAX_MS
    elif expect == "sheet-late":
        gates["offset"] = m is not None and m < -SHIFTED_MIN_MS
    elif expect == "sheet-early":
        gates["offset"] = m is not None and m > SHIFTED_MIN_MS
    gated = lang not in REPORTED_ONLY
    return {"language": lang, "gated": gated, "expect": expect, "search_ms": search_ms,
            "current": cur, "external": new, "silence": silence, "gates": gates,
            "passed": all(gates.values()) if gated else None,
            "external_source": ext.get("source"), "external_timing": ext.get("timing")}


def arm(reports: dict[str, dict]) -> dict:
    """One arm over the songs: pooled placed share at or above the current ear's,
    every expectation met, pooled silent-segment share within the bar. Songs
    whose language is reported-only stay out."""
    gated = {k: r for k, r in reports.items() if r["gated"]}
    lines = sum(r["external"]["lines"] for r in gated.values())
    cur = sum(r["current"]["placed"] for r in gated.values())
    new = sum(r["external"]["placed"] for r in gated.values())
    segs = sum(r["silence"]["segments"] for r in gated.values())
    inside = sum(r["silence"]["inside_silence"] for r in gated.values())
    misses = sorted(k for k, r in gated.items() if r["gates"].get("offset") is False)
    checks = {"placed_share": new >= cur, "offsets": not misses,
              "silence": (inside / segs if segs else 0.0) <= MAX_SILENT_SEGMENTS}
    return {"songs": len(gated), "reported_only": sorted(set(reports) - set(gated)),
            "placed_current": cur, "placed_external": new, "lines": lines,
            "silent_share": round(inside / segs, 3) if segs else 0.0,
            "offset_misses": misses, "checks": checks,
            "passed": bool(gated) and all(checks.values())}


def summary_line(video_id: str, r: dict) -> str:
    cur, new = r["current"], r["external"]
    verdict = "reported" if not r["gated"] else ("pass" if r["passed"] else "FAIL")
    return (f"{video_id} [{r['language']}] {verdict}: placed {cur['placed']}->{new['placed']} "
            f"of {new['lines']}, median {cur['median_ms']}->{new['median_ms']} ms"
            f"{' (' + r['expect'] + ')' if r['expect'] else ''}, "
            f"segments in CTC silence {r['silence']['inside_silence']}/{r['silence']['segments']}")


def main(argv: list[str] | None = None) -> int:
    from audit.docs import load_document

    parser = argparse.ArgumentParser(description="Second-ear gates (Faz 3.5 step 2).")
    parser.add_argument("id", nargs="?")
    parser.add_argument("--doc")
    parser.add_argument("--ear", help="the current CTC ear (whole.json)")
    parser.add_argument("--ext", help="the external ear (audit.ext_ear)")
    parser.add_argument("--expect", choices=EXPECTATIONS)
    parser.add_argument("--search-ms", type=int, default=SEARCH_MS,
                        help="the ear-text window; a manifest entry may set its own")
    parser.add_argument("--language", help="jpn / tur: reported, not gated (default: from the "
                                           "document); a manifest entry may set its own")
    parser.add_argument("--manifest", type=Path,
                        help="[{id, doc, ear, ext, expect[, search_ms, language]}] (paths "
                             "relative to the manifest)")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    if args.manifest:
        base = args.manifest.parent
        entries = json.loads(args.manifest.read_text())
    elif args.id and args.doc and args.ear and args.ext:
        base = Path(".")
        entries = [{"id": args.id, "doc": args.doc, "ear": args.ear, "ext": args.ext,
                    "expect": args.expect, "language": args.language}]
    else:
        parser.error("give <id> --doc --ear --ext, or --manifest")
    reports = {}
    for e in entries:
        doc = load_document(base / e["doc"], e["id"])
        r = report(doc, json.loads((base / e["ear"]).read_text()),
                   json.loads((base / e["ext"]).read_text()), e.get("expect"),
                   e.get("search_ms") or args.search_ms, e.get("language") or args.language)
        reports[e["id"]] = r
        print(summary_line(e["id"], r))
    out: dict = {"songs": reports}
    if len(reports) > 1 or args.manifest:
        out["arm"] = arm(reports)
        print(f"arm: {'pass' if out['arm']['passed'] else 'FAIL'} {out['arm']['checks']}")
    if args.json:
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
