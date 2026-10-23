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
    // The pre-LyricFix ladder: quick tries, then the error screen. Tests of
    // the background retries pass their own schedule.
    patientDelaysMs: [],
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
    expect(d.sent).toHaveLength(afterLadder);
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

describe('LyricFix — patient lrclib rung and the server head start', () => {
  afterEach(() => vi.useRealTimers());

  const LINES = [{ start_ms: 0, end_ms: 1000, text: 'la' }];

  function busy(retryAfterMs: number | null = null) {
    return Object.assign(new Error('lrclib /api/get failed: 503'), { retryAfterMs });
  }

  /** Flush microtasks between timer steps (each retry arms its timer late). */
  async function advance(ms: number) {
    await vi.advanceTimersByTimeAsync(ms);
    // Zero-delay timers armed by what just ran (the next quick try) belong
    // to the same instant.
    for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(0);
  }

  it('keeps asking after the quick tries and fills the screen when lrclib returns', async () => {
    vi.useFakeTimers();
    let up = false;
    const getLyrics = vi.fn(async () => {
      if (!up) throw busy();
      return { found: true, lines: LINES, via: 'lrclib' };
    });
    const emit = vi.fn();
    const d = deps({ getLyrics, emit, patientDelaysMs: [1000, 1000, 1000, 1000] });
    const done = new LookupOrchestrator(d).lookup(KEY, TRACK);
    await advance(10); // the three quick tries (a 0 ms timer armed in a tick lands 1 ms later)
    expect(d.sent).toContainEqual({ key: KEY, searching: true, retrying: true });
    expect(d.sent).not.toContainEqual(expect.objectContaining({ error: true }));
    await advance(1000);
    up = true;
    await advance(1000);
    await done;
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true, lines: LINES });
    expect(emit).toHaveBeenCalledWith('lyrics_outcome', expect.objectContaining({ source: 'error' }));
    expect(emit).toHaveBeenCalledWith(
      'lyrics_outcome',
      expect.objectContaining({ source: 'lrclib', via: 'lrclib', recovered_after_s: expect.any(Number) }),
    );
    // announced once, not on every retry
    expect(d.sent.filter((p) => p['retrying'])).toHaveLength(1);
  });

  it('never asks before the Retry-After lrclib named', async () => {
    vi.useFakeTimers();
    const getLyrics = vi
      .fn()
      .mockRejectedValueOnce(busy(30_000))
      .mockResolvedValue({ found: true, lines: LINES });
    const d = deps({ getLyrics, retryDelaysMs: [0, 1000] });
    const done = new LookupOrchestrator(d).lookup(KEY, TRACK);
    await advance(0);
    expect(getLyrics).toHaveBeenCalledTimes(1);
    await advance(29_999);
    expect(getLyrics).toHaveBeenCalledTimes(1); // the 1 s quick delay did not undercut it
    await advance(1);
    await done;
    expect(getLyrics).toHaveBeenCalledTimes(2);
  });

  it('a track change ends the background retries', async () => {
    vi.useFakeTimers();
    let current = true;
    const getLyrics = vi.fn(async () => {
      throw busy();
    });
    const d = deps({ getLyrics, isCurrent: () => current, patientDelaysMs: [1000, 1000, 1000] });
    const done = new LookupOrchestrator(d).lookup(KEY, TRACK);
    await advance(10);
    const asked = getLyrics.mock.calls.length;
    current = false;
    await advance(5000);
    await done;
    expect(getLyrics).toHaveBeenCalledTimes(asked);
    expect(d.sent).not.toContainEqual(expect.objectContaining({ error: true }));
  });

  it('a healed server document stops the lrclib retries (R-8)', async () => {
    vi.useFakeTimers();
    const getProcessed = vi
      .fn()
      .mockResolvedValueOnce({ error: true })
      .mockResolvedValue({ found: true, source: 'kashi-server', sync: 'word', qualityScore: 0.9, lines: [] });
    const getLyrics = vi.fn(async () => {
      throw busy();
    });
    const d = deps({
      getProcessed,
      getLyrics,
      serverRetryDelaysMs: [1500],
      patientDelaysMs: [1000, 1000, 1000, 1000, 1000],
    });
    const done = new LookupOrchestrator(d).lookup(KEY, TRACK);
    await advance(1500); // the probe heals the screen
    const asked = getLyrics.mock.calls.length;
    await advance(5000);
    await done;
    expect(getLyrics.mock.calls.length).toBeLessThanOrEqual(asked + 1);
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true, sync: 'word' });
  });

  it('a silent server gets a head start, then lrclib runs alongside it', async () => {
    vi.useFakeTimers();
    let answerServer: (v: unknown) => void = () => {};
    const getProcessed = vi.fn(
      () => new Promise((resolve) => (answerServer = resolve)),
    ) as unknown as LookupDeps['getProcessed'];
    const getLyrics = vi.fn(async () => ({ found: true, lines: LINES }));
    const d = deps({ getProcessed, getLyrics, serverHeadStartMs: 1500 });
    const done = new LookupOrchestrator(d).lookup(KEY, TRACK);
    await advance(1499);
    expect(getLyrics).not.toHaveBeenCalled(); // exactly at the edge: still the server's turn
    await advance(1);
    await advance(5); // the rung's first (0 ms) try
    expect(getLyrics).toHaveBeenCalled();
    expect(d.sent.at(-1)).toMatchObject({ found: true, lines: LINES });
    // the late server document wins the screen
    answerServer({ found: true, source: 'kashi-server', sync: 'word', qualityScore: 0.9, lines: [] });
    await advance(0);
    await done;
    expect(d.sent.at(-1)).toMatchObject({ found: true, sync: 'word' });
  });

  it('a server that answers inside its head start keeps lrclib out (R-8)', async () => {
    vi.useFakeTimers();
    const getProcessed = vi.fn(
      () =>
        new Promise((resolve) =>
          setTimeout(
            () => resolve({ found: true, source: 'kashi-server', sync: 'line', qualityScore: 0.8, lines: [] }),
            1000,
          ),
        ),
    ) as unknown as LookupDeps['getProcessed'];
    const d = deps({ getProcessed, serverHeadStartMs: 1500 });
    const done = new LookupOrchestrator(d).lookup(KEY, TRACK);
    await advance(5000);
    await done;
    expect(d.getLyrics).not.toHaveBeenCalled();
  });
});

