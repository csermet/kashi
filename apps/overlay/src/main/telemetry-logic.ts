/**
 * Pure telemetry buffering — no Electron/net imports, so the batching rules
 * are unit-testable and the IO half stays thin (telemetry.ts).
 *
 * Diagnostics are the least important thing this app does, and the buffer is
 * written to prove it: bounded memory, no retry queue, no backpressure onto
 * the caller. Losing events is always preferable to spending a byte of the
 * playback path on them.
 */

export type TelemetryKind =
  | 'session_start'
  | 'track_changed'
  | 'lyrics_outcome'
  | 'position_anomaly'
  | 'error'
  | 'watchdog';

export interface TelemetryEvent {
  /** ISO-8601, stamped when the event happened (not when it is sent). */
  ts: string;
  kind: TelemetryKind;
  payload: Record<string, unknown>;
}

/** Ceiling on unsent events; the oldest are dropped past it. */
export const MAX_BUFFERED_EVENTS = 200;
/** The server rejects a larger batch (its own contract). */
export const MAX_BATCH_EVENTS = 100;
/** Idle cadence: a batch every 10 s is plenty for post-hoc debugging. */
export const FLUSH_INTERVAL_MS = 10_000;
/** Flush early once this many events pile up (a burst deserves a send). */
export const FLUSH_AT_EVENTS = 50;

/**
 * Drop-oldest ring of pending events.
 *
 * Oldest-first is the right sacrifice: the events that matter are the ones
 * near the failure the user is about to report, and those are the newest.
 */
export class TelemetryBuffer {
  private events: TelemetryEvent[] = [];
  private droppedSinceFlush = 0;

  get size(): number {
    return this.events.length;
  }

  /** @returns true when the buffer is full enough to send now. */
  add(event: TelemetryEvent): boolean {
    this.events.push(event);
    if (this.events.length > MAX_BUFFERED_EVENTS) {
      this.events.splice(0, this.events.length - MAX_BUFFERED_EVENTS);
      this.droppedSinceFlush++;
    }
    return this.events.length >= FLUSH_AT_EVENTS;
  }

  /**
   * Removes and returns the next batch. Taken events are NOT kept for a
   * retry: a failed send is a dropped send, so a server outage can never
   * grow into a memory leak or a thundering resend.
   */
  take(limit: number = MAX_BATCH_EVENTS): TelemetryEvent[] {
    return this.events.splice(0, limit);
  }

  /** Count of events lost to the ceiling, reset by reading it. */
  readDropped(): number {
    const dropped = this.droppedSinceFlush;
    this.droppedSinceFlush = 0;
    return dropped;
  }
}

export interface SessionEnvelopeInput {
  appVersion: string;
  extensionVersion: string | null;
  os: string;
  osVersion: string;
  arch: string;
  electron: string;
  chromium: string;
  displayCount: number;
  displaySize: string;
  effectLevel: string;
  themeScope: string;
  fillStyle: string;
  timingOffsetMs: number;
  serverHost: string;
}

/**
 * The once-per-run header. Everything here answers "which machine produced
 * the events that follow" — the question a field report never answers well.
 * Field names mirror the server's allowlist (telemetry_contract.py); a
 * mismatch is silently dropped there, so the two lists move together.
 */
export function sessionEnvelope(input: SessionEnvelopeInput, ts: string): TelemetryEvent {
  return {
    ts,
    kind: 'session_start',
    payload: {
      app_version: input.appVersion,
      extension_version: input.extensionVersion ?? 'none',
      os: input.os,
      os_version: input.osVersion,
      arch: input.arch,
      electron: input.electron,
      chromium: input.chromium,
      display_count: input.displayCount,
      display_size: input.displaySize,
      effect_level: input.effectLevel,
      theme_scope: input.themeScope,
      fill_style: input.fillStyle,
      timing_offset_ms: input.timingOffsetMs,
      server_host: input.serverHost,
    },
  };
}

/** Host only — the path and any credentials in the URL never leave. */
export function serverHostOf(baseUrl: string): string {
  try {
    return new URL(baseUrl).host;
  } catch {
    return 'invalid';
  }
}
