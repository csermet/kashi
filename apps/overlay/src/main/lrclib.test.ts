import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { SourceCooldown } from './source-cooldown.js';
import { LrclibClient, LrclibUnavailableError, MAX_RETRY_AFTER_MS, normalizeArtist, parseLrc, parseRetryAfter,
  splitArtists,
} from './lrclib.js';

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

  it('stays linear on a long whitespace run (ReDoS)', () => {
    // Without the (?<!\s) lookbehind every position in the run restarted the
    // scan: 40 000 spaces took 3.5 s on the MAIN process. Now well under 1 ms.
    const artist = `a${' '.repeat(60_000)}b`;
    const started = performance.now();
    expect(normalizeArtist(artist)).toBe(artist);
    expect(performance.now() - started).toBeLessThan(500);
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
        new Promise<Response>((_resolve, _reject) => {
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

describe('splitArtists', () => {
  it('splits the conjunctions YTM actually emits', () => {
    // Every one of these is a real string from the 2026-08-13 field log.
    expect(splitArtists('Sean Paul ve David Guetta')).toEqual(['Sean Paul', 'David Guetta']);
    expect(splitArtists('Jim Yosef ve Scarlett')).toEqual(['Jim Yosef', 'Scarlett']);
    expect(splitArtists('TheFatRat ve Maisy Kay')).toEqual(['TheFatRat', 'Maisy Kay']);
    expect(splitArtists('Topic & Bebe Rexha')).toEqual(['Topic', 'Bebe Rexha']);
    expect(splitArtists('MXZI, Dj Samir ve DJ Javi26')).toEqual(['MXZI', 'Dj Samir', 'DJ Javi26']);
    expect(splitArtists('Don Omar ft. Lucenzo')).toEqual(['Don Omar', 'Lucenzo']);
  });

  it('says nothing about a single artist', () => {
    // The caller spends a request per non-empty answer, so "no split" has to
    // mean no split — not a one-element list that reruns the same query.
    expect(splitArtists('Ed Sheeran')).toEqual([]);
    expect(splitArtists('twenty one pilots')).toEqual([]);
    expect(splitArtists('')).toEqual([]);
  });

  it('does not tear apart a name that merely contains a separator word', () => {
    // Bare substrings must not split: only a SURROUNDED conjunction does.
    expect(splitArtists('Xavier')).toEqual([]);
    expect(splitArtists('Vevo')).toEqual([]);
    expect(splitArtists('Bandit')).toEqual([]);
  });
});

describe('multi-artist rung', () => {
  let cacheDir: string;
  beforeEach(async () => {
    cacheDir = await mkdtemp(join(tmpdir(), 'kashi-lrclib-multi-'));
  });
  afterEach(async () => {
    await rm(cacheDir, { recursive: true, force: true });
  });

  /** The 2026-08-13 field case, with the counts lrclib actually returned. */
  const MAD_LOVE = {
    title: 'Mad Love',
    artist: 'Sean Paul ve David Guetta',
    duration_ms: 200_000,
  };

  it('recovers a track the conjunction hid', async () => {
    const { fetchFn, calls } = makeFetch((url) => {
      if (url.includes('Sean+Paul+ve+David+Guetta')) {
        return url.includes('/api/get') ? jsonResponse(404, {}) : jsonResponse(200, []);
      }
      return jsonResponse(200, { id: 77, duration: 200, syncedLyrics: LRC });
    });
    const client = new LrclibClient({ cacheDir, fetchFn });

    const result = await client.getLyrics(MAD_LOVE);
    expect(result).toMatchObject({ found: true, sourceId: 77 });
    expect(calls.some((c) => c.url.includes('artist_name=Sean+Paul&'))).toBe(true);
  });

  it('keeps the duration filter on the retry', async () => {
    // The whole point of retrying HERE rather than leaning on the
    // duration-less rung: the scope that keeps another edit out stays on.
    const { fetchFn, calls } = makeFetch((url) => {
      if (url.includes('Sean+Paul+ve+David+Guetta')) {
        return url.includes('/api/get') ? jsonResponse(404, {}) : jsonResponse(200, []);
      }
      return jsonResponse(200, { id: 77, duration: 200, syncedLyrics: LRC });
    });
    const client = new LrclibClient({ cacheDir, fetchFn });

    await client.getLyrics(MAD_LOVE);
    const retries = calls.filter((c) => c.url.includes('artist_name=Sean+Paul&'));
    expect(retries.length).toBeGreaterThan(0);
    expect(retries.every((c) => c.url.includes('duration=200'))).toBe(true);
  });

  it('spends no extra request on a single-artist track', async () => {
    const { fetchFn, calls } = makeFetch((url) =>
      url.includes('/api/get') ? jsonResponse(404, {}) : jsonResponse(200, []),
    );
    const client = new LrclibClient({ cacheDir, fetchFn });

    await client.getLyrics({ title: 'Shape of You', artist: 'Ed Sheeran', duration_ms: 233_000 });
    expect(calls).toHaveLength(2); // get + search, and nothing more
  });

  it('never runs when the first rung already answered', async () => {
    const { fetchFn, calls } = makeFetch(() =>
      jsonResponse(200, { id: 5, duration: 200, syncedLyrics: LRC }),
    );
    const client = new LrclibClient({ cacheDir, fetchFn });

    await client.getLyrics(MAD_LOVE);
    expect(calls).toHaveLength(1);
  });
});

describe('negative cache is tied to the ladder that produced it', () => {
  let cacheDir: string;
  beforeEach(async () => {
    cacheDir = await mkdtemp(join(tmpdir(), 'kashi-lrclib-ladder-'));
  });
  afterEach(async () => {
    await rm(cacheDir, { recursive: true, force: true });
  });

  const QUERY = { title: 'Mad Love', artist: 'Sean Paul ve David Guetta', duration_ms: 200_000 };

  it('re-asks when the stored miss predates the current ladder', async () => {
    // The 0.27.3 field failure exactly: the multi-artist rung shipped and
    // changed nothing for the track it was built from, because the miss was
    // cached the day before. A stale-ladder miss must not answer.
    const { fetchFn, calls } = makeFetch((url) => {
      if (url.includes('Sean+Paul+ve+David+Guetta')) {
        return url.includes('/api/get') ? jsonResponse(404, {}) : jsonResponse(200, []);
      }
      return jsonResponse(200, { id: 77, duration: 200, syncedLyrics: LRC });
    });
    const client = new LrclibClient({ cacheDir, fetchFn });

    // A miss written by an older build carries no ladder version at all.
    const key = (client as unknown as { cacheKey: (q: typeof QUERY) => string }).cacheKey(QUERY);
    await writeFile(join(cacheDir, `${key}.json`), JSON.stringify({ found: false, at: Date.now() }));

    const result = await client.getLyrics(QUERY);
    expect(result.found).toBe(true);
    expect(calls.length).toBeGreaterThan(0);
  });

  it('still honours a miss the CURRENT ladder produced', async () => {
    // The cache has to keep working, or every skipped track re-asks lrclib.
    let requests = 0;
    const { fetchFn } = makeFetch((url) => {
      requests++;
      return url.includes('/api/get') ? jsonResponse(404, {}) : jsonResponse(200, []);
    });
    const client = new LrclibClient({ cacheDir, fetchFn });

    await client.getLyrics(QUERY);
    const afterFirst = requests;
    await client.getLyrics(QUERY);
    expect(requests).toBe(afterFirst); // second call never left the disk
  });
});

describe('LrclibClient — unavailability is typed (LyricFix)', () => {
  let cacheDir: string;
  const QUERY = { title: 'Song', artist: 'Band', duration_ms: 200_000 };
  const NOW = Date.parse('2026-10-16T08:00:00Z');

  beforeEach(async () => {
    cacheDir = await mkdtemp(join(tmpdir(), 'kashi-lrclib-typed-'));
  });
  afterEach(async () => {
    await rm(cacheDir, { recursive: true, force: true });
  });

  function client(handler: (url: string) => Response | Promise<Response>) {
    const fake = makeFetch(handler);
    return { c: new LrclibClient({ cacheDir, fetchFn: fake.fetchFn, nowFn: () => NOW }), calls: fake.calls };
  }

  async function failure(p: Promise<unknown>): Promise<unknown> {
    try {
      await p;
    } catch (err) {
      return err;
    }
    throw new Error('expected a rejection');
  }

  it('503 is busy and carries Retry-After; it is NOT negative-cached', async () => {
    const { c, calls } = client(
      () => new Response('{}', { status: 503, headers: { 'Retry-After': '7' } }),
    );
    const err = await failure(c.getLyrics(QUERY));
    expect(err).toBeInstanceOf(LrclibUnavailableError);
    expect(err).toMatchObject({ kind: 'busy', retryAfterMs: 7000 });
    await failure(c.getLyrics(QUERY));
    expect(calls).toHaveLength(2); // asked again: an outage is not an answer
  });

  it('429 is busy too, without a Retry-After the wait is null', async () => {
    const { c } = client(() => new Response('rate limited', { status: 429 }));
    expect(await failure(c.getLyrics(QUERY))).toMatchObject({ kind: 'busy', retryAfterMs: null });
  });

  it('any other failing status is network', async () => {
    const { c } = client(() => new Response('{}', { status: 500 }));
    expect(await failure(c.getLyrics(QUERY))).toMatchObject({ kind: 'network' });
  });

  it('a search 503 after a get 404 is busy as well', async () => {
    const { c } = client((url) =>
      url.includes('/api/get') ? jsonResponse(404, {}) : new Response('{}', { status: 503 }),
    );
    expect(await failure(c.getLyrics(QUERY))).toMatchObject({ kind: 'busy' });
  });

  it('a refused connection is network', async () => {
    const { c } = client(() => {
      throw new TypeError('fetch failed');
    });
    expect(await failure(c.getLyrics(QUERY))).toMatchObject({ kind: 'network' });
  });

  it("the caller's abort stays an abort, not an outage", async () => {
    const controller = new AbortController();
    const { c } = client(() => {
      controller.abort();
      throw new DOMException('aborted', 'AbortError');
    });
    const err = await failure(c.getLyrics(QUERY, controller.signal));
    expect(err).not.toBeInstanceOf(LrclibUnavailableError);
  });

  it('400 is a miss (the request itself is bad), cached like one', async () => {
    const { c, calls } = client(() => new Response('bad', { status: 400 }));
    expect(await c.getLyrics(QUERY)).toEqual({ found: false });
    const before = calls.length;
    expect(await c.getLyrics(QUERY)).toEqual({ found: false });
    expect(calls).toHaveLength(before);
  });
});

describe('parseRetryAfter', () => {
  const NOW = Date.parse('2026-10-16T08:00:00Z');

  it('reads delta-seconds', () => {
    expect(parseRetryAfter('1', NOW)).toBe(1000);
    expect(parseRetryAfter(' 0 ', NOW)).toBe(0);
  });

  it('reads an HTTP-date in the future, ignores one in the past', () => {
    expect(parseRetryAfter('Fri, 16 Oct 2026 08:00:30 GMT', NOW)).toBe(30_000);
    expect(parseRetryAfter('Fri, 16 Oct 2026 07:59:00 GMT', NOW)).toBeNull();
    expect(parseRetryAfter('Fri, 16 Oct 2026 08:00:00 GMT', NOW)).toBeNull();
  });

  it('never imposes more than the ceiling (a timer past 2^31 ms fires at once)', () => {
    expect(parseRetryAfter('99999999999999999999', NOW)).toBe(MAX_RETRY_AFTER_MS);
    expect(parseRetryAfter('Fri, 16 Oct 2099 08:00:00 GMT', NOW)).toBe(MAX_RETRY_AFTER_MS);
    expect(parseRetryAfter(String(MAX_RETRY_AFTER_MS / 1000), NOW)).toBe(MAX_RETRY_AFTER_MS);
  });

  it('gives null for garbage and absence', () => {
    expect(parseRetryAfter(null, NOW)).toBeNull();
    expect(parseRetryAfter('soon', NOW)).toBeNull();
    expect(parseRetryAfter('-5', NOW)).toBeNull();
  });
});

describe('LrclibClient — cooldown sits between the cache and the network', () => {
  let cacheDir: string;
  const NOW = Date.parse('2026-10-16T08:00:00Z');
  const CACHED = { title: 'Cached', artist: 'Band', duration_ms: 200_000 };
  const FRESH = { title: 'Fresh', artist: 'Band', duration_ms: 200_000 };
  const RECORD = { id: 5, syncedLyrics: '[00:01.00] la', duration: 200 };

  beforeEach(async () => {
    cacheDir = await mkdtemp(join(tmpdir(), 'kashi-lrclib-cd-'));
  });
  afterEach(async () => {
    await rm(cacheDir, { recursive: true, force: true });
  });

  async function setup() {
    let up = true;
    const fake = makeFetch(() =>
      up ? jsonResponse(200, RECORD) : new Response('{}', { status: 429, headers: { 'Retry-After': '600' } }),
    );
    const cooldown = new SourceCooldown(2000, 120_000, () => NOW);
    const client = new LrclibClient({ cacheDir, fetchFn: fake.fetchFn, nowFn: () => NOW, cooldown });
    await client.getLyrics(CACHED); // on disk from now on
    up = false;
    return { client, cooldown, calls: fake.calls, setUp: (v: boolean) => (up = v) };
  }

  it('a 429 starts the server-named wait; the next uncached lookup sends nothing', async () => {
    const { client, cooldown, calls } = await setup();
    await expect(client.getLyrics(FRESH)).rejects.toMatchObject({ kind: 'busy' });
    expect(cooldown.remainingMs()).toBe(600_000);
    const before = calls.length;
    await expect(client.getLyrics(FRESH)).rejects.toMatchObject({ kind: 'busy', retryAfterMs: 600_000 });
    expect(calls).toHaveLength(before);
  });

  it('cached lyrics are still served during the wait — and do not end it', async () => {
    const { client, cooldown } = await setup();
    await expect(client.getLyrics(FRESH)).rejects.toBeInstanceOf(LrclibUnavailableError);
    expect(await client.getLyrics(CACHED)).toMatchObject({ found: true, sourceId: 5 });
    expect(cooldown.remainingMs()).toBe(600_000);
  });

  it('a real network answer ends it — the back-off starts over', async () => {
    let t = NOW;
    const fake = makeFetch(() => jsonResponse(200, RECORD));
    const cooldown = new SourceCooldown(2000, 120_000, () => t);
    const client = new LrclibClient({ cacheDir, fetchFn: fake.fetchFn, nowFn: () => t, cooldown });
    cooldown.noteFailure(null);
    cooldown.noteFailure(null); // two failures: the next would wait 8 s
    t += 10_000; // both waits are over
    await client.getLyrics(FRESH);
    cooldown.noteFailure(null);
    expect(cooldown.remainingMs()).toBe(2000); // base again, not 8 s
  });

  it("an abort is not an outage: the cooldown is not charged", async () => {
    const controller = new AbortController();
    const cooldown = new SourceCooldown(2000, 120_000, () => NOW);
    const fake = makeFetch(() => {
      controller.abort();
      throw new DOMException('aborted', 'AbortError');
    });
    const client = new LrclibClient({ cacheDir, fetchFn: fake.fetchFn, nowFn: () => NOW, cooldown });
    await expect(client.getLyrics(FRESH, controller.signal)).rejects.toThrow();
    expect(cooldown.remainingMs()).toBe(0);
  });
});
