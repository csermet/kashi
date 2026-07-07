/**
 * Pure service-worker logic — no chrome.* APIs so it stays unit-testable.
 */

/** Reconnect backoff: 1→2→5→10→30 s cap, ±20% jitter, infinite retries. */
const BACKOFF_STEPS_MS = [1000, 2000, 5000, 10_000, 30_000] as const;
const JITTER = 0.2;

export function backoffDelayMs(attempt: number, random: () => number = Math.random): number {
  const index = Math.min(Math.max(attempt, 0), BACKOFF_STEPS_MS.length - 1);
  const base = BACKOFF_STEPS_MS[index] ?? 30_000;
  const jitter = 1 + (random() * 2 - 1) * JITTER;
  return Math.round(base * jitter);
}

export interface TabState {
  isPlaying: boolean;
  lastEventAt: number;
}

/**
 * Active-tab selection (protocol §multi-tab): a playing tab wins; among
 * several playing (or none), the most recent event wins.
 */
export function selectActiveTab(tabs: Record<number, TabState>): number | null {
  let best: number | null = null;
  let bestState: TabState | null = null;
  for (const [idStr, state] of Object.entries(tabs)) {
    const id = Number(idStr);
    if (
      bestState === null ||
      (state.isPlaying && !bestState.isPlaying) ||
      (state.isPlaying === bestState.isPlaying && state.lastEventAt > bestState.lastEventAt)
    ) {
      best = id;
      bestState = state;
    }
  }
  return best;
}
