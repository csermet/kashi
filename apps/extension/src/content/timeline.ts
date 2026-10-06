/**
 * Track time vs media time (field bug, Caner 2026-10-06).
 *
 * Under gapless playback (YouTube Music Premium) YTM appends each auto-advanced
 * track to the SAME media timeline. `video.currentTime` and `video.duration`
 * never return to zero: the fourth song of a gapless run starts where the
 * first three ended. Telemetry showed it to the millisecond — announced
 * "durations" grew 239237 → 430197 → 630142 ms, each step the previous track's
 * real length (190981 ms, 199961 ms), and the overlay's clock stood at 928 s
 * when a manual pick finally reset it.
 *
 * Every position the extension sent from there on was the queue's playhead,
 * not the track's. The overlay anchored on it, landed past the last line, and
 * showed only the interlude mark (♪) for every song that followed — "2-3 songs
 * work, then nothing; changing songs doesn't help; a page refresh does" (a
 * refresh builds a fresh timeline). The guards in `position-guard.ts` could
 * only withhold such reports, and they are budgeted so they never starve the
 * clock — the timeline itself was never going to come back.
 *
 * Only the player knows where the track starts, so the MAIN-world bridge reads
 * `#movie_player.getCurrentTime()` next to `video.currentTime` in one instant;
 * their difference is the offset to subtract. Without a sample (bridge not up,
 * player API changed) the offset is 0 and behavior is exactly what it was.
 */
import type { PlayheadSample } from '../shared/messages.js';

/**
 * Below this, a difference between media time and track time is read as
 * "one timeline per track" (no offset). A gapless offset is the length of
 * every track before this one, so it is never this small, and a timeline that
 * starts at zero stays byte-for-byte what it was.
 */
export const TIMELINE_OFFSET_MIN_MS = 1000;

/**
 * Both worlds read `currentTime` inside one synchronous dispatch, so on the
 * same element the two readings are equal. Anything further apart means the
 * bridge read ANOTHER element (the content script re-checks its own only every
 * 3 s), and one element's offset must not be applied to another's time.
 */
export const SAME_INSTANT_TOLERANCE_MS = 50;

/**
 * How much longer than the player's duration the media element's may be
 * before it is read as the whole run rather than this track. Same tolerance as
 * the position overshoot guard: duration rounding, nothing more.
 */
export const MEDIA_DURATION_EXCESS_MS = 2000;

function finiteNonNegative(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

/** Validate the bridge's answer: page data, so nothing is taken on faith. */
export function parsePlayheadSample(detail: unknown): PlayheadSample | null {
  if (typeof detail !== 'string') return null;
  let raw: unknown;
  try {
    raw = JSON.parse(detail);
  } catch {
    return null;
  }
  if (typeof raw !== 'object' || raw === null) return null;
  const s = raw as Record<string, unknown>;
  if (!finiteNonNegative(s.trackTimeS) || !finiteNonNegative(s.mediaTimeS)) return null;
  return {
    trackTimeS: s.trackTimeS,
    trackDurationS: finiteNonNegative(s.trackDurationS) ? s.trackDurationS : 0,
    mediaTimeS: s.mediaTimeS,
    videoId: typeof s.videoId === 'string' && s.videoId !== '' ? s.videoId : null,
  };
}

/**
 * The sample, if it describes the media time the content script is about to
 * use; null otherwise (no reply, no element, or a reply about another element).
 */
export function sampleFor(
  sample: PlayheadSample | null,
  mediaTimeS: number | undefined,
): PlayheadSample | null {
  if (!sample || mediaTimeS === undefined) return null;
  const apartMs = Math.abs(Math.round(sample.mediaTimeS * 1000) - Math.round(mediaTimeS * 1000));
  return apartMs <= SAME_INSTANT_TOLERANCE_MS ? sample : null;
}

/**
 * Where the current track starts on the media timeline, in ms (0 = it starts
 * at zero, the normal case). A NEGATIVE difference — the player claiming to be
 * further into the track than the media is into the whole timeline — is not a
 * shape gapless can produce, so it is not applied.
 */
export function timelineOffsetMs(sample: PlayheadSample | null): number {
  if (!sample) return 0;
  const offset = Math.round((sample.mediaTimeS - sample.trackTimeS) * 1000);
  return offset >= TIMELINE_OFFSET_MIN_MS ? offset : 0;
}

/**
 * Whether a new offset is worth a log line. Only a step of a whole threshold
 * counts: if the player's clock is not derived from the very same read, the
 * rounded difference jitters by milliseconds, and each line costs a message to
 * the overlay terminal at timeupdate rate.
 */
export function offsetWorthLogging(loggedMs: number, offsetMs: number): boolean {
  return Math.abs(offsetMs - loggedMs) >= TIMELINE_OFFSET_MIN_MS;
}

/** The media element's time, as a position inside the current track. */
export function trackPositionMs(mediaTimeS: number, offsetMs: number): number {
  return Math.max(0, Math.round(mediaTimeS * 1000) - offsetMs);
}

/**
 * The track's duration when the media element cannot give it: on an offset
 * timeline `video.duration` is the end of the whole run (the 630142 ms
 * "Instruction"), so only the player's number describes the track. Trusted
 * only for the video the player names — a sample taken mid-switch still
 * describes the previous track, and announcing ITS length is the 2026-08-13
 * Hey Mama bug.
 */
export function playerDurationMs(
  sample: PlayheadSample | null,
  videoId: string | null,
): number | undefined {
  if (!sample || sample.trackDurationS <= 0) return undefined;
  if (videoId === null || sample.videoId !== videoId) return undefined;
  return Math.round(sample.trackDurationS * 1000);
}

/**
 * The duration to announce, given the media element's own (already screened
 * for freshness, or undefined).
 *
 * On an offset timeline only the player's number describes the track. On the
 * FIRST track of a gapless run there is no offset yet, but once YTM appends
 * the next track the element's duration is both tracks together — and it
 * arrives through a genuine `durationchange`, so freshness cannot see it. The
 * player's number wins there too, but only when the element's is clearly
 * longer: without that shape the element's number stays in charge, exactly as
 * before.
 */
export function trackDurationMs(
  mediaMs: number | undefined,
  sample: PlayheadSample | null,
  videoId: string | null,
): number | undefined {
  const playerMs = playerDurationMs(sample, videoId);
  if (timelineOffsetMs(sample) > 0) return playerMs;
  const wholeRun =
    mediaMs !== undefined &&
    playerMs !== undefined &&
    mediaMs > playerMs + MEDIA_DURATION_EXCESS_MS;
  return wholeRun ? playerMs : mediaMs;
}
