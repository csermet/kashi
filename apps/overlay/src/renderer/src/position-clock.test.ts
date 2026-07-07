import { describe, expect, it } from 'vitest';
import {
  IGNORE_BELOW_MS,
  PositionClock,
  SEEK_SNAP_ABOVE_MS,
  SLEW_DURATION_MS,
} from './position-clock.js';

/** Deterministic clock pair: mono and epoch advance together. */
function makeClock(startEpoch = 1_000_000) {
  let now = 0;
  const clock = new PositionClock(
    () => now,
    () => startEpoch + now,
  );
  return {
    clock,
    advance(ms: number) {
      now += ms;
    },
    get now() {
      return now;
    },
    epoch(atMono = now) {
      return startEpoch + atMono;
    },
  };
}

function report(
  t: ReturnType<typeof makeClock>,
  position_ms: number,
  opts: Partial<{ rate: number; playing: boolean; latencyMs: number }> = {},
) {
  const { rate = 1, playing = true, latencyMs = 0 } = opts;
  return {
    position_ms,
    playback_rate: rate,
    is_playing: playing,
    captured_at: t.epoch() - latencyMs,
  };
}

describe('PositionClock', () => {
  it('extrapolates linearly while playing', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000));
    t.advance(1000);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(11_000, 0);
  });

  it('respects playback rate', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000, { rate: 1.25 }));
    t.advance(1000);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(11_250, 0);
  });

  it('freezes while paused', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000, { playing: false }));
    t.advance(5000);
    expect(t.clock.positionAt(t.now)).toBe(10_000);
  });

  it('compensates report latency while playing', () => {
    const t = makeClock();
    // Captured 100 ms ago → media has advanced 100 ms by arrival.
    t.clock.update(report(t, 10_000, { latencyMs: 100 }));
    expect(t.clock.positionAt(t.now)).toBeCloseTo(10_100, 0);
  });

  it('does not latency-compensate when paused', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000, { playing: false, latencyMs: 500 }));
    expect(t.clock.positionAt(t.now)).toBe(10_000);
  });

  it('ignores jitter below the threshold', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000));
    t.advance(250);
    t.clock.update(report(t, 10_250 + IGNORE_BELOW_MS - 5));
    // Anchor unchanged → still pure extrapolation.
    expect(t.clock.positionAt(t.now)).toBeCloseTo(10_250, 0);
  });

  it('slews medium drift smoothly (no jump, correct endpoint)', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000));
    t.advance(250);
    const before = t.clock.positionAt(t.now);
    t.clock.update(report(t, before + 200)); // +200 ms drift → slew
    // Continuity: immediately after the update nothing jumped.
    expect(t.clock.positionAt(t.now)).toBeCloseTo(before, 0);
    // Halfway through the slew, half the correction is applied.
    t.advance(SLEW_DURATION_MS / 2);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(
      before + SLEW_DURATION_MS / 2 + 100,
      0,
    );
    // After the slew completes the full correction is in.
    t.advance(SLEW_DURATION_MS / 2);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(before + SLEW_DURATION_MS + 200, 0);
  });

  it('slews (not snaps) drift in the 300-1500 ms band', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000));
    t.advance(250);
    const before = t.clock.positionAt(t.now);
    t.clock.update(report(t, before + 800)); // buffering stall, not a seek
    expect(t.clock.positionAt(t.now)).toBeCloseTo(before, 0); // no jump
    t.advance(SLEW_DURATION_MS);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(before + SLEW_DURATION_MS + 800, 0);
  });

  it('snaps on drift beyond the seek threshold', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000));
    t.advance(100);
    t.clock.update(report(t, 10_100 + SEEK_SNAP_ABOVE_MS + 500)); // real seek
    expect(t.clock.positionAt(t.now)).toBeCloseTo(10_100 + SEEK_SNAP_ABOVE_MS + 500, 0);
  });

  it('keeps the current rate when a report omits playback_rate', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000, { rate: 2 }));
    t.advance(500); // estimate ≈ 11_000 at 2×
    // seek/playback_state messages carry no rate — must not reset to 1×.
    t.clock.update(
      { position_ms: 11_000, is_playing: true, captured_at: t.epoch() },
      true,
    );
    t.advance(1000);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(13_000, 0); // still 2×
  });

  it('snaps on explicit seek even for small deltas', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000));
    t.advance(100);
    t.clock.update(report(t, 10_000), true);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(10_000, 0);
  });

  it('pause transition freezes without jumping', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000));
    t.advance(500);
    const before = t.clock.positionAt(t.now);
    t.clock.update(report(t, before, { playing: false }));
    t.advance(3000);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(before, 0);
    expect(t.clock.isPlaying).toBe(false);
  });

  it('resume transition continues from the reported position', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000, { playing: false }));
    t.advance(2000);
    t.clock.update(report(t, 10_000));
    t.advance(1000);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(11_000, 0);
  });

  it('a fresh report supersedes an in-flight slew continuously', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000));
    t.advance(250);
    const e1 = t.clock.positionAt(t.now);
    t.clock.update(report(t, e1 + 200)); // start slew #1
    t.advance(SLEW_DURATION_MS / 2); // slew #1 half done
    const e2 = t.clock.positionAt(t.now);
    t.clock.update(report(t, e2 + 150)); // slew #2 replaces remainder
    expect(t.clock.positionAt(t.now)).toBeCloseTo(e2, 0); // no jump
    t.advance(SLEW_DURATION_MS);
    expect(t.clock.positionAt(t.now)).toBeCloseTo(e2 + SLEW_DURATION_MS + 150, 0);
  });

  it('reset() forgets the anchor', () => {
    const t = makeClock();
    t.clock.update(report(t, 10_000));
    t.clock.reset();
    expect(t.clock.positionAt(t.now)).toBe(0);
    expect(t.clock.isPlaying).toBe(false);
  });

  it('never returns a negative position', () => {
    const t = makeClock();
    t.clock.update(report(t, 0, { playing: false }));
    expect(t.clock.positionAt(t.now)).toBe(0);
  });
});
