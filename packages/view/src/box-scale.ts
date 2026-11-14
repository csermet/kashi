/**
 * The user's box size, as the view sees it (Faz 7 P3): three presets and the
 * CHROME each one wears. Where the box sits in its window and how wide its
 * text may run is the host's geometry (the desktop's lives in
 * apps/overlay/src/shared/box-zone.ts) and reaches the view as CSS variables.
 */

export type BoxScale = 'small' | 'medium' | 'large';

export const BOX_SCALES: readonly BoxScale[] = ['small', 'medium', 'large'];

/** The size the app has always had. */
export const DEFAULT_BOX_SCALE: BoxScale = 'medium';

export function parseBoxScale(value: unknown): BoxScale {
  return BOX_SCALES.includes(value as BoxScale) ? (value as BoxScale) : DEFAULT_BOX_SCALE;
}

/**
 * The box CHROME each preset wears — the part of "Box size" the eye can
 * actually see (2026-08-12, field report: "I change Box size and nothing
 * happens"). The zone only changes the WRAP width, and the box is
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
