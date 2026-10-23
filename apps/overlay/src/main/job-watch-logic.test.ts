import { describe, expect, it } from 'vitest';
import {
  parseIngestOutcome,
  parseJobState,
  shouldWatch,
  watchStep,
  WATCH_DELAYS_MS,
} from './job-watch-logic.js';

describe('job watch (2026-10-24)', () => {
  it('a finished job means fetch now; a live one means wait', () => {
    expect(watchStep({ status: 'completed', errorType: null })).toBe('done');
    for (const status of ['queued', 'downloading', 'separating', 'aligning', 'postprocessing']) {
      expect(watchStep({ status, errorType: null })).toBe('wait');
    }
  });

  it('failed, canceled and unknown statuses end the watch', () => {
    expect(watchStep({ status: 'failed', errorType: 'lyrics_not_found' })).toBe('gone');
    expect(watchStep({ status: 'canceled', errorType: null })).toBe('gone');
    expect(watchStep({ status: 'mystery', errorType: null })).toBe('gone');
  });

  it('a job deferred for lrclib is not coming while anyone listens; a short retry is', () => {
    expect(watchStep({ status: 'queued', errorType: 'lrclib_unavailable' })).toBe('gone');
    expect(watchStep({ status: 'queued', errorType: 'cookie_expired' })).toBe('wait');
    // The deferral is a queued state only: a running job is past it.
    expect(watchStep({ status: 'aligning', errorType: 'lrclib_unavailable' })).toBe('wait');
  });

  it("the server's 7-day failure block is not watched", () => {
    expect(shouldWatch({ jobId: 'j', status: 'failed', reused: true })).toBe(false);
    expect(shouldWatch({ jobId: 'j', status: 'queued', reused: false })).toBe(true);
    expect(shouldWatch({ jobId: 'j', status: 'aligning', reused: true })).toBe(true);
    expect(shouldWatch({ jobId: 'j', status: 'completed', reused: true })).toBe(true);
  });

  it('the window is about ten minutes, and it ends', () => {
    const total = WATCH_DELAYS_MS.reduce((a, b) => a + b, 0);
    expect(total).toBeGreaterThanOrEqual(9 * 60_000);
    expect(total).toBeLessThanOrEqual(11 * 60_000);
  });

  it('parses the two server answers and refuses anything else', () => {
    expect(parseIngestOutcome({ job_id: 'j', status: 'queued', reused: true })).toEqual({
      jobId: 'j',
      status: 'queued',
      reused: true,
    });
    expect(parseIngestOutcome({ job_id: 'j', status: 'queued' })?.reused).toBe(false);
    expect(parseIngestOutcome({ status: 'queued' })).toBeNull();
    expect(parseIngestOutcome(null)).toBeNull();
    expect(parseJobState({ status: 'failed', error_type: 'x' })).toEqual({
      status: 'failed',
      errorType: 'x',
    });
    expect(parseJobState({ status: 'queued', error_type: null })?.errorType).toBeNull();
    expect(parseJobState({ detail: 'not_found' })).toBeNull();
  });
});
