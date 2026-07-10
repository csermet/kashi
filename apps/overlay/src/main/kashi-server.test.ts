import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { KashiServerClient } from './kashi-server.js';
import { cacheFileName } from './kashi-server-logic.js';

function healthyDoc() {
  return {
    schema_version: 1,
    sync: 'word',
    alignment: { quality_score: 0.7 },
    lines: [
      {
        start_ms: 0,
        end_ms: 900,
        text: 'hello',
        words: [{ start_ms: 0, end_ms: 900, text: 'hello' }],
      },
    ],
  };
}

type FetchStep = { status: number; body?: unknown; etag?: string; fail?: boolean };

function fetchScript(steps: FetchStep[], calls: Array<{ url: string; headers: Headers }>) {
  let index = 0;
  return (async (url: RequestInfo | URL, init?: RequestInit) => {
    const step = steps[Math.min(index++, steps.length - 1)]!;
    calls.push({ url: String(url), headers: new Headers(init?.headers) });
    if (step.fail) throw new Error('ECONNREFUSED');
    return new Response(step.status === 304 ? null : JSON.stringify(step.body ?? {}), {
      status: step.status,
      headers: step.etag ? { ETag: `"${step.etag}"` } : {},
    });
  }) as typeof fetch;
}

describe('KashiServerClient', () => {
  let dir: string;
  const calls: Array<{ url: string; headers: Headers }> = [];

  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), 'kashi-client-'));
    calls.length = 0;
  });
  afterEach(async () => {
    await rm(dir, { recursive: true, force: true });
  });

  function client(steps: FetchStep[]) {
    return new KashiServerClient({
      baseUrl: 'http://server.test',
      apiKey: 'ksh_' + 'a'.repeat(32),
      cacheDir: dir,
      fetchFn: fetchScript(steps, calls),
    });
  }

  it('200 -> payload + cache file with the etag', async () => {
    const c = client([{ status: 200, body: healthyDoc(), etag: 'tag1' }]);
    const result = await c.getProcessed('youtube', 'vid1');
    expect(result).toMatchObject({ found: true, sync: 'word' });
    expect(calls[0]!.url).toBe('http://server.test/v1/lyrics/youtube/vid1');
    expect(calls[0]!.headers.get('authorization')).toContain('ksh_');
    const cached = JSON.parse(await readFile(join(dir, cacheFileName('youtube', 'vid1')), 'utf8'));
    expect(cached.etag).toBe('tag1');
  });

  it('sends If-None-Match and serves the cached doc on 304', async () => {
    const c = client([
      { status: 200, body: healthyDoc(), etag: 'tag1' },
      { status: 304 },
    ]);
    await c.getProcessed('youtube', 'vid1');
    const second = await c.getProcessed('youtube', 'vid1');
    expect(second).toMatchObject({ found: true });
    expect(calls[1]!.headers.get('if-none-match')).toBe('"tag1"');
  });

  it('404 -> found:false and the stale cache entry is deleted', async () => {
    const path = join(dir, cacheFileName('youtube', 'vid1'));
    await writeFile(path, JSON.stringify({ etag: 'x', document: healthyDoc() }));
    const c = client([{ status: 404 }]);
    expect(await c.getProcessed('youtube', 'vid1')).toEqual({ found: false });
    await expect(readFile(path, 'utf8')).rejects.toThrow();
  });

  it('network failure -> cached document when present, error otherwise', async () => {
    const path = join(dir, cacheFileName('youtube', 'vid1'));
    await writeFile(path, JSON.stringify({ etag: 'x', document: healthyDoc() }));
    const c = client([{ status: 0, fail: true }]);
    expect(await c.getProcessed('youtube', 'vid1')).toMatchObject({ found: true });

    const cNoCache = client([{ status: 0, fail: true }]);
    expect(await cNoCache.getProcessed('youtube', 'vidMiss')).toEqual({ error: true });
  });

  it('5xx -> stale-or-error, malformed 200 body -> error and NOT cached', async () => {
    const c = client([{ status: 500 }]);
    expect(await c.getProcessed('youtube', 'vidX')).toEqual({ error: true });

    const cBad = client([{ status: 200, body: { schema_version: 99 } }]);
    expect(await cBad.getProcessed('youtube', 'vidY')).toEqual({ error: true });
    await expect(readFile(join(dir, cacheFileName('youtube', 'vidY')), 'utf8')).rejects.toThrow();
  });

  it('enqueue posts the source+hints and never throws', async () => {
    const c = client([{ status: 202, body: { job_id: 'x', status: 'queued' } }]);
    await c.enqueue({ type: 'youtube', id: 'vid1' }, { title: 'T', artist: 'A' });
    expect(calls[0]!.url).toBe('http://server.test/v1/ingest');

    const cDown = client([{ status: 0, fail: true }]);
    await expect(
      cDown.enqueue({ type: 'youtube', id: 'vid1' }, { title: 'T', artist: 'A' }),
    ).resolves.toBeUndefined();
  });
});
