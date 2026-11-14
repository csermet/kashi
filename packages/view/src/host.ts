/**
 * What a host gives the view, and what it gets back (client review E.4).
 *
 * The desktop host is the Electron preload's `window.kashi`, which carries
 * these members and its own desktop-only ones (click-through, drag, the menu,
 * the scroll gestures); an Android WebView host would implement the same eight
 * members over its own bridge. Payloads cross as `unknown` and the view
 * validates them — the bridge is a process boundary on every host.
 */
import type {
  AdStateMessage,
  PlaybackStateMessage,
  PositionMessage,
  SeekMessage,
} from '@kashi/protocol';
import type {
  AlignmentData,
  EnergyData,
  FxData,
  LyricLine,
} from './lyrics.js';
import type { BeatsLike, PaletteLike } from './effects-logic.js';
import type { SectionData } from './lyrics.js';

/** Removes the listener it was returned for. */
export type Unsubscribe = () => void;
export type Listener = (payload: unknown) => void;

export interface HostBridge {
  /** A track was announced ({@link TrackPayload}); the same key again is a re-announce. */
  onTrack: (cb: Listener) => Unsubscribe;
  /** Playback reports ({@link PlaybackPayload}): position, seek, play/pause, ad edges. */
  onPlayback: (cb: Listener) => Unsubscribe;
  /** Lyrics for a track key ({@link LyricsPayload}), or the state of their search. */
  onLyrics: (cb: Listener) => Unsubscribe;
  /** The player connection ({@link ConnectionPayload}); `false` returns the view to idle. */
  onConnection: (cb: Listener) => Unsubscribe;
  /** The player went away (tab closed): idle. */
  onSourceGone: (cb: Listener) => Unsubscribe;
  /** The full settings snapshot ({@link SettingsPayload}), every time any of them changes. */
  onSettings: (cb: Listener) => Unsubscribe;
  /** One diagnostic line (the desktop prints it to the overlay's terminal). */
  log: (line: string) => void;
  /** A clock jump the view classified (snap-classifier.ts), for telemetry. */
  reportAnomaly: (
    reason: string,
    deltaMs: number,
    positionMs: number,
    trackKey: string | null,
  ) => void;
}

export interface TrackPayload {
  /** `<source type>:<id>`, e.g. `youtube:dQw4w9WgXcQ`; lyrics name the same key. */
  key: string;
  track: { title: string; artist: string; artwork_url?: string };
}

export type PlaybackPayload = PositionMessage | SeekMessage | PlaybackStateMessage | AdStateMessage;

export interface LyricsPayload {
  key: string;
  found?: boolean;
  searching?: boolean;
  /** The lyrics source is unavailable and the host keeps asking in the background. */
  retrying?: boolean;
  /** No synced lyrics anywhere yet, but the server took the job. */
  syncing?: boolean;
  error?: boolean;
  lines?: LyricLine[];
  palette?: PaletteLike;
  beats?: BeatsLike;
  fx?: FxData;
  energy?: EnergyData;
  sections?: SectionData[];
  alignment?: AlignmentData;
}

export interface ConnectionPayload {
  connected: boolean;
}

/** Each field is optional and validated; garbage lands on the default. */
export interface SettingsPayload {
  box_alpha?: unknown;
  timing_offset_rel_ms?: unknown;
  effect_level?: unknown;
  theme_scope?: unknown;
  text_scale?: unknown;
  box_scale?: unknown;
}

export interface LyricsViewOptions {
  /**
   * The view just hid the box (ad, idle, watchdog). A host that made its
   * window take the pointer while it was over the box gives it back here: the
   * box can vanish under a motionless cursor, and no pointer event follows.
   */
  onBoxHidden?: () => void;
}

export interface LyricsView {
  /** The lyric box: where a host attaches its own input handling. */
  readonly box: HTMLElement;
  /**
   * Stops the view: host listeners removed, timers and the particle layer
   * stopped, its DOM taken out of the root. The document-level state it set
   * (body classes, :root variables) stays — the view owns its document.
   */
  destroy: () => void;
}
