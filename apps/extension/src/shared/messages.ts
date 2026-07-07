/**
 * Internal messages between the extension's own contexts.
 *
 * MAIN world → ISOLATED content script: window.postMessage with a namespaced
 * marker (page scripts can forge these — treat as untrusted display data).
 * Content script → service worker: chrome.runtime.sendMessage (these wake the
 * SW). The SW converts them to @kashi/protocol envelopes (adds seq + tab_id).
 */

export const MAIN_WORLD_MARKER = 'kashi-mw';

/** Posted by the MAIN-world bridge on mediaSession/player signals. */
export interface MainWorldSnapshot {
  source: typeof MAIN_WORLD_MARKER;
  kind: 'snapshot';
  title: string | null;
  artist: string | null;
  album: string | null;
  artworkUrl: string | null;
  playbackState: 'none' | 'paused' | 'playing';
  /**
   * From the player API (#movie_player getVideoData) — authoritative fallback
   * when YTM restores a paused session on a URL without ?v= (home page).
   */
  videoId: string | null;
  /** Set when the player fired `videodatachange` (primary track signal). */
  trackSignal: boolean;
}

/** Service worker → content script: re-announce current state (fresh beats stale). */
export interface ReannounceRequest {
  kind: 'reannounce';
}

/** Content script → service worker events (SW adds seq + tab_id). */
export type ContentEvent =
  | {
      kind: 'track_changed';
      videoId: string;
      title: string;
      artist: string;
      album?: string;
      duration_ms?: number;
      artwork_url?: string;
      sent_at: number;
    }
  | {
      kind: 'position' | 'seek' | 'playback_state';
      position_ms: number;
      playback_rate: number;
      is_playing: boolean;
      captured_at: number;
      sent_at: number;
    }
  | { kind: 'ad_state'; is_ad: boolean; sent_at: number };
