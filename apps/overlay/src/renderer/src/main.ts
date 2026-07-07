/**
 * Renderer: line-level lyrics display driven by the position clock.
 * All dynamic text goes through textContent — never innerHTML (plan R-7).
 */
import type {
  AdStateMessage,
  PlaybackStateMessage,
  PositionMessage,
  SeekMessage,
} from '@kashi/protocol';
import { PositionClock } from './position-clock.js';

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
  setInteractive: (interactive: boolean) => void;
  dragStart: () => void;
  dragEnd: () => void;
  log: (line: string) => void;
}

declare global {
  interface Window {
    kashi: KashiBridge;
  }
}

const lineEl = document.getElementById('lyric-line');

const clock = new PositionClock();
let currentKey: string | null = null;
let lines: LyricLine[] = [];
let activeIndex = -1;
let adActive = false;
// Idle default (Caner's call): no big "waiting" text — a small dim badge.
let statusText = 'Kashi';
let statusDim = true;
let trackLabel = '';
let lastPlaybackMono = performance.now();

function setLine(text: string, dim = false): void {
  if (!lineEl) return;
  if (lineEl.textContent !== text) lineEl.textContent = text;
  lineEl.classList.toggle('dim', dim);
}

window.kashi.onTrack((payload) => {
  const { key, track } = payload as { key: string; track: { title: string; artist: string } };
  currentKey = key;
  lines = [];
  activeIndex = -1;
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
    activeIndex = -1;
    statusText = `${trackLabel}  ·  lyrics ⋯`;
    statusDim = false;
    ensureLoop();
    return;
  }
  if (data.found && data.lines) {
    lines = data.lines;
    window.kashi.log(`lyrics applied: ${lines.length} lines`);
  } else {
    lines = [];
    statusText = data.error ? 'Lyrics unavailable (network)' : 'No synced lyrics found';
    statusDim = true;
    window.kashi.log(`lyrics ${data.error ? 'ERROR' : 'not found'}`);
  }
  activeIndex = -1;
  ensureLoop();
});

window.kashi.onPlayback((payload) => {
  const msg = payload as PlaybackMessage;
  if (msg.type === 'ad_state') {
    adActive = msg.is_ad;
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
  currentKey = null;
  lines = [];
  activeIndex = -1;
  adActive = false;
  clock.reset();
  statusText = 'Kashi';
  statusDim = true;
  ensureLoop();
});

window.kashi.onConnection((payload) => {
  const { connected } = payload as { connected: boolean };
  window.kashi.log(`connection: ${connected}`);
  if (!connected) {
    // Source gone → back to the small idle badge (no stale lyrics on screen).
    currentKey = null;
    lines = [];
    activeIndex = -1;
    adActive = false;
    clock.reset();
    statusText = 'Kashi';
    statusDim = true;
  }
  ensureLoop();
});

// Hover ↔ click-through: the window ignores mouse events by default but
// forwards mousemove; hovering the lyric text flips it interactive so it can
// be dragged, leaving flips it back (R-4). Dragging is manual (mousedown →
// IPC cursor-follow) because -webkit-app-region would swallow mouse events.
let interactive = false;
let dragging = false;

document.addEventListener('mousemove', (event) => {
  if (dragging) return;
  const over = lineEl?.contains(event.target as Node) ?? false;
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
lineEl?.addEventListener('mousedown', (event) => {
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
  // keep scrolling ghost lyrics forever, drop to the idle badge.
  if (clock.isPlaying && performance.now() - lastPlaybackMono > 10_000) {
    window.kashi.log('data-loss watchdog: no position for 10s -> idle');
    currentKey = null;
    lines = [];
    activeIndex = -1;
    adActive = false;
    clock.reset();
    statusText = 'Kashi';
    statusDim = true;
  }
  if (adActive) {
    setLine('');
  } else if (lines.length === 0) {
    setLine(statusText, statusDim);
  } else {
    const index = findActiveLine(clock.positionAt());
    if (index !== activeIndex) {
      activeIndex = index;
      setLine(index >= 0 ? (lines[index]?.text ?? '') : '♪');
    }
  }
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

export {};
