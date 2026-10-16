/**
 * The player's own view of the playhead, for the ISOLATED script's
 * synchronous sample (see PLAYHEAD_REQUEST_EVENT).
 *
 * Kept free of side effects so it can be tested without the page: the bridge
 * only wires it to the request event.
 */
import type { PlayheadSample } from '../shared/messages.js';

/** The slice of `#movie_player`'s API the sample needs. */
export interface PlayerApi {
  getCurrentTime?: () => unknown;
  getDuration?: () => unknown;
  getVideoData?: () => { video_id?: string } | undefined;
  getPlayerResponse?: () => { videoDetails?: VideoDetails } | undefined;
}

/** The part of the player response's `videoDetails` the sample reads. */
interface VideoDetails {
  videoId?: string;
  lengthSeconds?: unknown;
}

function finiteOr(value: unknown, fallback: number | null): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

/**
 * The track's length as its metadata lists it, for THIS video only.
 *
 * Field (2026-10-06..16, ext 0.1.15): `getDuration()` announced exactly
 * 29917 ms for nine different songs — gapless preloads the next track in a
 * ~30 s first segment, and the player reports that segment's length until the
 * rest lands. Its number then failed the server's edit check (a 155 s song
 * "is a 29 s edit") and blocked the song for 7 days. The listed length is the
 * metadata's, not the buffer's; it is trusted only when the player response
 * names the same video, since mid-switch it can still describe the last one.
 */
function listedLengthS(player: PlayerApi, videoId: string | null): number | null {
  try {
    const details = player.getPlayerResponse?.()?.videoDetails;
    if (!details || videoId === null || details.videoId !== videoId) return null;
    const seconds = Number(details.lengthSeconds);
    return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
  } catch {
    // Losing the listed length must not cost the whole sample (and its offset).
    return null;
  }
}

/**
 * Track time, track duration and media time, all read in one instant.
 *
 * Null whenever the player cannot say where the TRACK is: without that number
 * the media time alone is exactly what the content script already has, and a
 * sample made of it would only pretend to know more.
 */
export function readPlayhead(
  player: PlayerApi | null,
  video: { currentTime: number } | null,
): PlayheadSample | null {
  if (!player || !video) return null;
  try {
    const trackTimeS = finiteOr(player.getCurrentTime?.(), null);
    if (trackTimeS === null) return null;
    const videoId = player.getVideoData?.()?.video_id ?? null;
    const listedS = listedLengthS(player, videoId);
    return {
      trackTimeS,
      trackDurationS: listedS ?? finiteOr(player.getDuration?.(), 0) ?? 0,
      durationFrom: listedS === null ? 'player' : 'details',
      mediaTimeS: video.currentTime,
      videoId,
    };
  } catch {
    // A player mid-rebuild can throw from any of these; no sample beats a guess.
    return null;
  }
}
