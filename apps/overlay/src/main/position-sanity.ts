/**
 * Secondary defense against a stale first position after a track change.
 *
 * The extension guards this at the source (see the extension's
 * `position-guard.ts`), but the extension does NOT auto-update: a user still
 * running an older build would keep anchoring the clock on the previous
 * track's position under gapless playback. This layer costs one comparison
 * and makes the overlay correct on its own.
 *
 * Deliberately narrow: it only screens reports until the first plausible one
 * lands, because that report is the one the clock anchors on without any
 * delta check. Afterwards the clock's own slew/snap band owns outliers, and a
 * wrong duration can no longer suppress anything.
 *
 * Coverage USED to be partial by construction: the only test was "past the
 * new track's end", so a short track leaking into a longer one slipped
 * through — and on 2026-08-13 that is exactly what the field produced (a
 * 270 s track auto-advancing into a 300 s one, anchoring the clock at
 * 270.91 s of a song that had just started). The second rule below closes it
 * without needing a duration at all.
 */

/** Matches the extension's tolerance — duration rounding, not a real seek. */
export const POSITION_OVERSHOOT_TOLERANCE_MS = 2000;

/** The guard gives up after this many rejects, or this long. */
export const ANCHOR_GUARD_BUDGET_REPORTS = 12;
export const ANCHOR_GUARD_BUDGET_MS = 3000;

/**
 * How far a report may sit from where the OUTGOING clock would have reached and
 * still be recognised as that same clock.
 *
 * Its own number, not the overshoot tolerance above: that one answers "did the
 * duration round badly?", this one answers "is this the previous track still
 * ticking?" — and it is sized by report spacing (YTM streams `timeupdate` about
 * four times a second, the overlay sees them roughly every second), not by
 * rounding.
 */
export const OUTGOING_TRAJECTORY_TOLERANCE_MS = 2000;

/**
 * The ceiling on holding the line with POSITIVE evidence.
 *
 * The blind budget above exists because "this position looks implausible" is a
 * guess, and a guard that guesses forever would leave the clock unanchored. A
 * report that continues the previous track's timeline is not a guess — so it
 * gets a far longer rope (the field case ran 11.5 s, the extension's own hold
 * gives up at 12 s) — but not an unlimited one: if the old clock is somehow
 * STILL the only thing reporting after half a minute, the premise has failed
 * and an anchored clock beats a frozen screen.
 */
export const ANCHOR_GUARD_STALE_HOLD_MS = 30_000;

/**
 * A track that has just changed is at its beginning. Not necessarily zero —
 * a resumed track, a client that reports late, a user who starts mid-song by
 * seeking — but a FIRST report this far in describes a playhead that never
 * moved with the track, which is the leak this guard exists for.
 *
 * Deliberately generous. The cost of rejecting a real one is bounded: the
 * budget above releases the guard within 3 s or 12 reports, and the position
 * stream then anchors normally. The cost of accepting a leak is a clock
 * minutes past the last line, showing an interlude for the whole song.
 */
export const ANCHOR_MAX_FIRST_POSITION_MS = 15_000;

/**
 * True when a position cannot belong to the track it was reported for.
 * An unknown duration (the extension sends none when it is stale) never drops.
 */
export function positionOvershootsTrack(
  positionMs: number,
  durationMs: number | undefined,
): boolean {
  if (durationMs === undefined) return false;
  return positionMs > durationMs + POSITION_OVERSHOOT_TOLERANCE_MS;
}

/**
 * True when a FIRST post-change report is too deep into the track to be one.
 * Duration-free on purpose: the leak this catches is a previous track's
 * playhead, which sits inside the new track's range whenever the new track is
 * longer — invisible to every duration comparison.
 */
export function positionTooDeepForAFreshTrack(positionMs: number): boolean {
  return positionMs > ANCHOR_MAX_FIRST_POSITION_MS;
}

