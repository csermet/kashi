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

  /** Runs the ladder to completion under fake timers (lrclib sleeps too). */
  async function runLadder(d: ReturnType<typeof deps>) {
    const orch = new LookupOrchestrator(d);
    const done = orch.lookup(KEY, TRACK);
    await vi.advanceTimersByTimeAsync(1);
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
});
