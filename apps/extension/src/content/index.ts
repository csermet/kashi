/**
 * ISOLATED-world content script: owns the event pipeline to the service
 * worker. Position comes from the shared DOM <video> element via `timeupdate`
 * (media events keep firing in throttled background tabs — never poll).
 * videoId ALWAYS comes from the URL, never from mediaSession metadata (R-9).
 * All timestamps (`captured_at`/`sent_at`) are stamped HERE at capture time so
 * the overlay's clock can subtract transport latency (R-1).
 */
import {
  MAIN_WORLD_MARKER,
  type ContentEvent,
  type MainWorldSnapshot,
} from '../shared/messages.js';

const TRACK_DEBOUNCE_MS = 500;
const AD_SELECTOR = '.ytmusic-player-bar.advertisement';

let latestSnapshot: MainWorldSnapshot | null = null;
let announcedVideoId: string | null = null;
let trackTimer: number | undefined;
let adActive = false;
let video: HTMLVideoElement | null = null;
let lastDurationChangeAt = 0;
let videoIdChangedAt = 0;

/**
 * YTM streams via MSE: on track switches the <video> keeps its old duration
 * until the new source's metadata lands. A stale duration poisons the LRCLIB
 * duration-tolerance match, so only trust it when `durationchange` fired
 * after (or shortly before) the current videoId appeared.
 */
function freshDurationMs(): number | undefined {
  const durationS = video?.duration;
  if (!durationS || !Number.isFinite(durationS)) return undefined;
  const fresh =
    lastDurationChangeAt >= videoIdChangedAt ||
    videoIdChangedAt - lastDurationChangeAt < 8000;
  return fresh ? Math.round(durationS * 1000) : undefined;
}

function sendEvent(event: ContentEvent): void {
  // SW may be asleep; sendMessage wakes it. Errors (e.g. during extension
  // reload) are non-fatal — the next event retries naturally.
  void chrome.runtime.sendMessage(event).catch(() => {});
}

function currentVideoId(): string | null {
  // URL first; player-API fallback covers YTM restoring a paused session on a
  // URL without ?v= (home page). Never from mediaSession metadata (R-9).
  return (
    new URL(window.location.href).searchParams.get('v') ??
    latestSnapshot?.videoId ??
    null
  );
}

function isAdPlaying(): boolean {
  return document.querySelector(AD_SELECTOR) !== null;
}

function positionEvent(
  kind: 'position' | 'seek' | 'playback_state',
): ContentEvent | null {
  if (!video) return null;
  const now = Date.now();
  return {
    kind,
    position_ms: Math.round(video.currentTime * 1000),
    playback_rate: video.playbackRate,
    is_playing: !video.paused,
    captured_at: now,
    sent_at: now,
  };
}

function refreshAdState(): void {
  const isAd = isAdPlaying();
  if (isAd !== adActive) {
    adActive = isAd;
    sendEvent({ kind: 'ad_state', is_ad: isAd, sent_at: Date.now() });
  }
}

let pendingVideoId: string | null = null;

function maybeAnnounceTrack(): void {
  const videoId = currentVideoId();
  if (!videoId || videoId === announcedVideoId) return;
  if (videoId !== pendingVideoId) {
    pendingVideoId = videoId;
    videoIdChangedAt = Date.now(); // stamp the actual id change, not each call
  }

  // Debounce: metadata settles milliseconds after the track signal, and radio
  // skip-chains must not spam (R-9). Built from the LATEST snapshot at fire.
  clearTimeout(trackTimer);
  trackTimer = window.setTimeout(() => {
    const id = currentVideoId();
    if (!id || id === announcedVideoId) return;
    const meta = latestSnapshot;
    if (!meta?.title || adActive) return; // wait for metadata / skip ads
    announcedVideoId = id;
    sendEvent({
      kind: 'track_changed',
      videoId: id,
      title: meta.title,
      artist: meta.artist ?? '',
      album: meta.album ?? undefined,
      // undefined beats a stale value: the overlay's search fallback still
      // finds lyrics without a duration, but a WRONG duration rejects all.
      duration_ms: freshDurationMs(),
      artwork_url: meta.artworkUrl ?? undefined,
      sent_at: Date.now(),
    });
    const pos = positionEvent('position');
    if (pos) sendEvent(pos);
  }, TRACK_DEBOUNCE_MS);
}

function attachVideo(): boolean {
  const el = document.querySelector('video');
  if (!el || el === video) return el !== null;
  video = el;

  video.addEventListener('durationchange', () => {
    lastDurationChangeAt = Date.now();
  });
  video.addEventListener('timeupdate', () => {
    refreshAdState();
    if (adActive) return; // position stream pauses during ads
    const evt = positionEvent('position');
    if (evt) sendEvent(evt);
  });
  video.addEventListener('seeked', () => {
    if (adActive) return;
    const evt = positionEvent('seek');
    if (evt) sendEvent(evt);
  });
  for (const type of ['play', 'pause'] as const) {
    video.addEventListener(type, () => {
      refreshAdState();
      if (adActive) return;
      const evt = positionEvent('playback_state');
      if (evt) sendEvent(evt);
    });
  }
  return true;
}

// --- wiring ---------------------------------------------------------------

window.addEventListener('message', (event) => {
  if (event.source !== window) return;
  const data = event.data as MainWorldSnapshot;
  if (data?.source !== MAIN_WORLD_MARKER || data.kind !== 'snapshot') return;
  latestSnapshot = data;
  if (data.trackSignal || data.title) maybeAnnounceTrack();
});

document.addEventListener('yt-navigate-finish', () => {
  attachVideo();
  refreshAdState();
  maybeAnnounceTrack();
});

// SW asks for a fresh announce after (re)connecting to the overlay — live
// state must beat whatever stale snapshot the SW replayed.
chrome.runtime.onMessage.addListener((message: unknown) => {
  if ((message as { kind?: string })?.kind !== 'reannounce') return;
  announcedVideoId = null;
  pendingVideoId = null;
  attachVideo();
  refreshAdState();
  maybeAnnounceTrack();
  const evt = positionEvent('playback_state');
  if (evt) sendEvent(evt);
});

// The <video> element renders late on cold loads; retry until attached.
if (!attachVideo()) {
  const retry = setInterval(() => {
    if (attachVideo()) clearInterval(retry);
  }, 1000);
}

console.debug('[kashi] content script ready');
