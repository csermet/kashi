/**
 * Lyric timing lead, shared by both processes: main stores and labels the
 * user's number, the renderer adds the lead to its frame clock.
 *
 * Lyrics render at player position + BASE_LEAD_MS + the user's offset;
 * positive = earlier (karaoke convention). The base is invisible: the user's
 * number reads 0 at the base and moves ± from there (owner, 2026-11-10: the
 * shipped +200 showed as "+200", and setting "0" put lyrics 200 ms late).
 *
 * Why 200: the owner's ear on every tested machine (overlay 0.3.4,
 * 2026-07-17). Whether it is display-chain latency (YTM's position report +
 * paint) or a perceptual preference is NOT measured — see the faz-5 re-plan
 * note, "The overlay's 200 ms base lead". The audit tools mirror it
 * (apps/server/audit/moment.py BASE_LEAD_MS, guarded by a test).
 */

export const BASE_LEAD_MS = 200;
export const TIMING_OFFSET_MAX_ABS = 500;
/** The user's offset on a fresh install: the base alone. */
export const DEFAULT_TIMING_OFFSET_MS = 0;

/** The user's offset as an integer ms in [-500, 500]; garbage → 0 (the base). */
export function clampTimingOffset(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DEFAULT_TIMING_OFFSET_MS;
  return Math.max(-TIMING_OFFSET_MAX_ABS, Math.min(TIMING_OFFSET_MAX_ABS, Math.round(value)));
}

/** What the frame clock actually adds: the base plus the user's offset. */
export function appliedLeadMs(userOffsetMs: unknown): number {
  return BASE_LEAD_MS + clampTimingOffset(userOffsetMs);
}

/** "0 ms" / "+50 ms" / "−30 ms" (true minus sign) — the user's number only. */
export function formatTimingOffset(offsetMs: number): string {
  if (offsetMs === 0) return '0 ms';
  return offsetMs > 0 ? `+${offsetMs} ms` : `−${-offsetMs} ms`;
}
