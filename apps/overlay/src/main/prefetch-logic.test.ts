import { describe, expect, it } from 'vitest';
import type { TrackInfo } from '@kashi/protocol';
import {
  PREFETCH_AFTER_MS,
  PREFETCH_MAX_ENTRIES,
  PREFETCH_TTL_MS,
  PrefetchCache,
  SHORT_TRACK_MS,
  planPrefetch,
  prefetchDue,
  type PlaybackView,
  type PrefetchDepth,
} from './prefetch-logic.js';

const view = (over: Partial<PlaybackView> = {}): PlaybackView => ({
  enabled: true,
  isPlaying: true,
  isAd: false,
  positionMs: PREFETCH_AFTER_MS,
  durationMs: 200_000,
  ...over,
});

const track = (id: string): TrackInfo => ({
  source: { type: 'youtube', id },
  title: `Song ${id}`,
  artist: 'Artist',
  duration_ms: 200_000,
});

const nothingWarm = (): PrefetchDepth | null => null;

describe('prefetchDue', () => {
  it('starts exactly at the first minute, not before', () => {
    expect(prefetchDue(view({ positionMs: PREFETCH_AFTER_MS - 1 }))).toBe(false);
    expect(prefetchDue(view({ positionMs: PREFETCH_AFTER_MS }))).toBe(true);
  });

  it('starts at half of a short track', () => {
    const durationMs = SHORT_TRACK_MS - 2000; // 118 s
    expect(prefetchDue(view({ durationMs, positionMs: durationMs / 2 - 1 }))).toBe(false);
    expect(prefetchDue(view({ durationMs, positionMs: durationMs / 2 }))).toBe(true);
  });

  it('uses the minute rule when the duration is unknown', () => {
    expect(prefetchDue(view({ durationMs: undefined, positionMs: 30_000 }))).toBe(false);
    expect(prefetchDue(view({ durationMs: 0, positionMs: 30_000 }))).toBe(false);
  });

  it('never runs while paused, during an ad, or when switched off', () => {
    expect(prefetchDue(view({ isPlaying: false }))).toBe(false);
    expect(prefetchDue(view({ isAd: true }))).toBe(false);
    expect(prefetchDue(view({ enabled: false }))).toBe(false);
  });
});

describe('planPrefetch', () => {
  it('fetches the full chain for the next track and the server document for the one after', () => {
    const jobs = planPrefetch([track('a'), track('b')], 'youtube:cur', nothingWarm, true);
    expect(jobs.map((j) => [j.key, j.depth])).toEqual([
      ['youtube:a', 'full'],
      ['youtube:b', 'server'],
    ]);
  });

  it('never looks further than two tracks ahead', () => {
    const jobs = planPrefetch([track('a'), track('b'), track('c')], null, nothingWarm, true);
    expect(jobs.map((j) => j.key)).toEqual(['youtube:a', 'youtube:b']);
  });

  it('skips the track that is playing and duplicates', () => {
    const jobs = planPrefetch(
      [track('cur'), track('a'), track('a'), track('b')],
      'youtube:cur',
      nothingWarm,
      true,
    );
    expect(jobs.map((j) => [j.key, j.depth])).toEqual([
      ['youtube:a', 'full'],
      ['youtube:b', 'server'],
    ]);
  });

  it('downgrades the next track to its server document while lrclib cools down', () => {
    const jobs = planPrefetch([track('a'), track('b')], null, nothingWarm, false);
    expect(jobs.map((j) => j.depth)).toEqual(['server', 'server']);
  });

  it('does not fetch what is already warm, but upgrades a server-only answer', () => {
    const warm = (key: string): PrefetchDepth | null =>
      key === 'youtube:a' ? 'full' : key === 'youtube:b' ? 'server' : null;
    expect(planPrefetch([track('a'), track('b')], null, warm, true)).toEqual([]);
    // b becomes the NEXT track: its server-only answer is not enough any more.
    const jobs = planPrefetch([track('b'), track('c')], null, warm, true);
    expect(jobs.map((j) => [j.key, j.depth])).toEqual([
      ['youtube:b', 'full'],
      ['youtube:c', 'server'],
    ]);
  });

  it('keeps the second slot server-only even when the next track is already warm', () => {
    const warm = (key: string): PrefetchDepth | null => (key === 'youtube:a' ? 'full' : null);
    const jobs = planPrefetch([track('a'), track('b')], null, warm, true);
    expect(jobs.map((j) => [j.key, j.depth])).toEqual([['youtube:b', 'server']]);
  });

  it('plans nothing for an empty prediction', () => {
    expect(planPrefetch([], 'youtube:cur', nothingWarm, true)).toEqual([]);
  });
});

describe('PrefetchCache', () => {
  it('forgets an answer exactly at the TTL', () => {
    let now = 0;
    const cache = new PrefetchCache<string>(() => now);
    cache.set('k', 'v', 'full');
    now = PREFETCH_TTL_MS - 1;
    expect(cache.get('k')).toBe('v');
    expect(cache.depth('k')).toBe('full');
    now = PREFETCH_TTL_MS;
    expect(cache.get('k')).toBeUndefined();
    expect(cache.depth('k')).toBeNull();
  });

  it('evicts the oldest entry past the size cap', () => {
    const cache = new PrefetchCache<number>(() => 0);
    for (let i = 0; i <= PREFETCH_MAX_ENTRIES; i += 1) cache.set(`k${i}`, i, 'server');
    expect(cache.size).toBe(PREFETCH_MAX_ENTRIES);
    expect(cache.get('k0')).toBeUndefined();
    expect(cache.get(`k${PREFETCH_MAX_ENTRIES}`)).toBe(PREFETCH_MAX_ENTRIES);
  });

  it('refreshes an entry that is set again (and its depth)', () => {
    let now = 0;
    const cache = new PrefetchCache<string>(() => now);
    cache.set('k', 'server-doc', 'server');
    now = PREFETCH_TTL_MS - 1;
    cache.set('k', 'full-answer', 'full');
    now = PREFETCH_TTL_MS + 10;
    expect(cache.get('k')).toBe('full-answer');
    expect(cache.depth('k')).toBe('full');
  });

  it('hands an answer over once', () => {
    const cache = new PrefetchCache<string>(() => 0);
    cache.set('k', 'v', 'full');
    expect(cache.take('k')).toBe('v');
    expect(cache.take('k')).toBeUndefined();
  });
});
