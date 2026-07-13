"""Nightcore support (Faz 4): detect the speed factor of a sped-up reupload,
plan the slow-down for alignment, and rescale output times back onto the
nightcore clock.

Pure module in the line_qa tradition — no torch, no I/O. The ffmpeg call and
lrclib requests live in the worker/lrclib modules; every timeline decision
lives here, unit-tested. Upload-title hygiene (`clean_title`) moved to
pipeline/titles.py in Faz 5 P0 and is re-exported below for compatibility.

Timeline math (kashi-faz4-plan.md F4-D): with speed factor r (nightcore =
original sped up by r, typical 1.1-1.3), the slowed copy (tempo 1/r) lasts
D_n*r ~= D_o and an event at nightcore time t_n sits at ~t_n*r ~= t_o in it —
the same clock as the ORIGINAL song's lrclib stamps, so windowed alignment
and line QA run unchanged. ONE rescale after QA (t -> round(t/r)) lands
everything on the nightcore clock that actually plays. Beats and palette are
computed from the nightcore audio/artwork and are never rescaled.
"""

from dataclasses import replace

from kashi_server.pipeline.alignment import AlignResult
from kashi_server.pipeline.lrclib import choose_record

# Re-export from clean_title's historical home (tests and callers predate
# pipeline/titles.py).
from kashi_server.pipeline.titles import clean_title  # noqa: F401

# Plausible nightcore range for r = original_duration / nightcore_duration.
SPEED_FACTOR_MIN = 1.05
SPEED_FACTOR_MAX = 1.5
# Candidate r values within this of each other form one cluster.
CLUSTER_TOLERANCE = 0.02
# The slowed copy must land within this of nightcore_duration * r.
SLOW_DURATION_TOLERANCE_S = 1.0
# Explicit-r record pick: candidate duration must sit within this of
# r * track_duration (same tolerance the lrclib search uses).
PICK_DURATION_TOLERANCE_S = 3.0

def rubberband_filter(tempo: float) -> str:
    """ffmpeg -af value slowing BOTH tempo and pitch by `tempo` (= 1/r): the
    result approximates the original recording, which is what MMS aligns
    best. Fixed 6-decimal formatting — float repr must never leak into the
    filter string (plan 4.2-①)."""
    return f"rubberband=tempo={tempo:.6f}:pitch={tempo:.6f}"


def detect_speed_factor(
    candidates: list[dict], track_duration_s: float
) -> tuple[float, dict] | None:
    """Infer r from lrclib candidates for the ORIGINAL song.

    Per candidate r = candidate_duration / nightcore_duration; values outside
    the plausible nightcore band are discarded. Remaining r values cluster
    within CLUSTER_TOLERANCE and the LARGEST cluster wins (tie -> the
    tightest), its low-median being the representative r — an actual member,
    robust against one mislabeled record. The returned record prefers synced
    lyrics, then anything usable. None -> normal (r=1) flow.
    """
    if track_duration_s <= 0:
        return None
    scored: list[tuple[float, dict]] = []
    for record in candidates:
        duration = record.get("duration")
        if not duration:
            continue
        r = float(duration) / track_duration_s
        if SPEED_FACTOR_MIN <= r <= SPEED_FACTOR_MAX:
            scored.append((r, record))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0])
    clusters: list[list[tuple[float, dict]]] = []
    for pair in scored:
        if clusters and pair[0] - clusters[-1][-1][0] <= CLUSTER_TOLERANCE:
            clusters[-1].append(pair)
        else:
            clusters.append([pair])
    best = max(clusters, key=lambda c: (len(c), c[0][0] - c[-1][0]))
    # The cluster only VOTES; picking the member record is choose_record's
    # job (Faz 5 P3 — one selection policy: parsed-synced first, then usable;
    # the band already filtered durations, so that axis is off).
    record = choose_record([rec for _, rec in best], duration_s=None)
    if record is None:
        return None  # no usable member -> revert; a garbage pick was a
        # PERMANENT lyrics_not_found (reviewer finding)
    # The cluster only VOTES for the record; the operative r is the record's
    # own ratio — a cluster-median r on another record's stamps is a timeline
    # SCALE error line QA's median-offset compensation cannot absorb (retro).
    return float(record["duration"]) / track_duration_s, record


def pick_record_for_factor(
    candidates: list[dict], track_duration_s: float, r: float
) -> dict | None:
    """For an EXPLICIT r: the usable record whose duration best matches
    r * track_duration (synced lyrics preferred). Selection delegates to
    choose_record (Faz 5 P3); records without a duration stay excluded —
    the ratio IS the identity signal on this path."""
    wanted = track_duration_s * r
    viable = [rec for rec in candidates if rec.get("duration")]
    return choose_record(viable, duration_s=wanted, tolerance_s=PICK_DURATION_TOLERANCE_S)


def slow_duration_ok(slow_duration_s: float, nightcore_duration_s: float, r: float) -> bool:
    """Sanity gate: the slowed copy must last ~nightcore * r (= the original
    length). A miss means r was mis-detected — the caller reverts to r=1."""
    return abs(slow_duration_s - nightcore_duration_s * r) <= SLOW_DURATION_TOLERANCE_S


def rescale_result(result: AlignResult, r: float) -> AlignResult:
    """Map aligned times (slowed ~= original clock) onto the nightcore clock:
    t -> round(t / r), with a monotonic clamp against rounding inversions."""
    if r == 1.0:
        return result

    def ms(t: int) -> int:
        return max(0, round(t / r))

    lines = []
    prev = 0
    for line in result.lines:
        start = max(ms(line.start_ms), prev)
        end = max(ms(line.end_ms), start)
        lines.append(replace(line, start_ms=start, end_ms=end))
        prev = start
    words_per_line = []
    for chunk in result.words_per_line:
        out = []
        prev_w = 0
        for word in chunk:
            start = max(ms(word.start_ms), prev_w)
            end = max(ms(word.end_ms), start)
            out.append(replace(word, start_ms=start, end_ms=end))
            prev_w = start
        words_per_line.append(out)
    return replace(result, lines=lines, words_per_line=words_per_line)
