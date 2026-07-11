/**
 * Pure service-worker logic — no chrome.* APIs so it stays unit-testable.
 * The seat state machine (TAB-PINNED semantics) lives here; sw/index.ts is
 * only chrome glue: the storage lock, the connection, and event listeners.
 */

import type { ExtensionToOverlayMessage, TrackInfo } from '@kashi/protocol';
import type { ContentEvent } from '../shared/messages.js';
import type { Sendable } from './connection.js';

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

// --- seat state machine (pure; mutates the passed state under the caller's
// --- storage lock and returns what to send/log) -----------------------------

export type Snapshot = {
  track?: Extract<ExtensionToOverlayMessage, { type: 'track_changed' }>;
  position?: Extract<ExtensionToOverlayMessage, { type: 'position' }>;
  savedAt?: number;
};

/** Snapshots older than this are not replayed — a fresh reannounce wins. */
export const SNAPSHOT_MAX_AGE_MS = 5 * 60_000;

export interface SessionState {
  tabs: Record<number, TabState>;
  snapshots: Record<number, Snapshot>;
  activeTabId: number | null;
}

export interface SeatOutcome {
  sends: Sendable[];
  logs: Array<{ context: string; line: string }>;
}

export function toProtocolMessage(event: ContentEvent, tabId: number): Sendable | null {
  switch (event.kind) {
    case 'track_changed': {
      const track: TrackInfo = {
        source: { type: 'youtube', id: event.videoId },
        title: event.title,
        artist: event.artist,
        album: event.album,
        duration_ms: event.duration_ms,
        artwork_url: event.artwork_url,
      };
      return { type: 'track_changed', sent_at: event.sent_at, tab_id: tabId, track };
    }
    case 'position':
      return {
        type: 'position',
        sent_at: event.sent_at,
        tab_id: tabId,
        position_ms: event.position_ms,
        playback_rate: event.playback_rate,
        is_playing: event.is_playing,
        captured_at: event.captured_at,
      };
    case 'seek':
    case 'playback_state':
      return {
        type: event.kind,
        sent_at: event.sent_at,
        tab_id: tabId,
        position_ms: event.position_ms,
        is_playing: event.is_playing,
        captured_at: event.captured_at,
      };
    case 'ad_state':
      return { type: 'ad_state', sent_at: event.sent_at, tab_id: tabId, is_ad: event.is_ad };
    default:
      return null;
  }
}

