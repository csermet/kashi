/**
 * Kashi overlay — Electron main process.
 *
 * Window contract (see plan R-4/R-7): transparent, frameless, always-on-top at
 * screen-saver level, visible over fullscreen apps, click-through with hover
 * forwarding, no background throttling, hardened webPreferences.
 *
 * Phase 2 will add: local WS server (127.0.0.1:17890-17894, Origin allowlist),
 * position extrapolation clock, lrclib client + disk cache, tray menu,
 * display-aware position persistence.
 */
import { app, BrowserWindow } from 'electron';
import { join } from 'node:path';
import { PROTOCOL_VERSION } from '@kashi/protocol';

function createOverlayWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 560,
    height: 180,
    transparent: true,
    frame: false,
    hasShadow: false,
    skipTaskbar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });

  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  if (process.env['ELECTRON_RENDERER_URL']) {
    void win.loadURL(process.env['ELECTRON_RENDERER_URL']);
  } else {
    void win.loadFile(join(__dirname, '../renderer/index.html'));
  }
  return win;
}

app.whenReady().then(() => {
  console.debug(`[kashi] overlay starting (protocol v${PROTOCOL_VERSION})`);
  createOverlayWindow();
});

app.on('window-all-closed', () => {
  app.quit();
});
