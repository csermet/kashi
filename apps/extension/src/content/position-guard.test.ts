import { describe, expect, it } from 'vitest';
import {
  CLAMP_BUDGET_MS,
  CLAMP_BUDGET_REPORTS,
  durationIsAuthoritative,
  PositionClamp,
  positionOvershootsTrack,
  shouldDeferAnnouncePosition,
} from './position-guard.js';

describe('positionOvershootsTrack', () => {
  it('accepts a position inside the track', () => {
    expect(positionOvershootsTrack(90_000, 180_000)).toBe(false);
  });

  it('accepts the tolerance band past the end (duration rounding)', () => {
    expect(positionOvershootsTrack(181_500, 180_000)).toBe(false);
    expect(positionOvershootsTrack(182_000, 180_000)).toBe(false);
  });

  it('drops a position past the end (gapless cumulative offset)', () => {
    expect(positionOvershootsTrack(182_001, 180_000)).toBe(true);
    expect(positionOvershootsTrack(410_000, 180_000)).toBe(true);
  });

  it('never drops when the duration is unknown', () => {
    expect(positionOvershootsTrack(999_999, undefined)).toBe(false);
  });
});

describe('durationIsAuthoritative', () => {
  it('is true once durationchange fired for the current track', () => {
    expect(durationIsAuthoritative(5_000, 4_000)).toBe(true);
    expect(durationIsAuthoritative(4_000, 4_000)).toBe(true);
  });

  it('is false while the duration still belongs to the previous track', () => {
    // The permissive freshDurationMs() would call this "fresh" (< 8 s apart),
    // which is fine for lookup but must NOT arm the position clamp.
    expect(durationIsAuthoritative(1_000, 6_000)).toBe(false);
  });
});

describe('PositionClamp', () => {
  it('passes plausible positions silently', () => {
    const clamp = new PositionClamp();
    expect(clamp.decide(1_000, 180_000, 0)).toEqual({ send: true });
  });

  it('passes everything while the duration is unknown', () => {
    const clamp = new PositionClamp();
    expect(clamp.decide(999_999, undefined, 0)).toEqual({ send: true });
  });

  it('drops the leak, logs once, then reports the recovery', () => {
    const clamp = new PositionClamp();
    const first = clamp.decide(275_000, 180_000, 0);
    expect(first.send).toBe(false);
    expect(first.note).toMatch(/past duration/);

    const second = clamp.decide(275_250, 180_000, 250);
    expect(second.send).toBe(false);
    expect(second.note).toBeUndefined(); // no per-report spam

    const recovered = clamp.decide(500, 180_000, 500);
    expect(recovered.send).toBe(true);
    expect(recovered.note).toMatch(/recovered after 2 dropped/);
  });

  it('releases after the report budget and never suppresses again', () => {
    const clamp = new PositionClamp();
    for (let i = 1; i < CLAMP_BUDGET_REPORTS; i++) {
      expect(clamp.decide(275_000, 180_000, i).send).toBe(false);
    }
    const released = clamp.decide(275_000, 180_000, CLAMP_BUDGET_REPORTS);
    expect(released.send).toBe(true);
    expect(released.note).toMatch(/clamp released/);
    // Released is terminal for this track: a frozen overlay is worse than a
    // misplaced one, so it must not start suppressing again.
    expect(clamp.decide(999_000, 180_000, CLAMP_BUDGET_REPORTS + 1)).toEqual({ send: true });
  });

  it('releases on the time budget even when reports are sparse', () => {
    const clamp = new PositionClamp();
    expect(clamp.decide(275_000, 180_000, 1_000).send).toBe(false);
    const released = clamp.decide(275_000, 180_000, 1_000 + CLAMP_BUDGET_MS);
    expect(released.send).toBe(true);
    expect(released.note).toMatch(/clamp released/);
  });

  it('gives the next track a fresh budget', () => {
    const clamp = new PositionClamp();
    for (let i = 0; i <= CLAMP_BUDGET_REPORTS; i++) clamp.decide(275_000, 180_000, i);
    clamp.reset();
    expect(clamp.decide(275_000, 180_000, 0).send).toBe(false);
  });
});

describe('shouldDeferAnnouncePosition', () => {
  it('defers a mid-session switch with no fresh duration', () => {
    expect(shouldDeferAnnouncePosition(true, undefined)).toBe(true);
  });

  it('sends when the new duration already landed', () => {
    expect(shouldDeferAnnouncePosition(true, 180_000)).toBe(false);
  });

  it('leaves cold start and refresh untouched', () => {
    expect(shouldDeferAnnouncePosition(false, undefined)).toBe(false);
    expect(shouldDeferAnnouncePosition(false, 180_000)).toBe(false);
  });
});
