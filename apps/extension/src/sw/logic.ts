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
  /** Chrome's ground truth: is sound actually coming out of this tab? */
  audible?: boolean;
}

/**
 * Active-tab selection (protocol §multi-tab). Ranking:
 *   1. audible (Chrome-verified sound output — phantom/prerender contexts and
 *      metadata-only tabs can never fake this)
 *   2. isPlaying (self-reported playback events)
 *   3. most recent event
 *
 * STICKY: the current active tab keeps its seat unless a challenger has a
 * STRICTLY higher score. Two tabs playing at once must not ping-pong the
 * overlay (equal scores + recency tie-break would flip on every 4 Hz event);
 * the first captured source stays until it actually stops.
 */
export function selectActiveTab(
  tabs: Record<number, TabState>,
  currentId: number | null = null,
): number | null {
  const score = (s: TabState) => (s.audible ? 2 : 0) + (s.isPlaying ? 1 : 0);
  let best: number | null = null;
  let bestState: TabState | null = null;
  for (const [idStr, state] of Object.entries(tabs)) {
    const id = Number(idStr);
    if (
      bestState === null ||
      score(state) > score(bestState) ||
      (score(state) === score(bestState) && state.lastEventAt > bestState.lastEventAt)
    ) {
      best = id;
      bestState = state;
    }
  }
  const current = currentId !== null ? tabs[currentId] : undefined;
  if (current && bestState && score(bestState) <= score(current)) return currentId;
  return best;
}
