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
 * Coverage is PARTIAL by construction: it can only recognize a leaked
 * position that lands past the new track's end, so a short track leaking into
 * a longer one still slips through. The extension's own guard (0.1.12+) is
 * the real fix; this is the floor for users still on an older build.
 */

/** Matches the extension's tolerance — duration rounding, not a real seek. */
export const POSITION_OVERSHOOT_TOLERANCE_MS = 2000;

/** The guard gives up after this many rejects, or this long. */
export const ANCHOR_GUARD_BUDGET_REPORTS = 12;
export const ANCHOR_GUARD_BUDGET_MS = 3000;

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
 * Screens position reports between a track change and the first plausible one.
 * Armed at every track change; disarms as soon as a report passes, so the
 * clock's own slew/snap band owns everything after the anchor.
 *
 * It also disarms on a BUDGET. If every report keeps landing past the track's
 * end, the premise is wrong (a bad duration, or a timeline that never returns
 * to this track's range) and holding the line forever would leave the clock
 * unanchored: lyrics frozen on the first line, with no watchdog to notice —
 * worse than the misplaced timing this guards against.
 */
export class AnchorGuard {
  private armed = false;
  private rejected = 0;
  private armedAt = 0;

  arm(now: number): void {
    this.armed = true;
    this.rejected = 0;
    this.armedAt = now;
  }

  /** True when the report must be dropped instead of reaching the clock. */
  rejects(positionMs: number, durationMs: number | undefined, now: number): boolean {
    if (!this.armed) return false;
    if (!positionOvershootsTrack(positionMs, durationMs)) {
      this.armed = false;
      return false;
    }
    this.rejected++;
    if (
      this.rejected >= ANCHOR_GUARD_BUDGET_REPORTS ||
      now - this.armedAt >= ANCHOR_GUARD_BUDGET_MS
    ) {
      this.armed = false; // give up rather than leave the clock unanchored
      return false;
    }
    return true;
  }
}
