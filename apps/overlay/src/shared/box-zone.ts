/**
 * Window and box geometry — one definition, three readers (Faz 6.7 P4/P5).
 *
 * The main process hit-tests and migrates saved positions against BOX_ZONE,
 * the renderer aims particles around it, and the stylesheet lays the box out
 * with the matching padding. They were three hand-kept copies; two of them
 * now import this, and style-contract.test.ts nails the third to it.
 *
 * 0.15.0 grew the window a second time (640×300 → 800×460) so effects have
 * room on EVERY side of the box: 160 above as a runway, 120 below to fade out
 * in, 120 either side for a burst radius. The zone itself is the original
 * 560×180 rect and has never changed — that is what makes saved positions
 * migratable across both growths.
 *
 * FIXED size, always: transparent windows must never resize (Electron).
 */

export interface BoxRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const WINDOW_WIDTH = 800;
export const WINDOW_HEIGHT = 460;

export type BoxScale = 'small' | 'medium' | 'large';

export const BOX_SCALES: readonly BoxScale[] = ['small', 'medium', 'large'];

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
 * The box CHROME each preset wears — the part of "Box size" the eye can
 * actually see (2026-08-12, field report: "I change Box size and nothing
 * happens"). The zone above only changes the WRAP width, and the box is
 * content-sized around a fixed centre, so on lines shorter than the wrap
 * limit the three presets rendered pixel-identical. The chrome makes every
 * preset visibly different on every line: padding and corner radius scale
 * with the choice. `medium` is EXACTLY the stylesheet's shipped literals —
 * an untouched install cannot change appearance (contract-tested).
 */
export interface BoxChrome {
  padY: number;
  padX: number;
  radius: number;
}

export const BOX_CHROME: Record<BoxScale, BoxChrome> = {
  small: { padY: 7, padX: 13, radius: 11 },
  medium: { padY: 10, padX: 18, radius: 14 },
  large: { padY: 15, padX: 27, radius: 18 },
};

/**
 * The default geometry, unchanged since 0.15.0. Saved window positions and
 * the stylesheet's static padding are both anchored to THIS, so a user who
 * never touches the setting sees exactly what they saw before.
 */
export const BOX_ZONE: BoxRect = BOX_ZONE_PRESETS.medium;

/** The size the app has always had. */
export const DEFAULT_BOX_SCALE: BoxScale = 'medium';

export function parseBoxScale(value: unknown): BoxScale {
  return BOX_SCALES.includes(value as BoxScale) ? (value as BoxScale) : DEFAULT_BOX_SCALE;
}

/** The zone for a scale, tolerant of anything unexpected. */
export function boxZoneFor(scale: BoxScale | string | undefined): BoxRect {
  return BOX_ZONE_PRESETS[(scale ?? '') as BoxScale] ?? BOX_ZONE;
}
