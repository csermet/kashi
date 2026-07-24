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
import {
  DEFAULT_EFFECT_LEVEL,
  DEFAULT_FILL_STYLE,
  DEFAULT_THEME_SCOPE,
  parseEffectLevel,
  parseFillStyle,
  parseThemeScope,
} from '../shared/effect-level.js';
import { EXPECTED_EXTENSION, KASHI_VERSION } from '../shared/version.js';
import { EnqueueGate } from './enqueue-gate.js';
import { enableUtf8Console, makeLogger, makeWarnLogger } from './log.js';
import { KashiServerClient } from './kashi-server.js';
import { normalizeServerUrl } from './kashi-server-logic.js';
import { LookupOrchestrator } from './lookup-orchestrator.js';
import { LrclibClient } from './lrclib.js';
import { ReplayStore } from './replay-store.js';
import {
  applyExtensionMessage,
  clearReasonOnDisconnect,
  emptyLatch,
} from './source-latch-logic.js';
import { adjustAlpha, adjustTimingOffset, clampAlpha, isPositionVisible, clampTimingOffset,
  migrateWindowBounds,
} from './settings-logic.js';
import { SettingsStore } from './settings.js';
import { buildKashiMenu, createTray, type KashiMenuOptions, type TrayHandle } from './tray.js';
import { OverlayWsServer } from './ws-server.js';

// Fix the Windows console codepage BEFORE the first log line (mojibake fix).
enableUtf8Console();

const log = makeLogger('main');
const warn = makeWarnLogger('main');
const logExt = makeLogger('ext');
const logRenderer = makeLogger('renderer');

/** NOTE: the extension keeps its own announce debounce of the same length. */
const TRACK_DEBOUNCE_MS = 500;
/**
 * 0.9.0 window geometry (Faz 6.5 P2): the window grew ONCE (560×180 →
 * 640×300) to host effects OUTSIDE the lyric box — a 120px top icon stage
 * and 40px side gutters (the ambient ring stops clipping at the edge). The
 * BOX ZONE (bottom 640×… center 560×180) is the old window verbatim:
 * `#stage` pads the band away and centers the box in the remaining area, so
 * saved positions migrate by a constant shift (settings-logic
 * migrateWindowBounds) and the box stays exactly where the user parked it.
 * Still a FIXED size — transparent windows must never resize (Electron).
 */
const WINDOW_WIDTH = 640;
const WINDOW_HEIGHT = 300;
/** Box zone rect inside the window (must match #stage padding in style.css). */
const BOX_ZONE = { x: 40, y: 120, width: 560, height: 180 } as const;

let window: BrowserWindow | null = null;
let lrclib: LrclibClient;
const settingsStore = new SettingsStore(
  join(app.getPath('userData'), 'kashi-settings.json'),
  makeLogger('settings'),
);

// One overlay per machine: a lingering old instance steals the WS port (the
// extension silently drifts to 17891+) and holds Chromium's disk-cache locks
// ('Unable to move the cache: Access is denied' startup noise). app.relaunch
// starts the successor only after this instance exits, so the toggle-restart
// path never trips over the lock.
if (!app.requestSingleInstanceLock()) {
  log('another Kashi instance already runs -> exiting this one');
  app.quit();
}

// FIELD FIX (2026-07-13, "v0.1'den beri var"): Chromium's native window-
// occlusion tracker misjudges an always-on-top transparent overlay as
// occluded a few seconds after it goes idle and drops its rendering —
// on screen that reads as the box fading MORE TRANSPARENT until anything
// (screenshot tool, focus change) forces a re-evaluation. Textbook symptom
// set; the established fix is disabling the feature.
if (process.platform === 'win32') {
  app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');
}

// This app has no use for Chromium's disk caches (renderer loads local
// files; lrclib/server caching is OUR code in the main process). Orphaned
// helper processes after a dev Ctrl+C hold these cache dirs locked, and
// every next launch prints 'Unable to move the cache: Access is denied' —
// no cache dirs, no lock class. Applies packaged too, by design.
app.commandLine.appendSwitch('disable-http-cache');
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');

