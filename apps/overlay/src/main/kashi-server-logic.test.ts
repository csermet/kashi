import { describe, expect, it } from 'vitest';
import {
  cacheFileName,
  mapDocument,
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
