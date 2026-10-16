import { describe, expect, it, vi } from 'vitest';
import { LrclibUnavailableError, type LyricsResult } from './lrclib.js';
import { LyricsChain, type LyricsSource } from './lyrics-chain.js';
import { SourceCooldown } from './source-cooldown.js';

const QUERY = { title: 'Song', artist: 'Band', duration_ms: 200_000 };
const HIT: LyricsResult = {
  found: true,
  source: 'lrclib',
  sourceId: 7,
  lines: [{ start_ms: 0, end_ms: 1000, text: 'la' }],
  recordDurationMs: 200_000,
};

function source(impl: () => Promise<LyricsResult>): LyricsSource & { calls: number } {
  const s = {
    calls: 0,
    getLyrics: vi.fn(async () => {
      s.calls++;
      return impl();
    }),
  };
  return s;
}

function chain(mirror: LyricsSource | null, lrclib: LyricsSource) {
  return new LyricsChain({ mirror, lrclib, log: () => {} });
}

describe('LyricsChain', () => {
  it('a mirror hit answers alone — lrclib.net is not asked', async () => {
    const lrclib = source(async () => HIT);
    const result = await chain(source(async () => HIT), lrclib).getLyrics(QUERY);
    expect(result).toMatchObject({ found: true, via: 'mirror' });
    expect(lrclib.calls).toBe(0);
  });

  it('a mirror miss falls through: the mirror is an older subset', async () => {
    const result = await chain(source(async () => ({ found: false })), source(async () => HIT)).getLyrics(QUERY);
    expect(result).toMatchObject({ found: true, via: 'lrclib' });
  });

  it('a failing mirror falls through instead of failing the lookup', async () => {
    const mirror = source(async () => {
      throw new LrclibUnavailableError('network', null, 'refused');
    });
    const result = await chain(mirror, source(async () => HIT)).getLyrics(QUERY);
    expect(result).toMatchObject({ via: 'lrclib' });
  });

  it('without a mirror the chain is lrclib.net, failures included', async () => {
    expect(await chain(null, source(async () => ({ found: false }))).getLyrics(QUERY)).toEqual({
      found: false,
      via: 'lrclib',
    });
    const down = source(async () => {
      throw new LrclibUnavailableError('busy', 2000, 'lrclib /api/get failed: 503');
    });
    await expect(chain(null, down).getLyrics(QUERY)).rejects.toMatchObject({ retryAfterMs: 2000 });
  });

  it("the caller's abort ends the chain at the mirror", async () => {
    const controller = new AbortController();
    const mirror = source(async () => {
      controller.abort();
      throw new DOMException('aborted', 'AbortError');
    });
    const lrclib = source(async () => HIT);
    await expect(chain(mirror, lrclib).getLyrics(QUERY, controller.signal)).rejects.toThrow();
    expect(lrclib.calls).toBe(0);
  });
});

describe('SourceCooldown', () => {
  it('doubles from the base and stops exactly at the cap', () => {
    const cd = new SourceCooldown(2000, 16_000, () => 0);
    const waits: number[] = [];
    for (let i = 0; i < 6; i++) {
      cd.noteFailure(null);
      waits.push(cd.remainingMs());
    }
    expect(waits).toEqual([2000, 4000, 8000, 16_000, 16_000, 16_000]);
  });

  it("never waits less than the server asked for", () => {
    const cd = new SourceCooldown(2000, 16_000, () => 0);
    cd.noteFailure(30_000);
    expect(cd.remainingMs()).toBe(30_000);
  });

  it('is open again exactly when the wait runs out, and a success clears it', () => {
    let t = 0;
    const cd = new SourceCooldown(2000, 16_000, () => t);
    cd.noteFailure(null);
    t = 1999;
    expect(cd.remainingMs()).toBe(1);
    t = 2000;
    expect(cd.remainingMs()).toBe(0);
    cd.noteFailure(null);
    cd.noteSuccess();
    expect(cd.remainingMs()).toBe(0);
  });
});
