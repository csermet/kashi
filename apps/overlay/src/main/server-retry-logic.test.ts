import { describe, expect, it } from 'vitest';
import {
  isRetryable,
  RETRY_DELAYS_MS,
  retryDelayMs,
  shouldUpgrade,
  type DisplayedLyrics,
  enrichmentKeys,
} from './server-retry-logic.js';

const serverDoc = (sync: 'word' | 'line', qualityScore = 0.9, extra: Record<string, unknown> = {}) =>
  ({ found: true, source: 'kashi-server', sync, qualityScore, lines: [], ...extra }) as never;

describe('retryDelayMs', () => {
  it('widens, then ends — a spent schedule returns null', () => {
    expect(retryDelayMs(0)).toBe(10_000);
    expect(retryDelayMs(1)).toBe(20_000);
    expect(retryDelayMs(RETRY_DELAYS_MS.length - 1)).toBe(60_000);
    expect(retryDelayMs(RETRY_DELAYS_MS.length)).toBeNull();
  });

  it('spans about a song, so a dead server stops being asked', () => {
    const total = RETRY_DELAYS_MS.reduce((a, b) => a + b, 0);
    expect(total).toBeGreaterThan(120_000);
    expect(total).toBeLessThan(240_000);
  });
});

describe('isRetryable', () => {
  it('retries an error and accepts a 404 as an answer', () => {
    expect(isRetryable({ error: true })).toBe(true);
    expect(isRetryable({ found: false })).toBe(false);
    expect(isRetryable(serverDoc('word'))).toBe(false);
  });
});

describe('shouldUpgrade', () => {
  const lrclibLine: DisplayedLyrics = { source: 'lrclib', sync: 'line' };
  const serverWord: DisplayedLyrics = { source: 'kashi-server', sync: 'word', qualityScore: 0.8 };

  it('word timing over line timing is always worth the swap', () => {
    expect(shouldUpgrade(lrclibLine, serverDoc('word'))).toBe(true);
  });

  it('fills an empty screen', () => {
    expect(shouldUpgrade({ source: 'none', sync: 'line' }, serverDoc('line'))).toBe(true);
  });

  it('never downgrades word timing to line timing', () => {
    expect(shouldUpgrade(serverWord, serverDoc('line'))).toBe(false);
  });

  it('a revalidated (304) document is flicker, not an upgrade — whatever it scores', () => {
    // Not fresh = byte-identical to the cache the display came from.
    expect(shouldUpgrade(serverWord, serverDoc('word', 0.8))).toBe(false);
    expect(shouldUpgrade(serverWord, serverDoc('word', 0.7))).toBe(false);
    expect(shouldUpgrade(serverWord, serverDoc('word', 0.9))).toBe(false);
  });

  it('a fresh (200) document is authoritative — even at a LOWER score', () => {
    // The audit case: a reprocessed document with better timings and a lower
    // score was pinned out for the whole song. The score ranks real accuracy
    // at Spearman +0.24; the server rewriting its own document is the signal.
    expect(shouldUpgrade(serverWord, serverDoc('word', 0.5, { fresh: true }))).toBe(true);
    expect(shouldUpgrade(serverWord, serverDoc('word', 0.9, { fresh: true }))).toBe(true);
  });

  it('a stale cache fallback never upgrades — it IS the display', () => {
    expect(shouldUpgrade(serverWord, serverDoc('word', 0.9, { stale: true }))).toBe(false);
  });

  it('will not swap an lrclib line doc for a server line doc', () => {
    // Same granularity, no comparable score — the visible re-render buys
    // nothing the user can perceive.
    expect(shouldUpgrade(lrclibLine, serverDoc('line'))).toBe(false);
  });

  it('a miss or an error never replaces anything', () => {
    expect(shouldUpgrade(lrclibLine, { found: false })).toBe(false);
    expect(shouldUpgrade(lrclibLine, { error: true })).toBe(false);
  });
});

describe('shouldUpgrade — enrichment, not just quality (P7)', () => {
  const shown = (over: Partial<DisplayedLyrics> = {}): DisplayedLyrics => ({
    source: 'kashi-server',
    sync: 'word',
    qualityScore: 0.9,
    ...over,
  });
  const doc = (over: Record<string, unknown> = {}) =>
    ({ found: true, source: 'kashi-server', sync: 'word', qualityScore: 0.9, lines: [], ...over }) as never;

  it('a richer reprocess arrives as a 200, and fresh is what admits it', () => {
    // The case that made the old enrichment rule necessary: a reprocess that
    // adds effects leaves the score a wash. Such a document ALWAYS arrives as
    // a 200 (its content differs from the cache), so fresh admits it without
    // score archaeology.
    const current = shown({ enrichment: ['beats'] });
    expect(
      shouldUpgrade(current, doc({ beats: {}, fx: { select: 'density/1.1' }, fresh: true })),
    ).toBe(true);
    // ...and the identical document arrives as a 304 (not fresh): declined.
    expect(shouldUpgrade(current, doc({ beats: {} }))).toBe(false);
  });

  it('enrichmentKeys names what a document carries, sorted and stable', () => {
    expect(enrichmentKeys(doc({ fx: { select: 'density/1.1' }, beats: {} }))).toEqual([
      'beats',
      'fx',
      'fx.select',
    ]);
    expect(enrichmentKeys({ found: false } as never)).toEqual([]);
  });
});
