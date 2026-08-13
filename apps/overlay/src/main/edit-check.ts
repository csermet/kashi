/**
 * Is this lyric sheet written for THIS edit of the song?
 *
 * Field bug (Caner, 2026-08-13): "şarkı sözleri inanılmaz kaymış, sanki başka
 * şarkı ile karışıyor". It is not another song — it is another EDIT: nightcore,
 * sped-up, extended, radio vs album, a live take. lrclib stores one record per
 * edit and they share a title, so nothing but the DURATION separates them.
 *
 * That filter exists on the way in (`/api/get?duration=`, and the search picks
 * the closest candidate within 3 s) — but the orchestrator drops it on the
 * duration-less retry, which is precisely the path a stale auto-advance
 * duration sends us down. The field case: YTM announced Hey Mama with the
 * PREVIOUS video's duration (366 s instead of 193 s), the scoped lookup missed
 * because no Hey Mama record is six minutes long, the retry ran unscoped, and
 * some other edit's stamps went straight to the screen unchecked.
 *
 * So the check belongs on the way OUT, where it sees what actually arrived.
 * Two independent signals, because each covers the other's blind spot:
 *
 *  1. the record's own duration vs the track's — direct, but lrclib records
 *     may carry no duration at all;
 *  2. stamps that run PAST the end of the track — needs no record duration,
 *     and a lyric that is still singing after the audio stopped is proof on
 *     its own.
 *
 * The converse is deliberately NOT evidence: lyrics that end early are normal
 * (instrumental outros, fade-outs), so a short sheet says nothing.
 */
import type { LyricLine } from './lrclib.js';

/**
 * Same 5 s the server's different-edit probe uses (lrclib.py). One number for
 * one question, so a tolerance tuned in one place cannot silently disagree
 * with the other. Comfortably wider than the ±3 s lrclib's own search allows,
 * so this check never second-guesses a match lrclib itself made.
 */
export const EDIT_DURATION_TOLERANCE_MS = 5_000;

export type EditVerdict =
  /** Durations agree (or the stamps fit) — these stamps belong to this audio. */
  | 'match'
  /** No duration on the record and nothing overruns: no evidence either way. */
  | 'unverifiable'
  /** Positive evidence that the sheet was written against a different edit. */
  | 'different-edit';

export function classifyEdit(
  trackDurationMs: number | undefined,
  recordDurationMs: number | null,
  lines: readonly LyricLine[],
  toleranceMs: number = EDIT_DURATION_TOLERANCE_MS,
): EditVerdict {
  // Nothing to compare against. This is also the honest verdict when the
  // TRACK's duration is the thing that is wrong — we cannot tell which side
  // lied, so we do not get to convict either.
  if (!trackDurationMs || trackDurationMs <= 0) return 'unverifiable';

  if (recordDurationMs != null && recordDurationMs > 0) {
    if (Math.abs(recordDurationMs - trackDurationMs) > toleranceMs) return 'different-edit';
    return 'match';
  }

  // No record duration: fall back to the stamps themselves. The LAST line's
  // start (not its end — ends are synthesised by parseLrc, and the last one is
  // already clamped to the track) is the load-bearing number.
  const last = lines[lines.length - 1];
  if (last && last.start_ms > trackDurationMs + toleranceMs) return 'different-edit';
  return 'unverifiable';
}