describe('LyricFix — review round', () => {
  afterEach(() => vi.useRealTimers());

  const LINES = [{ start_ms: 0, end_ms: 1000, text: 'la' }];
  const busy = (retryAfterMs: number | null = null) =>
    Object.assign(new Error('lrclib /api/get failed: 503'), { retryAfterMs });
  async function advance(ms: number) {
    await vi.advanceTimersByTimeAsync(ms);
    for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(0);
  }

  it('a paused track does not keep asking; playback resumes the retries', async () => {
    vi.useFakeTimers();
    let playing = true;
    const getLyrics = vi.fn(async () => {
      throw busy();
    });
    const d = deps({ getLyrics, isPlaying: () => playing, patientDelaysMs: Array(10).fill(1000) });
    const done = new LookupOrchestrator(d).lookup(KEY, TRACK);
    await advance(10);
    playing = false;
    const asked = getLyrics.mock.calls.length;
    await advance(5000);
    expect(getLyrics).toHaveBeenCalledTimes(asked);
    playing = true;
    await advance(1000);
    expect(getLyrics.mock.calls.length).toBeGreaterThan(asked);
    await advance(10_000);
    await done;
  });

  it('a long Retry-After on a quick try is announced at once, not after it', async () => {
    vi.useFakeTimers();
    const getLyrics = vi
      .fn()
      .mockRejectedValueOnce(busy(60_000))
      .mockResolvedValue({ found: true, lines: LINES });
    const d = deps({ getLyrics, retryDelaysMs: [0, 2000, 6000] });
    const done = new LookupOrchestrator(d).lookup(KEY, TRACK);
    await advance(10);
    expect(d.sent).toContainEqual({ key: KEY, searching: true, retrying: true });
    await advance(60_000);
    await done;
    expect(d.sent.at(-1)).toMatchObject({ found: true });
  });

  it('an lrclib answer discarded under a server document is not counted', async () => {
    vi.useFakeTimers();
    let answerLrclib: (v: unknown) => void = () => {};
    const getLyrics = vi.fn(() => new Promise((r) => (answerLrclib = r))) as unknown as LookupDeps['getLyrics'];
    const getProcessed = vi.fn(
      () =>
        new Promise((resolve) =>
          setTimeout(
            () => resolve({ found: true, source: 'kashi-server', sync: 'word', qualityScore: 0.9, lines: [] }),
            3000,
          ),
        ),
    ) as unknown as LookupDeps['getProcessed'];
    const emit = vi.fn();
    const d = deps({ getProcessed, getLyrics, emit, serverHeadStartMs: 1500 });
    const done = new LookupOrchestrator(d).lookup(KEY, TRACK);
    await advance(3000); // lrclib started at 1.5 s, the server answers at 3 s
    answerLrclib({ found: true, lines: LINES });
    await advance(0);
    await done;
    const sources = emit.mock.calls.map((c) => (c[1] as { source: string }).source);
    expect(sources).toEqual(['kashi-server']);
    expect(d.sent.at(-1)).toMatchObject({ sync: 'word' });
  });
});

