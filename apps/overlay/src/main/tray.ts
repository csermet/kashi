/**
 * Tray menu: version label, box-opacity presets (radio), reset position, quit.
 * The menu is rebuilt on every settings change so radios track the live value
 * (Ctrl+scroll produces in-between values — shown as a disabled Custom entry).
 */
import { Menu, Tray, nativeImage, type MenuItemConstructorOptions } from 'electron';
// NOTE(Faz 5): ?asset resolves to ../../resources/tray.png RELATIVE to
// out/main — fine when running from the repo, but the packaged app must ship
// resources/ alongside (electron-builder extraResources), or the icon is blank.
import trayIconPath from '../../resources/tray.png?asset';
import { OPACITY_PRESETS, nearestPresetIndex, presetLabel } from './settings-logic.js';

export interface TrayOptions {
  version: string;
  getAlpha: () => number;
  onAlphaSelect: (alpha: number) => void;
  onResetPosition: () => void;
  onQuit: () => void;
}

export interface TrayHandle {
  /** Rebuild the menu (call after any settings change). */
  refresh: () => void;
}

export function createTray(opts: TrayOptions): TrayHandle {
  const tray = new Tray(nativeImage.createFromPath(trayIconPath));
  tray.setToolTip('Kashi');

  const refresh = (): void => {
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
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: `Kashi v${opts.version}`, enabled: false },
        { type: 'separator' },
        { label: 'Box opacity', submenu: opacityItems },
        { label: 'Reset position', click: opts.onResetPosition },
        { type: 'separator' },
        { label: 'Quit Kashi', click: opts.onQuit },
      ]),
    );
  };

  refresh();
  return { refresh };
}