let settings: SettingsStore | null = null;
let tray: TrayHandle | null = null;
let menuOptions: KashiMenuOptions | null = null;
// lrclib contribute-back (Faz 5 P6): the last word-sync SERVER hit, valid
// only while its track is still the current one.
let publishable: { key: string; source: { type: string; id: string } } | null = null;
// The current track's lookup inputs — replayed after a live server-settings
// re-init (Faz 6 P6) so lyrics refresh without waiting for a track change.
let lastTrack: { key: string; track: TrackInfo } | null = null;
/** Non-null only when settings carry a server_url — otherwise the code path
 * stays byte-for-byte the serverless v0.1.11 behavior (plan R-F3-8). */
let serverClient: KashiServerClient | null = null;
const enqueueGate = new EnqueueGate();
let gateTimer: NodeJS.Timeout | null = null;
/** Last known playing state (feeds the enqueue gate). */
let lastIsPlaying = false;

const latch = emptyLatch();
let debounceTimer: NodeJS.Timeout | null = null;
const replay = new ReplayStore();
/** Constructed in whenReady, once settings decide server vs serverless mode. */
let lookups: LookupOrchestrator;

function send(channel: string, payload: unknown): void {
  replay.record(channel, payload);
  if (window && !window.isDestroyed()) {
    window.webContents.send(channel, payload);
  }
}

/** Clear every trace of the current source (close/disconnect/source_gone). */
function clearSource(reason: string): void {
  log(`source cleared (${reason})`);
  lookups?.cancel();
  if (debounceTimer) clearTimeout(debounceTimer);
  Object.assign(latch, emptyLatch());
  replay.clearSourceChannels();
  send('kashi:source-gone', {});
}