describe('job watch — the document lands during this play (2026-10-24)', () => {
  const WORD_DOC = {
    found: true,
    source: 'kashi-server',
    sync: 'word',
    qualityScore: 0.9,
    lines: [],
  } as const;
  const QUEUED = { jobId: 'job-1', status: 'queued', reused: false };

  /** Server 404 then (in the watch) whatever `later` answers; lrclib as given. */
  function watched(
    lrclib: Awaited<ReturnType<LookupDeps['getLyrics']>>,
    jobs: Array<{ status: string; errorType: string | null } | null>,
    later: unknown = WORD_DOC,
  ) {
    const getProcessed = vi
      .fn()
      .mockResolvedValueOnce({ found: false })
      .mockResolvedValue(later);
    const getJob = vi.fn();
    for (const job of jobs) getJob.mockResolvedValueOnce(job);
    return deps({
      getProcessed,
      getLyrics: vi.fn(async () => lrclib),
      getJob,
      watchDelaysMs: jobs.map(() => 0),
    });
  }

  it('says the server is syncing, then puts the document up when the job finishes', async () => {
    const d = watched({ found: false }, [
      { status: 'aligning', errorType: null },
      { status: 'completed', errorType: null },
    ]);
    const o = new LookupOrchestrator(d);
    await o.lookup(KEY, TRACK);
    expect(d.sent.at(-1)).toEqual({ key: KEY, found: false });
    await o.watchJob(KEY, TRACK, QUEUED);
    expect(d.sent).toContainEqual({ key: KEY, found: false, syncing: true });
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true, sync: 'word' });
    expect(d.getJob).toHaveBeenCalledTimes(2);
  });

  it('takes the promise back when the job fails', async () => {
    const d = watched({ found: false }, [{ status: 'failed', errorType: 'lyrics_not_found' }]);
    const o = new LookupOrchestrator(d);
    await o.lookup(KEY, TRACK);
    await o.watchJob(KEY, TRACK, QUEUED);
    expect(d.sent.at(-2)).toEqual({ key: KEY, found: false, syncing: true });
    expect(d.sent.at(-1)).toEqual({ key: KEY, found: false });
  });

  it('takes it back when the job is deferred for lrclib, or outlives the window', async () => {
    const deferred = watched({ found: false }, [{ status: 'queued', errorType: 'lrclib_unavailable' }]);
    const o1 = new LookupOrchestrator(deferred);
    await o1.lookup(KEY, TRACK);
    await o1.watchJob(KEY, TRACK, QUEUED);
    expect(deferred.sent.at(-1)).toEqual({ key: KEY, found: false });

    const slow = watched({ found: false }, [null, { status: 'aligning', errorType: null }]);
    const o2 = new LookupOrchestrator(slow);
    await o2.lookup(KEY, TRACK);
    await o2.watchJob(KEY, TRACK, QUEUED);
    expect(slow.getJob).toHaveBeenCalledTimes(2); // a null read is asked again
    expect(slow.sent.at(-1)).toEqual({ key: KEY, found: false });
  });

  it('lrclib lines on screen are left alone, then upgraded to word sync', async () => {
    const d = watched({ found: true, lines: [] }, [{ status: 'completed', errorType: null }]);
    const o = new LookupOrchestrator(d);
    await o.lookup(KEY, TRACK);
    await o.watchJob(KEY, TRACK, QUEUED);
    expect(d.sent).not.toContainEqual(expect.objectContaining({ syncing: true }));
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true, sync: 'word' });
  });

  it('a finished document no richer than the lrclib lines on screen is not swapped in', async () => {
    const bareLine = { found: true, source: 'kashi-server', sync: 'line', qualityScore: 0.9, lines: [] };
    const d = watched({ found: true, lines: [] }, [{ status: 'completed', errorType: null }], bareLine);
    const o = new LookupOrchestrator(d);
    await o.lookup(KEY, TRACK);
    const before = d.sent.length;
    await o.watchJob(KEY, TRACK, QUEUED);
    expect(d.sent.length).toBe(before);
  });

  it('the network-error screen keeps its own message', async () => {
    const d = watched({ found: false }, [{ status: 'completed', errorType: null }]);
    d.getLyrics = vi.fn(async () => {
      throw new Error('timeout');
    });
    const o = new LookupOrchestrator(d);
    await o.lookup(KEY, TRACK);
    expect(d.sent.at(-1)).toEqual({ key: KEY, found: false, error: true });
    await o.watchJob(KEY, TRACK, QUEUED);
    expect(d.sent).not.toContainEqual(expect.objectContaining({ syncing: true }));
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true, sync: 'word' });
  });

  it("the server's failure block is not watched at all", async () => {
    const d = watched({ found: false }, []);
    const o = new LookupOrchestrator(d);
    await o.lookup(KEY, TRACK);
    const before = d.sent.length;
    await o.watchJob(KEY, TRACK, { jobId: 'old', status: 'failed', reused: true });
    expect(d.getJob).not.toHaveBeenCalled();
    expect(d.sent.length).toBe(before);
  });

  it('a track change ends the watch without a ghost swap', async () => {
    let current = true;
    const d = watched({ found: false }, [{ status: 'completed', errorType: null }]);
    d.isCurrent = () => current;
    (d.getJob as ReturnType<typeof vi.fn>).mockReset().mockImplementation(async () => {
      current = false; // the user skipped while the status was in flight
      return { status: 'completed', errorType: null };
    });
    const o = new LookupOrchestrator(d);
    await o.lookup(KEY, TRACK);
    await o.watchJob(KEY, TRACK, QUEUED);
    expect(d.sent.at(-1)).toEqual({ key: KEY, found: false, syncing: true });
    expect(d.getProcessed).toHaveBeenCalledTimes(1); // only the lookup's own request
  });

  it('a job already done is fetched without polling', async () => {
    const d = watched({ found: false }, []);
    const o = new LookupOrchestrator(d);
    await o.lookup(KEY, TRACK);
    await o.watchJob(KEY, TRACK, { jobId: 'j', status: 'completed', reused: true });
    expect(d.getJob).not.toHaveBeenCalled();
    expect(d.sent.at(-1)).toMatchObject({ key: KEY, found: true, sync: 'word' });
  });
});
