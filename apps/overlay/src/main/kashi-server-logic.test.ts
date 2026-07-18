import { describe, expect, it } from 'vitest';
import {
  cacheFileName,
  mapDocument,
  mapFx,
  normalizeServerUrl,
  QUALITY_GATE,
} from './kashi-server-logic.js';

function doc(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    sync: 'word',
    alignment: { quality_score: 0.7 },
    lines: [
      {
        start_ms: 1000,
        end_ms: 2000,
        text: 'hello world',
        words: [
          { start_ms: 1000, end_ms: 1400, text: 'hello' },
          { start_ms: 1500, end_ms: 2000, text: 'world' },
        ],
      },
    ],
    ...overrides,
  };
}

describe('normalizeServerUrl', () => {
  it('accepts http(s) and strips trailing slashes', () => {
    expect(normalizeServerUrl('http://cnr-intel:8080/')).toBe('http://cnr-intel:8080');
    expect(normalizeServerUrl('https://kashi.example.com')).toBe('https://kashi.example.com');
  });

  it('rejects garbage', () => {
    for (const bad of [null, 42, '', 'ftp://x', 'not a url', 'http://has space']) {
      expect(normalizeServerUrl(bad)).toBeNull();
    }
  });
});

describe('cacheFileName', () => {
  it('is stable and path-safe', () => {
    expect(cacheFileName('youtube', 'dQw4w9WgXcQ')).toBe('youtube_dQw4w9WgXcQ_v1.json');
    expect(cacheFileName('upload', 'a/b')).not.toContain('/');
  });
});

describe('mapDocument', () => {
  it('maps a healthy word document', () => {
    const payload = mapDocument(doc());
    expect(payload).not.toBeNull();
    expect(payload!.sync).toBe('word');
    expect(payload!.qualityScore).toBe(0.7);
    expect(payload!.lines[0]!.words).toHaveLength(2);
  });

  it('applies the quality gate: low-score word docs become line mode', () => {
    const payload = mapDocument(doc({ alignment: { quality_score: QUALITY_GATE - 0.01 } }));
    expect(payload!.sync).toBe('line');
    expect(payload!.lines[0]!.words).toBeUndefined();
  });

  it('keeps word mode exactly at the gate', () => {
    const payload = mapDocument(doc({ alignment: { quality_score: QUALITY_GATE } }));
    expect(payload!.sync).toBe('word');
  });

  it('passes the adlib line flag through, tolerating docs without it', () => {
    const flagged = mapDocument(
      doc({
        lines: [
          {
            start_ms: 1000,
            end_ms: 2000,
            text: 'Oh-ooh, whoa-oh',
            adlib: true,
            words: [{ start_ms: 1000, end_ms: 2000, text: 'Oh-ooh' }],
          },
        ],
      }),
    );
    expect(flagged!.lines[0]!.adlib).toBe(true);
    // Pre-2.1.0 documents lack the field entirely — tolerant parse.
    expect(mapDocument(doc())!.lines[0]!.adlib).toBeUndefined();
    // Garbage values never map to true (untrusted document).
    const garbage = mapDocument(
      doc({
        lines: [{ start_ms: 0, end_ms: 1, text: 'x', adlib: 'yes' }],
      }),
    );
    expect(garbage!.lines[0]!.adlib).toBeUndefined();
  });

  it('passes palette and beats through for Faz 4', () => {
    const payload = mapDocument(
      doc({
        palette: { primary: '#e84545' },
        beats: { bpm: 120, times_ms: [0, 500] },
      }),
    );
    expect(payload!.palette?.primary).toBe('#e84545');
    expect(payload!.beats?.bpm).toBe(120);
  });

  it('rejects malformed documents', () => {
    expect(mapDocument(null)).toBeNull();
    expect(mapDocument({})).toBeNull();
    expect(mapDocument(doc({ schema_version: 2 }))).toBeNull();
    expect(mapDocument(doc({ sync: 'sentence' }))).toBeNull();
    expect(mapDocument(doc({ lines: 'nope' }))).toBeNull();
    expect(
      mapDocument(doc({ lines: [{ start_ms: -1, end_ms: 2, text: 'x' }] })),
    ).toBeNull();
    expect(
      mapDocument(doc({ lines: [{ start_ms: 1.5, end_ms: 2, text: 'x' }] })),
    ).toBeNull();
  });

  it('tolerates unknown extra fields (additive schema)', () => {
    expect(mapDocument(doc({ future_field: { anything: true } }))).not.toBeNull();
  });
});

describe('mapFx (Faz 6)', () => {
  const base = { lexicon: 'kashi-fx/1.0.0', engine: 'keywords' };

  it('keeps valid word tags on word sync, clamping intensity', () => {
    const fx = mapFx(
      { ...base, words: [{ line: 0, word: 1, tag: 'love', intensity: 3 }] },
      'word',
    );
    expect(fx).toEqual({
      ...base,
      words: [{ line: 0, word: 1, tag: 'love', intensity: 1 }],
    });
  });

  it('drops word tags on line sync (quality gate stripped the words)', () => {
    const fx = mapFx(
      { ...base, words: [{ line: 0, word: 1, tag: 'love', intensity: 0.6 }] },
      'line',
    );
    expect(fx).toBeUndefined();
  });

  it('drops malformed ENTRIES, never the block; empty result is undefined', () => {
    const fx = mapFx(
      {
        ...base,
        words: [
          { line: -1, word: 0, tag: 'x', intensity: 0.5 },
          { line: 0.5, word: 0, tag: 'x', intensity: 0.5 },
          { line: 0, word: 0, tag: '', intensity: 0.5 },
          { line: 0, word: 0, tag: 'fire', intensity: 'hot' },
          { line: 2, word: 3, tag: 'fire', intensity: 0.8 },
        ],
        lines: [{ line: 1, tag: 'night' }, { line: 'x', tag: 'bad' }],
      },
      'word',
    );
    expect(fx!.words).toEqual([{ line: 2, word: 3, tag: 'fire', intensity: 0.8 }]);
    expect(fx!.lines).toEqual([{ line: 1, tag: 'night' }]);
    expect(mapFx({ ...base }, 'word')).toBeUndefined();
    expect(mapFx('garbage', 'word')).toBeUndefined();
    expect(mapFx({ lexicon: 1, engine: 'keywords' }, 'word')).toBeUndefined();
  });
});
