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

/**
 * How much of the album palette themes the box (field feedback 2026-07-12:
 * "renkler sabit kalabilmeli — 3 ayar"). Independent of the effect LEVEL:
 * effects can pulse while every color stays stock.
 *   full        — background + text + effect colors themed (default)
 *   fixed-bg    — background stays stock; text + effect colors themed
 *   fixed-text  — background + text stay stock; only effect colors themed
 *   none        — everything stays stock
 */
export type ThemeScope = 'full' | 'fixed-bg' | 'fixed-text' | 'none';

export const THEME_SCOPES: readonly ThemeScope[] = ['full', 'fixed-bg', 'fixed-text', 'none'];

export const DEFAULT_THEME_SCOPE: ThemeScope = 'full';

export function parseThemeScope(value: unknown): ThemeScope {
  return value === 'full' || value === 'fixed-bg' || value === 'fixed-text' || value === 'none'
    ? value
    : DEFAULT_THEME_SCOPE;
}

export function themeScopeLabel(scope: ThemeScope): string {
  switch (scope) {
    case 'full':
      return 'All colors';
    case 'fixed-bg':
      return 'Keep background';
    case 'fixed-text':
      return 'Keep background & text';
    case 'none':
      return 'Off';
  }
}
