import { describe, expect, it } from 'vitest';
import {
  IGNORE_BELOW_MS,
  PositionClock,
  SEEK_SNAP_ABOVE_MS,
  SLEW_DURATION_MS,
  type ClockReportInfo,
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

  it('says whether its 0 means "the start" or "nobody has said yet"', () => {
    // Both read 0 from positionAt, and the renderer draws very different things
    // for them: the first line of the song, or the ♪ that says it does not know.
    const t = makeClock();
    expect(t.clock.anchored).toBe(false);
    expect(t.clock.positionAt(t.now)).toBe(0);

    t.clock.update(report(t, 0, { playing: true }));
    expect(t.clock.anchored).toBe(true);
    expect(t.clock.positionAt(t.now)).toBe(0);

    // A track change forgets the anchor, so the next song starts out unknown
    // again rather than inheriting the last one's certainty.
    t.clock.reset();
    expect(t.clock.anchored).toBe(false);
  });
});

describe('PositionClock report observer (lead telemetry, review E.1)', () => {
  function observed(startEpoch = 1_000_000) {
    let now = 0;
    const seen: ClockReportInfo[] = [];
    const clock = new PositionClock(
      () => now,
      () => startEpoch + now,
      undefined,
      (info) => seen.push(info),
    );
    return {
      clock,
      seen,
      advance(ms: number) {
        now += ms;
      },
      report(position_ms: number, latencyMs = 0, playing = true) {
        return { position_ms, playback_rate: 1, is_playing: playing, captured_at: startEpoch + now - latencyMs };
      },
    };
  }

  it('names what the clock did, with the delta exactly on each threshold', () => {
    const t = observed();
    t.clock.update(t.report(10_000));
    t.advance(1000); // estimate 11 000
    t.clock.update(t.report(11_000 + IGNORE_BELOW_MS - 1)); // 29: ignored
    t.advance(1000); // estimate 12 000
    t.clock.update(t.report(12_000 + IGNORE_BELOW_MS)); // 30: slewed
    t.advance(1000); // the slew has landed: estimate 13 030
    t.clock.update(t.report(13_030 + SEEK_SNAP_ABOVE_MS)); // 1 500: still a slew
    t.advance(1000); // estimate 15 530
    t.clock.update(t.report(15_530 + SEEK_SNAP_ABOVE_MS + 1)); // 1 501: snap
    t.clock.update(t.report(40_000), true); // an explicit seek
    expect(t.seen.map((s) => [s.action, Math.round(s.deltaMs)])).toEqual([
      ['anchor', 0],
      ['deadband', IGNORE_BELOW_MS - 1],
      ['slew', IGNORE_BELOW_MS],
      ['slew', SEEK_SNAP_ABOVE_MS],
      ['snap', SEEK_SNAP_ABOVE_MS + 1],
      ['seek', 0],
    ]);
  });

  it('reports the transport delay unclamped, the way it arrived', () => {
    const t = observed();
    t.clock.update(t.report(10_000, 37));
    t.clock.update(t.report(10_000, -4));
    t.clock.update(t.report(10_000, 9000));
    expect(t.seen.map((s) => s.transportMs)).toEqual([37, -4, 9000]);
  });

  it('classifies a play-state change by the correction it makes', () => {
    const t = observed();
    t.clock.update(t.report(10_000));
    t.advance(500); // estimate 10 500
    t.clock.update(t.report(10_510, 0, false)); // pause, 10 ms off: ignored, frozen at 10 500
    t.clock.update(t.report(10_600, 0, true)); // resume, 100 ms off: slewed
    expect(t.seen.slice(1).map((s) => [s.action, Math.round(s.deltaMs)])).toEqual([
      ['deadband', 10],
      ['slew', 100],
    ]);
  });

  it('is only an observer: positions are identical with and without it', () => {
    // Seeded walk over reports that sit exactly on every threshold.
    let seed = 20261113;
    const random = () => {
      seed = (seed * 1103515245 + 12345) % 2 ** 31;
      return seed / 2 ** 31;
    };
    const plain = makeClock();
    const watched = observed();
    const offsets = [0, 29, 30, 31, 1499, 1500, 1501, -30, -1500, -1501, 4999, 5000, 5001];
    let position = 10_000;
    for (let step = 0; step < 400; step += 1) {
      const dt = Math.floor(random() * 400);
      plain.advance(dt);
      watched.advance(dt);
      position += dt;
      const off = offsets[Math.floor(random() * offsets.length)] ?? 0;
      const latency = offsets[Math.floor(random() * offsets.length)] ?? 0;
      const playing = random() < 0.9;
      const seek = random() < 0.05;
      const base = { position_ms: position + off, playback_rate: 1, is_playing: playing };
      plain.clock.update({ ...base, captured_at: plain.epoch() - latency }, seek);
      watched.clock.update({ ...base, captured_at: 1_000_000 + plain.now - latency }, seek);
      for (const probe of [0, 17, 250]) {
        expect(watched.clock.positionAt(plain.now + probe)).toBe(plain.clock.positionAt(plain.now + probe));
      }
    }
    expect(watched.seen).toHaveLength(400);
  });
});
