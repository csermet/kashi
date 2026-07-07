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

/**
 * Current videoId, best source first:
 * 1. the id delivered IN the `videodatachange` event payload (authoritative —
 *    a separate getVideoData() call can return the PREVIOUS video mid-switch)
 * 2. getVideoData() as cold-load fallback.
 */
let eventVideoId: string | null = null;

function playerVideoId(): string | null {
  if (eventVideoId) return eventVideoId;
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
let dataUpdatedFallback: number | undefined;

/**
 * `videodatachange` is the player's own callback API with (name, videoData)
 * args, two-phased: 'dataloaded' fires first (data may be incomplete) and
 * 'dataupdated' second (authoritative) — but 'dataupdated' can be silently
 * dropped on shuffle/auto-advance (bug shipped in pear-desktop too, fixed in
 * their v3.11.0), hence the 1.5 s fallback re-post.
 */
function onVideoDataChange(name?: string, videoData?: { video_id?: string }): void {
  if (videoData?.video_id) eventVideoId = videoData.video_id;
  clearTimeout(dataUpdatedFallback);
  if (name === 'dataloaded') {
    dataUpdatedFallback = window.setTimeout(() => post(true), 1500);
  }
  post(true);
}

function observePlayer(): void {
  const attach = () => {
    const player = document.querySelector('#movie_player');
    if (!player || player === attachedPlayer) return;
    attachedPlayer = player;
    (player.addEventListener as (type: string, cb: unknown) => void)(
      'videodatachange',
      onVideoDataChange,
    );
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
