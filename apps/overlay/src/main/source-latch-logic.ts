/**
 * Source latch (R-9): only the (client, tab) that sent the last track_changed
 * drives playback, so a second YTM tab cannot corrupt the clock. Pure decision
 * logic — the caller applies the returned action (sends, debounce, gate).
 */
import type { ExtensionToOverlayMessage, TrackInfo } from '@kashi/protocol';

export interface LatchState {
  currentTrackKey: string | null;
  /** Only the (client, tab) that sent the last track_changed drives playback. */
  activeSource: { clientId: number; tabId: number } | null;
  /** In-flight positions captured BEFORE the current track's announce are stale. */
  lastTrackSentAt: number;
}

export function emptyLatch(): LatchState {
  return { currentTrackKey: null, activeSource: null, lastTrackSentAt: 0 };
}

export function trackKey(track: TrackInfo): string {
  return `${track.source.type}:${track.source.id}`;
}

export type LatchDecision =
  | { action: 'ignore' }
  | { action: 'clear' }
  | { action: 'duplicate-track'; key: string }
  | { action: 'new-track'; key: string; track: TrackInfo }
  | { action: 'playback'; msg: ExtensionToOverlayMessage; isPlaying: boolean | null };

/**
 * Applies a non-log extension message to the latch (mutating `state` for
 * track changes) and says what the caller must do.
 */
export function applyExtensionMessage(
  state: LatchState,
  msg: ExtensionToOverlayMessage,
  clientId: number,
): LatchDecision {
  switch (msg.type) {
    case 'source_gone':
      return { action: 'clear' };
    case 'track_changed': {
      state.activeSource = { clientId, tabId: msg.tab_id };
      const key = trackKey(msg.track);
      if (key === state.currentTrackKey) {
        return { action: 'duplicate-track', key }; // metadata refresh for same track
      }
      state.currentTrackKey = key;
      state.lastTrackSentAt = msg.sent_at;
      return { action: 'new-track', key, track: msg.track };
    }
    case 'position':
    case 'seek':
    case 'playback_state':
    case 'ad_state': {
      if (
        state.activeSource &&
        (clientId !== state.activeSource.clientId || msg.tab_id !== state.activeSource.tabId)
      ) {
        return { action: 'ignore' }; // another tab/client — not the one playing our track
      }
      // An in-flight report captured BEFORE the current announce belongs to
      // the previous track — anchoring the fresh clock with it would start
      // the new lyrics at the old song's offset (audit).
      if ('captured_at' in msg && msg.captured_at < state.lastTrackSentAt) {
        return { action: 'ignore' };
      }
      return {
        action: 'playback',
        msg,
        isPlaying: 'is_playing' in msg ? msg.is_playing : null,
      };
    }
    default:
      return { action: 'ignore' };
  }
}

/**
 * Disconnect rule (audit K3): when the latch owner vanishes (browser closed /
 * reconnect under a new client id) the source must clear, otherwise the
 * surviving stream is filtered forever.
 */
export function clearReasonOnDisconnect(
  state: LatchState,
  remainingClients: number,
  clientId: number,
): string | null {
  if (remainingClients === 0) return 'last client disconnected';
  if (state.activeSource?.clientId === clientId) return 'latch owner disconnected';
  return null;
}
