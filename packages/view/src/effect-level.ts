/**
 * Effect-engine level (Faz 4; 4th tier Faz 6), shared by main (settings/
 * tray) and the renderer (CSS application). off = the pre-Faz-4 look, pixel
 * for pixel; simple = word easing + album palette theming; full = + beat
 * pulse; hype = + semantic word effects (fx tags: icon pops, bursts,
 * category tints, downbeat emphasis). The default stays `simple` — a
 * `full` user sees zero change until they opt into Hype (Caner kararı 7).
 */
export type EffectLevel = 'off' | 'simple' | 'full' | 'hype';

export const EFFECT_LEVELS: readonly EffectLevel[] = ['off', 'simple', 'full', 'hype'];

export const DEFAULT_EFFECT_LEVEL: EffectLevel = 'simple';

/** IPC/settings values are untrusted — garbage lands on the default.
 * Forward-tolerance note: an OLDER overlay reading a settings file that
 * says "hype" lands on the default — by design. */
export function parseEffectLevel(value: unknown): EffectLevel {
  return value === 'off' || value === 'simple' || value === 'full' || value === 'hype'
    ? value
    : DEFAULT_EFFECT_LEVEL;
}

export function effectLevelLabel(level: EffectLevel): string {
  switch (level) {
    case 'off':
      return 'Off';
    case 'simple':
      return 'Simple';
    case 'full':
      return 'Full';
    case 'hype':
      return 'Hype';
  }
}

/**
 * How much of the album palette themes the box (field feedback 2026-07-12).
 * Independent of the effect LEVEL: effects can pulse while every color stays
 * stock.
 *   full — background, text and effect colors themed (default)
 *   none — everything stays stock
 *
 * Two scopes, not four (client review step E.3, 2026-11-14): the owner uses
 * "All colors", and "Keep background" (`fixed-bg`) and "Keep background &
 * text" (`fixed-text`) had no user while every new effect had to be right in
 * all four. A settings file that still names one of them lands on `full`,
 * the nearest kept scope: both themed the text and effect colours, which
 * `full` does too.
 *
 * (The sweep's second base dialect, `fill_style: neutral`, went in the same
 * step: one dialect is left, so there is nothing to choose. settings-logic.ts
 * drops the stored key.)
 */
export type ThemeScope = 'full' | 'none';

export const THEME_SCOPES: readonly ThemeScope[] = ['full', 'none'];

export const DEFAULT_THEME_SCOPE: ThemeScope = 'full';

/** Untrusted value → scope; the removed scopes and garbage both land on `full`. */
export function parseThemeScope(value: unknown): ThemeScope {
  return value === 'none' ? 'none' : DEFAULT_THEME_SCOPE;
}

export function themeScopeLabel(scope: ThemeScope): string {
  switch (scope) {
    case 'full':
      return 'All colors';
    case 'none':
      return 'Off';
  }
}
