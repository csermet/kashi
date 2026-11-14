import { describe, expect, it } from 'vitest';
import { ClockStats, formatClockStats, TRANSPORT_CAP_MS } from './clock-stats.js';

describe('ClockStats', () => {
  it('summarises transport delays and corrections exactly at 1 ms', () => {
    const stats = new ClockStats();
    // 10 reports: transport 1..10 ms; 6 dead-band, 3 slews, 1 snap.
    const deltas: [number, 'deadband' | 'slew' | 'snap'][] = [
      [3, 'deadband'],
      [-5, 'deadband'],
      [12, 'deadband'],
      [-29, 'deadband'],
      [0, 'deadband'],
      [7, 'deadband'],
      [30, 'slew'],
      [-200, 'slew'],
      [1500, 'slew'],
      [-1501, 'snap'],
    ];
    deltas.forEach(([deltaMs, action], i) => stats.add({ transportMs: i + 1, action, deltaMs }));
    stats.add({ transportMs: 4, action: 'seek', deltaMs: 0 });
    stats.add({ transportMs: 4, action: 'anchor', deltaMs: 0 });
    const s = stats.summary();
    expect(s.reports).toBe(12);
    // Transports 1..10 plus 4, 4: sorted 1 2 3 4 4 4 5 6 7 8 9 10.
    expect(s.transport).toEqual({ p50: 4, p90: 9, p99: 10, max: 10, negative: 0, over_cap: 0 });
    // |deadband| 0 3 5 7 12 29.
    expect(s.deadband).toEqual({ n: 6, p50_abs: 5, p90_abs: 29 });
    // |slew| 30 200 1500.
    expect(s.slew).toEqual({ n: 3, p50_abs: 200, p90_abs: 1500, max_abs: 1500 });
    expect(s.snap).toEqual({ n: 1, max_abs: 1501 });
    expect(s.seek).toBe(1);
    expect(s.anchor).toBe(1);
  });

  it('counts reports from the future and replays past the clamp, bounded', () => {
    const stats = new ClockStats();
    stats.add({ transportMs: -3, action: 'deadband', deltaMs: 1 });
    stats.add({ transportMs: TRANSPORT_CAP_MS, action: 'deadband', deltaMs: 1 }); // the clamp itself: not over
    stats.add({ transportMs: TRANSPORT_CAP_MS + 1, action: 'deadband', deltaMs: 1 });
    stats.add({ transportMs: 60_000, action: 'deadband', deltaMs: 1 });
    const s = stats.summary();
    expect(s.transport.negative).toBe(1);
    expect(s.transport.over_cap).toBe(2);
    expect(s.transport.max).toBe(60_000);
    expect(s.transport.p50).toBe(TRANSPORT_CAP_MS); // piled into the top bucket
  });

  it('is empty until a report arrives, and clear() starts over', () => {
    const stats = new ClockStats();
    expect(stats.empty).toBe(true);
    expect(stats.summary().transport.p50).toBeNull();
    const fresh = new ClockStats().summary();
    for (const action of ['anchor', 'seek', 'deadband', 'slew', 'snap'] as const) {
      stats.add({ transportMs: 5, action, deltaMs: 40 });
    }
    stats.add({ transportMs: -1, action: 'snap', deltaMs: 4000 });
    stats.add({ transportMs: 9000, action: 'snap', deltaMs: 4000 });
    expect(stats.empty).toBe(false);
    stats.clear();
    expect(stats.empty).toBe(true);
    expect(stats.summary()).toEqual(fresh);
  });

  it('formats one log line with no identifier in it', () => {
    const stats = new ClockStats();
    stats.add({ transportMs: 6, action: 'anchor', deltaMs: 0 });
    stats.add({ transportMs: 8, action: 'deadband', deltaMs: -12 });
    stats.add({ transportMs: 9, action: 'slew', deltaMs: 45 });
    expect(formatClockStats('track', stats.summary())).toBe(
      'clock stats (track): 3 reports; transport ms p50 8 p90 9 p99 9 max 9; ' +
        'dead-band 1 (|d| p50 12 p90 12), slew 1 (|d| p50 45 p90 45 max 45), snap 0 (max 0), seek 0, anchor 1',
    );
  });
});
