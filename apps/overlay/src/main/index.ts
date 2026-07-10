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
 * Paket C: tray menu (opacity presets / reset position / quit), settings
 * persistence (box alpha + window position with display validation, R-4).
 * Still TODO (Faz 3+, plan D.7): extension-ID allowlist + optional token UI.
 */
import { app, BrowserWindow, ipcMain, net, screen } from 'electron';
import { join } from 'node:path';
import type { ExtensionToOverlayMessage, TrackInfo } from '@kashi/protocol';
import { EXPECTED_EXTENSION, KASHI_VERSION } from '../shared/version.js';
import { EnqueueGate } from './enqueue-gate.js';
import { KashiServerClient } from './kashi-server.js';
import { LrclibClient } from './lrclib.js';
import { adjustAlpha, clampAlpha, isPositionVisible } from './settings-logic.js';
import { SettingsStore } from './settings.js';
import { buildKashiMenu, createTray, type KashiMenuOptions, type TrayHandle } from './tray.js';
import { OverlayWsServer } from './ws-server.js';

const TRACK_DEBOUNCE_MS = 500;
/** Retry delays for transient lrclib failures (timeout/network). */
const LYRICS_RETRY_DELAYS_MS = [0, 2000, 6000];
const WINDOW_WIDTH = 560;
const WINDOW_HEIGHT = 180;

let window: BrowserWindow | null = null;
let lrclib: LrclibClient;
let settings: SettingsStore | null = null;
let tray: TrayHandle | null = null;
let menuOptions: KashiMenuOptions | null = null;
/** Non-null only when settings carry a server_url — otherwise the code path
 * stays byte-for-byte the serverless v0.1.11 behavior (plan R-F3-8). */
let serverClient: KashiServerClient | null = null;
const enqueueGate = new EnqueueGate();
let gateTimer: NodeJS.Timeout | null = null;
/** Last known playing state (feeds the enqueue gate). */
let lastIsPlaying = false;

let currentTrackKey: string | null = null;
/** Only the (client, tab) that sent the last track_changed drives playback. */
let activeSource: { clientId: number; tabId: number } | null = null;
let debounceTimer: NodeJS.Timeout | null = null;
let lookupAbort: AbortController | null = null;
/** In-flight positions captured BEFORE the current track's announce are stale. */
let lastTrackSentAt = 0;

/** Last payloads, replayed to the renderer on (re)load. */
const lastPayloads = new Map<string, unknown>();

function trackKey(track: TrackInfo): string {
  return `${track.source.type}:${track.source.id}`;
}

function send(channel: string, payload: unknown): void {
  if (channel !== 'kashi:playback') {
    lastPayloads.set(channel, payload);
  } else {
    const msg = payload as { type?: string; is_playing?: boolean };
    if (msg.type === 'position' || msg.type === 'seek' || msg.type === 'playback_state') {
      // Keep a PAUSED copy for renderer-reload replay: without an anchor the
      // reloaded renderer shows nothing until the next live report.
      lastPayloads.set('kashi:playback', { ...msg, is_playing: false });
    }
  }
  if (window && !window.isDestroyed()) {
    window.webContents.send(channel, payload);
  }
}

/** Replay order matters: settings -> connection -> track -> lyrics -> anchor. */
const REPLAY_ORDER = [
  'kashi:settings',
  'kashi:connection',
  'kashi:track',
  'kashi:lyrics',
  'kashi:playback',
];

/** Clear every trace of the current source (close/disconnect/source_gone). */
function clearSource(reason: string): void {
  console.debug(`[kashi] source cleared (${reason})`);
  lookupAbort?.abort();
  if (debounceTimer) clearTimeout(debounceTimer);
  currentTrackKey = null;
  activeSource = null;
  lastTrackSentAt = 0;
  lastPayloads.delete('kashi:track');
  lastPayloads.delete('kashi:lyrics');
  lastPayloads.delete('kashi:playback');
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
      lastTrackSentAt = msg.sent_at;
      enqueueGate.trackChanged(); // a 404 belongs to ONE track only (R-9)
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
      // An in-flight report captured BEFORE the current announce belongs to
      // the previous track — anchoring the fresh clock with it would start
      // the new lyrics at the old song's offset (audit).
      if ('captured_at' in msg && msg.captured_at < lastTrackSentAt) return;
      if ('is_playing' in msg) {
        lastIsPlaying = msg.is_playing;
        enqueueGate.playback(msg.is_playing, Date.now());
      }
      send('kashi:playback', msg);
      return;
    default:
      return;
  }
}

