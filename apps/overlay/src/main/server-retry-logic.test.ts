import { describe, expect, it } from 'vitest';
import {
  isRetryable,
  RETRY_DELAYS_MS,
  retryDelayMs,
  shouldUpgrade,
  type DisplayedLyrics,
  enrichmentKeys,
} from './server-retry-logic.js';

const serverDoc = (sync: 'word' | 'line', qualityScore = 0.9) =>
  ({ found: true, source: 'kashi-server', sync, qualityScore, lines: [] }) as never;

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

  it('re-rendering the same quality is flicker, not an upgrade', () => {
    expect(shouldUpgrade(serverWord, serverDoc('word', 0.8))).toBe(false);
    expect(shouldUpgrade(serverWord, serverDoc('word', 0.7))).toBe(false);
    expect(shouldUpgrade(serverWord, serverDoc('word', 0.9))).toBe(true);
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

  it('upgrades on equal quality when the incoming document is genuinely richer', () => {
    // The case that made this rule necessary: a reprocess that adds effects
    // leaves alignment untouched, so the score is a wash while the document
    // really is better. Comparing scores alone declined it, and the user kept
    // an unthemed document for the whole song.
    const current = shown({ enrichment: ['beats'] });
    expect(shouldUpgrade(current, doc({ beats: {}, fx: { select: 'density/1.1' } }))).toBe(true);
  });

  it('declines the identical document — re-rendering it is pure flicker', () => {
    const current = shown({ enrichment: ['beats', 'fx'] });
    expect(shouldUpgrade(current, doc({ beats: {}, fx: {} }))).toBe(false);
  });

  it('declines when each document has something the other lacks', () => {
    // Otherwise two probes would swap back and forth all song.
    const current = shown({ enrichment: ['palette'] });
    expect(shouldUpgrade(current, doc({ beats: {} }))).toBe(false);
  });

  it('still declines a strictly worse alignment, however rich it is', () => {
    const current = shown({ qualityScore: 0.9, enrichment: [] });
    expect(shouldUpgrade(current, doc({ qualityScore: 0.5, fx: {}, beats: {} }))).toBe(false);
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
