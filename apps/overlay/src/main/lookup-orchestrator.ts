/**
 * Lyrics lookup ladder, dependency-injected so the whole flow is unit-testable:
 *
 *   server (when configured) → hit: single source of truth, lrclib NEVER
 *   consulted or blended (R-8) → genuine 404: arm the >=20 s enqueue gate and
 *   fall through → error: fall through WITHOUT arming (R-9) →
 *   lrclib with transient-failure retries and a duration-less second try.
 *
 * Staleness is guarded twice: the per-lookup AbortController (a newer track
 * aborts the old lookup) and the isCurrent(key) check on every response.
 */
import type { TrackInfo } from '@kashi/protocol';
import type { ServerLyricsResult } from './kashi-server-logic.js';

export interface LrclibQuery {
  title: string;
  artist: string;
  album?: string;
  duration_ms?: number;
}

export interface LookupDeps {
  /** null = serverless mode (v0.1.11 behavior, byte-for-byte — R-F3-8). */
  getProcessed:
    | ((type: string, id: string, signal: AbortSignal) => Promise<ServerLyricsResult>)
    | null;
  getLyrics: (query: LrclibQuery, signal: AbortSignal) => Promise<{ found: boolean }>;
  /** Emit a kashi:lyrics payload (already carries the track key). */
  send: (payload: { key: string } & Record<string, unknown>) => void;
  /** Genuine server 404 for the CURRENT track — arm the enqueue gate. */
  onServerMiss: (key: string, track: TrackInfo) => void;
  /** Word-sync server hit — the only publishable moment (Faz 5 P6). */
  onServerWordHit?: (key: string, source: { type: string; id: string }) => void;
  isCurrent: (key: string) => boolean;
  log: (line: string) => void;
  /** Retry delays for transient lrclib failures (timeout/network). */
  retryDelaysMs?: number[];
}

const DEFAULT_RETRY_DELAYS_MS = [0, 2000, 6000];

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export class LookupOrchestrator {
  private abort: AbortController | null = null;

  constructor(private readonly deps: LookupDeps) {}

  /** Abort the in-flight lookup (track change, source gone, shutdown). */
  cancel(): void {
    this.abort?.abort();
  }

  async lookup(key: string, track: TrackInfo): Promise<void> {
    this.abort?.abort();
    const abort = new AbortController();
    this.abort = abort;
    const query: LrclibQuery = {
      title: track.title,
      artist: track.artist,
      album: track.album,
      duration_ms: track.duration_ms,
    };

    this.deps.send({ key, searching: true });

    if (this.deps.getProcessed) {
      const result = await this.deps.getProcessed(track.source.type, track.source.id, abort.signal);
      if (abort.signal.aborted || !this.deps.isCurrent(key)) return; // stale (R-9)
      if ('found' in result && result.found) {
        this.deps.log(`server hit: ${key} sync=${result.sync} quality=${result.qualityScore}`);
        if (result.sync === 'word') {
          this.deps.onServerWordHit?.(key, { type: track.source.type, id: track.source.id });
        }
        this.deps.send({ key, ...result });
        return;
      }
      if ('found' in result && !result.found) {
        // Genuinely unprocessed: arm the >=20 s listening gate (R-9), then let
        // the lrclib flow below fill the screen in the meantime.
        this.deps.onServerMiss(key, track);
        this.deps.log(`server 404: ${key} — lrclib fallback + enqueue gate armed`);
      } else {
        this.deps.log(`server error for ${key} — lrclib fallback (gate NOT armed)`);
      }
    }

    // Transient lrclib slowness (per-request 8s timeout) gets a few retries —
    // one hiccup must not mean a whole song without lyrics.
    const delays = this.deps.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
    for (const [attempt, delay] of delays.entries()) {
      await abortableSleep(delay, abort.signal);
      if (abort.signal.aborted) return; // superseded by a newer track
      try {
        let result = await this.deps.getLyrics(query, abort.signal);
        if (!this.deps.isCurrent(key)) return; // stale response guard (R-9)
        if (!result.found && query.duration_ms) {
          // The reported duration can be transiently WRONG during YTM's
          // auto-advance (MSE mid-transition) — a bad duration rejects every
          // candidate, so retry once without it before giving up.
          this.deps.log(
            `duration-scoped lookup missed (duration_ms=${query.duration_ms}), retrying without duration`,
          );
          result = await this.deps.getLyrics({ ...query, duration_ms: undefined }, abort.signal);
          if (!this.deps.isCurrent(key)) return;
        }
        if (!result.found) {
          this.deps.log(
            `no synced lyrics: "${track.artist} - ${track.title}"` +
              ` (duration_ms=${track.duration_ms ?? 'yok'})`,
          );
        }
        this.deps.send({ key, ...result });
        return;
      } catch (err) {
        if (abort.signal.aborted) return;
        this.deps.log(`lyrics lookup failed (attempt ${attempt + 1}/${delays.length}): ${err}`);
      }
    }
    // error !== genuine miss — renderer shows a different message.
    if (this.deps.isCurrent(key)) this.deps.send({ key, found: false, error: true });
  }
}
