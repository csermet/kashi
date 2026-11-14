/**
 * The shared LRC vectors (packages/schemas/vectors/lrc-lines.json), overlay side.
 *
 * The server runs the same file in apps/server/tests/test_lrc_vectors.py, so the
 * two lrclib parsers cannot drift apart on what a line says (2026-11-14:
 * enhanced-LRC word tags reached the screen as text on both twins).
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { isDegenerateSynced, parseLrc, parseLrcLine, stripInlineTags } from './lrclib.js';

interface LineVector {
  case: string;
  raw: string;
  stamps_ms: number[];
  text: string | null;
}

interface RecordVector {
  case: string;
  synced: string;
  duration_s: number | null;
  degenerate: boolean;
  texts: string[] | null;
  starts_ms: number[] | null;
}

const VECTORS = JSON.parse(
  readFileSync(new URL('../../../../packages/schemas/vectors/lrc-lines.json', import.meta.url), 'utf8'),
) as { lines: LineVector[]; records: RecordVector[] };

describe('shared LRC line vectors', () => {
  it.each(VECTORS.lines.map((v) => [v.case, v] as const))('%s', (_name, v) => {
    const parsed = parseLrcLine(v.raw);
    if (v.text === null) {
      expect(parsed).toBeNull();
    } else {
      expect(parsed).toEqual({ stamps: v.stamps_ms, text: v.text });
    }
  });
});

describe('shared LRC record vectors', () => {
  it.each(VECTORS.records.map((v) => [v.case, v] as const))('%s', (_name, v) => {
    expect(isDegenerateSynced(v.synced, v.duration_s)).toBe(v.degenerate);
    if (!v.degenerate) {
      const lines = parseLrc(v.synced);
      expect(lines.map((line) => line.text)).toEqual(v.texts);
      expect(lines.map((line) => line.start_ms)).toEqual(v.starts_ms);
    }
  });
});

describe('inline tag strip', () => {
  it('only trims text without a tag', () => {
    expect(stripInlineTags('  Velo   tarin\tmox  ')).toBe('Velo   tarin\tmox');
  });

  it('stays linear on a long blank run that never reaches a tag (ReDoS shape)', () => {
    const text = `Velo${' '.repeat(50_000)}x`;
    const started = performance.now();
    expect(stripInlineTags(text)).toBe(text);
    expect(performance.now() - started).toBeLessThan(500);
  });
});
