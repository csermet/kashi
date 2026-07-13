/** paletteFromPixels is pure — synthetic pixel grids, no canvas. */

import { describe, expect, it } from 'vitest';

import { paletteFromPixels } from './artwork-palette.js';

function pixels(colors: Array<[number, number, number, number?]>, repeat = 1): number[] {
  const out: number[] = [];
  for (let i = 0; i < repeat; i += 1) {
    for (const [r, g, b, a = 255] of colors) out.push(r, g, b, a);
  }
  return out;
}

describe('paletteFromPixels', () => {
  it('finds the dominant hue as primary and a distinct hue as accent', () => {
    const data = [
      ...pixels([[200, 30, 40]], 600), // dominant red
      ...pixels([[30, 60, 220]], 200), // blue accent block
      ...pixels([[10, 10, 12]], 200), // dark filler
    ];
    const palette = paletteFromPixels(data);
    expect(palette).not.toBeNull();
    expect(palette!.primary).toBe('#c81e28'); // bucket-averaged red
    // Accent must carry the OTHER hue, not a red neighbour.
    expect(palette!.accent).toBe('#1e3cdc');
    expect(palette!.background).toBe(palette!.primary); // tone mapper darkens
  });

  it('returns null for gray/B&W art (the neutral rule owns those)', () => {
    const data = [
      ...pixels([[20, 20, 20]], 500),
      ...pixels([[240, 240, 240]], 300),
      ...pixels([[128, 128, 128]], 200),
    ];
    expect(paletteFromPixels(data)).toBeNull();
  });

  it('ignores transparent pixels and noise-floor specks', () => {
    const data = [
      ...pixels([[200, 30, 40]], 900),
      ...pixels([[30, 220, 60, 0]], 500), // transparent green: invisible
      ...pixels([[30, 220, 60]], 2), // 2 pixels of green: under the floor
    ];
    const palette = paletteFromPixels(data);
    expect(palette).not.toBeNull();
    expect(palette!.primary).toBe('#c81e28');
    expect(palette!.accent).toBe('#c81e28'); // no distinct hue survived
  });

  it('handles empty input', () => {
    expect(paletteFromPixels([])).toBeNull();
  });
});
