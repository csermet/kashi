/**
 * MAIN-world bridge: reads what ISOLATED content scripts cannot —
 * `navigator.mediaSession.metadata` — and forwards snapshots via
 * window.postMessage (web-scrobbler's proven pattern).
 *
 * Signals that trigger a snapshot:
 *  - `videodatachange` on #movie_player (primary per-track signal; fires on
 *    radio/autoplay advance without a page navigation — ytm-scout 2026-07)
 *  - MutationObserver on the play/pause button and title wrapper
 *  - a slow safety-net interval (covers observer misses after YTM updates)
 */
import { MAIN_WORLD_MARKER, type MainWorldSnapshot } from '../shared/messages.js';

const SAFETY_NET_MS = 5000;

function playerVideoId(): string | null {
  const player = document.querySelector('#movie_player') as
    | { getVideoData?: () => { video_id?: string } }
    | null;
  try {
    return player?.getVideoData?.()?.video_id ?? null;
  } catch {
    return null;
  }
}

function readSnapshot(trackSignal: boolean): MainWorldSnapshot {
  const meta = navigator.mediaSession.metadata;
  const artwork = meta?.artwork?.[meta.artwork.length - 1]?.src ?? null;
  return {
    source: MAIN_WORLD_MARKER,
    kind: 'snapshot',
    title: meta?.title ?? null,
    artist: meta?.artist ?? null,
    album: meta?.album ?? null,
    artworkUrl: artwork,
    playbackState: navigator.mediaSession.playbackState,
    videoId: playerVideoId(),
    trackSignal,
  };
}

let lastSerialized = '';

function post(trackSignal: boolean): void {
  const snapshot = readSnapshot(trackSignal);
  const serialized = JSON.stringify(snapshot);
  if (!trackSignal && serialized === lastSerialized) return; // dedup noise
  lastSerialized = serialized;
  window.postMessage(snapshot, window.location.origin);
}

function observePlayerBar(): void {
  const observer = new MutationObserver(() => post(false));
  const attach = () => {
    const playPause = document.querySelector('#play-pause-button');
    const info = document.querySelector('.content-info-wrapper');
    let attached = false;
    for (const el of [playPause, info]) {
      if (el) {
        observer.observe(el, { subtree: true, attributes: true, childList: true });
        attached = true;
      }
    }
    return attached;
  };
  if (!attach()) {
    // Player bar renders late on cold loads — retry until present.
    const retry = setInterval(() => {
      if (attach()) clearInterval(retry);
    }, 1000);
  }
}

let attachedPlayer: Element | null = null;

function observePlayer(): void {
  const attach = () => {
    const player = document.querySelector('#movie_player');
    if (!player || player === attachedPlayer) return;
    attachedPlayer = player;
    player.addEventListener('videodatachange', () => post(true));
    console.debug(
      `[kashi-mw] player attached, getVideoData -> ${playerVideoId() ?? 'null'}`,
    );
  };
  attach();
  // YTM can replace the player element on SPA transitions — a listener on a
  // detached node dies silently, so keep verifying and re-attach when needed.
  setInterval(() => {
    if (!attachedPlayer?.isConnected) attach();
  }, 5000);
}

observePlayerBar();
observePlayer();
document.addEventListener('yt-navigate-finish', () => post(true));
setInterval(() => post(false), SAFETY_NET_MS);
post(false);
