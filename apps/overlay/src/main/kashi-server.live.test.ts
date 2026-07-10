/**
 * OPT-IN integration test against a REAL kashi-server (skipped by default).
 *
 *   KASHI_LIVE_URL=http://localhost:8080 KASHI_LIVE_KEY=ksh_... pnpm vitest run kashi-server.live
 *
 * Expects the server to have processed at least one track (the 3A acceptance
 * run leaves youtube:dQw4w9WgXcQ in the database).
 */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { KashiServerClient } from './kashi-server.js';

const LIVE_URL = process.env['KASHI_LIVE_URL'];
const LIVE_KEY = process.env['KASHI_LIVE_KEY'];
const PROCESSED_ID = process.env['KASHI_LIVE_TRACK'] ?? 'dQw4w9WgXcQ';

describe.skipIf(!LIVE_URL || !LIVE_KEY)('live kashi-server', () => {
  let dir: string;
  let client: KashiServerClient;

  beforeAll(async () => {
    dir = await mkdtemp(join(tmpdir(), 'kashi-live-'));
    client = new KashiServerClient({
      baseUrl: LIVE_URL!,
      apiKey: LIVE_KEY!,
      cacheDir: dir,
      fetchFn: fetch,
    });
  });
  afterAll(async () => {
    await rm(dir, { recursive: true, force: true });
  });

  it('fetches the processed document and revalidates via ETag', async () => {
    const first = await client.getProcessed('youtube', PROCESSED_ID);
    expect(first).toMatchObject({ found: true, source: 'kashi-server' });
    if (!('found' in first) || !first.found) throw new Error('unreachable');
    expect(first.lines.length).toBeGreaterThan(10);
    if (first.sync === 'word') {
      expect(first.lines.some((l) => (l.words?.length ?? 0) > 0)).toBe(true);
    }

    // Second call must revalidate from cache (304 path).
    const second = await client.getProcessed('youtube', PROCESSED_ID);
    expect(second).toMatchObject({ found: true, sync: first.sync });
  });

  it('answers found:false for an unprocessed track', async () => {
    expect(await client.getProcessed('youtube', 'zzUnprocessed0')).toEqual({ found: false });
  });
});
