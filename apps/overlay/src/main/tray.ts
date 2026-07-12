/**
 * The Kashi context menu (version label, box-opacity presets, reset position,
 * quit) — served from TWO places with the same template: the tray icon and a
 * right-click on the lyric box (users reach for the box first; the tray icon
 * can hide in the Windows overflow area). Menus are rebuilt on every open /
 * settings change so radios track the live value (Ctrl+scroll produces
 * in-between values — shown as a disabled Custom entry).
 */
import { Menu, Tray, nativeImage, type MenuItemConstructorOptions } from 'electron';
// NOTE(Faz 5): ?asset resolves to ../../resources/tray.png RELATIVE to
// out/main — fine when running from the repo, but the packaged app must ship
// resources/ alongside (electron-builder extraResources), or the icon is blank.
import trayIconPath from '../../resources/tray.png?asset';
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
  onResetPosition: () => void;
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
  return Menu.buildFromTemplate([
    { label: `Kashi v${opts.version}`, enabled: false },
    { type: 'separator' },
    { label: 'Box opacity', submenu: opacityItems },
    { label: 'Timing offset', submenu: timingItems },
    { label: 'Reset position', click: opts.onResetPosition },
    { type: 'separator' },
    { label: 'Quit Kashi', click: opts.onQuit },
  ]);
}

/**
 * The shipped icon is a 64 px white glyph on transparency. Windows/Linux trays
 * scale that themselves; the macOS menu bar does not — it would render a
 * 64 pt white shape, invisible on a light menu bar. There the icon must be a
 * ~16 pt TEMPLATE image: macOS reads only its alpha channel and paints the
 * glyph black or white to match the current appearance.
 */
function trayIcon() {
  const image = nativeImage.createFromPath(trayIconPath);
  if (process.platform !== 'darwin') return image;
  const scaled = image.resize({ width: 16, height: 16 });
  scaled.setTemplateImage(true);
  return scaled;
}

export function createTray(opts: KashiMenuOptions): TrayHandle {
  const tray = new Tray(trayIcon());
  tray.setToolTip('Kashi');
  const refresh = (): void => {
    tray.setContextMenu(buildKashiMenu(opts));
  };
  refresh();
  return { refresh };
}
