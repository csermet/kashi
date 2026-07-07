import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { LrclibClient, normalizeArtist, parseLrc } from './lrclib.js';

const LRC = '[00:12.34] First line\n[00:15.20] Second line\n';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(status === 404 ? '{}' : JSON.stringify(body), { status });
}

interface FetchCall {
  url: string;
}

function makeFetch(handler: (url: string) => Response | Promise<Response>) {
  const calls: FetchCall[] = [];
  const fetchFn = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push({ url });
    return handler(url);
  }) as typeof fetch;
  return { fetchFn, calls };
}

describe('parseLrc', () => {
  it('parses timestamps into integer ms with line ends', () => {
    const lines = parseLrc(LRC, 200_000);
    expect(lines).toEqual([
      { start_ms: 12_340, end_ms: 15_200, text: 'First line' },
      { start_ms: 15_200, end_ms: 20_200, text: 'Second line' },
    ]);
  });

  it('caps the last line at track duration', () => {
    const lines = parseLrc('[03:00.00] Ending', 182_000);
    expect(lines[0]).toEqual({ start_ms: 180_000, end_ms: 182_000, text: 'Ending' });
  });

  it('does not zero out the last line when duration is before its start', () => {
    // Album-version LRC outlasting a shorter YTM edit (duration 3:00 < 3:05).
    const lines = parseLrc('[03:05.00] Last words', 180_000);
    expect(lines[0]).toEqual({ start_ms: 185_000, end_ms: 190_000, text: 'Last words' });
  });

  it('supports multiple timestamps per line and sorts output', () => {
    const lines = parseLrc('[00:30.00][00:10.00] Chorus\n[00:20.00] Verse');
    expect(lines.map((l) => [l.start_ms, l.text])).toEqual([
      [10_000, 'Chorus'],
      [20_000, 'Verse'],
      [30_000, 'Chorus'],
    ]);
  });

  it('skips metadata/empty lines', () => {
    expect(parseLrc('[ar:Artist]\n[00:05.00]\n[al:Album]')).toEqual([]);
  });
});

describe('normalizeArtist', () => {
  it('strips the YTM Topic suffix', () => {
    expect(normalizeArtist('Rick Astley - Topic')).toBe('Rick Astley');
    expect(normalizeArtist('Rick Astley')).toBe('Rick Astley');
    expect(normalizeArtist('Topic - Band')).toBe('Topic - Band');
  });
});

