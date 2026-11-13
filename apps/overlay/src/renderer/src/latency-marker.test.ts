import { describe, expect, it } from 'vitest';
import {
  LATENCY_MARKER,
  MARKER_CELLS,
  MARKER_CODE_BITS,
  lineWordOffsets,
  markerCells,
} from './latency-marker.js';

describe('latency marker (dev-only, KASHI_LATENCY_MARKER=1 builds)', () => {
  it('is off without the build flag', () => {
    // Nothing defines __KASHI_LATENCY_MARKER__ here, exactly as in a build
    // made without KASHI_LATENCY_MARKER=1 (which folds it to false).
    expect(LATENCY_MARKER).toBe(false);
  });

  it('encodes parity then the word index mod 64, most significant bit first', () => {
    expect(MARKER_CELLS).toBe(7);
    expect(MARKER_CODE_BITS).toBe(6);
    expect(markerCells(0, true)).toEqual([true, false, false, false, false, false, false]);
    expect(markerCells(5, false)).toEqual([false, false, false, false, true, false, true]);
    expect(markerCells(63, true)).toEqual([true, true, true, true, true, true, true]);
    expect(markerCells(64, true)).toEqual(markerCells(0, true)); // wraps
    expect(markerCells(64 + 37, false)).toEqual(markerCells(37, false));
  });

  it('numbers words through the whole document', () => {
    expect(lineWordOffsets([{ words: [1, 2, 3] }, {}, { words: [1] }, { words: [1, 2] }])).toEqual([
      0, 3, 3, 4,
    ]);
  });
});