/** Poll the gate once per second while armed; fire-and-forget the ingest. */
function armGateTimer(track: TrackInfo): void {
  if (gateTimer) clearInterval(gateTimer);
  gateTimer = setInterval(() => {
    const firedKey = enqueueGate.tick(Date.now());
    if (!enqueueGate.armed && gateTimer) {
      clearInterval(gateTimer);
      gateTimer = null;
    }
    if (firedKey && serverClient) {
      console.debug(`[kashi] 20s of listening on ${firedKey} — enqueueing for processing`);
      void serverClient.enqueue(
        { type: track.source.type, id: track.source.id },
        {
          title: track.title,
          artist: track.artist,
          album: track.album ?? undefined,
          duration_ms: track.duration_ms ?? undefined,
          artwork_url: track.artwork_url ?? undefined,
        },
      );
    }
  }, 1000);
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

  // Server first when configured. A processed document is the SINGLE source
  // of truth — on a hit lrclib is never consulted, never blended (R-8).
  if (serverClient) {
    const result = await serverClient.getProcessed(
      track.source.type,
      track.source.id,
      abort.signal,
    );
    if (abort.signal.aborted || key !== currentTrackKey) return; // stale (R-9)
    if ('found' in result && result.found) {
      console.debug(
        `[kashi] server hit: ${key} sync=${result.sync} quality=${result.qualityScore}`,
      );
      send('kashi:lyrics', { key, ...result });
      return;
    }
    if ('found' in result && !result.found) {
      // Genuinely unprocessed: arm the >=20 s listening gate (R-9), then let
      // the lrclib flow below fill the screen in the meantime.
      enqueueGate.serverMiss(key, Date.now(), lastIsPlaying);
      armGateTimer(track);
      console.debug(`[kashi] server 404: ${key} — lrclib fallback + enqueue gate armed`);
    } else {
      console.debug(`[kashi] server error for ${key} — lrclib fallback (gate NOT armed)`);
    }
  }

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
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
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
  // window interactive while the cursor hovers the lyric box (drag support).
  win.setIgnoreMouseEvents(true, { forward: true });

  // Restore the saved position ONLY when it still lands on a live display —
  // monitors get unplugged and resolutions change between runs (R-4). Both
  // the visibility check and the restore use the REAL window size: stored
  // width/height could be hand-edited, and a position-only move across a DPI
  // boundary rescales the window (same trap as the drag path — review).
  const savedBounds = settings?.get().window_bounds;
  if (savedBounds) {
    const target = {
      x: savedBounds.x,
      y: savedBounds.y,
      width: WINDOW_WIDTH,
      height: WINDOW_HEIGHT,
    };
    if (isPositionVisible(target, screen.getAllDisplays())) {
      win.setBounds(target);
    }
  }

  win.on('closed', () => {
    window = null;
  });
  // Replay last known state after every load (startup, reload, crash restart).
  win.webContents.on('did-finish-load', () => {
    for (const channel of REPLAY_ORDER) {
      const payload = lastPayloads.get(channel);
      if (payload !== undefined) win.webContents.send(channel, payload);
    }
  });

  if (process.env['ELECTRON_RENDERER_URL']) {
    void win.loadURL(process.env['ELECTRON_RENDERER_URL']);
  } else {
    void win.loadFile(join(__dirname, '../renderer/index.html'));
  }
  return win;
}

function persistWindowBounds(): void {
  if (!window || window.isDestroyed() || !settings) return;
  settings.update({ window_bounds: window.getBounds() });
}

/** Persist + broadcast a new box alpha (tray preset or Ctrl+scroll nudge). */
let trayRefreshTimer: NodeJS.Timeout | null = null;
function applyBoxAlpha(alpha: number): void {
  const clamped = clampAlpha(alpha);
  settings?.update({ box_alpha: clamped });
  send('kashi:settings', { box_alpha: clamped });
  // Debounced: a scroll burst must not rebuild the tray menu per event.
  if (trayRefreshTimer) clearTimeout(trayRefreshTimer);
  trayRefreshTimer = setTimeout(() => tray?.refresh(), 200);
}

