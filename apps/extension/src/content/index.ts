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
import {
  durationIsAuthoritative,
  PositionClamp,
  shouldDeferAnnouncePosition,
} from './position-guard.js';

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

/** Diagnostic: local console + relayed to the overlay terminal via the SW. */
function log(line: string): void {
  console.debug(`[kashi] ${line}`);
  sendEvent({ kind: 'log', line, sent_at: Date.now() });
}

function currentVideoId(): string | null {
  // Player API FIRST: on queue auto-advance YTM does not navigate, so the URL
  // ?v= stays stuck on the first song of the list. getVideoData tracks the
  // actually-playing video; the URL is only a fallback (cold load, before the
  // MAIN-world bridge posts). Never from mediaSession metadata (R-9).
  return (
    latestSnapshot?.videoId ??
    new URL(window.location.href).searchParams.get('v')
  );
}

function isAdPlaying(): boolean {
  return document.querySelector(AD_SELECTOR) !== null;
}

const clamp = new PositionClamp();

function positionEvent(
  kind: 'position' | 'seek' | 'playback_state',
): ContentEvent | null {
  if (!video) return null;
  const positionMs = Math.round(video.currentTime * 1000);

  // Guard 1: a position past the end of THIS track belongs to another
  // timeline. Only armed once durationchange fired for the current track —
  // clamping against the previous track's duration would suppress the
  // legitimate positions of a longer one.
  const authoritativeDuration = durationIsAuthoritative(
    lastDurationChangeAt,
    videoIdChangedAt,
  )
    ? freshDurationMs()
    : undefined;
  const verdict = clamp.decide(positionMs, authoritativeDuration, Date.now());
  if (verdict.note) log(verdict.note);
  if (!verdict.send) return null;

  const now = Date.now();
  return {
    kind,
    position_ms: positionMs,
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
    log(`ad_state -> ${isAd}`);
    sendEvent({ kind: 'ad_state', is_ad: isAd, sent_at: Date.now() });
  }
}

let pendingVideoId: string | null = null;
let lastAnnouncedTitle: string | null = null;
let staleTitleRetries = 0;
/**
 * The id `videoIdChangedAt` was stamped for. Kept apart from `pendingVideoId`
 * because a `reannounce` clears the pending id: re-stamping the SAME track
 * would tell the guards a track change just happened, and since no further
 * `durationchange` is coming for a track already playing, they would stay
 * disarmed for the rest of it.
 */
let stampedVideoId: string | null = null;
/** Set when guard 2 withheld the announce position; flushed on durationchange. */
let announcePositionPending = false;

function maybeAnnounceTrack(): void {
  const videoId = currentVideoId();
  if (!videoId || videoId === announcedVideoId) return;
  if (videoId !== pendingVideoId) {
    pendingVideoId = videoId;
    staleTitleRetries = 0;
  }
  if (videoId !== stampedVideoId) {
    stampedVideoId = videoId;
    videoIdChangedAt = Date.now(); // stamp the actual id change, not each call
    clamp.reset(); // budget is per track
    announcePositionPending = false; // never flush a previous track's report
  }

  // Debounce: metadata settles milliseconds after the track signal, and radio
  // skip-chains must not spam (R-9). Built from the LATEST snapshot at fire.
  clearTimeout(trackTimer);
  trackTimer = window.setTimeout(() => {
    const id = currentVideoId();
    if (!id || id === announcedVideoId) return;
    const meta = latestSnapshot;
    if (!meta?.title || adActive) return; // wait for metadata / skip ads
    // New id but the title still equals the previous track's → mediaSession
    // hasn't caught up yet; announcing now would look up the WRONG lyrics.
    if (meta.title === lastAnnouncedTitle && staleTitleRetries < 4) {
      staleTitleRetries++;
      log(`announce deferred (stale title "${meta.title}", retry ${staleTitleRetries})`);
      trackTimer = window.setTimeout(() => maybeAnnounceTrack(), TRACK_DEBOUNCE_MS);
      return;
    }
    // Captured BEFORE the assignment below: a null announcedVideoId means cold
    // start or refresh, which guard 2 must leave untouched.
    const wasMidSession = announcedVideoId !== null;
    announcedVideoId = id;
    lastAnnouncedTitle = meta.title;
    log(
      `announce ${id} "${meta.title}" (id via ${latestSnapshot?.videoId ? 'player-api' : 'url'},` +
        ` duration=${freshDurationMs() ?? 'n/a'}, currentTime=${video?.currentTime.toFixed(2) ?? 'n/a'}s)`,
    );
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
    // Guard 2: this report becomes the overlay's clock anchor, so a stale
    // currentTime here misplaces the whole song. The next timeupdate
    // (~250 ms) anchors instead — imperceptible, lyrics are still "searching".
    if (shouldDeferAnnouncePosition(wasMidSession, freshDurationMs())) {
      announcePositionPending = true;
      log('announce position deferred (duration for this track has not landed)');
      return;
    }
    const pos = positionEvent('position');
    if (pos) sendEvent(pos);
  }, TRACK_DEBOUNCE_MS);
}

/**
 * Sends the position guard 2 withheld at the announce, once this track's
 * duration lands. Under a track changed while PAUSED this is the only chance:
 * `timeupdate` only fires while playback advances.
 */
function flushDeferredPosition(): void {
  if (!announcePositionPending) return;
  announcePositionPending = false;
  const evt = positionEvent('position');
  if (evt) sendEvent(evt);
}

function attachVideo(): boolean {
  const el = document.querySelector('video');
  if (!el || el === video) return el !== null;
  video = el;
  log('video element attached');
  // The element can be REPLACED on auto-advance. If the new one already has
  // its metadata, `durationchange` fired before we could listen and would
  // never fire again for this track — leaving the clamp disarmed exactly in
  // the auto-advance case it exists for. Treat "attached with metadata" as
  // the durationchange we missed.
  if (el.readyState >= HTMLMediaElement.HAVE_METADATA && Number.isFinite(el.duration)) {
    lastDurationChangeAt = Date.now();
    flushDeferredPosition();
  }

  video.addEventListener('durationchange', () => {
    lastDurationChangeAt = Date.now();
    flushDeferredPosition();
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

function init(): void {
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const data = event.data as MainWorldSnapshot;
    if (data?.source !== MAIN_WORLD_MARKER || data.kind !== 'snapshot') return;
    latestSnapshot = data;
    if (data.trackSignal) attachVideo(); // element may have been swapped
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
    log('reannounce requested by SW');
    announcedVideoId = null;
    pendingVideoId = null;
    lastAnnouncedTitle = null; // avoid the stale-title delay on same-track reannounce
    attachVideo();
    refreshAdState();
    maybeAnnounceTrack();
    const evt = positionEvent('playback_state');
    if (evt) sendEvent(evt);
  });

  // The <video> element renders late on cold loads AND can be REPLACED on
  // queue auto-advance — a listener on the old element dies silently and the
  // position stream stops. Keep verifying forever (cheap identity check).
  attachVideo();
  setInterval(() => {
    attachVideo();
    refreshAdState(); // a lost ad_state=false must not blank the overlay forever
  }, 3000);

  console.debug(`[kashi] content script ready v${chrome.runtime.getManifest().version}`);
}

// Chrome PRERENDERS list/next pages: our script would run in those phantom
// documents and announce tracks that are not actually playing, fighting the
// real tab for the overlay. Do nothing until the document becomes active.
if ((document as Document & { prerendering?: boolean }).prerendering) {
  document.addEventListener('prerenderingchange', () => init(), { once: true });
} else {
  init();
}
