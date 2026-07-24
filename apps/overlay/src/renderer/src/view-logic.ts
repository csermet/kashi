/**
 * Pure display-state derivation for the renderer. Keeping this DOM-free makes
 * the "what should be on screen right now" rules unit-testable — several Faz 2
 * bugs were exactly these rules interacting badly (render-gap audit).
 */
import type { EffectLevel } from '../../shared/effect-level.js';

export interface ViewState {
  /** An ad is playing — the overlay must show nothing at all. */
  adActive: boolean;
  /** Synced lyric lines are loaded for the current track. */
  hasLines: boolean;
  /** Text of the line covering the clock position; null before/between lines. */
  activeText: string | null;
  /** Status/idle text shown when no lines are loaded. */
  statusText: string;
  statusDim: boolean;
  /** A lyrics lookup is in flight (animated dots on their own row). */
  searching: boolean;
  /** The active line is a nonlexical ad-lib (server flag, Faz 4 styling). */
  activeAdlib?: boolean;
}

export interface ViewOutput {
  boxVisible: boolean;
  lineText: string;
  lineDim: boolean;
  searchVisible: boolean;
  /** Animated ♪ (intro / long instrumental break) instead of a static glyph. */
  interlude: boolean;
  /** Style the line as an ad-lib (italic/faded — Faz 4). */
  lineAdlib: boolean;
}

export function deriveView(state: ViewState): ViewOutput {
  if (state.adActive) {
    return {
      boxVisible: false,
      lineText: '',
      lineDim: false,
      searchVisible: false,
      interlude: false,
      lineAdlib: false,
    };
  }
  if (state.hasLines) {
    // activeText null = interlude territory (short gaps HOLD the previous
    // line upstream in findDisplayLine, so this really is a long break).
    return {
      boxVisible: true,
      lineText: state.activeText ?? '♪',
      lineDim: false,
      searchVisible: false,
      interlude: state.activeText === null,
      lineAdlib: state.activeText !== null && state.activeAdlib === true,
    };
  }
  return {
    boxVisible: true,
    lineText: state.statusText,
    lineDim: state.statusDim,
    searchVisible: state.searching,
    interlude: false,
    lineAdlib: false,
  };
}

/**
 * Whether a repaint should re-arm the one-shot line-entrance animation
 * (Faz 4 saha turu 2). Never on the first paint (no startup flicker), at
 * effect level off (pixel identity), on interlude views (that state owns the
 * `animation` property), or when the text did not actually change. Keyed off
 * the previous ViewOutput's lineText — word spans may have rewritten the
 * DOM's textContent with normalized spacing.
 */
export function shouldAnimateLineChange(
  prev: ViewOutput | null,
  next: ViewOutput,
  level: EffectLevel,
): boolean {
  return (
    level !== 'off' &&
    prev !== null &&
    next.boxVisible &&
    !next.interlude &&
    prev.lineText !== next.lineText
  );
}

import type { WordTiming } from '../../shared/lyrics.js';

export type { WordTiming };

export interface LineSpan {
  start_ms: number;
  end_ms: number;
}

/**
 * Gaps shorter than this HOLD the previous line on screen (Caner's call:
 * "bir sonraki kısım gelene kadar öncekiler yazmalı"); only genuinely long
 * instrumental breaks show the animated interlude mark instead.
 */
export const INTERLUDE_GAP_MS = 5_000;

/**
 * Which line to DISPLAY at `pos`: the covering line, or the previous one held
 * through a short gap. -1 means interlude territory (intro, a long break, or
 * long past the last line).
 */
export function findDisplayLine(
  lines: readonly LineSpan[],
  pos: number,
  gapMs: number = INTERLUDE_GAP_MS,
): number {
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
  if (found === -1) return -1; // intro — nothing sung yet
  const line = lines[found];
  if (!line) return -1;
  if (pos < line.end_ms) return found; // inside the line
  const next = lines[found + 1];
  if (!next) {
    // Outro: hold the last line briefly, then hand over to the interlude.
    return pos - line.end_ms > gapMs ? -1 : found;
  }
  // Short gap: hold the previous line. Long break: show the interlude.
  return next.start_ms - line.end_ms > gapMs ? -1 : found;
}

/**
 * Index of the word covering `pos` — the LAST word whose start_ms <= pos, so
 * the previous word stays lit through inter-word gaps (no flicker between
 * words). -1 before the first word. Binary search, same pattern as
 * findActiveLine in main.ts.
 */
export function findActiveWord(words: readonly WordTiming[], pos: number): number {
  let lo = 0;
  let hi = words.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const word = words[mid];
    if (!word) break;
    if (word.start_ms <= pos) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

// 60 s, not 10 s (field bug, Caner 2026-07-24): a big SEEK stalls YTM's MSE
// buffer with `paused=false`, so the clock reads "playing" while timeupdate
// goes silent — at 10 s the watchdog mistook the buffer for a dead source and
// wiped the rich document (palette/fx/lines) to the plain white fallback.
// Flaky WiFi backhaul (RE700X) makes 10 s+ buffers common. 60 s covers the
// buffer and still bounds a genuinely dead source (a closed tab drops the WS
// and resets via the connection path far sooner anyway).
export const WATCHDOG_THRESHOLD_MS = 60_000;
/**
 * Ads get a longer leash, NOT a full exemption: position silence during an ad
 * is deliberate (a >10 s ad must not wipe the lyrics — Faz 2 closure review),
 * but a content script that DIES mid-ad (tab navigated away, page crash)
 * never sends ad_state=false, and with a full exemption the overlay would
 * stay invisible forever (Paket C review). No real ad break outlasts this.
 */
export const AD_WATCHDOG_THRESHOLD_MS = 180_000;

/**
 * Data-loss watchdog predicate: a "playing" clock with no position reports
 * means the source died mid-play.
 */
export function watchdogShouldReset(
  clockPlaying: boolean,
  adActive: boolean,
  msSinceLastPlayback: number,
  thresholdMs: number = WATCHDOG_THRESHOLD_MS,
  adThresholdMs: number = AD_WATCHDOG_THRESHOLD_MS,
): boolean {
  return clockPlaying && msSinceLastPlayback > (adActive ? adThresholdMs : thresholdMs);
}

/**
 * Wheel deltas are device-dependent: pixel touchpads fire dozens of tiny
 * events per swipe while classic wheels send one ±100-px notch. Accumulate
 * into ~100-px steps so one notch ≈ one opacity step and a touchpad swipe
 * stays fine-grained instead of slamming the value to a bound.
 */
export const WHEEL_PX_PER_STEP = 100;
/** deltaMode 1 = lines, 2 = pages — normalize to approximate pixels. */
const WHEEL_LINE_PX = 20;
const WHEEL_PAGE_PX = 100;

export function accumulateWheel(
  accumulatedPx: number,
  deltaY: number,
  deltaMode: number,
): { accumulatedPx: number; steps: number } {
  if (deltaY === 0 || !Number.isFinite(deltaY)) {
    // Pure horizontal scroll (tilt wheel / two-finger sideways) is not an
    // opacity gesture — treating it as one dims the box by surprise.
    return { accumulatedPx, steps: 0 };
  }
  const px = deltaY * (deltaMode === 1 ? WHEEL_LINE_PX : deltaMode === 2 ? WHEEL_PAGE_PX : 1);
  const total = accumulatedPx + px;
  // `|| 0` normalizes Math.trunc's -0 — don't leak it into IPC/comparisons.
  const steps = Math.trunc(total / WHEEL_PX_PER_STEP) || 0;
  return { accumulatedPx: total - steps * WHEEL_PX_PER_STEP, steps };
}