function onExtensionMessage(msg: ExtensionToOverlayMessage, clientId: number): void {
  if (msg.type === 'log') {
    logExt(`[${msg.context}] ${msg.line}`);
    return; // diagnostics only — never forwarded to the renderer
  }
  const wasKey = latch.currentTrackKey;
  const decision = applyExtensionMessage(latch, msg, clientId);
  switch (decision.action) {
    case 'clear':
      clearSource('source_gone');
      return;
    case 'duplicate-track':
    case 'new-track': {
      const dup = decision.action === 'duplicate-track';
      const track = (msg as Extract<ExtensionToOverlayMessage, { type: 'track_changed' }>).track;
      log(
        `track_changed: ${decision.key} "${track.artist} - ${track.title}"` +
          ` (tab ${(msg as { tab_id: number }).tab_id}, client ${clientId})` +
          `${dup && decision.key === wasKey ? ' [dup]' : ''}`,
      );
      if (dup) return; // metadata refresh for same track
      lastTrack = { key: decision.key, track: decision.track };
      enqueueGate.trackChanged(); // a 404 belongs to ONE track only (R-9)
      send('kashi:track', { key: decision.key, track: decision.track });

      // Debounce: radio-mode skip chains must not spam LRCLIB (R-9).
      if (debounceTimer) clearTimeout(debounceTimer);
      lookups.cancel();
      debounceTimer = setTimeout(
        () => void lookups.lookup(decision.key, decision.track),
        TRACK_DEBOUNCE_MS,
      );
      return;
    }
    case 'playback':
      if (decision.isPlaying !== null) {
        lastIsPlaying = decision.isPlaying;
        enqueueGate.playback(decision.isPlaying, Date.now());
      }
      send('kashi:playback', decision.msg);
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
      log(`20s of listening on ${firedKey} -> enqueueing for processing`);
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
    // macOS-only: disable AppKit's constrainFrameRect, which otherwise clamps
    // the window top below the menu bar. Without this the lyric box — pinned
    // to the BOTTOM of the 640×300 window, 120px below its top — cannot reach
    // the upper ~30% of the screen (Caner, M2). No-op on Windows/Linux, which
    // already allow off-screen positioning; it does NOT resize the window
    // (resizable stays false), only lets setBounds place it freely, even
    // partly off-screen — exactly the ask.
    enableLargerThanScreen: true,
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
    // ≤0.8.x bounds (the legacy 560×180 size is the sentinel) shift by the
    // band/gutter so the lyric BOX lands exactly where the user parked it.
    const target = migrateWindowBounds(savedBounds, WINDOW_WIDTH, WINDOW_HEIGHT);
    if (target.x !== savedBounds.x || target.y !== savedBounds.y) {
      // A box parked <120px from the top edge now puts the band off-screen
      // (icons drop invisibly; some Linux WMs clamp instead) — log the
      // shift so a field report is one grep away (reviewer nit).
      log(
        `window bounds migrated from legacy 560x180: ` +
          `(${savedBounds.x},${savedBounds.y}) -> (${target.x},${target.y})`,
      );
    }
    if (isPositionVisible(target, screen.getAllDisplays())) {
      win.setBounds(target);
    }
  }

  win.on('closed', () => {
    window = null;
  });
  // Replay last known state after every load (startup, reload, crash restart).
  win.webContents.on('did-finish-load', () => {
    replay.replayInto((channel, payload) => win.webContents.send(channel, payload));
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

/**
 * Every settings broadcast carries the FULL snapshot: the replay store keeps
 * one payload per channel, so a partial `{box_alpha}` send would make a
 * reloaded renderer lose the other settings (they'd silently reset until the
 * next live change).
 */
function broadcastSettings(): void {
  if (!settings) return;
  const current = settings.get();
  send('kashi:settings', {
    box_alpha: current.box_alpha,
    timing_offset_ms: current.timing_offset_ms,
    effect_level: current.effect_level,
    theme_scope: current.theme_scope,
    fill_style: current.fill_style,
  });
}

/** Debounced tray rebuild: a scroll burst must not rebuild the menu per event. */
let trayRefreshTimer: NodeJS.Timeout | null = null;
function scheduleTrayRefresh(): void {
  if (trayRefreshTimer) clearTimeout(trayRefreshTimer);
  trayRefreshTimer = setTimeout(() => tray?.refresh(), 200);
}

function applyTimingOffset(offsetMs: number): void {
  const clamped = clampTimingOffset(offsetMs);
  settings?.update({ timing_offset_ms: clamped });
  log(`setting: timing offset -> ${clamped}ms`);
  broadcastSettings();
  tray?.refresh();
}

function applyEffectLevel(level: unknown): void {
  const parsed = parseEffectLevel(level);
  settings?.update({ effect_level: parsed });
  log(`setting: effects -> ${parsed}`);
  broadcastSettings();
  tray?.refresh();
}

function applyThemeScope(scope: unknown): void {
  const parsed = parseThemeScope(scope);
  settings?.update({ theme_scope: parsed });
  log(`setting: theme colors -> ${parsed}`);
  broadcastSettings();
  tray?.refresh();
}

function applyFillStyle(style: unknown): void {
  const parsed = parseFillStyle(style);
  settings?.update({ fill_style: parsed });
  log(`setting: fill style -> ${parsed}`);
  broadcastSettings();
  tray?.refresh();
}

function applyBoxAlpha(alpha: number): void {
  settings?.update({ box_alpha: clampAlpha(alpha) });
  broadcastSettings();
  scheduleTrayRefresh();
}

function resetWindowPosition(): void {
  if (!window || window.isDestroyed()) return;
  const workArea = screen.getPrimaryDisplay().workArea;
  // Center the BOX ZONE, not the window — the asymmetric top band would
  // otherwise park the visible box 60px below screen center.
  window.setBounds({
    x: workArea.x + Math.round(workArea.width / 2 - (BOX_ZONE.x + BOX_ZONE.width / 2)),
    y: workArea.y + Math.round(workArea.height / 2 - (BOX_ZONE.y + BOX_ZONE.height / 2)),
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
  });
  persistWindowBounds();
}

/**
 * "Other…" timing offset: a tiny single-instance prompt window. Menus can't
 * host text input and the overlay box is click-through, so arbitrary values
 * get their own ephemeral window (same hardened webPreferences + preload).
 */
const PROMPT_WIDTH = 320;
const PROMPT_HEIGHT = 150;
let promptWindow: BrowserWindow | null = null;

function openTimingOffsetPrompt(): void {
  if (promptWindow && !promptWindow.isDestroyed()) {
    promptWindow.focus();
    return;
  }
  const win = new BrowserWindow({
    width: PROMPT_WIDTH,
    height: PROMPT_HEIGHT,
    frame: false,
    resizable: false,
    skipTaskbar: true,
    show: false,
    backgroundColor: '#14161f',
    webPreferences: {
      preload: join(__dirname, '../preload/index.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  // Same elevation as the overlay: the prompt must be reachable above
  // fullscreen apps, or "Other…" silently opens underneath them.
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  // No blur-close: offset tuning means alt-tabbing to pause/seek the music,
  // and losing the typed value on every focus change made the prompt useless
  // (retro finding). Escape/Cancel/OK are the exits.
  // Open centered OVER the lyric box — that is where the user's eyes are —
  // clamped onto its display's work area (a box parked at a screen edge must
  // not push the prompt off-screen).
  if (window && !window.isDestroyed()) {
    const box = window.getBounds();
    const area = screen.getDisplayMatching(box).workArea;
    // Center on the BOX ZONE (the window itself now carries an off-center
    // icon band — window-centering would float the prompt over the band).
    const x = Math.round(box.x + BOX_ZONE.x + (BOX_ZONE.width - PROMPT_WIDTH) / 2);
    const y = Math.round(box.y + BOX_ZONE.y + (BOX_ZONE.height - PROMPT_HEIGHT) / 2);
    win.setBounds({
      x: Math.max(area.x, Math.min(x, area.x + area.width - PROMPT_WIDTH)),
      y: Math.max(area.y, Math.min(y, area.y + area.height - PROMPT_HEIGHT)),
      width: PROMPT_WIDTH,
      height: PROMPT_HEIGHT,
    });
  }
  win.on('closed', () => {
    if (promptWindow === win) promptWindow = null;
  });
  win.once('ready-to-show', () => win.show());
  const value = String(settings?.get().timing_offset_ms ?? 0);
  if (process.env['ELECTRON_RENDERER_URL']) {
    void win.loadURL(`${process.env['ELECTRON_RENDERER_URL']}/timing-offset.html?value=${value}`);
  } else {
    void win.loadFile(join(__dirname, '../renderer/timing-offset.html'), { query: { value } });
  }
  promptWindow = win;
}

ipcMain.on('kashi:timing-offset-submit', (event, value: unknown) => {
  if (!promptWindow || event.sender !== promptWindow.webContents) return;
  // Renderer only submits finite numbers; anything else changes nothing.
  if (typeof value === 'number' && Number.isFinite(value)) applyTimingOffset(value);
  promptWindow.close();
});

ipcMain.on('kashi:timing-offset-cancel', (event) => {
  // cancelPrompt() is shared by BOTH prompt pages (timing-offset and
  // server-settings) — the handler must serve both, or Escape/Cancel on the
  // frameless always-on-top settings window silently does nothing
  // (reviewer catch, Faz 6 closure).
  if (promptWindow && event.sender === promptWindow.webContents) promptWindow.close();
  if (serverPromptWindow && event.sender === serverPromptWindow.webContents) {
    serverPromptWindow.close();
  }
});

// Server settings prompt (Faz 6 P6): the SAME prompt-window recipe as the
// timing-offset dialog — always-on-top, no blur-close, Escape cancels. It
// exists to kill the hand-edit trap: the running app's debounced flush used
// to overwrite manual kashi-settings.json edits (field, twice).
const SERVER_PROMPT_HEIGHT = 230;
let serverPromptWindow: BrowserWindow | null = null;

function openServerSettingsPrompt(): void {
  if (serverPromptWindow && !serverPromptWindow.isDestroyed()) {
    serverPromptWindow.focus();
    return;
  }
  const win = new BrowserWindow({
    width: PROMPT_WIDTH,
    height: SERVER_PROMPT_HEIGHT,
    frame: false,
    resizable: false,
    skipTaskbar: true,
    show: false,
    backgroundColor: '#14161f',
    webPreferences: {
      preload: join(__dirname, '../preload/index.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  if (window && !window.isDestroyed()) {
    const box = window.getBounds();
    const area = screen.getDisplayMatching(box).workArea;
    // Box-zone centering, same reasoning as the timing-offset prompt.
    const x = Math.round(box.x + BOX_ZONE.x + (BOX_ZONE.width - PROMPT_WIDTH) / 2);
    const y = Math.round(box.y + BOX_ZONE.y + (BOX_ZONE.height - SERVER_PROMPT_HEIGHT) / 2);
    win.setBounds({
      x: Math.max(area.x, Math.min(x, area.x + area.width - PROMPT_WIDTH)),
      y: Math.max(area.y, Math.min(y, area.y + area.height - SERVER_PROMPT_HEIGHT)),
      width: PROMPT_WIDTH,
      height: SERVER_PROMPT_HEIGHT,
    });
  }
  win.on('closed', () => {
    if (serverPromptWindow === win) serverPromptWindow = null;
  });
  win.once('ready-to-show', () => win.show());
  // The key NEVER rides the query string — hasKey=1 just marks that one is
  // stored (the page shows an "unchanged" placeholder).
  const current = settings?.get();
  const query = {
    url: current?.server_url ?? '',
    hasKey: current?.server_api_key ? '1' : '0',
  };
  if (process.env['ELECTRON_RENDERER_URL']) {
    const params = new URLSearchParams(query).toString();
    void win.loadURL(`${process.env['ELECTRON_RENDERER_URL']}/server-settings.html?${params}`);
  } else {
    void win.loadFile(join(__dirname, '../renderer/server-settings.html'), { query });
  }
  serverPromptWindow = win;
}

ipcMain.on('kashi:server-settings-submit', (event, raw: unknown) => {
  if (!serverPromptWindow || event.sender !== serverPromptWindow.webContents) return;
  serverPromptWindow.close();
  if (typeof raw !== 'object' || raw === null || !settings) return;
  const { url, key } = raw as { url?: unknown; key?: unknown };
  const trimmedUrl = typeof url === 'string' ? url.trim() : '';
  if (trimmedUrl === '') {
    // Empty URL = serverless mode; the stored key dies with it (a key
    // without a URL is meaningless and the old pair must not resurrect).
    settings.update({ server_url: null, server_api_key: null });
    log('server settings: cleared -> serverless mode');
  } else {
    const normalized = normalizeServerUrl(trimmedUrl);
    if (normalized === null) {
      warn(`server settings: invalid URL ${JSON.stringify(trimmedUrl)} — nothing changed`);
      return;
    }
    const nextKey =
      key === null
        ? (settings.get().server_api_key ?? null) // untouched field = keep
        : typeof key === 'string' && key.trim() !== ''
          ? key.trim()
          : null;
    settings.update({ server_url: normalized, server_api_key: nextKey });
    log(`server settings: ${normalized} (key ${nextKey ? 'set' : 'MISSING'})`);
  }
  reinitServerConnections();
});

ipcMain.on('kashi:set-interactive', (_event, interactive: unknown) => {
  window?.setIgnoreMouseEvents(interactive !== true, { forward: true });
});

ipcMain.on('kashi:adjust-opacity', (_event, deltaSteps: unknown) => {
  if (!settings) return;
  // adjustAlpha sanitizes the untrusted IPC delta (R-7) and clamps the result.
  applyBoxAlpha(adjustAlpha(settings.get().box_alpha, deltaSteps as number));
});

ipcMain.on('kashi:adjust-timing-offset', (_event, deltaSteps: unknown) => {
  if (!settings) return;
  const next = adjustTimingOffset(settings.get().timing_offset_ms, deltaSteps as number);
  if (next === settings.get().timing_offset_ms) return; // pinned at a clamp edge
  settings.update({ timing_offset_ms: next });
  log(`setting: timing offset -> ${next}ms (scroll)`);
  broadcastSettings();
  scheduleTrayRefresh();
});

ipcMain.on('kashi:rlog', (_event, line: unknown) => {
  logRenderer(String(line).slice(0, 500));
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

function buildServerConnections(): void {
  if (!settings) return;
  const { server_url: serverUrl, server_api_key: serverApiKey } = settings.get();
  if (serverUrl && serverApiKey) {
    serverClient = new KashiServerClient({
      baseUrl: serverUrl,
      apiKey: serverApiKey,
      cacheDir: join(app.getPath('userData'), 'cache', 'kashi-server'),
      fetchFn: net.fetch.bind(net) as typeof fetch,
      log: makeLogger('server'),
    });
    log(`kashi-server configured: ${serverUrl}`);
  } else if (serverUrl || serverApiKey) {
    warn('server_url and server_api_key must BOTH be set -> server disabled');
  }

  lookups = new LookupOrchestrator({
    // Server first when configured. A processed document is the SINGLE source
    // of truth — on a hit lrclib is never consulted, never blended (R-8).
    getProcessed: serverClient
      ? (type, id, signal) => serverClient!.getProcessed(type, id, signal)
      : null,
    getLyrics: (query, signal) => lrclib.getLyrics(query, signal),
    send: (payload) => send('kashi:lyrics', payload),
    onServerMiss: (key, track) => {
      enqueueGate.serverMiss(key, Date.now(), lastIsPlaying);
      armGateTimer(track);
    },
    onServerWordHit: (key, source) => {
      publishable = { key, source };
      tray?.refresh(); // the Report entry appears while this doc is on screen
    },
    isCurrent: (key) => key === latch.currentTrackKey,
    log: makeLogger('lookup'),
  });
}

/** Live re-init after a server-settings save (Faz 6 P6): rebuild the client
 * + orchestrator against the new settings and replay the current track so
 * lyrics refresh in place. Old in-flight lookups die by staleness (R-9's
 * isCurrent guard); a wrong key lands on the existing graceful
 * "server disabled"/error paths. */
function reinitServerConnections(): void {
  lookups?.cancel();
  serverClient = null;
  publishable = null; // the old server's word-doc claim is void now
  enqueueGate.trackChanged(); // the OLD server's 404/enqueue state is void too
  buildServerConnections();
  tray?.refresh();
  if (lastTrack) void lookups.lookup(lastTrack.key, lastTrack.track);
}

app.whenReady().then(async () => {
  settings = settingsStore;

  lrclib = new LrclibClient({
    cacheDir: join(app.getPath('userData'), 'cache', 'lrclib'),
    // Chromium's network stack (proper happy-eyeballs/IPv6 fallback, OS proxy)
    // — Node's fetch stalls for seconds on broken IPv6 routes.
    fetchFn: net.fetch.bind(net) as typeof fetch,
  });

  buildServerConnections();

  window = createOverlayWindow();
  // Seed the replay map so every renderer load starts with current settings.
  broadcastSettings();

  menuOptions = {
    version: KASHI_VERSION,
    getAlpha: () => settings?.get().box_alpha ?? 0,
    onAlphaSelect: applyBoxAlpha,
    getTimingOffset: () => settings?.get().timing_offset_ms ?? 0,
    onTimingOffsetSelect: applyTimingOffset,
    onTimingOffsetCustom: openTimingOffsetPrompt,
    getEffectLevel: () => settings?.get().effect_level ?? DEFAULT_EFFECT_LEVEL,
    onEffectLevelSelect: applyEffectLevel,
    getThemeScope: () => settings?.get().theme_scope ?? DEFAULT_THEME_SCOPE,
    onThemeScopeSelect: applyThemeScope,
    getFillStyle: () => settings?.get().fill_style ?? DEFAULT_FILL_STYLE,
    onFillStyleSelect: applyFillStyle,
    onServerSettings: openServerSettingsPrompt,
    onResetPosition: resetWindowPosition,
    getCanReportSync: () =>
      serverClient !== null && publishable !== null && publishable.key === latch.currentTrackKey,
    onReportSync: () => {
      const target = publishable;
      if (!target || !serverClient || target.key !== latch.currentTrackKey) return;
      log(`publish request: ${target.source.type}:${target.source.id}`);
      void serverClient.requestPublish(target.source).then((outcome) => {
        // disabled = the server-side flag is off (default) — expected until
        // the operator opts in; the menu item still records honest intent.
        log(`publish request outcome: ${outcome}`);
      });
    },
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
      const reason = clearReasonOnDisconnect(latch, count, clientId);
      if (reason) clearSource(reason);
      send('kashi:connection', { connected: count > 0 });
    },
    log: makeLogger('ws'),
  });
  const port = await server.start();
  log(`overlay v${KASHI_VERSION} ready, ws on 127.0.0.1:${port}`);
  const boot = settings.get();
  log(
    `settings: effects=${boot.effect_level} theme=${boot.theme_scope} ` +
      `offset=${boot.timing_offset_ms}ms alpha=${boot.box_alpha} ` +
      `server=${boot.server_url ? 'on' : 'off'}`,
  );

  app.on('before-quit', () => {
    settings?.flush();
    void server.stop();
  });
});

app.on('window-all-closed', () => {
  app.quit();
});
