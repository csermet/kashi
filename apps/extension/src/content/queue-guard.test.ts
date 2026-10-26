import { describe, expect, it } from 'vitest';
import { parseQueueEntries } from './queue-guard.js';

const entry = (over: Record<string, unknown> = {}) => ({
  videoId: 'abcdefghijk',
  title: 'Song',
  artist: 'Artist',
  durationMs: 200_000,
  ...over,
});

describe('parseQueueEntries', () => {
  it('accepts up to two well-formed entries, and an empty list', () => {
    expect(parseQueueEntries([entry(), entry({ videoId: 'bcdefghijkl' })])).toHaveLength(2);
    expect(parseQueueEntries([])).toEqual([]);
  });

  it('keeps only the known fields', () => {
    expect(parseQueueEntries([entry({ extra: '<script>' })])).toEqual([entry()]);
    const { durationMs: _drop, ...noDuration } = entry();
    expect(parseQueueEntries([noDuration])).toEqual([noDuration]);
  });

  it('rejects a list longer than two', () => {
    expect(parseQueueEntries([entry(), entry(), entry()])).toBeNull();
  });

  it('rejects the whole post on one bad entry', () => {
    const bad = [
      { videoId: 'short' },
      { title: '' },
      { title: '   ' },
      { artist: 'x'.repeat(301) },
      { durationMs: 0 },
      { durationMs: -5 },
      { durationMs: Number.NaN },
      { durationMs: 200_000.5 },
      { durationMs: 3 * 3600_000 + 1 },
      { durationMs: '200000' },
    ];
    for (const over of bad) expect(parseQueueEntries([entry(), entry(over)])).toBeNull();
  });

  it('rejects things that are not a list of objects', () => {
    for (const value of [null, undefined, 'x', 3, {}, [null], ['x']]) {
      expect(parseQueueEntries(value)).toBeNull();
    }
  });
});
