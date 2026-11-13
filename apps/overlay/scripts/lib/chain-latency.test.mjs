import { describe, expect, it } from 'vitest';
import {
  cellMeans,
  cellRects,
  codeChanges,
  coarseOffset,
  crossCorrelate,
  fft,
  fineOffsets,
  fitOffsets,
  frameCodes,
  latencySummary,
  MarkerFinder,
  matchActivations,
  peakLag,
} from './chain-latency.mjs';

/** Seeded uniform noise in [-1, 1). */
function noise(n, seed) {
  let s = seed;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i += 1) {
    s = (s * 1103515245 + 12345) % 2 ** 31;
    out[i] = (s / 2 ** 31) * 2 - 1;
  }
  return out;
}

/** Noise with a slow loudness contour, so envelopes have something to align. */
function musicLike(n, seed, rate) {
  const out = noise(n, seed);
  for (let i = 0; i < n; i += 1) {
    const t = i / rate;
    out[i] *= 0.15 + 0.85 * Math.abs(Math.sin(2.1 * t) * Math.sin(0.7 * t + 1));
  }
  return out;
}

describe('fft', () => {
  it('matches a naive DFT', () => {
    const n = 64;
    const x = noise(n, 3);
    const re = Float64Array.from(x);
    const im = new Float64Array(n);
    fft(re, im);
    for (const k of [0, 1, 7, 31, 32, 63]) {
      let sr = 0;
      let si = 0;
      for (let t = 0; t < n; t += 1) {
        sr += x[t] * Math.cos((-2 * Math.PI * k * t) / n);
        si += x[t] * Math.sin((-2 * Math.PI * k * t) / n);
      }
      expect(re[k]).toBeCloseTo(sr, 9);
      expect(im[k]).toBeCloseTo(si, 9);
    }
    fft(re, im, true);
    expect(re[5]).toBeCloseTo(x[5], 9);
  });
});

describe('cross-correlation', () => {
  it('finds a known shift, with the documented sign', () => {
    const y = noise(4000, 11);
    const d = 123;
    // x[t] = y[t - d]: x is y delayed by d samples.
    const x = new Float32Array(4000);
    for (let t = d; t < 4000; t += 1) x[t] = y[t - d];
    expect(peakLag(crossCorrelate(x, y)).lag).toBeCloseTo(d, 2);
    expect(peakLag(crossCorrelate(x, y, { phat: true })).lag).toBeCloseTo(d, 2);
  });
});

describe('audio alignment', () => {
  const rate = 16000;
  const reference = musicLike(20 * rate, 5, rate);
  // The recording: 1.2345 s of quiet hiss, then the song mixed with other
  // sound (another "instrument") at half level, plus hiss throughout.
  const lead = Math.round(1.2345 * rate);
  const recording = new Float32Array(lead + reference.length);
  const other = musicLike(recording.length, 9, rate);
  const hiss = noise(recording.length, 13);
  for (let i = 0; i < recording.length; i += 1) {
    const song = i >= lead ? reference[i - lead] : 0;
    recording[i] = song + 0.5 * other[i] * (i >= lead ? 1 : 0) + 0.02 * hiss[i];
  }

  it('places the song in the recording to the sample', () => {
    const coarse = coarseOffset(recording, reference, rate);
    expect(Math.abs(coarse.offsetS - 1.2345)).toBeLessThan(0.015);
    const fine = fineOffsets(recording, reference, rate, coarse.offsetS, { segmentS: 2, stepS: 5 });
    expect(fine.length).toBeGreaterThanOrEqual(3);
    const line = fitOffsets(fine);
    expect(line.used).toBe(fine.length);
    expect(Math.abs(line.a + line.b * 10 - lead / rate)).toBeLessThan(0.0005);
  });

  it('follows a recording clock that drifts against the song', () => {
    // 200 ppm: the recording's clock runs fast; the offset grows 2 ms per 10 s.
    const points = [2, 12, 22, 32, 42].map((atS) => ({ atS, offsetS: 1 + 0.0002 * atS, ratio: 5 }));
    points.push({ atS: 27, offsetS: 1.5, ratio: 5 }); // a stray segment
    const line = fitOffsets(points);
    expect(line.used).toBe(5);
    expect(line.a).toBeCloseTo(1, 6);
    expect(line.b).toBeCloseTo(0.0002, 8);
  });
});

