import { describe, expect, it, vi } from 'vitest';
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
