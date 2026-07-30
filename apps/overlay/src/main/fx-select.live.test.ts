/**
 * OPT-IN: does the client actually honour a REAL selected document?
 *
 *   KASHI_LIVE_URL=https://kashi.railguncnr.com \
 *   KASHI_LIVE_KEY=$(cat ~/kashi-user-key-k8s.txt) \
 *   KASHI_LIVE_TRACK=IL74nTSQxds \
 *   pnpm --filter kashi-overlay exec vitest run fx-select.live
 *
 * Why this exists rather than another unit test. The `fx.select` marker is a
 * contract between two processes and a transport: the server stamps it, the
 * main process maps it, the renderer changes behaviour on it. Every half was
 * unit-tested and green while nothing had ever carried the marker end to end
 * — which is exactly the shape of the Faz 6.7 failure, where a layer with a
 * full green suite did not run at all.
 *
 * It reads only counts and indices out of the document. Lyric text is never
 * asserted on or printed.
 */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { buildFxIndex, MAX_FX_PER_LINE } from '../renderer/src/effects-logic.js';
import { KashiServerClient } from './kashi-server.js';
import type { FxData, LyricLine } from '../shared/lyrics.js';

const LIVE_URL = process.env['KASHI_LIVE_URL'];
const LIVE_KEY = process.env['KASHI_LIVE_KEY'];
/** A track the 2.13.0 validation wave reprocessed. */
const TRACK = process.env['KASHI_LIVE_TRACK'] ?? 'IL74nTSQxds';

describe.skipIf(!LIVE_URL || !LIVE_KEY)('live fx selection', () => {
  let dir: string;
  let lines: readonly LyricLine[];
  let fx: FxData | undefined;

  beforeAll(async () => {
    dir = await mkdtemp(join(tmpdir(), 'kashi-fxsel-'));
    const client = new KashiServerClient({
      baseUrl: LIVE_URL!,
      apiKey: LIVE_KEY!,
      cacheDir: dir,
      fetchFn: fetch,
      log: () => {},
    });
    const result = await client.getProcessed('youtube', TRACK);
    if (!('found' in result) || !result.found) throw new Error(`no document for ${TRACK}`);
    lines = result.lines as readonly LyricLine[];
    fx = result.fx;
  });

  afterAll(async () => {
    if (dir) await rm(dir, { recursive: true, force: true });
  });

  it('the marker survives the whole trip to the renderer', () => {
    // mapFx is deliberately strict about what it forwards; a marker dropped
    // here would silently leave the client on its legacy rule and nobody
    // would see an error — only fewer effects than the server intended.
    // ANY non-empty value means "the server already chose" — that is the
    // whole contract, and pinning one version would break this test on every
    // plan bump while proving nothing extra.
    expect(fx?.select).toMatch(/^density\/\d+\.\d+$/);
  });

  it('a real selected document produces the effects the server chose', () => {
    const index = buildFxIndex(fx, lines);
    const hits = [...index.values()].flat();

    // Every word the server kept is rendered — the whole point of the marker.
    expect(hits.length).toBe(fx!.words!.length);
    expect(hits.length).toBeGreaterThan(0);

    // And it is a THINNED set: this track carried 60 candidates before 2.13.0.
    expect(hits.length).toBeLessThanOrEqual(24);
  });

  it('the multi-hit path really engages on real data', () => {
    // The path that only a selected document can reach. If the marker were
    // ignored, every line here would collapse to exactly one.
    const index = buildFxIndex(fx, lines);
    const multi = [...index.values()].filter((line) => line.length > 1);
    expect(multi.length).toBeGreaterThan(0);
    for (const line of multi) expect(line.length).toBeLessThanOrEqual(MAX_FX_PER_LINE);
  });

  it('the spacing the server promised holds after the client parses it', () => {
    // The server enforces at least two plain words between two effects. This
    // checks the promise survives mapping and indexing, not that the server
    // computed it — a reordering bug in either would show up here.
    for (const line of buildFxIndex(fx, lines).values()) {
      const words = line.map((hit) => hit.word);
      expect([...words].sort((a, b) => a - b)).toEqual(words); // word order
      for (let i = 1; i < words.length; i += 1) {
        expect(words[i]! - words[i - 1]!).toBeGreaterThan(2);
      }
    }
  });

  it('the same document with its marker removed falls back to one per line', () => {
    // The safety direction: an archive document has no marker, and rendering
    // all of its candidates would put two or three effects where there is one
    // today. Proven here against real data rather than a fixture.
    const legacy = { ...fx!, select: undefined };
    for (const line of buildFxIndex(legacy, lines).values()) {
      expect(line.length).toBe(1);
    }
  });
});
