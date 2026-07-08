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
import { OPACITY_PRESETS, nearestPresetIndex, presetLabel } from './settings-logic.js';

export interface KashiMenuOptions {
  version: string;
  getAlpha: () => number;
  onAlphaSelect: (alpha: number) => void;
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
  return Menu.buildFromTemplate([
    { label: `Kashi v${opts.version}`, enabled: false },
    { type: 'separator' },
    { label: 'Box opacity', submenu: opacityItems },
    { label: 'Reset position', click: opts.onResetPosition },
    { type: 'separator' },
    { label: 'Quit Kashi', click: opts.onQuit },
  ]);
}

export function createTray(opts: KashiMenuOptions): TrayHandle {
  const tray = new Tray(nativeImage.createFromPath(trayIconPath));
  tray.setToolTip('Kashi');
  const refresh = (): void => {
    tray.setContextMenu(buildKashiMenu(opts));
  };
  refresh();
  return { refresh };
}
