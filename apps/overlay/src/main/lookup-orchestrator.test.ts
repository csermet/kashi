import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TrackInfo } from '@kashi/protocol';
import { LookupOrchestrator, type LookupDeps } from './lookup-orchestrator.js';

const TRACK: TrackInfo = {
  source: { type: 'youtube', id: 'vid1' },
  title: 'T',
  artist: 'A',
  duration_ms: 200_000,
};
const KEY = 'youtube:vid1';

function deps(overrides: Partial<LookupDeps> = {}) {
  const sent: Array<Record<string, unknown>> = [];
  const d: LookupDeps & { sent: typeof sent } = {
    getProcessed: null,
    getLyrics: vi.fn(async () => ({ found: true, lines: [] })),
    send: (payload) => sent.push(payload),
    onServerMiss: vi.fn(),
    isCurrent: () => true,
    log: () => {},
    retryDelaysMs: [0, 0, 0],
    sent,
    ...overrides,
  };
  return d;
}

describe('LookupOrchestrator', () => {
  it('server hit is the single source of truth — lrclib is never consulted (R-8)', async () => {
    const d = deps({
      getProcessed: async () => ({ found: true, source: 'kashi-server', sync: 'word', qualityScore: 0.9, lines: [] }),
    });
    await new LookupOrchestrator(d).lookup(KEY, TRACK);
    expect(d.getLyrics).not.toHaveBeenCalled();
    expect(d.onServerMiss).not.toHaveBeenCalled();
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true, sync: 'word' });
  });

  it('server 404 arms the gate and falls back to lrclib', async () => {
    const d = deps({ getProcessed: async () => ({ found: false }) });
    await new LookupOrchestrator(d).lookup(KEY, TRACK);
    expect(d.onServerMiss).toHaveBeenCalledWith(KEY, TRACK);
    expect(d.getLyrics).toHaveBeenCalled();
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true });
  });

  it('server error falls back WITHOUT arming the gate (R-9)', async () => {
    const d = deps({ getProcessed: async () => ({ error: true }) });
    await new LookupOrchestrator(d).lookup(KEY, TRACK);
    expect(d.onServerMiss).not.toHaveBeenCalled();
    expect(d.getLyrics).toHaveBeenCalled();
  });

  it('transient lrclib failures are retried; exhaustion reports error (not a miss)', async () => {
    const getLyrics = vi.fn(async () => {
      throw new Error('timeout');
    });
    const d = deps({ getLyrics });
    await new LookupOrchestrator(d).lookup(KEY, TRACK);
    expect(getLyrics).toHaveBeenCalledTimes(3);
    expect(d.sent.at(-1)).toEqual({ key: KEY, found: false, error: true });
  });

  it('a duration-scoped miss retries once without the duration', async () => {
    const getLyrics = vi
      .fn()
      .mockResolvedValueOnce({ found: false })
      .mockResolvedValueOnce({ found: true, lines: [] });
    const d = deps({ getLyrics });
    await new LookupOrchestrator(d).lookup(KEY, TRACK);
    expect(getLyrics).toHaveBeenCalledTimes(2);
    expect(getLyrics.mock.calls[1]?.[0]).toMatchObject({ duration_ms: undefined });
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true });
  });

  it('stale results are dropped when the track changed mid-flight', async () => {
    const d = deps({ isCurrent: () => false, getLyrics: vi.fn(async () => ({ found: true })) });
    await new LookupOrchestrator(d).lookup(KEY, TRACK);
    // only the initial "searching" ping went out
    expect(d.sent).toEqual([{ key: KEY, searching: true }]);
  });

  it('cancel aborts the ladder between retries', async () => {
    let calls = 0;
    const orchestrator = new LookupOrchestrator(
      deps({
        getLyrics: vi.fn(async () => {
          calls += 1;
          orchestrator.cancel();
          throw new Error('slow');
        }),
      }),
    );
    await orchestrator.lookup(KEY, TRACK);
    expect(calls).toBe(1); // no second attempt after cancel
  });
});

