"""Nightcore support (Faz 4): detect the speed factor of a sped-up reupload,
plan the slow-down for alignment, and rescale output times back onto the
nightcore clock.

Pure module in the line_qa tradition — no torch, no I/O. The ffmpeg call and
lrclib requests live in the worker/lrclib modules; every decision lives here,
unit-tested.

Timeline math (kashi-faz4-plan.md F4-D): with speed factor r (nightcore =
original sped up by r, typical 1.1-1.3), the slowed copy (tempo 1/r) lasts
D_n*r ~= D_o and an event at nightcore time t_n sits at ~t_n*r ~= t_o in it —
the same clock as the ORIGINAL song's lrclib stamps, so windowed alignment
and line QA run unchanged. ONE rescale after QA (t -> round(t/r)) lands
everything on the nightcore clock that actually plays. Beats and palette are
computed from the nightcore audio/artwork and are never rescaled.
"""

import re
from dataclasses import replace

from kashi_server.pipeline.alignment import AlignResult
from kashi_server.pipeline.lrclib import has_usable_lyrics

# Title markers that trigger auto-detection when no explicit factor is given.
# \b guards: "Speed Upgrade Tutorial" and "Godspeed Up High" must NOT match
# (retro finding — empirically bitten); [ -]? covers sped-up/sped up/spedup.
NIGHTCORE_TOKENS = re.compile(r"\b(?:nightcore|sped[ -]?up|speed[ -]?up)\b", re.IGNORECASE)
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

_EMPTY_BRACKETS = re.compile(r"[(\[{]\s*[)\]}]")
_EDGE_SEPARATORS = re.compile(r"^[\s\-–—|:~•/]+|[\s\-–—|:~•/]+$")
_BRACKET_GROUP = re.compile(r"[(\[{]([^)\]}]*)[)\]}]")
_TITLE_WORD = re.compile(r"[\w']+")
# Upload-title noise ("(Lyrics)", "(Official Video)"): removed only when a
# WHOLE bracket group is noise/marker — deleting the words globally mangled
# real titles ("Nightcore - Video Games" → "Games"; retro finding).
_NOISE_WORD = re.compile(
    r"^(?:lyrics?|official|video|audio|visualizer|hq|hd|4k|mv|version)$", re.IGNORECASE
)


def _is_noise_group(content: str) -> bool:
    """A bracket group is droppable when, once the nightcore markers are
    removed, nothing but noise words remains ("Sped-Up Version", "Nightcore",
    "Official Video"). "(Video Games)" keeps its group — "Games" is real."""
    if not _TITLE_WORD.findall(content):
        return False
    remaining = _TITLE_WORD.findall(NIGHTCORE_TOKENS.sub(" ", content))
    return all(_NOISE_WORD.match(w) for w in remaining)


def clean_title(title: str) -> str | None:
    """Search query for the ORIGINAL song, or None when the title carries no
    nightcore/sped-up marker (auto-detection must not run on normal songs)."""
    if not NIGHTCORE_TOKENS.search(title):
        return None
    out = _BRACKET_GROUP.sub(lambda m: " " if _is_noise_group(m.group(1)) else m.group(0), title)
    out = NIGHTCORE_TOKENS.sub(" ", out)
    out = _EMPTY_BRACKETS.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip()
    out = _EDGE_SEPARATORS.sub("", out)
    return out.strip() or None


def rubberband_filter(tempo: float) -> str:
    """ffmpeg -af value slowing BOTH tempo and pitch by `tempo` (= 1/r): the
    result approximates the original recording, which is what MMS aligns
    best. Fixed 6-decimal formatting — float repr must never leak into the
    filter string (plan 4.2-①)."""
    return f"rubberband=tempo={tempo:.6f}:pitch={tempo:.6f}"


# Usability = the pipeline's own parse (lrclib._extract) — truthiness
# lookalikes drift (reviewer finding: syncedLyrics="\n\n" is truthy junk).
_usable = has_usable_lyrics


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
    record = next(
        (rec for _, rec in best if rec.get("syncedLyrics") and _usable(rec)),
        next((rec for _, rec in best if _usable(rec)), None),
    )
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
    r * track_duration (synced lyrics preferred)."""
    wanted = track_duration_s * r
    viable = [
        rec
        for rec in candidates
        if rec.get("duration")
        and abs(float(rec["duration"]) - wanted) <= PICK_DURATION_TOLERANCE_S
        and _usable(rec)
    ]
    if not viable:
        return None
    viable.sort(
        key=lambda rec: (not rec.get("syncedLyrics"), abs(float(rec["duration"]) - wanted))
    )
    return viable[0]


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
