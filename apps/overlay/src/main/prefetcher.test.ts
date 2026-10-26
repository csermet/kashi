import { describe, expect, it, vi } from 'vitest';
import type { TrackInfo } from '@kashi/protocol';
import { Prefetcher, type PrefetcherDeps } from './prefetcher.js';
import { PREFETCH_AFTER_MS, type PlaybackView } from './prefetch-logic.js';

const track = (id: string): TrackInfo => ({
  source: { type: 'youtube', id },
  title: `Song ${id}`,
  artist: 'Artist',
  duration_ms: 200_000,
});
const due: PlaybackView = {
  enabled: true,
  isPlaying: true,
  isAd: false,
  positionMs: PREFETCH_AFTER_MS,
  durationMs: 200_000,
};
const early: PlaybackView = { ...due, positionMs: PREFETCH_AFTER_MS - 1 };
const SERVER_DOC = {
  found: true as const,
  source: 'kashi-server' as const,
  sync: 'line' as const,
  qualityScore: 0.8,
  lines: [],
};
const LYRICS = { found: true, lines: [], recordDurationMs: 200_000, via: 'mirror' };

async function flush(): Promise<void> {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

function deps(over: Partial<PrefetcherDeps> = {}) {
  const getProcessed = vi.fn<NonNullable<PrefetcherDeps['getProcessed']>>(async () => ({
    found: false as const,
  }));
  const getLyrics = vi.fn<PrefetcherDeps['getLyrics']>(async () => LYRICS);
  const d: PrefetcherDeps = {
    getProcessed,
    getLyrics,
    lyricsAvailable: () => true,
    log: () => {},
    ...over,
  };
  return { d, getProcessed, getLyrics };
}

describe('Prefetcher', () => {
  it('does nothing before the warm-up is due', async () => {
    const { d, getProcessed } = deps();
    const p = new Prefetcher(d);
    p.onQueue([track('a'), track('b')]);
    p.tick(early, 'youtube:cur');
    await flush();
    expect(getProcessed).not.toHaveBeenCalled();
  });

  it('asks the server for both, and lrclib only for the next track', async () => {
    const { d, getProcessed, getLyrics } = deps();
    const p = new Prefetcher(d);
    p.onQueue([track('a'), track('b')]);
    p.tick(due, 'youtube:cur');
    await flush();
    expect(getProcessed.mock.calls.map((c) => c[1])).toEqual(['a', 'b']);
    expect(getLyrics).toHaveBeenCalledTimes(1);
    expect(getLyrics.mock.calls[0]?.[0]).toMatchObject({ title: 'Song a', duration_ms: 200_000 });
    expect(p.take('youtube:a')).toEqual({ kind: 'lrclib', result: LYRICS });
    expect(p.take('youtube:b')).toBeUndefined(); // server 404: nothing to hand over
  });

  it('asks each item once per prediction, however often it ticks', async () => {
    const { d, getProcessed, getLyrics } = deps();
    const p = new Prefetcher(d);
    p.onQueue([track('a'), track('b')]);
    p.tick(due, null);
    p.tick(due, null); // still in flight
    await flush();
    p.tick(due, null); // done
    await flush();
    expect(getProcessed).toHaveBeenCalledTimes(2);
    expect(getLyrics).toHaveBeenCalledTimes(1);
  });

  it('a server document is the answer: lrclib is not asked for it', async () => {
    const { d, getLyrics } = deps({ getProcessed: vi.fn(async () => SERVER_DOC) });
    const p = new Prefetcher(d);
    p.onQueue([track('a')]);
    p.tick(due, null);
    await flush();
    expect(getLyrics).not.toHaveBeenCalled();
    expect(p.take('youtube:a')).toEqual({ kind: 'server', result: SERVER_DOC });
  });

  it('hands an answer over once', async () => {
    const { d } = deps();
    const p = new Prefetcher(d);
    p.onQueue([track('a')]);
    p.tick(due, null);
    await flush();
    expect(p.take('youtube:a')).toBeDefined();
    expect(p.take('youtube:a')).toBeUndefined();
  });

  it('never retries a failure in the same prediction', async () => {
    const getLyrics = vi.fn(async () => {
      throw new Error('busy');
    });
    const { d } = deps({ getLyrics });
    const p = new Prefetcher(d);
    p.onQueue([track('a')]);
    p.tick(due, null);
    await flush();
    p.tick(due, null);
    await flush();
    expect(getLyrics).toHaveBeenCalledTimes(1);
    expect(p.take('youtube:a')).toBeUndefined();
  });

  it('tries once more without the duration, like the live ladder', async () => {
    const getLyrics = vi
      .fn<PrefetcherDeps['getLyrics']>()
      .mockResolvedValueOnce({ found: false })
      .mockResolvedValueOnce(LYRICS);
    const { d } = deps({ getLyrics });
    const p = new Prefetcher(d);
    p.onQueue([track('a')]);
    p.tick(due, null);
    await flush();
    expect(getLyrics.mock.calls.map((c) => c[0].duration_ms)).toEqual([200_000, undefined]);
    expect(p.take('youtube:a')?.kind).toBe('lrclib');
  });

  it('asks only the server while no lyrics source can answer', async () => {
    const { d, getProcessed, getLyrics } = deps({ lyricsAvailable: () => false });
    const p = new Prefetcher(d);
    p.onQueue([track('a')]);
    p.tick(due, null);
    await flush();
    expect(getProcessed).toHaveBeenCalledTimes(1);
    expect(getLyrics).not.toHaveBeenCalled();
  });

  it('upgrades a server-only item to the full chain once it becomes the next track', async () => {
    const { d, getLyrics } = deps();
    const p = new Prefetcher(d);
    p.onQueue([track('a'), track('b')]);
    p.tick(due, 'youtube:cur');
    await flush();
    p.onQueue([track('b'), track('c')]); // 'a' started playing
    p.tick(due, 'youtube:a');
    await flush();
    expect(getLyrics.mock.calls.map((c) => c[0]?.title)).toEqual(['Song a', 'Song b']);
  });

  it('a server document found for the second track covers it when it moves up', async () => {
    const getProcessed = vi.fn<NonNullable<PrefetcherDeps['getProcessed']>>(async (_type, id) =>
      id === 'b' ? SERVER_DOC : { found: false as const },
    );
    const { d, getLyrics } = deps({ getProcessed });
    const p = new Prefetcher(d);
    p.onQueue([track('a'), track('b')]);
    p.tick(due, null);
    await flush();
    p.onQueue([track('b')]);
    p.tick(due, 'youtube:a');
    await flush();
    expect(getProcessed).toHaveBeenCalledTimes(2); // b is not asked again
    expect(getLyrics.mock.calls.map((c) => c[0]?.title)).toEqual(['Song a']);
  });

  it('starts at most four prefetches while one track plays, however the queue churns', async () => {
    const { d, getProcessed } = deps();
    const p = new Prefetcher(d);
    for (const pair of [['a', 'b'], ['c', 'd'], ['e', 'f']]) {
      p.onQueue(pair.map(track));
      p.tick(due, 'youtube:cur');
      await flush();
    }
    expect(getProcessed).toHaveBeenCalledTimes(4);
    p.onQueue([track('g')]); // a new track is playing: a fresh budget
    p.tick(due, 'youtube:e');
    await flush();
    expect(getProcessed.mock.calls.map((c) => c[1])).toEqual(['a', 'b', 'c', 'd', 'g']);
  });

  it('works without a server', async () => {
    const { d, getLyrics } = deps({ getProcessed: null });
    const p = new Prefetcher(d);
    p.onQueue([track('a'), track('b')]);
    p.tick(due, null);
    await flush();
    expect(getLyrics).toHaveBeenCalledTimes(1);
  });

  it('after stop, starts nothing and keeps nothing', async () => {
    let release: (value: typeof LYRICS) => void = () => {};
    const getLyrics = vi.fn(() => new Promise<typeof LYRICS>((resolve) => (release = resolve)));
    const { d } = deps({ getLyrics });
    const p = new Prefetcher(d);
    p.onQueue([track('a')]);
    p.tick(due, null);
    await flush();
    p.stop();
    release(LYRICS);
    await flush();
    expect(p.take('youtube:a')).toBeUndefined();
    p.onQueue([track('z')]);
    p.tick(due, null);
    await flush();
    expect(getLyrics).toHaveBeenCalledTimes(1);
  });

  it('drops a result that lands after its track started (no second answer)', async () => {
    let release: (value: typeof LYRICS) => void = () => {};
    const getLyrics = vi.fn(() => new Promise<typeof LYRICS>((resolve) => (release = resolve)));
    const lines: string[] = [];
    const { d } = deps({ getLyrics, log: (l) => lines.push(l) });
    const p = new Prefetcher(d);
    p.onQueue([track('a')]);
    p.tick(due, null);
    await flush();
    expect(p.take('youtube:a')).toBeUndefined(); // still in flight: the live lookup goes ahead
    release(LYRICS);
    await flush();
    expect(p.take('youtube:a')).toBeUndefined(); // and the late answer was not kept
    expect(lines.some((l) => l.includes('arrived after its track started'))).toBe(true);
  });

  it('logs what each started track found: the hit-rate line', async () => {
    const lines: string[] = [];
    const { d } = deps({ log: (l) => lines.push(l) });
    const p = new Prefetcher(d);
    p.onQueue([track('a'), track('b')]);
    p.tick(due, null);
    await flush();
    p.take('youtube:a');
    p.take('youtube:b');
    p.take('youtube:x');
    expect(lines.filter((l) => / for youtube:/.test(l) && l.startsWith('prefetch '))).toEqual([
      'prefetch lrclib for youtube:a',
      'prefetch none for youtube:b',
      'prefetch cold for youtube:x',
    ]);
  });

  it('logs a spent budget once per track, not on every tick', async () => {
    const lines: string[] = [];
    const { d } = deps({ log: (l) => lines.push(l) });
    const p = new Prefetcher(d);
    for (const pair of [['a', 'b'], ['c', 'd'], ['e', 'f']]) {
      p.onQueue(pair.map(track));
      for (let i = 0; i < 5; i += 1) p.tick(due, 'youtube:cur');
      await flush();
    }
    expect(lines.filter((l) => l.includes('budget spent'))).toHaveLength(1);
  });

  it('never runs the same key twice at once', async () => {
    let releaseB: (value: { found: false }) => void = () => {};
    let heldOnce = false;
    const getProcessed = vi.fn<NonNullable<PrefetcherDeps['getProcessed']>>((_type, id) => {
      if (id === 'b' && !heldOnce) {
        heldOnce = true; // only the first b request hangs
        return new Promise((resolve) => (releaseB = resolve));
      }
      return Promise.resolve({ found: false as const });
    });
    const { d, getLyrics } = deps({ getProcessed });
    const p = new Prefetcher(d);
    p.onQueue([track('a'), track('b')]);
    p.tick(due, 'youtube:cur'); // b: server-only run, held in flight
    await flush();
    p.onQueue([track('b')]); // b moves up while its run is still out
    p.tick(due, 'youtube:a');
    await flush();
    expect(getProcessed.mock.calls.filter((c) => c[1] === 'b')).toHaveLength(1);
    releaseB({ found: false });
    await flush();
    p.tick(due, 'youtube:a'); // now the full run goes
    await flush();
    expect(getLyrics.mock.calls.map((c) => c[0]?.title)).toEqual(['Song a', 'Song b']);
  });
});
