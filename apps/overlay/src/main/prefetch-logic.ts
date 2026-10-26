/**
 * Next-track prefetch (LyricFix round 3), pure decisions only: WHEN to warm the
 * upcoming tracks and WHAT to fetch for each. The extension reads YouTube
 * Music's queue and sends `queue_changed`; `prefetcher.ts` runs the fetches.
 *
 * Budget, by design (kashi-reviewer rule E allows one-ahead, never bulk):
 * - only while the current track is playing, past its first minute (or past
 *   half of a short track), never during an ad;
 * - the NEXT track gets the full chain once (server document, then the mirror
 *   and lrclib.net), so a first listen has lyrics at its first tick;
 * - the one AFTER gets the server document only (cheap, LAN, ETag-cached);
 * - each item is fetched at most once per prediction; no retries.
 */
import { QUEUE_LOOKAHEAD_MAX, type TrackInfo } from '@kashi/protocol';
import { trackKey } from './source-latch-logic.js';

/** Warm the queue once the current track has played this long. */
export const PREFETCH_AFTER_MS = 60_000;
/** Tracks shorter than this start the warm-up at half their length instead. */
export const SHORT_TRACK_MS = 120_000;
/** How long a warmed answer stays usable. */
export const PREFETCH_TTL_MS = 30 * 60_000;
/** Most warmed answers kept at once (two ahead plus a skip or two). */
export const PREFETCH_MAX_ENTRIES = 4;
/**
 * Most prefetches started while one track plays. Bounds queue churn: a user
 * reordering again and again, or a page posting predictions in a loop (the
 * hint is page data; a well-formed forgery cannot be told apart).
 */
export const PREFETCH_BUDGET_PER_TRACK = 4;

export interface PlaybackView {
  enabled: boolean;
  isPlaying: boolean;
  isAd: boolean;
  positionMs: number;
  durationMs: number | undefined;
}

/** Has the current track played long enough for the warm-up to be worth it? */
export function prefetchDue(view: PlaybackView): boolean {
  if (!view.enabled || !view.isPlaying || view.isAd) return false;
  const { durationMs } = view;
  const threshold =
    durationMs !== undefined && durationMs > 0 && durationMs < SHORT_TRACK_MS
      ? durationMs / 2
      : PREFETCH_AFTER_MS;
  return view.positionMs >= threshold;
}

export type PrefetchDepth = 'full' | 'server';

export interface PrefetchJob {
  key: string;
  track: TrackInfo;
  depth: PrefetchDepth;
}

/**
 * The fetches to start for this prediction. `warmDepth(key)` says what is
 * already held for a key (or in flight): a 'full' answer covers both depths,
 * a 'server' one covers only 'server'. With lrclib unavailable (cooldown)
 * the next track is downgraded to its server document.
 */
export function planPrefetch(
  next: readonly TrackInfo[],
  currentKey: string | null,
  warmDepth: (key: string) => PrefetchDepth | null,
  lrclibAvailable: boolean,
): PrefetchJob[] {
  // Slots first: the next distinct tracks that are not the one playing.
  const slots: Array<{ key: string; track: TrackInfo }> = [];
  for (const track of next) {
    const key = trackKey(track);
    if (key === currentKey || slots.some((slot) => slot.key === key)) continue;
    slots.push({ key, track });
    if (slots.length === QUEUE_LOOKAHEAD_MAX) break;
  }
  const jobs: PrefetchJob[] = [];
  slots.forEach(({ key, track }, index) => {
    const wanted: PrefetchDepth = index === 0 && lrclibAvailable ? 'full' : 'server';
    const held = warmDepth(key);
    if (held === 'full' || held === wanted) return;
    jobs.push({ key, track, depth: wanted });
  });
  return jobs;
}

/** A small TTL + size-capped map for warmed answers (oldest evicted first). */
export class PrefetchCache<T> {
  private readonly entries = new Map<string, { value: T; depth: PrefetchDepth; at: number }>();

  constructor(
    private readonly now: () => number = Date.now,
    private readonly ttlMs: number = PREFETCH_TTL_MS,
    private readonly maxEntries: number = PREFETCH_MAX_ENTRIES,
  ) {}

  set(key: string, value: T, depth: PrefetchDepth): void {
    this.entries.delete(key);
    this.entries.set(key, { value, depth, at: this.now() });
    while (this.entries.size > this.maxEntries) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  private live(key: string): { value: T; depth: PrefetchDepth; at: number } | undefined {
    const entry = this.entries.get(key);
    if (entry === undefined) return undefined;
    if (this.now() - entry.at >= this.ttlMs) {
      this.entries.delete(key);
      return undefined;
    }
    return entry;
  }

  get(key: string): T | undefined {
    return this.live(key)?.value;
  }

  depth(key: string): PrefetchDepth | null {
    return this.live(key)?.depth ?? null;
  }

  /** Hand the answer over once: the live lookup consumes it. */
  take(key: string): T | undefined {
    const value = this.get(key);
    this.entries.delete(key);
    return value;
  }

  get size(): number {
    return this.entries.size;
  }
}
