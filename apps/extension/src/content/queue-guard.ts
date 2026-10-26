/**
 * Re-validation of the MAIN-world queue post (LyricFix round 3). Anything on
 * the page can postMessage a forged one, so the content script accepts only
 * the exact shape, bounded: at most two entries, YouTube video ids, short
 * non-empty strings and a plausible duration. A rejected post is dropped.
 */
import type { QueueEntry } from '../shared/messages.js';

const MAX_ENTRIES = 2;
const MAX_TEXT = 300;
const MAX_DURATION_MS = 3 * 3600_000;
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

const isText = (v: unknown): v is string =>
  typeof v === 'string' && v.trim().length > 0 && v.length <= MAX_TEXT;

export function parseQueueEntries(value: unknown): QueueEntry[] | null {
  if (!Array.isArray(value) || value.length > MAX_ENTRIES) return null;
  const entries: QueueEntry[] = [];
  for (const raw of value) {
    if (typeof raw !== 'object' || raw === null) return null;
    const e = raw as Record<string, unknown>;
    if (typeof e['videoId'] !== 'string' || !VIDEO_ID.test(e['videoId'])) return null;
    if (!isText(e['title']) || !isText(e['artist'])) return null;
    const duration = e['durationMs'];
    if (duration !== undefined) {
      if (typeof duration !== 'number' || !Number.isInteger(duration)) return null;
      if (duration <= 0 || duration > MAX_DURATION_MS) return null;
    }
    entries.push(
      duration === undefined
        ? { videoId: e['videoId'], title: e['title'], artist: e['artist'] }
        : { videoId: e['videoId'], title: e['title'], artist: e['artist'], durationMs: duration },
    );
  }
  return entries;
}
