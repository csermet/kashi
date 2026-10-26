/**
 * Next-track prefetch (LyricFix round 3): warms what the upcoming tracks will
 * need, so a track's lyrics are on screen at its first tick. Decisions live in
 * prefetch-logic.ts; this runs them.
 *
 * Deliberately NOT the live lookup's machinery:
 * - its own AbortController, aborted only on shutdown/settings re-init — an
 *   in-flight prefetch is never cancelled when its track becomes current, so
 *   the lrclib client's in-flight sharing (bound to the first caller's signal)
 *   can never fail the live lookup;
 * - results keyed by video id (`youtube:<id>`): the queue's metadata (no
 *   album, a rounded duration) need not match the later announce, so the
 *   lrclib disk cache alone would often miss;
 * - never arms the enqueue gate (R-9: only listening enqueues), never emits
 *   telemetry, never retries: one pass per item and prediction.
 */
import type { TrackInfo } from '@kashi/protocol';
import type { LookupDeps, LrclibQuery, WarmAnswer } from './lookup-orchestrator.js';
import {
  PREFETCH_BUDGET_PER_TRACK,
  PrefetchCache,
  planPrefetch,
  prefetchDue,
  type PlaybackView,
  type PrefetchDepth,
  type PrefetchJob,
} from './prefetch-logic.js';

type Held = WarmAnswer | { kind: 'none' };

export interface PrefetcherDeps {
  getProcessed: LookupDeps['getProcessed'];
  /** The live lookup's chain (mirror -> lrclib.net), disk caches and cooldowns included. */
  getLyrics: LookupDeps['getLyrics'];
  /** Can a lyrics source answer right now (mirror or lrclib.net not cooling down)? */
  lyricsAvailable: () => boolean;
  log: (line: string) => void;
  now?: () => number;
}

export class Prefetcher {
  private readonly cache: PrefetchCache<Held>;
  private readonly inflight = new Map<string, PrefetchDepth>();
  /** Keys whose track started while their prefetch was still in flight. */
  private readonly claimed = new Set<string>();
  private readonly abort = new AbortController();
  private predicted: TrackInfo[] = [];
  /** Prefetches started while `key` plays (PREFETCH_BUDGET_PER_TRACK). */
  private budget: { key: string | null; used: number; logged: boolean } = {
    key: null,
    used: 0,
    logged: false,
  };

  constructor(private readonly deps: PrefetcherDeps) {
    this.cache = new PrefetchCache<Held>(deps.now ?? Date.now);
  }

  /** The extension's latest prediction of what plays next ([] clears it). */
  onQueue(next: readonly TrackInfo[]): void {
    this.predicted = [...next];
  }

  /** Called on the current track's playback reports; starts what is due. */
  tick(view: PlaybackView, currentKey: string | null): void {
    if (this.abort.signal.aborted || !prefetchDue(view)) return;
    const jobs = planPrefetch(
      this.predicted,
      currentKey,
      // A key with a run in flight counts as fully warm: one run per key at a
      // time (a server-only run moving up waits for its own result first).
      (key) => (this.inflight.has(key) ? 'full' : this.cache.depth(key)),
      this.deps.lyricsAvailable(),
    );
    if (this.budget.key !== currentKey) this.budget = { key: currentKey, used: 0, logged: false };
    for (const job of jobs) {
      if (this.budget.used >= PREFETCH_BUDGET_PER_TRACK) {
        if (!this.budget.logged) {
          this.budget.logged = true;
          this.deps.log(`prefetch budget spent for ${currentKey ?? '-'}; no more this track`);
        }
        break;
      }
      this.budget.used += 1;
      void this.run(job);
    }
  }

  /**
   * Hand the warmed answer for a track that just started to the live lookup,
   * once. A prefetch still in flight is claimed: the live lookup runs its own
   * ladder, and the late result is dropped (never a second answer for a
   * current track). Logs the outcome: that line is the hit-rate measure.
   */
  take(key: string): WarmAnswer | undefined {
    if (this.inflight.has(key)) {
      this.claimed.add(key);
      this.deps.log(`prefetch in-flight for ${key} (the live lookup goes ahead)`);
      return undefined;
    }
    const held = this.cache.take(key);
    this.deps.log(`prefetch ${held?.kind ?? 'cold'} for ${key}`);
    return held === undefined || held.kind === 'none' ? undefined : held;
  }

  /** Shutdown or settings re-init: in-flight prefetches end, nothing new starts. */
  stop(): void {
    this.abort.abort();
  }

  private async run(job: PrefetchJob): Promise<void> {
    const { key, track, depth } = job;
    this.inflight.set(key, depth);
    let held: Held = { kind: 'none' };
    try {
      held = await this.fetch(track, depth);
    } catch (err) {
      if (!this.abort.signal.aborted) this.deps.log(`prefetch ${key} failed: ${String(err)}`);
    } finally {
      this.inflight.delete(key);
    }
    if (this.abort.signal.aborted) return;
    if (this.claimed.delete(key)) {
      this.deps.log(`prefetch ${key} (${depth}): ${held.kind}, arrived after its track started — dropped`);
      return;
    }
    // A server document is final; anything else remembers the depth it was
    // asked at, so this prediction never asks again.
    this.cache.set(key, held, held.kind === 'server' ? 'full' : depth);
    this.deps.log(`prefetch ${key} (${depth}): ${held.kind}`);
  }

  private async fetch(track: TrackInfo, depth: PrefetchDepth): Promise<Held> {
    const signal = this.abort.signal;
    if (this.deps.getProcessed) {
      const result = await this.deps.getProcessed(track.source.type, track.source.id, signal);
      if ('found' in result && result.found) return { kind: 'server', result };
    }
    if (depth === 'server') return { kind: 'none' };
    const query: LrclibQuery = {
      title: track.title,
      artist: track.artist,
      album: track.album,
      duration_ms: track.duration_ms,
    };
    let result = await this.deps.getLyrics(query, signal);
    // The same duration-less second try the live ladder makes: the queue's
    // length is rounded to the second.
    if (!result.found && query.duration_ms) {
      result = await this.deps.getLyrics({ ...query, duration_ms: undefined }, signal);
    }
    return result.found ? { kind: 'lrclib', result } : { kind: 'none' };
  }
}
