/**
 * The Kashi context menu (version label, box-opacity presets, reset position,
 * quit) — served from TWO places with the same template: the tray icon and a
 * right-click on the lyric box (users reach for the box first; the tray icon
 * can hide in the Windows overflow area). Menus are rebuilt on every open /
 * settings change so radios track the live value (Ctrl+scroll produces
 * in-between values — shown as a disabled Custom entry).
 */
import { join } from 'node:path';
import { Menu, Tray, app, nativeImage, type MenuItemConstructorOptions } from 'electron';
// ?asset resolves relative to out/main — correct when running from the repo.
// The PACKAGED app ships the icon via electron-builder extraResources and
// reads it from process.resourcesPath instead (Faz 5 P5).
import trayIconPath from '../../resources/tray.png?asset';

const resolvedTrayIcon = app.isPackaged
  ? join(process.resourcesPath, 'tray.png')
  : trayIconPath;
import {
  EFFECT_LEVELS,
  FILL_STYLES,
  THEME_SCOPES,
  effectLevelLabel,
  fillStyleLabel,
  themeScopeLabel,
  type EffectLevel,
  type FillStyle,
  type ThemeScope,
} from '../shared/effect-level.js';
import {
  OPACITY_PRESETS,
  TIMING_OFFSET_PRESETS,
  nearestPresetIndex,
  presetLabel,
  timingOffsetLabel,
} from './settings-logic.js';

export interface KashiMenuOptions {
  version: string;
  getAlpha: () => number;
  onAlphaSelect: (alpha: number) => void;
  getTimingOffset: () => number;
  onTimingOffsetSelect: (offsetMs: number) => void;
  /** "Other…" — open the small numeric-entry window for arbitrary values. */
  onTimingOffsetCustom: () => void;
  getEffectLevel: () => EffectLevel;
  onEffectLevelSelect: (level: EffectLevel) => void;
  getThemeScope: () => ThemeScope;
  onThemeScopeSelect: (scope: ThemeScope) => void;
  getFillStyle: () => FillStyle;
  onFillStyleSelect: (style: FillStyle) => void;
  onResetPosition: () => void;
  /** lrclib contribute-back (Faz 5 P6): visible only while a kashi-server
   * word-sync document is on screen; the server still gates/dry-runs. */
  getCanReportSync: () => boolean;
  onReportSync: () => void;
  onQuit: () => void;
}

export interface TrayHandle {
  /** Rebuild the tray menu (call after any settings change). */
  refresh: () => void;
}

export function buildKashiMenu(opts: KashiMenuOptions): Menu {
  const alpha = opts.getAlpha();
  const presetIndex = nearestPresetIndex(alpha);
  const opacityItems: MenuItemConstructorOptions[] = OPACITY_PRESETS.map((preset, index) => ({
    label: presetLabel(preset),
    type: 'radio',
    checked: index === presetIndex,
    click: () => opts.onAlphaSelect(preset),
  }));
  if (presetIndex === -1) {
    opacityItems.push({
      label: `Custom: ${Math.round(alpha * 100)}%`,
      type: 'radio',
      checked: true,
      enabled: false,
    });
  }
  const offset = opts.getTimingOffset();
  const timingItems: MenuItemConstructorOptions[] = TIMING_OFFSET_PRESETS.map((preset) => ({
    label: timingOffsetLabel(preset),
    type: 'radio',
    checked: preset === offset,
    click: () => opts.onTimingOffsetSelect(preset),
  }));
  if (!TIMING_OFFSET_PRESETS.includes(offset as (typeof TIMING_OFFSET_PRESETS)[number])) {
    timingItems.push({
      label: `Custom: ${offset > 0 ? '+' : ''}${offset} ms`,
      type: 'radio',
      checked: true,
      enabled: false,
    });
  }
  timingItems.push(
    { type: 'separator' },
    // Arbitrary values are typed into a tiny prompt window (menus can't host
    // text input); main clamps to ±TIMING_OFFSET_MAX_ABS like every other path.
    { label: 'Other…', click: () => opts.onTimingOffsetCustom() },
  );
  const effectLevel = opts.getEffectLevel();
  const effectItems: MenuItemConstructorOptions[] = EFFECT_LEVELS.map((level) => ({
    label: effectLevelLabel(level),
    type: 'radio',
    checked: level === effectLevel,
    click: () => opts.onEffectLevelSelect(level),
  }));
  const themeScope = opts.getThemeScope();
  const themeItems: MenuItemConstructorOptions[] = THEME_SCOPES.map((scope) => ({
    label: themeScopeLabel(scope),
    type: 'radio',
    checked: scope === themeScope,
    click: () => opts.onThemeScopeSelect(scope),
  }));
  const fillStyle = opts.getFillStyle();
  const fillItems: MenuItemConstructorOptions[] = FILL_STYLES.map((style) => ({
    label: fillStyleLabel(style),
    type: 'radio',
    checked: style === fillStyle,
    click: () => opts.onFillStyleSelect(style),
  }));
  // Parent labels carry the live value (retro 4.5): the menu is rebuilt on
  // every settings change anyway, so "— Full" / "— +100 ms" is free and saves
  // a submenu dive just to check the current state.
  return Menu.buildFromTemplate([
    { label: `Kashi v${opts.version}`, enabled: false },
    { type: 'separator' },
    { label: `Effects — ${effectLevelLabel(effectLevel)}`, submenu: effectItems },
    { label: `Theme colors — ${themeScopeLabel(themeScope)}`, submenu: themeItems },
    { label: `Word fill — ${fillStyleLabel(fillStyle)}`, submenu: fillItems },
    { label: `Box opacity — ${presetLabel(alpha)}`, submenu: opacityItems },
    { label: `Timing offset — ${shortOffsetLabel(offset)}`, submenu: timingItems },
    ...(opts.getCanReportSync()
      ? [{ label: 'Report good sync to LRCLIB', click: opts.onReportSync } as const]
      : []),
    { label: 'Reset position', click: opts.onResetPosition },
    { type: 'separator' },
    { label: 'Quit Kashi', click: opts.onQuit },
  ]);
}

/** "+100 ms" / "Off" — the parent label / tooltip variant without the
 * (earlier)/(later) coaching the preset rows carry. */
function shortOffsetLabel(offsetMs: number): string {
  if (offsetMs === 0) return 'Off';
  return `${offsetMs > 0 ? '+' : ''}${offsetMs} ms`;
}

/**
 * The shipped icon is a 64 px white glyph on transparency. Windows/Linux trays
 * scale that themselves; the macOS menu bar does not — it would render a
 * 64 pt white shape, invisible on a light menu bar. There the icon must be a
 * ~16 pt TEMPLATE image: macOS reads only its alpha channel and paints the
 * glyph black or white to match the current appearance.
 */
function trayIcon() {
  const image = nativeImage.createFromPath(resolvedTrayIcon);
  if (process.platform !== 'darwin') return image;
  const scaled = image.resize({ width: 16, height: 16 });
  scaled.setTemplateImage(true);
  return scaled;
}

export function createTray(opts: KashiMenuOptions): TrayHandle {
  const tray = new Tray(trayIcon());
  const refresh = (): void => {
    tray.setContextMenu(buildKashiMenu(opts));
    // The tooltip answers "what is Kashi set to right now" without a click.
    tray.setToolTip(
      `Kashi v${opts.version} — ${effectLevelLabel(opts.getEffectLevel())}, ` +
        `opacity ${presetLabel(opts.getAlpha())}, ` +
        `offset ${shortOffsetLabel(opts.getTimingOffset())}`,
    );
  };
  refresh();
  return { refresh };
}