describe('marker reading', () => {
  it('turns cell means into codes and skips frames caught mid-change', () => {
    const W = 250;
    const B = 5;
    const frames = [
      [B, B, B, B, B, B, B], // before the first word: all black
      [W, B, B, B, B, B, B], // parity 1, word 0
      [W, B, B, B, B, B, B],
      [B, B, B, B, B, B, W], // parity 0, word 1
      [128, B, B, B, B, W, B], // mid-change: dropped
      [W, B, B, B, B, W, B], // parity 1, word 2
    ];
    const codes = frameCodes(frames);
    expect(codes).toEqual([0, 64, 64, 1, null, 66]);
    expect(codeChanges(codes)).toEqual([
      { frame: 1, code: 64 },
      { frame: 3, code: 1 },
      { frame: 5, code: 66 },
    ]);
    // A recording that starts with a code already up: that is no change.
    expect(codeChanges([null, 66, 66, 3])).toEqual([{ frame: 3, code: 3 }]);
  });

  it('matches each change to its word through the mod-64 wrap', () => {
    const activations = [
      { g: 5, mediaS: 1.0 },
      { g: 6, mediaS: 1.05 }, // nearer in time to the first change, but not its code
      { g: 69, mediaS: 30.0 }, // 69 mod 64 = 5, the same code as g 5
    ];
    const changes = [
      { code: 64 + 5, mediaS: 1.06 },
      { code: 5, mediaS: 30.05 },
      { code: 64 + 40, mediaS: 12 }, // no such word nearby
    ];
    const { matched, unmatched, missed } = matchActivations(changes, activations);
    expect(matched.map((m) => [m.g, Math.round(m.latencyMs)])).toEqual([
      [5, 60],
      [69, 50],
    ]);
    expect(unmatched).toHaveLength(1);
    expect(missed).toBe(1);
  });

  it('summarises with median and IQR', () => {
    const s = latencySummary([10, 20, 30, 40, 50].map((latencyMs) => ({ latencyMs })));
    expect(s).toMatchObject({ n: 5, median_ms: 30, p25_ms: 20, p75_ms: 40, iqr_ms: 20 });
  });
});

describe('MarkerFinder', () => {
  it('keeps the strip whole when a high code bit never flips', () => {
    // Bit 5 changes once per 32 words: in a short take it may not change at all.
    const width = 64;
    const height = 24;
    const finder = new MarkerFinder(width, height);
    for (let f = 0; f < 40; f += 1) {
      const gray = new Uint8Array(width * height).fill(30);
      for (let c = 0; c < 7; c += 1) {
        const white = c === 1 ? false : ((f * (c + 3)) >> 1) % 2 === 1;
        for (let y = 6; y < 10; y += 1) for (let x = 8 + 4 * c; x < 12 + 4 * c; x += 1) gray[y * width + x] = white ? 250 : 5;
      }
      finder.add(gray);
    }
    expect(finder.locate().box).toMatchObject({ x: 8, y: 6, width: 28, height: 4 });
  });

  it('finds the flipping strip and ignores text, static blocks and mid greys', () => {
    const width = 96;
    const height = 48;
    const strip = { x: 20, y: 10, cell: 4 };
    const finder = new MarkerFinder(width, height);
    for (let f = 0; f < 60; f += 1) {
      const gray = new Uint8Array(width * height).fill(12);
      // A static white block and a flickering mid-grey "text" area.
      for (let y = 30; y < 40; y += 1) for (let x = 5; x < 30; x += 1) gray[y * width + x] = 250;
      for (let y = 30; y < 40; y += 1) for (let x = 50; x < 90; x += 1) gray[y * width + x] = f % 3 === 0 ? 140 : 200;
      const code = (f * 37) % 128;
      for (let c = 0; c < 7; c += 1) {
        const white = ((code >> (6 - c)) & 1) === 1 || (c === 0 && f % 2 === 1);
        for (let y = strip.y; y < strip.y + strip.cell; y += 1) {
          for (let x = strip.x + c * strip.cell; x < strip.x + (c + 1) * strip.cell; x += 1) {
            gray[y * width + x] = white ? 245 : 8;
          }
        }
      }
      finder.add(gray);
    }
    const { box } = finder.locate();
    expect(box).toMatchObject({ x: 20, y: 10, width: 28, height: 4 });
    const rects = cellRects(box);
    expect(rects[0]).toEqual({ x: 21, y: 11, width: 2, height: 2 });
    const frame = new Uint8Array(width * height).fill(12);
    for (let y = 10; y < 14; y += 1) for (let x = 24; x < 28; x += 1) frame[y * width + x] = 240;
    expect(cellMeans(frame, width, rects).map(Math.round)).toEqual([12, 240, 12, 12, 12, 12, 12]);
  });
});