function resetWindowPosition(): void {
  if (!window || window.isDestroyed()) return;
  const workArea = screen.getPrimaryDisplay().workArea;
  window.setBounds({
    x: workArea.x + Math.round((workArea.width - WINDOW_WIDTH) / 2),
    y: workArea.y + Math.round((workArea.height - WINDOW_HEIGHT) / 2),
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
  });
  persistWindowBounds();
}

ipcMain.on('kashi:set-interactive', (_event, interactive: unknown) => {
  window?.setIgnoreMouseEvents(interactive !== true, { forward: true });
});

ipcMain.on('kashi:adjust-opacity', (_event, deltaSteps: unknown) => {
  if (!settings) return;
  // adjustAlpha sanitizes the untrusted IPC delta (R-7) and clamps the result.
  applyBoxAlpha(adjustAlpha(settings.get().box_alpha, deltaSteps as number));
});

ipcMain.on('kashi:rlog', (_event, line: unknown) => {
  console.debug(`[renderer] ${String(line).slice(0, 500)}`);
});

// Right-click on the lyric box pops the same menu the tray serves — the tray
// icon can be buried in the Windows overflow area, the box is always at hand.
ipcMain.on('kashi:open-menu', () => {
  if (!window || window.isDestroyed() || !menuOptions) return;
  buildKashiMenu(menuOptions).popup({ window });
});

/**
 * Manual dragging: the window follows the cursor while the renderer reports a
 * drag (mousedown on the lyric box). Because the window moves WITH the cursor,
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
    persistWindowBounds(); // remember where the user parked the box (R-4)
  }
}

ipcMain.on('kashi:drag-start', () => {
  if (!window || window.isDestroyed() || drag) return;
  const cursor = screen.getCursorScreenPoint();
  const [winX = 0, winY = 0] = window.getPosition();
  const [width = WINDOW_WIDTH, height = WINDOW_HEIGHT] = window.getSize();
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
  settings = new SettingsStore(join(app.getPath('userData'), 'kashi-settings.json'), (line) =>
    console.debug(`[kashi] ${line}`),
  );

  lrclib = new LrclibClient({
    cacheDir: join(app.getPath('userData'), 'cache', 'lrclib'),
    // Chromium's network stack (proper happy-eyeballs/IPv6 fallback, OS proxy)
    // — Node's fetch stalls for seconds on broken IPv6 routes.
    fetchFn: net.fetch.bind(net) as typeof fetch,
  });

  const { server_url: serverUrl, server_api_key: serverApiKey } = settings.get();
  if (serverUrl && serverApiKey) {
    serverClient = new KashiServerClient({
      baseUrl: serverUrl,
      apiKey: serverApiKey,
      cacheDir: join(app.getPath('userData'), 'cache', 'kashi-server'),
      fetchFn: net.fetch.bind(net) as typeof fetch,
      log: (line) => console.debug(`[kashi] ${line}`),
    });
    console.debug(`[kashi] server configured: ${serverUrl}`);
  } else if (serverUrl || serverApiKey) {
    console.warn('[kashi] server_url and server_api_key must BOTH be set — server disabled');
  }

  window = createOverlayWindow();
  // Seed the replay map so every renderer load starts with current settings.
  send('kashi:settings', { box_alpha: settings.get().box_alpha });

  menuOptions = {
    version: KASHI_VERSION,
    getAlpha: () => settings?.get().box_alpha ?? 0,
    onAlphaSelect: applyBoxAlpha,
    onResetPosition: resetWindowPosition,
    onQuit: () => app.quit(),
  };
  tray = createTray(menuOptions);

  const server = new OverlayWsServer({
    // TODO(R-6): once the extension ID is pinned via the manifest `key`, pass
    // allowedOrigins (+ optional token) from settings. Until then any
    // chrome-extension:// origin is accepted; payloads are shape-validated.
    expectedClient: EXPECTED_EXTENSION, // bump in shared/version.ts with the manifest
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
  console.debug(`[kashi] overlay v${KASHI_VERSION} ready, ws on 127.0.0.1:${port}`);

  app.on('before-quit', () => {
    settings?.flush();
    void server.stop();
  });
});

app.on('window-all-closed', () => {
  app.quit();
});
