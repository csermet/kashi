/**
 * Kashi overlay — Electron main process.
 *
 * Window contract (plan R-4/R-7): transparent, frameless, always-on-top at
 * screen-saver level, visible over fullscreen apps, hardened webPreferences.
 *
 * Wiring: OverlayWsServer (extension messages) → renderer via IPC; lyrics are
 * fetched from LRCLIB on track changes (500 ms debounce, AbortController +
 * 10 s timeout per lookup, stale responses matched by track key — plan R-9).
 * Playback messages are latched to the (client, tab) that sent the last
 * track_changed so a second YTM tab cannot corrupt the clock (R-9).
 * Last known state is replayed to the renderer after (re)load.
 *
 * Still TODO (later in Phase 2 / Phase 4, plan D.7/R-4): tray menu, settings
 * UI (extension-ID allowlist + optional token), window position persistence
 * with display-id validation.
 */
import { app, BrowserWindow, ipcMain, net, screen } from 'electron';
import { join } from 'node:path';
import type { ExtensionToOverlayMessage, TrackInfo } from '@kashi/protocol';
import { LrclibClient } from './lrclib.js';
import { OverlayWsServer } from './ws-server.js';

const TRACK_DEBOUNCE_MS = 500;
/** Retry delays for transient lrclib failures (timeout/network). */
const LYRICS_RETRY_DELAYS_MS = [0, 2000, 6000];

let window: BrowserWindow | null = null;
let lrclib: LrclibClient;

let currentTrackKey: string | null = null;
/** Only the (client, tab) that sent the last track_changed drives playback. */
let activeSource: { clientId: number; tabId: number } | null = null;
let debounceTimer: NodeJS.Timeout | null = null;
let lookupAbort: AbortController | null = null;

/** Last payloads, replayed to the renderer on (re)load. */
const lastPayloads = new Map<string, unknown>();

function trackKey(track: TrackInfo): string {
  return `${track.source.type}:${track.source.id}`;
}

function send(channel: string, payload: unknown): void {
  if (channel !== 'kashi:playback') lastPayloads.set(channel, payload);
  if (window && !window.isDestroyed()) {
    window.webContents.send(channel, payload);
  }
}

/** Clear every trace of the current source (close/disconnect/source_gone). */
function clearSource(reason: string): void {
  console.debug(`[kashi] source cleared (${reason})`);
  lookupAbort?.abort();
  if (debounceTimer) clearTimeout(debounceTimer);
  currentTrackKey = null;
  activeSource = null;
  lastPayloads.delete('kashi:track');
  lastPayloads.delete('kashi:lyrics');
  send('kashi:source-gone', {});
}

function onExtensionMessage(msg: ExtensionToOverlayMessage, clientId: number): void {
  if (msg.type === 'log') {
    console.debug(`[ext:${msg.context}] ${msg.line}`);
    return; // diagnostics only — never forwarded to the renderer
  }
  if (msg.type === 'source_gone') {
    clearSource('source_gone');
    return;
  }
  switch (msg.type) {
    case 'track_changed': {
      activeSource = { clientId, tabId: msg.tab_id };
      const key = trackKey(msg.track);
      console.debug(
        `[kashi] track_changed: ${key} "${msg.track.artist} - ${msg.track.title}"` +
          ` (tab ${msg.tab_id}, client ${clientId})${key === currentTrackKey ? ' [dup]' : ''}`,
      );
      if (key === currentTrackKey) return; // metadata refresh for same track
      currentTrackKey = key;
      send('kashi:track', { key, track: msg.track });

      // Debounce: radio-mode skip chains must not spam LRCLIB (R-9).
      if (debounceTimer) clearTimeout(debounceTimer);
      lookupAbort?.abort();
      debounceTimer = setTimeout(() => void lookupLyrics(key, msg.track), TRACK_DEBOUNCE_MS);
      return;
    }
    case 'position':
    case 'seek':
    case 'playback_state':
    case 'ad_state':
      if (
        activeSource &&
        (clientId !== activeSource.clientId || msg.tab_id !== activeSource.tabId)
      ) {
        return; // another tab/client — not the one playing our track
      }
      send('kashi:playback', msg);
      return;
    default:
      return;
  }
}

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

async function lookupLyrics(key: string, track: TrackInfo): Promise<void> {
  const abort = new AbortController();
  lookupAbort = abort;
  const query = {
    title: track.title,
    artist: track.artist,
    album: track.album,
    duration_ms: track.duration_ms,
  };

  send('kashi:lyrics', { key, searching: true });

  // Transient lrclib slowness (per-request 8s timeout) gets a few retries —
  // one hiccup must not mean a whole song without lyrics.
  for (const [attempt, delay] of LYRICS_RETRY_DELAYS_MS.entries()) {
    await abortableSleep(delay, abort.signal);
    if (abort.signal.aborted) return; // superseded by a newer track
    try {
      let result = await lrclib.getLyrics(query, abort.signal);
      if (key !== currentTrackKey) return; // stale response guard (R-9)
      if (!result.found && query.duration_ms) {
        // The reported duration can be transiently WRONG during YTM's
        // auto-advance (MSE mid-transition) — a bad duration rejects every
        // candidate, so retry once without it before giving up.
        console.debug(
          `[kashi] duration-scoped lookup missed (duration_ms=${query.duration_ms}), retrying without duration`,
        );
        result = await lrclib.getLyrics({ ...query, duration_ms: undefined }, abort.signal);
        if (key !== currentTrackKey) return;
      }
      if (!result.found) {
        console.debug(
          `[kashi] no synced lyrics: "${track.artist} - ${track.title}"` +
            ` (duration_ms=${track.duration_ms ?? 'yok'})`,
        );
      }
      send('kashi:lyrics', { key, ...result });
      return;
    } catch (err) {
      if (abort.signal.aborted) return;
      console.warn(
        `[kashi] lyrics lookup failed (attempt ${attempt + 1}/${LYRICS_RETRY_DELAYS_MS.length}):`,
        err,
      );
    }
  }
  // error !== genuine miss — renderer shows a different message.
  if (key === currentTrackKey) send('kashi:lyrics', { key, found: false, error: true });
}

function createOverlayWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 560,
    height: 180,
    transparent: true,
    frame: false,
    hasShadow: false,
    skipTaskbar: true,
    // Electron docs: transparent windows must not be resizable — resizing can
    // break transparency on some platforms.
    resizable: false,
    fullscreenable: false,
    webPreferences: {
      preload: join(__dirname, '../preload/index.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });

  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  // Click-through by default (R-4): clicks land on the app underneath.
  // forward:true keeps mousemove flowing to the renderer, which flips the
  // window interactive while the cursor hovers the lyric text (drag support).
  win.setIgnoreMouseEvents(true, { forward: true });

  win.on('closed', () => {
    window = null;
  });
  // Replay last known state after every load (startup, reload, crash restart).
  win.webContents.on('did-finish-load', () => {
    for (const [channel, payload] of lastPayloads) {
      win.webContents.send(channel, payload);
    }
  });

  if (process.env['ELECTRON_RENDERER_URL']) {
    void win.loadURL(process.env['ELECTRON_RENDERER_URL']);
  } else {
    void win.loadFile(join(__dirname, '../renderer/index.html'));
  }
  return win;
}

ipcMain.on('kashi:set-interactive', (_event, interactive: unknown) => {
  window?.setIgnoreMouseEvents(interactive !== true, { forward: true });
});

ipcMain.on('kashi:rlog', (_event, line: unknown) => {
  console.debug(`[renderer] ${String(line).slice(0, 500)}`);
});

/**
 * Manual dragging: the window follows the cursor while the renderer reports a
 * drag (mousedown on the lyric). Because the window moves WITH the cursor,
 * the cursor never leaves it and the terminating mouseup always arrives.
 *
 * DPI trap (Windows, scaled displays): repeated setPosition calls make the
 * window CREEP right/down — DIP↔pixel rounding is not idempotent and the
 * error accumulates at 60 fps. Fix: pin the size via setBounds (cached at
 * drag start) and skip ticks whose target did not change.
 */
interface DragState {
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
  lastX: number;
  lastY: number;
  timer: NodeJS.Timeout;
}
let drag: DragState | null = null;

function stopDrag(): void {
  if (drag) {
    clearInterval(drag.timer);
    drag = null;
  }
}

ipcMain.on('kashi:drag-start', () => {
  if (!window || window.isDestroyed() || drag) return;
  const cursor = screen.getCursorScreenPoint();
  const [winX = 0, winY = 0] = window.getPosition();
  const [width = 560, height = 180] = window.getSize();
  drag = {
    offsetX: cursor.x - winX,
    offsetY: cursor.y - winY,
    width,
    height,
    lastX: Number.NaN,
    lastY: Number.NaN,
    timer: setInterval(() => {
      if (!window || window.isDestroyed() || !drag) {
        stopDrag();
        return;
      }
      const point = screen.getCursorScreenPoint();
      const x = point.x - drag.offsetX;
      const y = point.y - drag.offsetY;
      if (x === drag.lastX && y === drag.lastY) return; // no-op tick: no rounding creep
      drag.lastX = x;
      drag.lastY = y;
      window.setBounds({ x, y, width: drag.width, height: drag.height });
    }, 16),
  };
});
ipcMain.on('kashi:drag-end', stopDrag);

app.whenReady().then(async () => {
  lrclib = new LrclibClient({
    cacheDir: join(app.getPath('userData'), 'cache', 'lrclib'),
    // Chromium's network stack (proper happy-eyeballs/IPv6 fallback, OS proxy)
    // — Node's fetch stalls for seconds on broken IPv6 routes.
    fetchFn: net.fetch.bind(net) as typeof fetch,
  });

  window = createOverlayWindow();

  const server = new OverlayWsServer({
    // TODO(R-6): once the extension ID is pinned via the manifest `key`, pass
    // allowedOrigins (+ optional token) from settings. Until then any
    // chrome-extension:// origin is accepted; payloads are shape-validated.
    expectedClient: 'kashi-extension/0.1.8', // keep in sync with the manifest
    onMessage: onExtensionMessage,
    onClientConnected: (count) => send('kashi:connection', { connected: count > 0 }),
    onClientDisconnected: (count, clientId) => {
      // The latch owner vanished (browser closed / reconnect with a new id):
      // without this, the surviving stream is filtered forever (audit K3).
      if (count === 0 || activeSource?.clientId === clientId) {
        clearSource(count === 0 ? 'last client disconnected' : 'latch owner disconnected');
      }
      send('kashi:connection', { connected: count > 0 });
    },
    log: (line) => console.debug(`[kashi] ${line}`),
  });
  const port = await server.start();
  console.debug(`[kashi] overlay ready, ws on 127.0.0.1:${port}`);

  app.on('before-quit', () => void server.stop());
});

app.on('window-all-closed', () => {
  app.quit();
});
