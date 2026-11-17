/**
 * The DESKTOP window's geometry — one definition, three readers (Faz 6.7
 * P4/P5).
 *
 * The main process hit-tests and migrates saved positions against BOX_ZONE,
 * and the renderer's desktop host hands the same zone to the shared view as
 * CSS variables (stage padding, wrap width). The view itself knows nothing of
 * the window: the box scale and its chrome live in @kashi/view/box-scale.
 *
 * 0.15.0 grew the window a second time (640×300 → 800×460) so effects have
 * room on EVERY side of the box: 160 above as a runway, 120 below to fade out
 * in, 120 either side for a burst radius. The zone itself is the original
 * 560×180 rect and has never changed — that is what makes saved positions
 * migratable across both growths.
 *
 * FIXED size, always: transparent windows must never resize (Electron).
 */
import type { BoxScale } from '@kashi/view/box-scale';

export interface BoxRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const WINDOW_WIDTH = 800;
export const WINDOW_HEIGHT = 460;

/**
 * How big the box is, at the user's choice (Faz 7 P3). The WINDOW never
 * changes — that constraint above is not negotiable — so the box grows and
 * shrinks INSIDE it, around a fixed centre. Moving the centre would make a
 * resize feel like the box jumped somewhere else.
 *
 * Every preset keeps at least EDGE_FADE_PX (96) of margin on all four sides,
 * so particles still have somewhere to fade out and no burst ever gets cut
 * off against the window edge. That is what caps `large` at 608 wide rather
 * than a rounder 640.
 */
export const BOX_ZONE_PRESETS: Record<BoxScale, BoxRect> = {
  small: { x: 160, y: 180, width: 480, height: 140 },
  medium: { x: 120, y: 160, width: 560, height: 180 },
  large: { x: 96, y: 140, width: 608, height: 220 },
};

/**
 * The default geometry, unchanged since 0.15.0. Saved window positions are
 * anchored to THIS, so a user who never touches the setting sees exactly what
 * they saw before.
 */
export const BOX_ZONE: BoxRect = BOX_ZONE_PRESETS.medium;

/** The zone for a scale, tolerant of anything unexpected. */
export function boxZoneFor(scale: string | undefined): BoxRect {
  return BOX_ZONE_PRESETS[(scale ?? '') as BoxScale] ?? BOX_ZONE;
}

/**
 * The geometry the desktop host hands the view for a box size, as the CSS
 * variables the view's stylesheet reads: the stage padding that places the
 * zone inside the fixed window, and the wrap width — the box is content-sized
 * within its zone, and 60px is the gutter the shipped 560px zone left around
 * a 500px box.
 */
export function geometryVars(scale: string | undefined): Record<string, string> {
  const zone = boxZoneFor(scale);
  return {
    '--kashi-box-max-width': `${zone.width - 60}px`,
    '--kashi-stage-padding': `${zone.y}px ${zone.x}px ${WINDOW_HEIGHT - zone.y - zone.height}px`,
  };
}
