/**
 * Pure display-state derivation for the renderer. Keeping this DOM-free makes
 * the "what should be on screen right now" rules unit-testable — several Faz 2
 * bugs were exactly these rules interacting badly (render-gap audit).
 */

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
}

export interface ViewOutput {
  boxVisible: boolean;
  lineText: string;
  lineDim: boolean;
  searchVisible: boolean;
}

export function deriveView(state: ViewState): ViewOutput {
  if (state.adActive) {
    return { boxVisible: false, lineText: '', lineDim: false, searchVisible: false };
  }
  if (state.hasLines) {
    // ♪ marks intros/instrumental gaps — the box must not go blank mid-song.
    return {
      boxVisible: true,
      lineText: state.activeText ?? '♪',
      lineDim: false,
      searchVisible: false,
    };
  }
  return {
    boxVisible: true,
    lineText: state.statusText,
    lineDim: state.statusDim,
    searchVisible: state.searching,
  };
}

export const WATCHDOG_THRESHOLD_MS = 10_000;
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
