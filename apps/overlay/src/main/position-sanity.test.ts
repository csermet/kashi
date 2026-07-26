import { describe, expect, it } from 'vitest';
import {
  AnchorGuard,
  ANCHOR_GUARD_BUDGET_MS,
  ANCHOR_GUARD_BUDGET_REPORTS,
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

  it('cannot deadlock a track whose duration is unknown', () => {
    const guard = new AnchorGuard();
    guard.arm(0);
    expect(guard.rejects(999_999, undefined, 0)).toBe(false);
  });
});
