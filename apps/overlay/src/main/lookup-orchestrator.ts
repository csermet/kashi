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
import {
  isRetryable,
  retryDelayMs,
  shouldUpgrade,
  type DisplayedLyrics,
} from './server-retry-logic.js';

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
  /** Optional diagnostics sink (Faz 6.7 P2). Never awaited, never throws —
   * the ladder's behavior must not depend on whether anyone is listening. */
  emit?: (kind: 'lyrics_outcome', payload: Record<string, unknown>) => void;
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
  /** What the current track is showing — the self-heal upgrade rule reads it. */
  private displayed: { key: string; state: DisplayedLyrics } | null = null;

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
        this.deps.emit?.('lyrics_outcome', {
          source: 'kashi-server',
          sync: result.sync,
          quality: result.qualityScore,
        });
        if (result.sync === 'word') {
          this.deps.onServerWordHit?.(key, { type: track.source.type, id: track.source.id });
        }
        this.displayed = {
          key,
          state: { source: 'kashi-server', sync: result.sync, qualityScore: result.qualityScore },
        };
        this.deps.send({ key, ...result });
        return;
      }
      if ('found' in result && !result.found) {
        // Genuinely unprocessed: arm the >=20 s listening gate (R-9), then let
        // the lrclib flow below fill the screen in the meantime.
        this.deps.onServerMiss(key, track);
        this.deps.log(`server 404: ${key} — lrclib fallback + enqueue gate armed`);
      } else {
        this.deps.log(`server error for ${key} — lrclib fallback, probing in the background`);
        this.deps.emit?.('lyrics_outcome', { source: 'server-error' });
        // Do NOT await: the ladder must reach lrclib now. The probe rides the
        // same AbortController, so a track change kills it.
        void this.selfHeal(key, track, abort);
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
        this.deps.emit?.('lyrics_outcome', {
          source: result.found ? 'lrclib' : 'none',
          attempt: attempt + 1,
        });
        this.displayed = {
          key,
          state: { source: result.found ? 'lrclib' : 'none', sync: 'line' },
        };
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

  /**
   * Background probe after a server ERROR (Faz 6.7 P3).
   *
   * A timeout used to cost the whole song: lrclib's plain text stayed up
   * until the next track, because the ladder asks the server exactly once.
   * This asks again on a widening schedule and swaps the document in only if
   * it is genuinely richer — a mid-song re-render has to earn itself.
   */
  private async selfHeal(key: string, track: TrackInfo, abort: AbortController): Promise<void> {
    const getProcessed = this.deps.getProcessed;
    if (!getProcessed) return;
    for (let attempt = 0; ; attempt++) {
      const delay = retryDelayMs(attempt);
      if (delay === null) {
        this.deps.log(`server probe gave up on ${key} after ${attempt} attempts`);
        return;
      }
      await abortableSleep(delay, abort.signal);
      if (abort.signal.aborted || !this.deps.isCurrent(key)) return;

      let result: ServerLyricsResult;
      try {
        result = await getProcessed(track.source.type, track.source.id, abort.signal);
      } catch {
        continue; // the client swallows its own errors; be defensive anyway
      }
      if (abort.signal.aborted || !this.deps.isCurrent(key)) return;

      if (isRetryable(result)) continue;

      if ('found' in result && !result.found) {
        // A 404 is an answer, not a failure: stop probing and arm the gate
        // the first attempt could not (an error never proves "unprocessed").
        this.deps.log(`server probe: ${key} genuinely unprocessed — enqueue gate armed`);
        this.deps.onServerMiss(key, track);
        return;
      }

      const current = this.displayed?.key === key ? this.displayed.state : null;
      if (!current || !shouldUpgrade(current, result)) {
        this.deps.log(`server probe: ${key} recovered but not richer than what is shown`);
        return; // the server answers now; nothing left to heal
      }
      if ('found' in result && result.found) {
        this.deps.log(
          `server probe: upgrading ${key} to sync=${result.sync} on attempt ${attempt + 1}`,
        );
        this.deps.emit?.('lyrics_outcome', {
          source: 'kashi-server',
          sync: result.sync,
          quality: result.qualityScore,
          upgraded: true,
          attempt: attempt + 1,
        });
        if (result.sync === 'word') {
          this.deps.onServerWordHit?.(key, { type: track.source.type, id: track.source.id });
        }
        this.displayed = {
          key,
          state: { source: 'kashi-server', sync: result.sync, qualityScore: result.qualityScore },
        };
        this.deps.send({ key, ...result });
      }
      return;
    }
  }

}
