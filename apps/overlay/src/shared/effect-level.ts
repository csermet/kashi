/**
 * Effect-engine level (Faz 4), shared by main (settings/tray) and the
 * renderer (CSS application). off = the pre-Faz-4 look, pixel for pixel;
 * simple = word easing + album palette theming; full = + beat pulse.
 */
export type EffectLevel = 'off' | 'simple' | 'full';

export const EFFECT_LEVELS: readonly EffectLevel[] = ['off', 'simple', 'full'];

export const DEFAULT_EFFECT_LEVEL: EffectLevel = 'simple';

/** IPC/settings values are untrusted — garbage lands on the default. */
export function parseEffectLevel(value: unknown): EffectLevel {
  return value === 'off' || value === 'simple' || value === 'full'
    ? value
    : DEFAULT_EFFECT_LEVEL;
}

export function effectLevelLabel(level: EffectLevel): string {
  return level === 'off' ? 'Off' : level === 'simple' ? 'Simple' : 'Full';
}
