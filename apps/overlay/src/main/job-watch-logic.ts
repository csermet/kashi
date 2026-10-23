/**
 * Watching the server process the track the user is listening to (2026-10-24).
 *
 * The ladder asks the server once per track start. A genuine 404 arms the
 * enqueue gate and the track is queued 20 s later — but nothing asked again,
 * so a document finished three minutes later only appeared the NEXT time the
 * song played, and the screen said "No synced lyrics found" all along. Field:
 * Lunity "Star Walkin'", whose lrclib record carries plain text only — exactly
 * the case the server exists for.
 *
 * The ingest answer names the job. The orchestrator polls it on this schedule
 * and fetches the document the moment it is done. Pure here; the timers live
 * in the orchestrator, where the track's AbortController owns cancellation.
 */

/** POST /v1/ingest's 202 body: the job that represents this source. */
export interface IngestOutcome {
  jobId: string;
  status: string;
  /** An existing job answered (live, done, or the server's 7-day failure block). */
  reused: boolean;
}

/** GET /v1/jobs/{id}, reduced to what the watch reads. */
export interface JobState {
  status: string;
  errorType: string | null;
}

/** The server's live statuses (db/models.py LIVE_STATUSES). */
export const LIVE_JOB_STATUSES: readonly string[] = [
  'queued',
  'downloading',
  'separating',
  'aligning',
  'postprocessing',
];

/**
 * Thirty minutes. The worker takes ~12 min per song on CPU (2026-10-23), so a
 * track two places back in the queue needs most of that; a 3-minute song has
 * long ended by then, and the watch ends with it. The window only matters
 * while the same track stays current — paused, or on repeat.
 */
export const WATCH_DELAYS_MS: readonly number[] = [
  15_000,
  15_000,
  20_000,
  30_000,
  30_000,
  ...Array<number>(29).fill(60_000),
];

/** A job that will not finish while anyone waits: LyricFix defers it without
 * downloading while lrclib is unreachable, for up to a day. It stays queued
 * with this error recorded; a short transient retry (60 s) does not. */
const DEFERRED_ERROR = 'lrclib_unavailable';

export type WatchStep = 'done' | 'gone' | 'wait';

/** done: fetch the document now; gone: no document is coming while the user
 * listens; wait: ask again later. */
export function watchStep(job: JobState): WatchStep {
  if (job.status === 'completed') return 'done';
  if (!LIVE_JOB_STATUSES.includes(job.status)) return 'gone'; // failed, canceled, unknown
  if (job.status === 'queued' && job.errorType === DEFERRED_ERROR) return 'gone';
  return 'wait';
}

/** Whether an ingest answer is worth watching. A reused failed job is the
 * server's 7-day block speaking, not a fresh attempt: nothing is coming. */
export function shouldWatch(outcome: IngestOutcome): boolean {
  return watchStep({ status: outcome.status, errorType: null }) !== 'gone';
}

export function parseIngestOutcome(body: unknown): IngestOutcome | null {
  if (typeof body !== 'object' || body === null) return null;
  const { job_id: jobId, status, reused } = body as Record<string, unknown>;
  if (typeof jobId !== 'string' || typeof status !== 'string') return null;
  return { jobId, status, reused: reused === true };
}

export function parseJobState(body: unknown): JobState | null {
  if (typeof body !== 'object' || body === null) return null;
  const { status, error_type: errorType } = body as Record<string, unknown>;
  if (typeof status !== 'string') return null;
  return { status, errorType: typeof errorType === 'string' ? errorType : null };
}