describe('server self-heal (Faz 6.7 P3)', () => {
  afterEach(() => vi.useRealTimers());

  /**
   * Runs the ladder to completion under fake timers. Each lrclib retry only
   * creates its timer once the previous one resolved, so a single advance
   * cannot cover a ladder that retries — step it.
   */
  async function runLadder(d: ReturnType<typeof deps>) {
    const orch = new LookupOrchestrator(d);
    const done = orch.lookup(KEY, TRACK);
    for (let i = 0; i < 6; i++) await vi.advanceTimersByTimeAsync(1);
    await done;
    return orch;
  }

  it('recovers the rich document mid-song after a server timeout', async () => {
    vi.useFakeTimers();
    // The first call times out (the Danza Kuduro case), the next one answers.
    const getProcessed = vi
      .fn()
      .mockResolvedValueOnce({ error: true })
      .mockResolvedValue({
        found: true,
        source: 'kashi-server',
        sync: 'word',
        qualityScore: 0.92,
        lines: [],
      });
    const onServerWordHit = vi.fn();
    const emit = vi.fn();
    const d = deps({ getProcessed, onServerWordHit, emit });

    await runLadder(d);
    // lrclib filled the screen immediately — the probe never blocks the ladder.
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true });
    expect(getProcessed).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(10_001);
    expect(getProcessed).toHaveBeenCalledTimes(2);
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, sync: 'word', qualityScore: 0.92 });
    expect(onServerWordHit).toHaveBeenCalledOnce();
    expect(emit).toHaveBeenCalledWith(
      'lyrics_outcome',
      expect.objectContaining({ upgraded: true, attempt: 1 }),
    );
  });

  it('a track change kills the probe — no ghost swap on the next song', async () => {
    vi.useFakeTimers();
    const getProcessed = vi.fn().mockResolvedValue({ error: true });
    const d = deps({ getProcessed });

    const orch = await runLadder(d);
    orch.cancel();
    await vi.advanceTimersByTimeAsync(200_000);
    expect(getProcessed).toHaveBeenCalledTimes(1); // only the blocking attempt
  });

  it('a genuine 404 stops the probe and arms the enqueue gate', async () => {
    vi.useFakeTimers();
    const getProcessed = vi
      .fn()
      .mockResolvedValueOnce({ error: true })
      .mockResolvedValue({ found: false });
    const d = deps({ getProcessed });

    await runLadder(d);
    await vi.advanceTimersByTimeAsync(10_001);
    expect(d.onServerMiss).toHaveBeenCalledWith(KEY, TRACK);

    await vi.advanceTimersByTimeAsync(200_000);
    expect(getProcessed).toHaveBeenCalledTimes(2); // an answer ends the probe
  });

  it('a healthy server never arms the probe', async () => {
    vi.useFakeTimers();
    const getProcessed = vi.fn().mockResolvedValue({
      found: true,
      source: 'kashi-server',
      sync: 'word',
      qualityScore: 0.9,
      lines: [],
    });
    const d = deps({ getProcessed });

    await runLadder(d);
    await vi.advanceTimersByTimeAsync(200_000);
    expect(getProcessed).toHaveBeenCalledTimes(1);
  });

  it('fills a screen that lrclib left empty (the correlated-failure case)', async () => {
    vi.useFakeTimers();
    // The network event that timed out the server usually takes lrclib too —
    // and that is precisely when a late server document is worth the most.
    const getProcessed = vi
      .fn()
      .mockResolvedValueOnce({ error: true })
      .mockResolvedValue({
        found: true,
        source: 'kashi-server',
        sync: 'word',
        qualityScore: 0.9,
        lines: [],
      });
    const d = deps({
      getProcessed,
      getLyrics: vi.fn(async () => {
        throw new Error('offline');
      }),
    });

    await runLadder(d);
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, error: true }); // screen empty
    await vi.advanceTimersByTimeAsync(10_001);
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, sync: 'word' });
  });

  it('a late lrclib answer never overwrites a healed server document (R-8)', async () => {
    vi.useFakeTimers();
    const getProcessed = vi
      .fn()
      .mockResolvedValueOnce({ error: true })
      .mockResolvedValue({
        found: true,
        source: 'kashi-server',
        sync: 'word',
        qualityScore: 0.9,
        lines: [],
      });
    // lrclib is slow enough that the probe heals the track first.
    const getLyrics = vi.fn(
      () => new Promise((resolve) => setTimeout(() => resolve({ found: true, lines: [] }), 30_000)),
    ) as unknown as LookupDeps['getLyrics'];
    const d = deps({ getProcessed, getLyrics });

    const orch = new LookupOrchestrator(d);
    const done = orch.lookup(KEY, TRACK);
    // Let the blocking attempt fail first: the probe's timer does not exist
    // until then, and advancing past a timer that was never created is a no-op.
    await vi.advanceTimersByTimeAsync(1);
    await vi.advanceTimersByTimeAsync(10_001); // probe wins the race
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, sync: 'word' });

    await vi.advanceTimersByTimeAsync(30_000); // lrclib finally answers
    await done;
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, sync: 'word' }); // not clobbered
  });

  it('the schedule really ends — a dead server is asked 1 + 5 times, then never', async () => {
    vi.useFakeTimers();
    const getProcessed = vi.fn().mockResolvedValue({ error: true });
    const d = deps({ getProcessed });

    await runLadder(d);
    await vi.advanceTimersByTimeAsync(600_000);
    expect(getProcessed).toHaveBeenCalledTimes(6); // 1 blocking + 5 probes
  });

  it('a recovered but not richer document is never re-rendered', async () => {
    vi.useFakeTimers();
    // lrclib line doc on screen; the server answers with a BARE line doc —
    // same granularity, no palette/beats/fx to buy the flicker.
    const getProcessed = vi
      .fn()
      .mockResolvedValueOnce({ error: true })
      .mockResolvedValue({ found: true, source: 'kashi-server', sync: 'line', qualityScore: 0.9, lines: [] });
    const d = deps({ getProcessed });

    await runLadder(d);
    const afterLadder = d.sent.length;
    await vi.advanceTimersByTimeAsync(10_001);
    expect(d.sent.length).toBe(afterLadder);
  });

  it('a bare line doc is declined, an enriched one is taken', async () => {
    vi.useFakeTimers();
    const getProcessed = vi
      .fn()
      .mockResolvedValueOnce({ error: true })
      .mockResolvedValue({
        found: true,
        source: 'kashi-server',
        sync: 'line',
        qualityScore: 0.9,
        lines: [],
        beats: { bpm: 120, times_ms: [] },
      });
    const d = deps({ getProcessed });

    await runLadder(d);
    await vi.advanceTimersByTimeAsync(10_001);
    // Beats/palette/fx are invisible in the text and very visible on screen.
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, sync: 'line', beats: { bpm: 120 } });
  });
});

