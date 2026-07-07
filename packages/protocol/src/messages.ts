/**
 * Kashi extension ↔ overlay local WebSocket protocol (v1).
 *
 * Transport: the overlay hosts a WebSocket server bound to 127.0.0.1 only;
 * the extension SERVICE WORKER connects (never a content script — Chrome 147+
 * Local Network Access prompts apply to page-context loopback sockets).
 *
 * Timing rule: `sent_at` / `captured_at` are epoch milliseconds stamped in the
 * content script AT CAPTURE TIME, so service-worker and socket latency can be
 * subtracted out by the overlay's extrapolation clock.
 */

export const PROTOCOL_VERSION = 1;

/** Default WS port; if taken, the overlay walks up through PORT_MAX. */
export const DEFAULT_PORT = 17890;
export const PORT_MAX = 17894;

/** Overlay → extension ping cadence. Must stay under Chrome's 30 s SW idle kill. */
export const PING_INTERVAL_MS = 20_000;

export interface Envelope {
  type: string;
  /** Monotonic per-connection sequence number. */
  seq: number;
  /** Epoch ms, stamped at capture time (content script) where applicable. */
  sent_at: number;
}

export interface TrackSource {
  type: 'youtube' | 'plex' | 'upload';
  id: string;
}

export interface TrackInfo {
  source: TrackSource;
  title: string;
  artist: string;
  album?: string;
  duration_ms?: number;
  artwork_url?: string;
}

// ---------------------------------------------------------------------------
// Extension → overlay
// ---------------------------------------------------------------------------

export interface HelloMessage extends Envelope {
  type: 'hello';
  protocol_version: number;
  /** e.g. "kashi-extension/0.1.0" */
  client: string;
  /** Optional shared token (off by default; configurable in overlay settings). */
  token?: string;
}

export interface TrackChangedMessage extends Envelope {
  type: 'track_changed';
  tab_id: number;
  track: TrackInfo;
}

export interface PositionMessage extends Envelope {
  type: 'position';
  tab_id: number;
  position_ms: number;
  playback_rate: number;
  is_playing: boolean;
  /** Epoch ms when video.currentTime was read. */
  captured_at: number;
}

export interface SeekMessage extends Envelope {
  type: 'seek';
  tab_id: number;
  position_ms: number;
  is_playing: boolean;
  captured_at: number;
}

export interface AdStateMessage extends Envelope {
  type: 'ad_state';
  tab_id: number;
  is_ad: boolean;
}

export interface PlaybackStateMessage extends Envelope {
  type: 'playback_state';
  tab_id: number;
  is_playing: boolean;
  position_ms: number;
  captured_at: number;
}

export interface PongMessage extends Envelope {
  type: 'pong';
}

export type ExtensionToOverlayMessage =
  | HelloMessage
  | TrackChangedMessage
  | PositionMessage
  | SeekMessage
  | AdStateMessage
  | PlaybackStateMessage
  | PongMessage;

// ---------------------------------------------------------------------------
// Overlay → extension
// ---------------------------------------------------------------------------

export interface HelloAckMessage extends Envelope {
  type: 'hello_ack';
  protocol_version: number;
  /** e.g. "kashi-overlay/0.1.0" */
  server: string;
  accepted: boolean;
}

export interface PingMessage extends Envelope {
  type: 'ping';
}

export type OverlayToExtensionMessage = HelloAckMessage | PingMessage;

export type AnyMessage = ExtensionToOverlayMessage | OverlayToExtensionMessage;
