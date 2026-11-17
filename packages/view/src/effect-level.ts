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
 * all four. What those two showed (overlay 0.31.0, paletteToCssVars and its
 * test): the background stock, the text white - base text has been pinned
 * white in every scope since 0.2.4 - and only the effect colours themed; the
 * two rendered identically. Neither kept scope reproduces that: `full` would
 * turn the background tint back on, the one thing both names asked to keep
 * off; `none` keeps background and text as they were and returns the effect
 * colours to stock. A stored `fixed-bg` / `fixed-text` therefore reads as
 * `none` (review E.3 follow-up, 2026-11-16): a removed setting falls toward
 * stock, never toward more theming than the user chose. The move is lossy by
 * design: the settings file is rewritten on exit, so an older overlay
 * started afterwards reads `none` too, not the scope the user had picked.
 *
 * (The sweep's second base dialect, `fill_style: neutral`, went in the same
 * step: one dialect is left, so there is nothing to choose. settings-logic.ts
 * drops the stored key.)
 */
export type ThemeScope = 'full' | 'none';

export const THEME_SCOPES: readonly ThemeScope[] = ['full', 'none'];

export const DEFAULT_THEME_SCOPE: ThemeScope = 'full';

/** Untrusted value → scope: the removed fixed scopes read as `none`, garbage as the default. */
export function parseThemeScope(value: unknown): ThemeScope {
  return value === 'none' || value === 'fixed-bg' || value === 'fixed-text'
    ? 'none'
    : DEFAULT_THEME_SCOPE;
}

export function themeScopeLabel(scope: ThemeScope): string {
  switch (scope) {
    case 'full':
      return 'All colors';
    case 'none':
      return 'Off';
  }
}