export function applyContentEvent(
  state: SessionState,
  event: Exclude<ContentEvent, { kind: 'log' }>,
  tabId: number,
  freshAudible: boolean | undefined,
  now: number,
): SeatOutcome {
  const outcome: SeatOutcome = { sends: [], logs: [] };

  // A tab earns isPlaying only through playback events — announcing a track
  // proves nothing (phantom/prerender pages announce without ever playing).
  const wasPlaying = state.tabs[tabId]?.isPlaying ?? false;
  const isPlaying =
    event.kind === 'track_changed' || event.kind === 'ad_state' ? wasPlaying : event.is_playing;
  const audible = freshAudible ?? state.tabs[tabId]?.audible;
  state.tabs[tabId] = { isPlaying, lastEventAt: now, audible };

  const msg = toProtocolMessage(event, tabId);
  if (!msg) return outcome;

  const snapshot = state.snapshots[tabId] ?? {};
  if (msg.type === 'track_changed') {
    snapshot.track = { ...msg, seq: 0 } as Snapshot['track'];
    snapshot.position = undefined;
    snapshot.savedAt = now;
  } else if (msg.type === 'position') {
    snapshot.position = { ...msg, seq: 0 } as Snapshot['position'];
    snapshot.savedAt = now;
  } else if (msg.type === 'seek' || msg.type === 'playback_state') {
    // A pause MUST land in the snapshot: replaying a stale is_playing=true
    // report catapults the overlay clock forward by the whole gap.
    snapshot.position = {
      type: 'position',
      seq: 0,
      sent_at: msg.sent_at,
      tab_id: tabId,
      position_ms: msg.position_ms,
      playback_rate: snapshot.position?.playback_rate ?? 1,
      is_playing: msg.is_playing,
      captured_at: msg.captured_at,
    } as Snapshot['position'];
    snapshot.savedAt = now;
  }
  state.snapshots[tabId] = snapshot;

  const before = state.activeTabId;
  // TAB-PINNED seat (user verdict 2026-07-08 final): lyrics belong to a TAB
  // until that tab CLOSES. Pause/resume/track-changes/transient silence
  // never move the seat — YTM goes briefly silent on every track switch,
  // which made any state-based handover jump to another tab mid-listening.
  // An empty seat is claimed by the first tab that plays (or announces);
  // succession on close lives in handleTabRemoved.
  if (state.activeTabId === null && (isPlaying || msg.type === 'track_changed')) {
    state.activeTabId = tabId;
    outcome.logs.push({ context: 'seat', line: `tab - -> ${tabId} (claim, via ${event.kind})` });
  }

  if (state.activeTabId !== tabId) return outcome; // not the seat tab — recorded only

  // Fresh claim via a non-track event: re-key the overlay first so the
  // position stream never applies to a previous source's lyrics.
  if (before === null && msg.type !== 'track_changed') {
    const track = state.snapshots[tabId]?.track;
    if (track) outcome.sends.push(track);
  }
  outcome.sends.push(msg);
  return outcome;
}

export interface TabRemovedOutcome extends SeatOutcome {
  /** Successor tab to ask for a live reannounce (its snapshot may be stale). */
  reannounceTabId: number | null;
}

export function handleTabRemoved(
  state: SessionState,
  tabId: number,
  now: number,
): TabRemovedOutcome {
  const outcome: TabRemovedOutcome = { sends: [], logs: [], reannounceTabId: null };
  delete state.tabs[tabId];
  delete state.snapshots[tabId];
  outcome.logs.push({ context: 'tabs', line: `tab ${tabId} closed` });
  if (state.activeTabId !== tabId) return outcome;

  // Only a tab that is actually PLAYING may take over; a paused leftover
  // must not resurrect stale lyrics. No successor -> clear immediately.
  const playing = Object.fromEntries(Object.entries(state.tabs).filter(([, t]) => t.isPlaying));
  state.activeTabId = selectActiveTab(playing, null);
  outcome.logs.push({
    context: 'seat',
    line: `tab ${tabId} -> ${state.activeTabId ?? '-'} (active tab closed)`,
  });
  if (state.activeTabId === null) {
    outcome.sends.push({ type: 'source_gone', sent_at: now });
    return outcome;
  }
  const next = state.snapshots[state.activeTabId];
  if (next?.track) outcome.sends.push(next.track);
  if (next?.position) outcome.sends.push({ ...next.position, is_playing: false });
  outcome.reannounceTabId = state.activeTabId;
  return outcome;
}

export function snapshotReplayMessages(state: SessionState, now: number): Sendable[] {
  if (state.activeTabId === null) return [];
  const snapshot = state.snapshots[state.activeTabId];
  // Stale snapshots (e.g. overlay restarted hours later) must not resurrect
  // an old track — the reannounce that follows will bring live state.
  if (!snapshot?.savedAt || now - snapshot.savedAt > SNAPSHOT_MAX_AGE_MS) return [];
  const sends: Sendable[] = [];
  if (snapshot.track) sends.push(snapshot.track);
  // Replayed positions go out PAUSED: the clock anchors at the last known
  // spot instead of extrapolating a stale is_playing=true report (the
  // latency clamp caps compensation at 5s, so minutes-old gaps time-shift).
  if (snapshot.position) sends.push({ ...snapshot.position, is_playing: false });
  return sends;
}
