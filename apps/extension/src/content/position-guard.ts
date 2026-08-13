/**
 * Position staleness guards.
 *
 * Duration already has a staleness guard (`freshDurationMs`), position had
 * none. At a mid-session track switch `video.currentTime` can still describe
 * the PREVIOUS track: YouTube Music Premium plays gapless, so the media
 * timeline does not necessarily restart at zero on a track boundary. The
 * overlay accepts the first report after a track change as its clock anchor
 * WITHOUT a delta check, so one bad value misplaces the lyrics for the rest of
 * the song — until a real seek resnaps, or the page is reloaded.
 *
 * Both guards are pure/testable so the content script keeps one decision
 * surface. Neither ever fabricates a position: they only withhold a report,
 * and the next `timeupdate` (~250 ms later) carries the real value.
 */

/**
 * How far past the track's end a position may sit before it cannot be real.
 * Covers rounding between the reported duration and the decoded stream.
 */
export const POSITION_OVERSHOOT_TOLERANCE_MS = 2000;

/**
 * The clamp gives up after this many suppressed reports, or this long — see
 * `PositionClamp` for why giving up is mandatory.
 */
export const CLAMP_BUDGET_REPORTS = 12;
export const CLAMP_BUDGET_MS = 3000;

/**
 * Guard 1 — sanity clamp. A position beyond the end of the track it claims to
 * belong to is impossible, so it describes some other timeline (the gapless
 * cumulative offset being the leading candidate).
 *
 * `durationMs` MUST come from a `durationchange` that fired for the CURRENT
 * track — see `durationIsAuthoritative`. A stale duration would make this drop
 * legitimate positions of a longer track.
 */
export function positionOvershootsTrack(
  positionMs: number,
  durationMs: number | undefined,
): boolean {
  if (durationMs === undefined) return false;
  return positionMs > durationMs + POSITION_OVERSHOOT_TOLERANCE_MS;
}

/**
 * True only when `durationchange` has fired since the current videoId
 * appeared, i.e. the duration describes THIS track rather than the previous
 * one. `freshDurationMs()` is deliberately more permissive (it also accepts a
 * duration from just before the id change, which is right for lyrics lookup
 * but not safe enough to drop position reports on).
 */
export function durationIsAuthoritative(
  lastDurationChangeAt: number,
  videoIdChangedAt: number,
): boolean {
  return lastDurationChangeAt >= videoIdChangedAt;
}

/**
 * A track that just changed cannot already be this deep. Same threshold and
 * same reasoning as the overlay's AnchorGuard — applied here so the stale
 * playhead never leaves the page in the first place.
 */
export const FRESH_TRACK_MAX_POSITION_MS = 15_000;
/**
 * How long we are willing to sit on deep reports waiting for the duration to
 * land. The field case took 11.5 s (YTM rebuilt the <video> element), so a
 * shorter budget would release the stale playhead just before the truth
 * arrived. The renderer's starvation watchdog is 60 s, so this can never cost
 * the user their clock — and SHALLOW reports flow the whole time anyway.
 */
export const DURATION_LANDING_BUDGET_MS = 12_000;

/**
 * Guard 3 (field bug, Caner 2026-08-13 — Hey Mama): hold a report that is
 * implausibly deep for a track that JUST changed, while its duration has not
 * landed yet.
 *
 * The 0.1.13 fix deferred the position carried BY the announce, but YTM keeps
 * streaming `timeupdate` reports from the old timeline, and those were still
 * going out. Downstream the overlay's AnchorGuard rejected them correctly —
 * then its anti-deadlock budget released one anyway and the clock anchored
 * 352 s into a 193 s song, showed one line, and fell to the interlude mark
 * until a reload.
 *
 * Deliberately narrow: it needs no duration (the duration is exactly what is
 * missing), it only fires in the window where the page is known to be
 * self-contradictory, and a real fresh position (0-15 s) is never touched.
 */
