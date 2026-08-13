import { describe, expect, it } from 'vitest';
import {
  AnchorGuard,
  ANCHOR_GUARD_BUDGET_MS,
  ANCHOR_GUARD_BUDGET_REPORTS,
  ANCHOR_GUARD_STALE_HOLD_MS,
  positionOvershootsTrack,
} from './position-sanity.js';

describe('positionOvershootsTrack', () => {
  it('accepts a position inside the track', () => {
    expect(positionOvershootsTrack(0, 180_000)).toBe(false);
    expect(positionOvershootsTrack(179_999, 180_000)).toBe(false);
  });

  it('tolerates the rounding band past the end', () => {
    expect(positionOvershootsTrack(182_000, 180_000)).toBe(false);
  });

  it('drops the previous track leaking in under gapless playback', () => {
    expect(positionOvershootsTrack(182_001, 180_000)).toBe(true);
    expect(positionOvershootsTrack(275_000, 180_000)).toBe(true);
  });

  it('never drops when the track carries no duration', () => {
    // The extension omits duration_ms rather than sending a stale one, and a
    // missing duration must not cost the user their clock.
    expect(positionOvershootsTrack(999_999, undefined)).toBe(false);
  });
});

describe('AnchorGuard', () => {
  it('passes everything while disarmed', () => {
    const guard = new AnchorGuard();
    expect(guard.rejects(275_000, 180_000, 0)).toBe(false);
  });

  it('drops the leaked report, then anchors on the next honest one', () => {
    const guard = new AnchorGuard();
    guard.arm(0);
    expect(guard.rejects(275_000, 180_000, 0)).toBe(true);
    expect(guard.rejects(400, 180_000, 250)).toBe(false);
  });

  it('disarms after the first plausible report — a later seek is never screened', () => {
    const guard = new AnchorGuard();
    guard.arm(0);
    expect(guard.rejects(400, 180_000, 0)).toBe(false);
    // Seeking past the reported end (duration under-reported) must still pass:
    // once anchored, outliers belong to the clock's slew/snap band.
    expect(guard.rejects(275_000, 180_000, 5_000)).toBe(false);
  });

  it('re-arms on the next track change', () => {
    const guard = new AnchorGuard();
    guard.arm(0);
    expect(guard.rejects(400, 180_000, 0)).toBe(false);
    guard.arm(1_000);
    expect(guard.rejects(275_000, 180_000, 1_000)).toBe(true);
  });

  it('gives up on the report budget rather than leave the clock unanchored', () => {
    const guard = new AnchorGuard();
    guard.arm(0);
    for (let i = 1; i < ANCHOR_GUARD_BUDGET_REPORTS; i++) {
      expect(guard.rejects(275_000, 180_000, i)).toBe(true);
    }
    expect(guard.rejects(275_000, 180_000, ANCHOR_GUARD_BUDGET_REPORTS)).toBe(false);
    expect(guard.rejects(275_000, 180_000, ANCHOR_GUARD_BUDGET_REPORTS + 1)).toBe(false);
  });

  it('gives up on the time budget when reports are sparse', () => {
    const guard = new AnchorGuard();
    guard.arm(0);
    expect(guard.rejects(275_000, 180_000, 100)).toBe(true);
    expect(guard.rejects(275_000, 180_000, ANCHOR_GUARD_BUDGET_MS)).toBe(false);
  });

  it('screens a deep first position even with no duration to compare against', () => {
    // The 2026-08-13 leak carries a perfectly plausible duration: the finished
    // track's playhead sits INSIDE the new track's range whenever the new
    // track is longer, so every duration comparison waves it through. A first
    // report minutes into a track that just changed is the tell, and it needs
    // no duration at all.
    const guard = new AnchorGuard();
    guard.arm(0);
    expect(guard.rejects(270_910, 299_584, 0)).toBe(true); // the field case
    expect(guard.rejects(999_999, undefined, 0)).toBe(true);
  });

  it('still cannot deadlock — the budget releases it, not the duration', () => {
    // Anti-deadlock was never the unknown-duration exemption; it is the
    // budget. A clock left unanchored freezes on the first line with nothing
    // to notice it, which is worse than a misplaced anchor.
    const guard = new AnchorGuard();
    guard.arm(0);
    expect(guard.rejects(999_999, undefined, 0)).toBe(true);
    expect(guard.rejects(999_999, undefined, ANCHOR_GUARD_BUDGET_MS)).toBe(false);
  });

  it('lets a normal track start through untouched', () => {
    const guard = new AnchorGuard();
    guard.arm(0);
    expect(guard.rejects(640, 234_000, 0)).toBe(false); // a real fresh start
  });
});

