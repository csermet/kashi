/**
 * The desktop window's geometry (Faz 6.7 P4 / Faz 7 P3): main hit-tests and
 * migrates saved positions against BOX_ZONE, and the renderer's host hands the
 * shared view the stage padding and wrap width derived from the same zones
 * (geometryVars). The view's stylesheet only reads the variables; its own
 * contracts live in packages/view/src/style-contract.test.ts.
 */
import { BOX_SCALES, EDGE_FADE_PX } from '@kashi/view';
import { describe, expect, it } from 'vitest';
import {
  BOX_ZONE,
  BOX_ZONE_PRESETS,
  boxZoneFor,
  geometryVars,
  WINDOW_HEIGHT,
  WINDOW_WIDTH,
} from '../shared/box-zone.js';

describe('desktop geometry: the box zone matches the window', () => {
  it('the geometry handed to the view IS the zone, for every box size', () => {
    for (const scale of BOX_SCALES) {
      const zone = BOX_ZONE_PRESETS[scale];
      const vars = geometryVars(scale);
      const [top, side, bottom] = (vars['--kashi-stage-padding'] ?? '')
        .split(' ')
        .map((v) => Number.parseFloat(v));
      expect({ top, side, bottom }, scale).toEqual({
        top: zone.y,
        side: zone.x,
        bottom: WINDOW_HEIGHT - zone.y - zone.height,
      });
      expect(vars['--kashi-box-max-width'], scale).toBe(`${zone.width - 60}px`);
    }
    expect(geometryVars(undefined)).toEqual(geometryVars('medium'));
    expect(BOX_ZONE.x * 2 + BOX_ZONE.width).toBe(WINDOW_WIDTH); // horizontally centered
  });

  it('every box scale stays centred, inside the window, with room to fade', () => {
    // A box the user can resize must not push particles against the window
    // edge: below EDGE_FADE_PX of margin they would be cut off mid-flight
    // instead of fading, which is exactly the visible boundary Caner ruled out.
    const centre = { x: BOX_ZONE.x + BOX_ZONE.width / 2, y: BOX_ZONE.y + BOX_ZONE.height / 2 };
    for (const scale of BOX_SCALES) {
      const zone = BOX_ZONE_PRESETS[scale];
      expect(zone.x * 2 + zone.width, `${scale} not centred horizontally`).toBe(WINDOW_WIDTH);
      expect(
        { x: zone.x + zone.width / 2, y: zone.y + zone.height / 2 },
        `${scale} moved the centre`,
      ).toEqual(centre);
      const margins = [
        zone.x,
        zone.y,
        WINDOW_WIDTH - zone.x - zone.width,
        WINDOW_HEIGHT - zone.y - zone.height,
      ];
      for (const margin of margins) {
        expect(margin, `${scale} leaves only ${margin}px of margin`).toBeGreaterThanOrEqual(
          EDGE_FADE_PX,
        );
      }
    }
  });

  it('the default scale IS the shipped zone — an untouched install cannot move', () => {
    expect(boxZoneFor('medium')).toEqual(BOX_ZONE);
    expect(boxZoneFor(undefined)).toEqual(BOX_ZONE);
    expect(boxZoneFor('not-a-scale')).toEqual(BOX_ZONE);
  });
});