/**
 * Screens position reports between a track change and the first plausible one.
 * Armed at every track change; disarms as soon as a report passes, so the
 * clock's own slew/snap band owns everything after the anchor.
 *
 * It also disarms on a BUDGET. If every report keeps landing past the track's
 * end, the premise is wrong (a bad duration, or a timeline that never returns
 * to this track's range) and holding the line forever would leave the clock
 * unanchored: lyrics frozen on the first line, with no watchdog to notice —
 * worse than the misplaced timing this guards against.
 *
 * That budget is the reason the 2026-08-13 field case still reached the screen:
 * the guard rejected the previous track's playhead correctly, ran out of
 * budget, and then anchored the clock at 352 s of a 193 s song. The budget was
 * not too small — it was being spent on a question the guard could not answer.
 * "This position looks implausible" is a guess, and a guess has to time out.
 * "This position is the previous track's clock, still ticking" is an
 * identification, and it holds the line without spending anything (up to
 * ANCHOR_GUARD_STALE_HOLD_MS, because even evidence gets stale).
 */
export class AnchorGuard {
  private armed = false;
  private rejected = 0;
  private armedAt = 0;
  /** The last report the clock accepted — the trajectory of the current track. */
  private accepted: { positionMs: number; at: number; rate: number } | null = null;
  /** That trajectory, frozen at the moment the track changed. */
  private outgoing: { positionMs: number; at: number; rate: number } | null = null;

  arm(now: number): void {
    this.armed = true;
    this.rejected = 0;
    this.armedAt = now;
    // Whatever the clock was last told becomes the timeline we can recognise a
    // leak BY. Cleared so the next arm cannot reuse a trajectory two tracks old.
    this.outgoing = this.accepted;
    this.accepted = null;
  }

  /** True when the report must be dropped instead of reaching the clock. */
  rejects(
    positionMs: number,
    durationMs: number | undefined,
    now: number,
    rate = 1,
  ): boolean {
    if (!this.armed) {
      this.accepted = { positionMs, at: now, rate };
      return false;
    }
    const deep = positionTooDeepForAFreshTrack(positionMs);
    if (!positionOvershootsTrack(positionMs, durationMs) && !deep) {
      this.armed = false;
      this.accepted = { positionMs, at: now, rate };
      return false;
    }
    // Two hypotheses, and only their DISAGREEMENT is evidence: the report has
    // to be one the previous track's clock could have produced AND one this
    // track could not. When both can explain it — a skip chain, where the
    // previous track was two seconds in and so is this one — nothing has been
    // shown, and the blind budget below owns the decision as it always did.
    if (deep && this.continuesOutgoing(positionMs, now)) {
      if (now - this.armedAt < ANCHOR_GUARD_STALE_HOLD_MS) return true;
      this.armed = false; // the premise has outlived its plausibility
      this.accepted = { positionMs, at: now, rate };
      return false;
    }
    this.rejected++;
    if (
      this.rejected >= ANCHOR_GUARD_BUDGET_REPORTS ||
      now - this.armedAt >= ANCHOR_GUARD_BUDGET_MS
    ) {
      this.armed = false; // give up rather than leave the clock unanchored
      this.accepted = { positionMs, at: now, rate };
      return false;
    }
    return true;
  }

  /**
   * Could the clock that just stopped have produced this report?
   *
   * The old clock either kept running (advancing by the time since we last
   * heard from it) or was paused (frozen where it was), so the honest test is
   * the whole interval between those two, not a single predicted point.
   */
  private continuesOutgoing(positionMs: number, now: number): boolean {
    const out = this.outgoing;
    if (!out) return false;
    const elapsed = Math.max(0, now - out.at);
    const furthest = out.positionMs + elapsed * (out.rate > 0 ? out.rate : 1);
    return (
      positionMs >= out.positionMs - OUTGOING_TRAJECTORY_TOLERANCE_MS &&
      positionMs <= furthest + OUTGOING_TRAJECTORY_TOLERANCE_MS
    );
  }
}
