/**
 * Telemetry transport (overlay main process).
 *
 * Constructed ONLY when a server URL, an API key and the user's consent are
 * all present — with any of them missing the class never exists and the code
 * path stays byte-for-byte the serverless behavior, exactly like
 * KashiServerClient. Diagnostics may never change what the app does.
 *
 * Every send is fire-and-forget with one attempt: no retries, no queue that
 * survives a failure, no promise the caller has to await. If the server is
 * down, the events are gone and playback never noticed.
 */
import {
  FLUSH_INTERVAL_MS,
  TelemetryBuffer,
  type TelemetryEvent,
  type TelemetryKind,
} from './telemetry-logic.js';

const REQUEST_TIMEOUT_MS = 5_000;

export interface TelemetryClientOptions {
  baseUrl: string; // already normalized
  apiKey: string;
  sessionId: string;
  fetchFn: typeof fetch;
  log?: (line: string) => void;
}

export class TelemetryClient {
  private readonly buffer = new TelemetryBuffer();
  private readonly log: (line: string) => void;
  private timer: NodeJS.Timeout | null = null;
  private sending = false;

  constructor(private readonly opts: TelemetryClientOptions) {
    this.log = opts.log ?? (() => {});
  }

  /** Begins the idle flush cadence. Safe to call twice. */
  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => void this.flush(), FLUSH_INTERVAL_MS);
    // Diagnostics must not be the reason the process stays alive.
    this.timer.unref?.();
  }

  /**
   * Queues one event. Never throws, never awaits — call it from anywhere,
   * including an error handler. NOT for the render loop: events are edges
   * (a track changed, a position looked impossible), not frames.
   */
  record(kind: TelemetryKind, payload: Record<string, unknown>, ts = new Date()): void {
    const full = this.buffer.add({ ts: ts.toISOString(), kind, payload });
    if (full) void this.flush();
  }

  /** Sends one batch. Overlapping calls collapse into the in-flight one. */
  async flush(): Promise<void> {
    if (this.sending) return;
    const batch = this.buffer.take();
    if (batch.length === 0) return;
    const dropped = this.buffer.readDropped();
    if (dropped) this.log(`telemetry: buffer overflow dropped ${dropped} event(s)`);
    this.sending = true;
    try {
      await this.post(batch);
    } finally {
      this.sending = false;
    }
  }

  private async post(events: TelemetryEvent[]): Promise<void> {
    try {
      const response = await this.opts.fetchFn(`${this.opts.baseUrl}/v1/telemetry`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${this.opts.apiKey}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify({ session_id: this.opts.sessionId, events }),
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
      if (response.status !== 202) {
        // Logged once per batch, not per event: a disabled or older server
        // must not turn into a wall of noise in the user's terminal.
        this.log(`telemetry: ${events.length} event(s) refused (HTTP ${response.status})`);
      }
    } catch (err) {
      this.log(`telemetry: ${events.length} event(s) lost (${String(err).slice(0, 100)})`);
    }
  }

  /** Final flush on quit or a settings change that tears this client down. */
  async stop(): Promise<void> {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    await this.flush();
  }
}