describe('AnchorGuard — telling a leak from an implausible guess', () => {
  /** The previous track ticking along, so the guard knows its trajectory. */
  const playing = (guard: AnchorGuard, positionMs: number, at: number) => {
    expect(guard.rejects(positionMs, 366_451, at)).toBe(false);
  };

  it('holds the line past the blind budget when the old clock is still ticking', () => {
    // The field case: 337 s of the previous track, then Hey Mama starts and
    // YTM keeps streaming the OLD timeline for 11.5 s. The budget used to run
    // out after 3 s and anchor the clock at 352 s of a 193 s song.
    const guard = new AnchorGuard();
    playing(guard, 337_000, 0);
    guard.arm(100);
    expect(guard.rejects(337_900, 193_000, 1_000)).toBe(true);
    expect(guard.rejects(341_900, 193_000, 5_000)).toBe(true); // past the old budget
    expect(guard.rejects(348_500, 193_000, 11_500)).toBe(true); // the full field window
    // ...and the first report that actually belongs to the new track anchors.
    expect(guard.rejects(400, 193_000, 11_600)).toBe(false);
  });

  it('recognises the old clock even while it is paused', () => {
    // A paused timeline reports the same number forever; that is still the old
    // clock, not a fresh track that happens to start three minutes in.
    const guard = new AnchorGuard();
    playing(guard, 337_000, 0);
    guard.arm(100);
    expect(guard.rejects(337_000, 193_000, 6_000)).toBe(true);
  });

  it('never locks up on a skip chain — the fresh hypothesis explains it too', () => {
    // Both timelines say "about two seconds in", so nothing has been shown and
    // the report anchors immediately. This is the case that would deadlock a
    // guard which only asked "does this continue the previous track?".
    const guard = new AnchorGuard();
    playing(guard, 2_000, 0);
    guard.arm(100);
    expect(guard.rejects(2_500, 200_000, 1_000)).toBe(false);
  });

  it('still spends budget on a deep report the old clock cannot explain', () => {
    // Implausible, but not identified: this is a guess again, so it times out
    // exactly as it always did rather than holding on evidence it lacks.
    const guard = new AnchorGuard();
    playing(guard, 337_000, 0);
    guard.arm(100);
    expect(guard.rejects(100_000, 193_000, 1_000)).toBe(true);
    expect(guard.rejects(100_000, 193_000, 100 + ANCHOR_GUARD_BUDGET_MS)).toBe(false);
  });

  it('lets go once even the evidence has gone stale', () => {
    const guard = new AnchorGuard();
    playing(guard, 337_000, 0);
    guard.arm(100);
    expect(guard.rejects(337_500, 193_000, 1_000)).toBe(true);
    expect(guard.rejects(337_500, 193_000, 100 + ANCHOR_GUARD_STALE_HOLD_MS)).toBe(false);
  });

  it('does not reuse a trajectory two tracks old', () => {
    // Track A's clock explains nothing about the leak between B and C; carrying
    // it forward would hold reports on the strength of ancient evidence.
    const guard = new AnchorGuard();
    playing(guard, 337_000, 0);
    guard.arm(100); // A -> B
    guard.arm(200); // B -> C, and B never reported anything
    expect(guard.rejects(337_900, 193_000, 1_000)).toBe(true); // still deep: budget
    expect(guard.rejects(337_900, 193_000, 200 + ANCHOR_GUARD_BUDGET_MS)).toBe(false);
  });
});