describe('a stale cache hit is not an answer (P7)', () => {
  const staleDoc = {
    found: true,
    source: 'kashi-server',
    sync: 'word',
    qualityScore: 0.9,
    lines: [],
    stale: true,
  };
  const freshDoc = { ...staleDoc, stale: false, qualityScore: 0.95 };

  it('keeps probing while the answers are still coming from the cache', async () => {
    // Two things have to hold, and they are separate. The screen filling made
    // this invisible — a cache fallback looked exactly like a live hit, so
    // nothing ever asked again. And a probe that ALSO gets served from cache
    // is the same failure wearing the same clothes: if that counts as an
    // answer, the ladder ends on its first rung and the stale document stays
    // up even though the server came back.
    let calls = 0;
    const d = deps({
      getProcessed: async () => {
        calls += 1;
        return calls <= 2 ? staleDoc : freshDoc;
      },
      serverRetryDelaysMs: [0, 0, 0],
    } as never);
    await new LookupOrchestrator(d).lookup(KEY, TRACK);
    await new Promise((r) => setTimeout(r, 20));

    expect(calls).toBeGreaterThanOrEqual(3);
    expect(d.getLyrics).not.toHaveBeenCalled();
  });

  it('reports staleness so the field data can tell the two apart', async () => {
    const emit = vi.fn();
    const d = deps({
      getProcessed: async () => staleDoc,
      emit,
      serverRetryDelaysMs: [],
    } as never);
    await new LookupOrchestrator(d).lookup(KEY, TRACK);
    expect(emit).toHaveBeenCalledWith(
      'lyrics_outcome',
      expect.objectContaining({ source: 'kashi-server', stale: true }),
    );
  });

  it('a fresh hit is never marked stale', async () => {
    const emit = vi.fn();
    const d = deps({ getProcessed: async () => freshDoc, emit } as never);
    await new LookupOrchestrator(d).lookup(KEY, TRACK);
    const payload = emit.mock.calls.at(-1)?.[1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('stale');
  });
});

describe('different-edit guard on the duration-less retry', () => {
  const track = {
    title: 'Hey Mama',
    artist: 'David Guetta',
    duration_ms: 366_451, // the PREVIOUS video's duration, leaked by auto-advance
    source: { type: 'youtube', id: 'Hsz6hL_a69Q' },
  };

  function harness(second: { found: boolean; [k: string]: unknown }) {
    const sent: Record<string, unknown>[] = [];
    const logs: string[] = [];
    const orchestrator = new LookupOrchestrator({
      getProcessed: null,
      // Scoped lookup misses (no six-minute record exists), unscoped answers.
      getLyrics: async (query) => (query.duration_ms ? { found: false } : second),
      send: (payload) => void sent.push(payload),
      onServerMiss: () => {},
      isCurrent: () => true,
      log: (line) => void logs.push(line),
      retryDelaysMs: [0],
    });
    return { orchestrator, sent, logs };
  }

  it('discards the foreign edit instead of driving a clock with it', async () => {
    const { orchestrator, sent, logs } = harness({
      found: true,
      sourceId: 12345,
      recordDurationMs: 193_000,
      lines: [{ start_ms: 1000, end_ms: 3000, text: 'hey mama' }],
    });
    await orchestrator.lookup('youtube:Hsz6hL_a69Q', track as never);
    const shown = sent[sent.length - 1];
    expect(shown?.found).toBe(false);
    expect(shown?.lines).toBeUndefined();
    expect(logs.some((l) => l.includes('different edit'))).toBe(true);
  });

  it('still shows a record that matches this edit', async () => {
    const ok = { ...track, duration_ms: 193_000 };
    const { orchestrator, sent } = harness({
      found: true,
      sourceId: 12345,
      recordDurationMs: 193_000,
      lines: [{ start_ms: 1000, end_ms: 3000, text: 'hey mama' }],
    });
    await orchestrator.lookup('youtube:Hsz6hL_a69Q', ok as never);
    expect(sent[sent.length - 1]?.found).toBe(true);
  });

  it('leaves an unverifiable record alone — no duration is not a conviction', async () => {
    const { orchestrator, sent } = harness({
      found: true,
      sourceId: 12345,
      recordDurationMs: null,
      lines: [{ start_ms: 1000, end_ms: 3000, text: 'hey mama' }],
    });
    await orchestrator.lookup('youtube:Hsz6hL_a69Q', track as never);
    expect(sent[sent.length - 1]?.found).toBe(true);
  });
});
