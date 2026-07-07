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
  setInteractive: (interactive: boolean) => void;
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
let statusText = 'Kashi — waiting for music…';

function setLine(text: string): void {
  if (lineEl && lineEl.textContent !== text) lineEl.textContent = text;
}

window.kashi.onTrack((payload) => {
  const { key, track } = payload as { key: string; track: { title: string; artist: string } };
  currentKey = key;
  lines = [];
  activeIndex = -1;
  clock.reset();
  statusText = `♪ ${track.artist} — ${track.title}`;
  ensureLoop();
});

window.kashi.onLyrics((payload) => {
  const data = payload as { key: string; found: boolean; lines?: LyricLine[] };
  if (data.key !== currentKey) return; // stale (R-9)
  if (data.found && data.lines) {
    lines = data.lines;
  } else {
    lines = [];
    statusText = 'No synced lyrics found';
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
  ensureLoop();
});

window.kashi.onConnection((payload) => {
  const { connected } = payload as { connected: boolean };
  if (!connected) statusText = 'Kashi — extension disconnected';
  ensureLoop();
});

// Hover ↔ click-through: the window ignores mouse events by default but
// forwards mousemove; hovering the lyric text flips it interactive so it can
// be dragged, leaving flips it back (R-4).
let interactive = false;
document.addEventListener('mousemove', (event) => {
  const over = lineEl?.contains(event.target as Node) ?? false;
  if (over !== interactive) {
    interactive = over;
    window.kashi.setInteractive(over);
  }
});
document.documentElement.addEventListener('mouseleave', () => {
  if (interactive) {
    interactive = false;
    window.kashi.setInteractive(false);
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
  if (adActive) {
    setLine('');
  } else if (lines.length === 0) {
    setLine(statusText);
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
