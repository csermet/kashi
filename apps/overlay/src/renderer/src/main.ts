/**
 * Renderer: line-level lyrics display driven by the position clock.
 * All dynamic text goes through textContent — never innerHTML (plan R-7).
 * Screen-state rules live in view-logic.ts (pure, unit-tested); this file is
 * the wiring: IPC subscriptions, the rAF loop, hover/drag/wheel plumbing.
 */
import type {
  AdStateMessage,
  PlaybackStateMessage,
  PositionMessage,
  SeekMessage,
} from '@kashi/protocol';
import { PositionClock } from './position-clock.js';
import {
  accumulateWheel,
  deriveView,
  watchdogShouldReset,
  type ViewOutput,
} from './view-logic.js';

interface LyricLine {
  start_ms: number;
  end_ms: number;
  text: string;
}

type PlaybackMessage = PositionMessage | SeekMessage | PlaybackStateMessage | AdStateMessage;

interface KashiBridge {
  version: string;
  onTrack: (cb: (payload: unknown) => void) => () => void;
  onPlayback: (cb: (payload: unknown) => void) => () => void;
  onLyrics: (cb: (payload: unknown) => void) => () => void;
  onConnection: (cb: (payload: unknown) => void) => () => void;
  onSourceGone: (cb: (payload: unknown) => void) => () => void;
  onSettings: (cb: (payload: unknown) => void) => () => void;
  setInteractive: (interactive: boolean) => void;
  dragStart: () => void;
  dragEnd: () => void;
  adjustOpacity: (deltaSteps: number) => void;
  log: (line: string) => void;
}

declare global {
  interface Window {
    kashi: KashiBridge;
  }
}

const boxEl = document.getElementById('lyric-box');
const lineEl = document.getElementById('lyric-line');
const searchEl = document.getElementById('search-line');

const clock = new PositionClock();
let currentKey: string | null = null;
let lines: LyricLine[] = [];
let adActive = false;
let searching = false;
// Idle default (Caner's call): no big "waiting" text — a small dim badge.
let statusText = 'Kashi';
let statusDim = true;
let trackLabel = '';
let lastPlaybackMono = performance.now();

/** Last applied view — repaint only on change (keeps idle frames free). */
let appliedView: ViewOutput | null = null;

function applyView(view: ViewOutput): void {
  if (
    appliedView &&
    appliedView.boxVisible === view.boxVisible &&
    appliedView.lineText === view.lineText &&
    appliedView.lineDim === view.lineDim &&
    appliedView.searchVisible === view.searchVisible
  ) {
    return;
  }
  appliedView = view;
  boxEl?.classList.toggle('hidden', !view.boxVisible);
  if (lineEl) {
    if (lineEl.textContent !== view.lineText) lineEl.textContent = view.lineText;
    lineEl.classList.toggle('dim', view.lineDim);
  }
  if (searchEl) searchEl.hidden = !view.searchVisible;
  // The box can hide under a MOTIONLESS cursor (ad start, watchdog reset) —
  // no mousemove follows, so without this the invisible window keeps
  // swallowing clicks until the user happens to move the mouse.
  if (!view.boxVisible && interactive && !dragging) {
    interactive = false;
    window.kashi.setInteractive(false);
  }
}

function resetToIdle(): void {
  currentKey = null;
  lines = [];
  adActive = false;
  searching = false;
  clock.reset();
  statusText = 'Kashi';
  statusDim = true;
}

window.kashi.onTrack((payload) => {
  const { key, track } = payload as { key: string; track: { title: string; artist: string } };
  currentKey = key;
  lines = [];
  searching = false;
  adActive = false; // a track announce proves no ad is playing (audit: a lost
  // ad_state=false otherwise blanks every following song forever)
  clock.reset();
  trackLabel = `♪ ${track.artist} — ${track.title}`;
  statusText = trackLabel;
  statusDim = false;
  window.kashi.log(`track set: ${key} ${trackLabel}`);
  ensureLoop();
});

window.kashi.onLyrics((payload) => {
  const data = payload as {
    key: string;
    found?: boolean;
    searching?: boolean;
    error?: boolean;
    lines?: LyricLine[];
  };
  if (data.key !== currentKey) return; // stale (R-9)
  if (data.searching) {
    lines = [];
    searching = true;
    statusText = trackLabel;
    statusDim = false;
    ensureLoop();
    return;
  }
  searching = false;
  if (data.found && data.lines) {
    lines = data.lines;
    window.kashi.log(`lyrics applied: ${lines.length} lines`);
  } else {
    lines = [];
    statusText = data.error ? 'Lyrics unavailable (network)' : 'No synced lyrics found';
    statusDim = true;
    window.kashi.log(`lyrics ${data.error ? 'ERROR' : 'not found'}`);
  }
  ensureLoop();
});

