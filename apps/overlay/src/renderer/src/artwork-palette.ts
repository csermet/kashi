/**
 * Serverless palette (Faz 5 P5, retro #10): in lrclib mode there is no server
 * document and the box stayed unthemed — but track_changed already carries
 * artwork_url. Extract dominant HUES locally and feed the existing OKLCH
 * tone-mapping pipeline ("hue is data, tone is design" — color-tone.ts fixes
 * lightness/chroma, so the extractor only has to find honest hues).
 *
 * Server palettes always win; this only fills the serverless gap. Extraction
 * is split pure (paletteFromPixels — unit-tested) / shell (loadArtworkPalette
 * — Image+canvas, every failure returns null and the plain look stays).
 */

import type { PaletteLike } from './effects-logic.js';

interface Bucket {
  count: number;
  r: number;
  g: number;
  b: number;
}

const SAMPLE_SIZE = 32;
const MIN_BUCKET_SHARE = 0.004; // noise floor: <0.4% of pixels is not a color

function hex(r: number, g: number, b: number): string {
  const to = (v: number) => Math.round(v).toString(16).padStart(2, '0');
  return `#${to(r)}${to(g)}${to(b)}`;
}

function saturation(r: number, g: number, b: number): number {
  const max = Math.max(r, g, b) / 255;
  const min = Math.min(r, g, b) / 255;
  return max === 0 ? 0 : (max - min) / max;
}

function luminance(r: number, g: number, b: number): number {
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}

function hue(r: number, g: number, b: number): number {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  if (max === min) return 0;
  const d = max - min;
  let h: number;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}

function hueDelta(a: number, b: number): number {
  const d = Math.abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
}

/** Dominant-hue palette from RGBA pixel data, or null when the artwork is
 * effectively colorless (the tone mapper's neutral rule owns gray art). */
export function paletteFromPixels(data: Uint8ClampedArray | number[]): PaletteLike | null {
  const buckets = new Map<number, Bucket>();
  let total = 0;
  for (let i = 0; i + 3 < data.length; i += 4) {
    const alpha = data[i + 3] as number;
    if (alpha < 128) continue;
    const r = data[i] as number;
    const g = data[i + 1] as number;
    const b = data[i + 2] as number;
    total += 1;
    const key = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);
    const bucket = buckets.get(key);
    if (bucket) {
      bucket.count += 1;
      bucket.r += r;
      bucket.g += g;
      bucket.b += b;
    } else {
      buckets.set(key, { count: 1, r, g, b });
    }
  }
  if (total === 0) return null;

  const floor = Math.max(2, total * MIN_BUCKET_SHARE);
  const colors = [...buckets.values()]
    .filter((bucket) => bucket.count >= floor)
    .map((bucket) => {
      const r = bucket.r / bucket.count;
      const g = bucket.g / bucket.count;
      const b = bucket.b / bucket.count;
      return {
        count: bucket.count,
        r,
        g,
        b,
        sat: saturation(r, g, b),
        lum: luminance(r, g, b),
        hue: hue(r, g, b),
      };
    });

  // Vivid = carries a usable hue and is neither near-black nor near-white.
  const vivid = colors.filter((c) => c.sat >= 0.15 && c.lum > 0.08 && c.lum < 0.95);
  if (vivid.length === 0) return null; // gray/B&W art: defaults look better
  vivid.sort((a, b) => b.count - a.count);

  const primary = vivid[0]!;
  const distinct = vivid.filter((c) => hueDelta(c.hue, primary.hue) > 30);
  // Accent favours saturation but must still cover real pixels.
  const accent = [...(distinct.length ? distinct : vivid)].sort(
    (a, b) => b.sat * Math.sqrt(b.count) - a.sat * Math.sqrt(a.count)
  )[0]!;
  const secondary = distinct[0] ?? primary;

  return {
    primary: hex(primary.r, primary.g, primary.b),
    secondary: hex(secondary.r, secondary.g, secondary.b),
    accent: hex(accent.r, accent.g, accent.b),
    // Any dark-ish carrier of the dominant hue works: the tone mapper clamps
    // background to its own L/C band regardless.
    background: hex(primary.r, primary.g, primary.b),
  };
}

/** Artwork URL → palette, or null on ANY failure (timeout, CORS-tainted
 * canvas, decode error) — the caller keeps the plain look. */
export function loadArtworkPalette(url: string, timeoutMs = 8000): Promise<PaletteLike | null> {
  return new Promise((settle) => {
    if (!/^https:\/\//.test(url)) {
      settle(null);
      return;
    }
    const img = new Image();
    img.crossOrigin = 'anonymous'; // canvas readback needs CORS or it taints
    let done = false;
    const finish = (value: PaletteLike | null) => {
      if (done) return;
      done = true;
      settle(value);
    };
    const timer = setTimeout(() => {
      img.src = '';
      finish(null);
    }, timeoutMs);
    img.onload = () => {
      clearTimeout(timer);
      try {
        const canvas = document.createElement('canvas');
        canvas.width = SAMPLE_SIZE;
        canvas.height = SAMPLE_SIZE;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        if (!ctx) {
          finish(null);
          return;
        }
        ctx.drawImage(img, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE);
        finish(paletteFromPixels(ctx.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE).data));
      } catch {
        finish(null); // tainted canvas: the host didn't grant CORS
      }
    };
    img.onerror = () => {
      clearTimeout(timer);
      finish(null);
    };
    img.src = url;
  });
}