describe('LrclibClient', () => {
  let cacheDir: string;

  beforeEach(async () => {
    cacheDir = await mkdtemp(join(tmpdir(), 'kashi-lrclib-'));
  });

  afterEach(async () => {
    await rm(cacheDir, { recursive: true, force: true });
  });

  const QUERY = {
    title: 'Never Gonna Give You Up',
    artist: 'Rick Astley - Topic',
    album: 'Whenever You Need Somebody',
    duration_ms: 213_000,
  };

  it('returns parsed lines from an exact hit and caches positively', async () => {
    const { fetchFn, calls } = makeFetch((url) => {
      expect(url).toContain('artist_name=Rick+Astley'); // Topic stripped
      return jsonResponse(200, { id: 42, duration: 213, syncedLyrics: LRC });
    });
    const client = new LrclibClient({ cacheDir, fetchFn });

    const first = await client.getLyrics(QUERY);
    expect(first).toMatchObject({ found: true, sourceId: 42 });
    if (first.found) expect(first.lines).toHaveLength(2);

    const second = await client.getLyrics(QUERY);
    expect(second).toMatchObject({ found: true, sourceId: 42 });
    expect(calls).toHaveLength(1); // second answer came from disk cache
  });

  it('falls back to search when the exact hit has no synced lyrics', async () => {
    const { fetchFn, calls } = makeFetch((url) =>
      url.includes('/api/get')
        ? jsonResponse(200, { id: 1, duration: 213, syncedLyrics: null }) // plain-only
        : jsonResponse(200, [{ id: 2, duration: 213, syncedLyrics: LRC }]),
    );
    const client = new LrclibClient({ cacheDir, fetchFn });
    const result = await client.getLyrics(QUERY);
    expect(result).toMatchObject({ found: true, sourceId: 2 });
    expect(calls).toHaveLength(2);
  });

  it('falls back to search and picks the closest duration', async () => {
    const { fetchFn, calls } = makeFetch((url) => {
      if (url.includes('/api/get')) return jsonResponse(404, {});
      return jsonResponse(200, [
        { id: 1, duration: 300, syncedLyrics: LRC },
        { id: 2, duration: 214, syncedLyrics: LRC },
        { id: 3, duration: 213, syncedLyrics: null },
      ]);
    });
    const client = new LrclibClient({ cacheDir, fetchFn });

    const result = await client.getLyrics(QUERY);
    expect(result).toMatchObject({ found: true, sourceId: 2 });
    expect(calls).toHaveLength(2);
  });

  it('rejects search candidates outside the duration tolerance', async () => {
    const { fetchFn } = makeFetch((url) =>
      url.includes('/api/get')
        ? jsonResponse(404, {})
        : jsonResponse(200, [{ id: 9, duration: 300, syncedLyrics: LRC }]),
    );
    const client = new LrclibClient({ cacheDir, fetchFn });
    expect(await client.getLyrics(QUERY)).toEqual({ found: false });
  });

  it('negative-caches misses and honors the TTL', async () => {
    let now = 1_000_000;
    const { fetchFn, calls } = makeFetch((url) =>
      url.includes('/api/get') ? jsonResponse(404, {}) : jsonResponse(200, []),
    );
    const client = new LrclibClient({
      cacheDir,
      fetchFn,
      nowFn: () => now,
      negativeTtlMs: 1000,
    });

    expect(await client.getLyrics(QUERY)).toEqual({ found: false });
    expect(await client.getLyrics(QUERY)).toEqual({ found: false });
    expect(calls).toHaveLength(2); // get + search once; second call cached

    now += 2000; // TTL expired → asks again
    await client.getLyrics(QUERY);
    expect(calls).toHaveLength(4);
  });

  it('coalesces concurrent lookups into one request', async () => {
    let resolveFetch: ((r: Response) => void) | undefined;
    const { fetchFn, calls } = makeFetch(
      () => new Promise<Response>((resolve) => (resolveFetch = resolve)),
    );
    const client = new LrclibClient({ cacheDir, fetchFn });

    const [a, b] = [client.getLyrics(QUERY), client.getLyrics(QUERY)];
    await new Promise((r) => setTimeout(r, 10));
    expect(calls).toHaveLength(1);
    resolveFetch?.(jsonResponse(200, { id: 7, duration: 213, syncedLyrics: LRC }));
    const [ra, rb] = await Promise.all([a, b]);
    expect(ra).toMatchObject({ found: true, sourceId: 7 });
    expect(rb).toMatchObject({ found: true, sourceId: 7 });
  });

  it('times out a hung request via the per-request budget', async () => {
    const { fetchFn } = makeFetch(
      (url) =>
        new Promise<Response>((resolve, reject) => {
          // Simulate a black-holed connection: resolve never, but honor abort.
          void url;
        }),
    );
    // Wire abort through like real fetch would:
    const hangingFetch = ((input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(new DOMException('timeout', 'TimeoutError')),
        );
      })) as typeof fetch;
    const client = new LrclibClient({
      cacheDir,
      fetchFn: hangingFetch,
      requestTimeoutMs: 50,
    });
    await expect(client.getLyrics(QUERY)).rejects.toThrow();
    void fetchFn;
  });

  it('does not negative-cache aborted lookups', async () => {
    const controller = new AbortController();
    const { fetchFn, calls } = makeFetch(() => {
      controller.abort();
      throw new DOMException('aborted', 'AbortError');
    });
    const client = new LrclibClient({ cacheDir, fetchFn });

    await expect(client.getLyrics(QUERY, controller.signal)).rejects.toThrow();

    // Next lookup hits the network again (nothing was cached).
    const { fetchFn: fetch2, calls: calls2 } = makeFetch(() =>
      jsonResponse(200, { id: 5, duration: 213, syncedLyrics: LRC }),
    );
    const client2 = new LrclibClient({ cacheDir, fetchFn: fetch2 });
    expect(await client2.getLyrics(QUERY)).toMatchObject({ found: true });
    expect(calls).toHaveLength(1);
    expect(calls2).toHaveLength(1);
  });
});