window.kashi.onPlayback((payload) => {
  const msg = payload as PlaybackMessage;
  if (msg.type === 'ad_state') {
    adActive = msg.is_ad;
    // Both ad edges reset the starvation timer: positions are suppressed on
    // purpose during ads, and after one the clock may extrapolate for a beat
    // before the first fresh report — neither is data loss (closure review).
    lastPlaybackMono = performance.now();
    ensureLoop(); // repaint — ad end must not leave a blank stopped screen
    return;
  }
  clock.update(
    {
      position_ms: msg.position_ms,
      // seek/playback_state carry no rate — undefined keeps the current rate.
      playback_rate: 'playback_rate' in msg ? msg.playback_rate : undefined,
      is_playing: msg.is_playing,
      captured_at: msg.captured_at,
    },
    msg.type === 'seek',
  );
  lastPlaybackMono = performance.now();
  ensureLoop();
});

window.kashi.onSourceGone(() => {
  window.kashi.log('source gone -> idle');
  resetToIdle();
  ensureLoop();
});

window.kashi.onConnection((payload) => {
  const { connected } = payload as { connected: boolean };
  window.kashi.log(`connection: ${connected}`);
  if (!connected) {
    // Source gone → back to the small idle badge (no stale lyrics on screen).
    resetToIdle();
  }
  ensureLoop();
});

window.kashi.onSettings((payload) => {
  const { box_alpha } = payload as { box_alpha?: unknown };
  if (typeof box_alpha !== 'number' || !Number.isFinite(box_alpha)) return;
  document.documentElement.style.setProperty('--kashi-box-alpha', String(box_alpha));
});

// Hover ↔ click-through: the window ignores mouse events by default but
// forwards mousemove; hovering the lyric BOX flips it interactive so it can
// be dragged (and Ctrl+scrolled), leaving flips it back (R-4). Dragging is
// manual (mousedown → IPC cursor-follow) because -webkit-app-region would
// swallow mouse events.
let interactive = false;
let dragging = false;

document.addEventListener('mousemove', (event) => {
  if (dragging) return;
  const over = boxEl?.contains(event.target as Node) ?? false;
  if (over !== interactive) {
    interactive = over;
    window.kashi.setInteractive(over);
  }
});
document.documentElement.addEventListener('mouseleave', () => {
  if (interactive && !dragging) {
    interactive = false;
    window.kashi.setInteractive(false);
  }
});
boxEl?.addEventListener('mousedown', (event) => {
  if (event.button !== 0) return;
  dragging = true;
  window.kashi.dragStart();
});
window.addEventListener('mouseup', () => {
  if (dragging) {
    dragging = false;
    window.kashi.dragEnd();
  }
});
window.addEventListener('blur', () => {
  if (dragging) {
    dragging = false;
    window.kashi.dragEnd();
  }
});
// Ctrl+scroll over the box tunes its background opacity (persisted in main).
// Deltas go through an accumulator (view-logic) so touchpads and classic
// wheels both land on ~1 step per comfortable gesture unit.
let wheelAccPx = 0;
boxEl?.addEventListener(
  'wheel',
  (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const { accumulatedPx, steps } = accumulateWheel(wheelAccPx, event.deltaY, event.deltaMode);
    wheelAccPx = accumulatedPx;
    // Scroll up (negative deltaY) increases opacity.
    if (steps !== 0) window.kashi.adjustOpacity(-steps);
  },
  { passive: false },
);

/** Index of the line covering `pos`, or -1. Assumes lines sorted by start. */
function findActiveLine(pos: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const line = lines[mid];
    if (!line) break;
    if (line.start_ms <= pos) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  if (found === -1) return -1;
  const line = lines[found];
  return line && pos < line.end_ms ? found : -1;
}

// The rAF loop only runs while there is motion to render (playing clock);
// otherwise we render the static state once and stop — keeps the compositor
// asleep when idle (battery).
let loopActive = false;

function frame(): void {
  // Data-loss watchdog: a "playing" clock with no position reports for 10 s
  // means the source vanished mid-play (tab closed, browser gone) — don't
  // keep scrolling ghost lyrics forever, drop to the idle badge. Ads get a
  // 3-minute leash instead (see watchdogShouldReset).
  if (watchdogShouldReset(clock.isPlaying, adActive, performance.now() - lastPlaybackMono)) {
    window.kashi.log('data-loss watchdog: position stream starved -> idle');
    resetToIdle();
  }
  let activeText: string | null = null;
  if (!adActive && lines.length > 0) {
    const index = findActiveLine(clock.positionAt());
    activeText = index >= 0 ? (lines[index]?.text ?? null) : null;
  }
  applyView(
    deriveView({ adActive, hasLines: lines.length > 0, activeText, statusText, statusDim, searching }),
  );
  if (clock.isPlaying && !adActive) {
    requestAnimationFrame(frame);
  } else {
    loopActive = false;
  }
}

function ensureLoop(): void {
  if (loopActive) return;
  loopActive = true;
  requestAnimationFrame(frame);
}
ensureLoop();

// Self-healing repaint: several halt states (stopped loop + stale screen) had
// no exit path when the position stream dies mid-transition. One cheap frame
// per second guarantees the display always converges to current state.
setInterval(() => ensureLoop(), 1000);

export {};
