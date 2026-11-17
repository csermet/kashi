import { describe, expect, it } from 'vitest';
// The server's own allowlist, read from its source so the two cannot drift.
import contract from '../../../apps/server/src/kashi_server/telemetry_contract.py?raw';
import {
  CLOCK_STATS_FIELDS,
  ClockStats,
  clockStatsFields,
  formatClockStats,
  TRANSPORT_CAP_MS,
} from './clock-stats.js';

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

/** The names inside `"clock_stats": frozenset({ ... })` of telemetry_contract.py. */
function serverClockStatsFields(): Set<string> {
  const block = /"clock_stats":\s*frozenset\(\s*\{([^}]*)\}/.exec(contract);
  if (!block) throw new Error('clock_stats allowlist not found in telemetry_contract.py');
  return new Set([...(block[1] ?? '').matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1] ?? ''));
}

describe('clockStatsFields (telemetry kind clock_stats)', () => {
  it('names exactly the fields the server allows', () => {
    const server = serverClockStatsFields();
    expect(server.size).toBeGreaterThan(10);
    expect(new Set(CLOCK_STATS_FIELDS)).toEqual(server);
  });

  it('sends only allowed, flat keys, under the server names', () => {
    const stats = new ClockStats();
    stats.add({ transportMs: 6, action: 'anchor', deltaMs: 0 });
    stats.add({ transportMs: 8, action: 'deadband', deltaMs: -12 });
    stats.add({ transportMs: 9, action: 'slew', deltaMs: 45 });
    stats.add({ transportMs: -2, action: 'snap', deltaMs: 2400 });
    stats.add({ transportMs: 7000, action: 'seek', deltaMs: 0 });
    const fields = clockStatsFields('track', stats.summary());
    const server = serverClockStatsFields();
    for (const key of Object.keys(fields)) expect(server.has(key), key).toBe(true);
    expect(fields).toEqual({
      scope: 'track',
      reports: 5,
      transport_p50_ms: 8,
      transport_p90_ms: 5000,
      transport_p99_ms: 5000,
      transport_max_ms: 7000,
      transport_negative: 1,
      transport_over_cap: 1,
      deadband_n: 1,
      deadband_p50_ms: 12,
      deadband_p90_ms: 12,
      slew_n: 1,
      slew_p50_ms: 45,
      slew_p90_ms: 45,
      slew_max_ms: 45,
      snap_n: 1,
      snap_max_ms: 2400,
      seek_n: 1,
      anchor_n: 1,
    });
    for (const value of Object.values(fields)) expect(typeof value === 'object').toBe(false);
  });

  it('leaves a quantile with no value out instead of sending null', () => {
    const stats = new ClockStats();
    stats.add({ transportMs: 5, action: 'seek', deltaMs: 0 });
    const fields = clockStatsFields('session', stats.summary());
    expect(fields.scope).toBe('session');
    expect(fields.transport_p50_ms).toBe(5);
    for (const absent of ['deadband_p50_ms', 'deadband_p90_ms', 'slew_p50_ms', 'slew_p90_ms']) {
      expect(absent in fields, absent).toBe(false);
    }
    expect(fields.deadband_n).toBe(0);
    expect(Object.values(fields).some((v) => v === null || v === undefined)).toBe(false);
  });
});
