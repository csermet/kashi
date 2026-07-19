/**
 * fx-layer spike main (Faz 6.5 P3): a full-screen transparent, click-through,
 * always-on-top window running a particle layer — measuring whether a
 * separate effect-layer window is viable (GO gate for P7).
 *
 * DEV-ONLY: nodeIntegration is deliberately ON so the renderer can
 * require('pixi.js') without a bundler. This directory is not part of the
 * workspace, not built, not shipped.
 */
import { app, BrowserWindow, screen } from 'electron';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

function arg(name, fallback) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.split('=')[1] : fallback;
}

app.whenReady().then(() => {
  // Full display bounds (not workArea): the real effect layer would cover
  // the whole screen, taskbar included.
  const { bounds } = screen.getPrimaryDisplay();
  const win = new BrowserWindow({
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
    transparent: true,
    frame: false,
    hasShadow: false,
    skipTaskbar: true,
    resizable: false,
    fullscreenable: false,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      backgroundThrottling: false,
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true);
  void win.loadFile(join(__dirname, 'index.html'), {
    query: {
      mode: arg('mode', 'pixi'), // pixi | canvas
      n: arg('particles', '300'), // 100 | 300 | 1000
    },
  });
});

app.on('window-all-closed', () => app.quit());
