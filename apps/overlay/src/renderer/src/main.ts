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
import { parseEffectLevel, type EffectLevel } from '../../shared/effect-level.js';
import {
  BEAT_IDLE,
  BeatCursor,
  beatsUsable,
  fillProgress,
  isFillWord,
  paletteToCssVars,
  type BeatFrame,
  type BeatsLike,
  type PaletteLike,
} from './effects-logic.js';
import { PositionClock } from './position-clock.js';
import {
  accumulateWheel,
  deriveView,
  findActiveWord,
  findDisplayLine,
  watchdogShouldReset,
  type ViewOutput,
  type WordTiming,
} from './view-logic.js';

interface LyricLine {
  start_ms: number;
  end_ms: number;
  text: string;
  /** Nonlexical ad-lib line (server 2.1.0+, Faz 4 styling). */
  adlib?: boolean;
  /** Present on kashi-server word-sync documents (Faz 3B). */
  words?: WordTiming[];
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
  openMenu: () => void;
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

// Effect engine (Faz 4). Palette/beats arrive with server lyrics; the level
// comes from settings. All per-frame beat work is class toggles on edges.
let effectLevel: EffectLevel = 'simple';
let currentPalette: PaletteLike | undefined;
let currentBeats: BeatsLike | undefined;
let beatCursor: BeatCursor | null = null;
let appliedBeat: BeatFrame = BEAT_IDLE;

/** Write the palette CSS vars (defaults when off / no palette — the v0.1.x look). */
function applyPaletteVars(): void {
  const vars = paletteToCssVars(effectLevel === 'off' ? undefined : currentPalette);
  for (const [name, value] of Object.entries(vars)) {
    document.documentElement.style.setProperty(name, value);
  }
}

/** Rebuild the beat cursor whenever the level or the beat grid changes. */
function rebuildBeatCursor(): void {
  beatCursor = beatsUsable(effectLevel, currentBeats)
    ? new BeatCursor(
        (currentBeats?.times_ms as number[]) ?? [],
        Array.isArray(currentBeats?.downbeat_indices)
          ? (currentBeats.downbeat_indices as number[]).filter((i) => Number.isInteger(i))
          : [],
      )
    : null;
  if (!beatCursor) setBeatClasses(BEAT_IDLE);
}

function setBeatClasses(frame: BeatFrame): void {
  if (frame.active === appliedBeat.active && frame.down === appliedBeat.down) return;
  appliedBeat = frame;
  boxEl?.classList.toggle('beat', frame.active);
  boxEl?.classList.toggle('beat-down', frame.down);
}

function applyEffectLevelClass(): void {
  document.body.classList.remove('fx-off', 'fx-simple', 'fx-full');
  document.body.classList.add(`fx-${effectLevel}`);
}

// Word mode: spans are (re)built ONLY when the active line changes; the
// per-frame work is toggling one class. Built with createElement/textContent
// exclusively — innerHTML stays banned (R-7).
let wordLineIndex = -1; // index of the line the spans belong to (-1 = none)
let wordSpans: HTMLSpanElement[] = [];
let activeWordIndex = -1;

function clearWordSpans(): void {
  wordLineIndex = -1;
  wordSpans = [];
  activeWordIndex = -1;
  fillSpanIndex = -1; // spans are gone — nothing carries .word-fill anymore
}

// Sustained-fill (Faz 4): the active long-held/ad-lib word sweeps via a CSS
// gradient driven by --kashi-fill. One style write per frame, on ONE span.
let fillSpanIndex = -1;

function clearWordFill(): void {
  if (fillSpanIndex < 0) return;
  const span = wordSpans[fillSpanIndex];
  span?.classList.remove('word-fill');
  span?.style.removeProperty('--kashi-fill');
  fillSpanIndex = -1;
}

function updateWordFill(
  words: readonly WordTiming[],
  index: number,
  lineAdlib: boolean,
  pos: number,
): void {
  const word = index >= 0 ? words[index] : undefined;
  if (!word || !isFillWord(word, lineAdlib, effectLevel)) {
    clearWordFill();
    return;
  }
  if (fillSpanIndex !== index) {
    clearWordFill();
    if (!wordSpans[index]) return;
    wordSpans[index].classList.add('word-fill');
    fillSpanIndex = index;
  }
  wordSpans[index]?.style.setProperty('--kashi-fill', fillProgress(word, pos).toFixed(3));
}

function buildWordSpans(lineIndex: number, words: readonly WordTiming[]): void {
  if (!lineEl) return;
  lineEl.replaceChildren();
  wordSpans = words.map((word, i) => {
    if (i > 0) lineEl.appendChild(document.createTextNode(' '));
    const span = document.createElement('span');
    span.className = 'word';
    span.textContent = word.text;
    lineEl.appendChild(span);
    return span;
  });
  wordLineIndex = lineIndex;
  activeWordIndex = -1;
}

function highlightWord(index: number): void {
  if (index === activeWordIndex) return;
  wordSpans[activeWordIndex]?.classList.remove('word-active');
  wordSpans[index]?.classList.add('word-active');
  activeWordIndex = index;
}

function applyView(view: ViewOutput): void {
  if (
    appliedView &&
    appliedView.boxVisible === view.boxVisible &&
    appliedView.lineText === view.lineText &&
    appliedView.lineDim === view.lineDim &&
    appliedView.searchVisible === view.searchVisible &&
    appliedView.interlude === view.interlude &&
    appliedView.lineAdlib === view.lineAdlib
  ) {
    return;
  }
  appliedView = view;
  boxEl?.classList.toggle('hidden', !view.boxVisible);
  if (lineEl) {
    // Plain-text mode always wins here; word mode repopulates right after.
    if (lineEl.textContent !== view.lineText) lineEl.textContent = view.lineText;
    lineEl.classList.toggle('dim', view.lineDim);
    lineEl.classList.toggle('interlude', view.interlude);
    lineEl.classList.toggle('adlib', view.lineAdlib);
  }
  clearWordSpans(); // any full repaint invalidates the span cache
  if (searchEl) searchEl.hidden = !view.searchVisible;
  // The box can hide under a MOTIONLESS cursor (ad start, watchdog reset) —
  // no mousemove follows, so without this the invisible window keeps
  // swallowing clicks until the user happens to move the mouse.
  if (!view.boxVisible && interactive && !dragging) {
    interactive = false;
    window.kashi.setInteractive(false);
  }
}

function clearEnrichment(): void {
  currentPalette = undefined;
  currentBeats = undefined;
  rebuildBeatCursor();
  applyPaletteVars();
}

function resetToIdle(): void {
  currentKey = null;
  lines = [];
  adActive = false;
  searching = false;
  clock.reset();
  statusText = 'Kashi';
  statusDim = true;
  clearEnrichment();
}

window.kashi.onTrack((payload) => {
  const { key, track } = payload as { key: string; track: { title: string; artist: string } };
  currentKey = key;
  lines = [];
  searching = false;
  adActive = false; // a track announce proves no ad is playing (audit: a lost
  // ad_state=false otherwise blanks every following song forever)
  clock.reset();
  clearEnrichment(); // last track's palette/beats must not theme this one
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
    palette?: PaletteLike;
    beats?: BeatsLike;
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
    // Server enrichment (Faz 4): palette themes the box, beats drive the
    // pulse. lrclib results carry neither — defaults keep the plain look.
    currentPalette = data.palette;
    currentBeats = data.beats;
    rebuildBeatCursor();
    applyPaletteVars();
    window.kashi.log(`lyrics applied: ${lines.length} lines`);
  } else {
    lines = [];
    clearEnrichment();
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
  const { box_alpha, timing_offset_ms, effect_level } = payload as {
    box_alpha?: unknown;
    timing_offset_ms?: unknown;
    effect_level?: unknown;
  };
  if (typeof box_alpha === 'number' && Number.isFinite(box_alpha)) {
    document.documentElement.style.setProperty('--kashi-box-alpha', String(box_alpha));
  }
  if (typeof timing_offset_ms === 'number' && Number.isFinite(timing_offset_ms)) {
    // positive = lyrics fire earlier (clamped main-side; belt here)
    timingOffsetMs = Math.max(-500, Math.min(500, Math.round(timing_offset_ms)));
  }
  if (effect_level !== undefined && parseEffectLevel(effect_level) !== effectLevel) {
    // Instant switch: a body class + variable/cursor reset — nothing rebuilt.
    effectLevel = parseEffectLevel(effect_level);
    applyEffectLevelClass();
    applyPaletteVars();
    rebuildBeatCursor();
  }
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
// Right-click opens the Kashi menu (opacity presets / reset / quit) — same
// template the tray uses; the box is easier to find than the tray icon.
boxEl?.addEventListener('contextmenu', (event) => {
  event.preventDefault();
  window.kashi.openMenu();
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

// The rAF loop only runs while there is motion to render (playing clock);
// otherwise we render the static state once and stop — keeps the compositor
// asleep when idle (battery).
let loopActive = false;
let timingOffsetMs = 0;

function frame(): void {
  // Data-loss watchdog: a "playing" clock with no position reports for 10 s
  // means the source vanished mid-play (tab closed, browser gone) — don't
  // keep scrolling ghost lyrics forever, drop to the idle badge. Ads get a
  // 3-minute leash instead (see watchdogShouldReset).
  if (watchdogShouldReset(clock.isPlaying, adActive, performance.now() - lastPlaybackMono)) {
    window.kashi.log('data-loss watchdog: position stream starved -> idle');
    resetToIdle();
  }
  const pos = clock.positionAt() + timingOffsetMs;
  let activeText: string | null = null;
  let lineIndex = -1;
  if (!adActive && lines.length > 0) {
    // Short gaps HOLD the previous line; only long breaks yield the interlude
    // mark (Caner's feedback — the ♪ was flashing between every section).
    lineIndex = findDisplayLine(lines, pos);
    activeText = lineIndex >= 0 ? (lines[lineIndex]?.text ?? null) : null;
  }
  const activeAdlib = lineIndex >= 0 && lines[lineIndex]?.adlib === true;
  applyView(
    deriveView({
      adActive,
      hasLines: lines.length > 0,
      activeText,
      statusText,
      statusDim,
      searching,
      activeAdlib,
    }),
  );

  // Word karaoke (kashi-server word-sync documents): applyView's change
  // detection leaves the spans alone on quiet frames; a line change repaints
  // the text and clears the span cache, and they are rebuilt here once.
  const words = lineIndex >= 0 ? lines[lineIndex]?.words : undefined;
  if (words && words.length > 0 && activeText !== null && !adActive) {
    if (wordLineIndex !== lineIndex) buildWordSpans(lineIndex, words);
    const wordIndex = findActiveWord(words, pos);
    highlightWord(wordIndex);
    updateWordFill(words, wordIndex, activeAdlib, pos);
  }

  // Beat pulse (effect level "full"): per-frame cost is a couple of integer
  // comparisons; DOM classes change only on window edges. No work while
  // paused/hidden/ad — and never a stuck .beat class after a stop.
  if (beatCursor && clock.isPlaying && !adActive && lines.length > 0) {
    setBeatClasses(beatCursor.frame(pos));
  } else {
    setBeatClasses(BEAT_IDLE);
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
// First paint uses the default level; the settings replay (first channel in
// replay order) corrects it before any lyrics arrive.
applyEffectLevelClass();
applyPaletteVars();
ensureLoop();

// Self-healing repaint: several halt states (stopped loop + stale screen) had
// no exit path when the position stream dies mid-transition. One cheap frame
// per second guarantees the display always converges to current state.
setInterval(() => ensureLoop(), 1000);

export {};