export function shouldHoldStalePlayhead(
  durationAuthoritative: boolean,
  positionMs: number,
  msSinceVideoIdChanged: number,
  maxPositionMs: number = FRESH_TRACK_MAX_POSITION_MS,
  budgetMs: number = DURATION_LANDING_BUDGET_MS,
): boolean {
  if (durationAuthoritative) return false; // the duration describes THIS track
  if (msSinceVideoIdChanged > budgetMs) return false; // give up, never starve
  return positionMs > maxPositionMs;
}

/** `note`, when present, is a line worth logging; `send` is the decision. */
export interface ClampDecision {
  send: boolean;
  note?: string;
}

/**
 * Stateful half of guard 1: suppresses implausible reports, but only on a
 * BUDGET.
 *
 * The budget is not politeness, it is the safety property. The root-cause
 * hypothesis (gapless cumulative offset) is mechanism-level unverified, and
 * one of its readings is that `currentTime` never returns to this track's
 * range at all. An unbounded clamp would then suppress every report for the
 * whole song: the overlay's clock would never anchor, lyrics would sit frozen
 * on the first line, and the data-loss watchdog could not even complain
 * (it only fires while the clock is playing). That is a worse failure than the
 * bug being fixed. So when suppression stops looking like a boundary glitch,
 * the clamp releases, says so loudly, and the user is back to today's
 * behavior — lyrics that run, possibly at the wrong offset.
 */
export class PositionClamp {
  private dropped = 0;
  private firstDropAt = 0;
  private released = false;

  /** Called whenever the videoId changes: a fresh track gets a fresh budget. */
  reset(): void {
    this.dropped = 0;
    this.firstDropAt = 0;
    this.released = false;
  }

  decide(
    positionMs: number,
    durationMs: number | undefined,
    now: number,
  ): ClampDecision {
    if (this.released) return { send: true };

    if (!positionOvershootsTrack(positionMs, durationMs)) {
      if (this.dropped === 0) return { send: true };
      const note = `position recovered after ${this.dropped} dropped report(s)`;
      this.reset();
      return { send: true, note };
    }

    this.dropped++;
    if (this.dropped === 1) this.firstDropAt = now;

    const exhausted =
      this.dropped >= CLAMP_BUDGET_REPORTS ||
      now - this.firstDropAt >= CLAMP_BUDGET_MS;
    if (exhausted) {
      this.released = true;
      return {
        send: true,
        note:
          `position ${positionMs}ms still past duration ${durationMs}ms after` +
          ` ${this.dropped} reports — clamp released, timing may be off for this track`,
      };
    }

    return {
      send: false,
      note:
        this.dropped === 1
          ? `position ${positionMs}ms is past duration ${durationMs}ms — dropping (gapless offset?)`
          : undefined,
    };
  }
}

/**
 * Guard 2 — defer the announce-accompanying position. That single report is
 * the one that becomes the overlay's anchor, and at the announce instant the
 * new source's metadata has usually not landed yet (no fresh duration), so
 * currentTime cannot be trusted.
 *
 * Cold start and refresh (`wasMidSession === false`) are exempt: there is no
 * previous track to leak from, and that path is what makes "a refresh fixes
 * it" work today. Keeping it byte-for-byte preserves the known-good recovery.
 *
 * A deferred report is not lost: the caller flushes one as soon as
 * `durationchange` lands, which also covers a track changed while PAUSED
 * (no `timeupdate` would ever arrive to replace it).
 *
 * The fresh duration used to exempt a mid-session change too — "metadata has
 * landed, so currentTime can be trusted". Field-diagnosed as wrong on
 * 2026-08-13, with the arithmetic to prove it: on AUTO-ADVANCE, YTM keeps the
 * same <video> and its timeline does not reset, so `currentTime` still reads
 * the PREVIOUS track's end (270.91 s — exactly the finished track's duration)
 * while the new duration is already correct. Metadata landing says nothing
 * about the playhead. That report anchored the clock 4.5 minutes into a song
 * that had just started, past its last line, and the overlay showed a
 * blinking interlude for two and a half minutes until a reload.
 *
 * Every guard downstream compares position against DURATION, so this only
 * escapes when the finished track is SHORTER than the new one — which is why
 * it read as "happens a lot, but not always".
 */
export function shouldDeferAnnouncePosition(wasMidSession: boolean): boolean {
  return wasMidSession;
}
