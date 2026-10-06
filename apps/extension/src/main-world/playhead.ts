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
}

function finiteOr(value: unknown, fallback: number | null): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
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
    return {
      trackTimeS,
      trackDurationS: finiteOr(player.getDuration?.(), 0) ?? 0,
      mediaTimeS: video.currentTime,
      videoId: player.getVideoData?.()?.video_id ?? null,
    };
  } catch {
    // A player mid-rebuild can throw from any of these; no sample beats a guess.
    return null;
  }
}
