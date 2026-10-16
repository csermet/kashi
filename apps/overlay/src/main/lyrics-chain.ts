/**
 * Where plain synced lyrics come from on first listen (LyricFix, 2026-10):
 *
 *   mirror (optional, an lrclib-compatible copy on the user's own network)
 *     → hit: answer
 *     → miss / unreachable / cooling down / unconfigured: lrclib.net
 *
 * The mirror is a periodically refreshed SUBSET of lrclib (synced records
 * only, as of its last dump), so its miss is never the final word: lrclib.net
 * may have a record added since. The two clients keep separate disk caches for
 * the same reason — a mirror miss must not become a seven-day lrclib.net
 * negative.
 *
 * Each client carries its own SourceCooldown (source-cooldown.ts), applied
 * between its disk cache and the network. lrclib.net's is what keeps a skip
 * chain during an outage from multiplying requests: while it runs the client
 * fails fast with the remaining wait, and the caller schedules its retry no
 * earlier than that. The mirror's only stops an unreachable mirror (the laptop
 * away from home) from charging every lookup its timeout.
 */
import type { LyricsResult, TrackQuery } from './lrclib.js';

export interface LyricsSource {
  getLyrics(query: TrackQuery, signal?: AbortSignal): Promise<LyricsResult>;
}

export type LyricsVia = 'mirror' | 'lrclib';

export type ChainResult = LyricsResult & { via: LyricsVia };

export interface LyricsChainOptions {
  mirror: LyricsSource | null;
  lrclib: LyricsSource;
  log: (line: string) => void;
}

export class LyricsChain {
  constructor(private readonly opts: LyricsChainOptions) {}

  async getLyrics(query: TrackQuery, signal?: AbortSignal): Promise<ChainResult> {
    const fromMirror = await this.tryMirror(query, signal);
    if (fromMirror) return fromMirror;
    return { ...(await this.opts.lrclib.getLyrics(query, signal)), via: 'lrclib' };
  }

  /** A mirror hit, or null to fall through (miss, failure, cooldown, none). */
  private async tryMirror(query: TrackQuery, signal?: AbortSignal): Promise<ChainResult | null> {
    const { mirror, log } = this.opts;
    if (!mirror) return null;
    try {
      const result = await mirror.getLyrics(query, signal);
      return result.found ? { ...result, via: 'mirror' } : null;
    } catch (err) {
      if (signal?.aborted) throw err;
      log(`mirror unavailable, falling through to lrclib: ${String(err)}`);
      return null;
    }
  }
}
