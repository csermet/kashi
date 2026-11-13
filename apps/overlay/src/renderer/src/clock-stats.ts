/**
 * Two numbers about the position chain, per listening session (client
 * review step E.1, "measure the 200 ms lead"): how old a position report is
 * when it reaches the renderer (now - captured_at), and how large the
 * corrections the clock then makes are (dead-band hits, slews, snaps). With
 * them, part of the hidden 200 ms base lead answers itself from every listen.
 *
 * Bounded by construction: fixed 1 ms histograms, no samples kept, so a
 * day-long session costs the same few kilobytes as a single song. No
 * identifier of any kind — counts and milliseconds only. The server's
 * telemetry contract cannot carry these yet (unknown kinds and keys are
 * dropped there), so the overlay logs the summary; see formatClockStats.
 */
import {
  IGNORE_BELOW_MS,
  SEEK_SNAP_ABOVE_MS,
  type ClockReportInfo,
} from './position-clock.js';

/** Transport delays at or past this land in the last bucket (the clock clamps at 5 s). */
export const TRANSPORT_CAP_MS = 5000;

/** A 1 ms histogram over [0, cap]; values past cap pile into the top bucket. */
class Histogram {
  private readonly buckets: Uint32Array;
  count = 0;
  max = 0;

  constructor(private readonly cap: number) {
    this.buckets = new Uint32Array(cap + 1);
  }

  add(value: number): void {
    const v = Math.min(this.cap, Math.max(0, Math.round(value)));
    this.buckets[v] = (this.buckets[v] ?? 0) + 1;
    this.count += 1;
    if (value > this.max) this.max = value;
  }

  /** The smallest bucket at or below which a share `q` of the values lie. */
  quantile(q: number): number | null {
    if (this.count === 0) return null;
    const rank = Math.max(1, Math.ceil(q * this.count));
    let seen = 0;
    for (let v = 0; v < this.buckets.length; v += 1) {
      seen += this.buckets[v] ?? 0;
      if (seen >= rank) return v;
    }
    return this.cap;
  }

  clear(): void {
    this.buckets.fill(0);
    this.count = 0;
    this.max = 0;
  }
}

export interface ClockStatsSummary {
  reports: number;
  transport: {
    p50: number | null;
    p90: number | null;
    p99: number | null;
    max: number;
    /** captured_at in the renderer's future (clock skew or rounding). */
    negative: number;
    /** Past the clock's 5 s clamp: replayed snapshots. */
    over_cap: number;
  };
  deadband: { n: number; p50_abs: number | null; p90_abs: number | null };
  slew: { n: number; p50_abs: number | null; p90_abs: number | null; max_abs: number };
  snap: { n: number; max_abs: number };
  seek: number;
  anchor: number;
}

export class ClockStats {
  private reports = 0;
  private readonly transport = new Histogram(TRANSPORT_CAP_MS);
  private negative = 0;
  private overCap = 0;
  private readonly deadband = new Histogram(IGNORE_BELOW_MS);
  private readonly slew = new Histogram(SEEK_SNAP_ABOVE_MS);
  private snaps = 0;
  private snapMax = 0;
  private seeks = 0;
  private anchors = 0;

  add(info: ClockReportInfo): void {
    this.reports += 1;
    if (info.transportMs < 0) this.negative += 1;
    if (info.transportMs > TRANSPORT_CAP_MS) this.overCap += 1;
    this.transport.add(info.transportMs);
    const size = Math.abs(info.deltaMs);
    switch (info.action) {
      case 'deadband':
        this.deadband.add(size);
        break;
      case 'slew':
        this.slew.add(size);
        break;
      case 'snap':
        this.snaps += 1;
        this.snapMax = Math.max(this.snapMax, size);
        break;
      case 'seek':
        this.seeks += 1;
        break;
      case 'anchor':
        this.anchors += 1;
        break;
    }
  }

  get empty(): boolean {
    return this.reports === 0;
  }

  summary(): ClockStatsSummary {
    return {
      reports: this.reports,
      transport: {
        p50: this.transport.quantile(0.5),
        p90: this.transport.quantile(0.9),
        p99: this.transport.quantile(0.99),
        max: Math.round(Math.max(0, this.transport.max)),
        negative: this.negative,
        over_cap: this.overCap,
      },
      deadband: {
        n: this.deadband.count,
        p50_abs: this.deadband.quantile(0.5),
        p90_abs: this.deadband.quantile(0.9),
      },
      slew: {
        n: this.slew.count,
        p50_abs: this.slew.quantile(0.5),
        p90_abs: this.slew.quantile(0.9),
        max_abs: Math.round(this.slew.max),
      },
      snap: { n: this.snaps, max_abs: Math.round(this.snapMax) },
      seek: this.seeks,
      anchor: this.anchors,
    };
  }

  clear(): void {
    this.reports = 0;
    this.transport.clear();
    this.negative = 0;
    this.overCap = 0;
    this.deadband.clear();
    this.slew.clear();
    this.snaps = 0;
    this.snapMax = 0;
    this.seeks = 0;
    this.anchors = 0;
  }
}

const ms = (value: number | null): string => (value === null ? '-' : `${value}`);

/** One log line: `clock stats (<scope>): transport ... | corrections ...`. */
export function formatClockStats(scope: string, s: ClockStatsSummary): string {
  return (
    `clock stats (${scope}): ${s.reports} reports; transport ms p50 ${ms(s.transport.p50)} ` +
    `p90 ${ms(s.transport.p90)} p99 ${ms(s.transport.p99)} max ${s.transport.max}` +
    (s.transport.negative ? ` negative ${s.transport.negative}` : '') +
    (s.transport.over_cap ? ` over-5s ${s.transport.over_cap}` : '') +
    `; dead-band ${s.deadband.n} (|d| p50 ${ms(s.deadband.p50_abs)} p90 ${ms(s.deadband.p90_abs)})` +
    `, slew ${s.slew.n} (|d| p50 ${ms(s.slew.p50_abs)} p90 ${ms(s.slew.p90_abs)} max ${s.slew.max_abs})` +
    `, snap ${s.snap.n} (max ${s.snap.max_abs}), seek ${s.seek}, anchor ${s.anchor}`
  );
}
