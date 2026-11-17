/**
 * Lyric text size, shared by both processes (Faz 7 P3).
 *
 * It lives here beside box-zone.ts rather than in settings-logic.ts because
 * the RENDERER needs it too, and the renderer must not import main-process
 * modules — that is how node-only dependencies end up in a bundle that runs
 * under a strict CSP.
 */

export type TextScale = 'small' | 'medium' | 'large';

export const TEXT_SCALES: readonly TextScale[] = ['small', 'medium', 'large'];

/** The size the app has always had. */
export const DEFAULT_TEXT_SCALE: TextScale = 'medium';

/** Multipliers of the stylesheet's 28px. Legibility, not zoom. */
export const TEXT_SCALE_FACTORS: Record<TextScale, number> = {
  small: 0.8,
  medium: 1,
  large: 1.2,
};

export function parseTextScale(value: unknown): TextScale {
  return TEXT_SCALES.includes(value as TextScale) ? (value as TextScale) : DEFAULT_TEXT_SCALE;
}
