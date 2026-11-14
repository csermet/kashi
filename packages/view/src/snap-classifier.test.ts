import { describe, expect, it } from 'vitest';

import {
  MAX_PENDING_BUCKETS,
  SEEK_GRACE_MS,
  SILENCE_WINDOW_MS,
  SnapClassifier,
} from './snap-classifier.js';

/** Feeds jumps at the extension's real cadence (~4 Hz). */
function drag(
  classifier: SnapClassifier,
  startMono: number,
  seconds: number,
): number {
  const step = 250;
  let at = startMono;
  for (let elapsed = 0; elapsed < seconds * 1000; elapsed += step) {
    at = startMono + elapsed;
    classifier.onJump(4000, 60_000 + elapsed, at);
  }
  return at;
}

describe('SnapClassifier', () => {
  it('collapses a six-second drag into one user_seek and zero anomalies', () => {
    // The exact field scenario: jumps at 4 Hz throughout the drag, then the
    // single `seeked` that explains all of them, arriving last.
    const classifier = new SnapClassifier();
    const lastJumpAt = drag(classifier, 1000, 6);
    classifier.onSeek(lastJumpAt + 50);

    // Nothing is reported while the drag is still in progress.
    expect(classifier.flush(lastJumpAt + 100)).toEqual([]);

    const events = classifier.flush(lastJumpAt + SILENCE_WINDOW_MS);
    expect(events).toHaveLength(1);
    expect(events[0]?.reason).toBe('user_seek');
    expect(events[0]?.jumpCount).toBe(24);
    expect(events.filter((e) => e.reason === 'unexplained_snap')).toEqual([]);
  });

  it('still reports a jump nobody explained', () => {
    const classifier = new SnapClassifier();
    classifier.onJump(9000, 30_000, 1000);

    const events = classifier.flush(1000 + SILENCE_WINDOW_MS);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      reason: 'unexplained_snap',
      deltaMs: 9000,
      positionMs: 30_000,
      jumpCount: 1,
    });
  });

  it('explains jumps that arrive just after a click-seek', () => {
    // Clicking the bar snaps currentTime first; the position reports trail it.
    const classifier = new SnapClassifier();
    classifier.onSeek(1000);
    classifier.onJump(5000, 80_000, 1000 + SEEK_GRACE_MS - 1);

    const events = classifier.flush(1000 + SEEK_GRACE_MS + SILENCE_WINDOW_MS);
    expect(events).toHaveLength(1);
    expect(events[0]?.reason).toBe('user_seek');
  });

  it('does not let a stale seek explain a much later jump', () => {
    const classifier = new SnapClassifier();
    classifier.onSeek(1000);
    classifier.onJump(5000, 80_000, 1000 + SEEK_GRACE_MS + 1);

    const events = classifier.flush(1000 + SEEK_GRACE_MS + SILENCE_WINDOW_MS + 100);
    expect(events).toHaveLength(1);
    expect(events[0]?.reason).toBe('unexplained_snap');
  });

  it('separates two incidents that are far apart', () => {
    const classifier = new SnapClassifier();
    classifier.onJump(3000, 10_000, 1000);
    // Far enough that the first bucket has gone quiet.
    classifier.onJump(4000, 90_000, 1000 + SILENCE_WINDOW_MS + 500);

    const events = classifier.flush(1000 + 2 * SILENCE_WINDOW_MS + 600);
    expect(events).toHaveLength(2);
    expect(events.map((e) => e.positionMs)).toEqual([10_000, 90_000]);
  });

  it('reports the last jump of a bucket, not the first', () => {
    const classifier = new SnapClassifier();
    classifier.onJump(2000, 10_000, 1000);
    classifier.onJump(3000, 40_000, 1200);
    classifier.onJump(4000, 75_000, 1400);

    const events = classifier.flush(1400 + SILENCE_WINDOW_MS);
    expect(events[0]).toMatchObject({ deltaMs: 4000, positionMs: 75_000, jumpCount: 3 });
  });

  it('always drains eventually — no input sequence can suppress a bucket', () => {
    // The budget guard must not be able to sit on an event forever.
    const classifier = new SnapClassifier();
    classifier.onJump(5000, 20_000, 1000);
    expect(classifier.hasOpenBucket()).toBe(true);

    const events = classifier.flush(1000 + SILENCE_WINDOW_MS);
    expect(events).toHaveLength(1);
    expect(classifier.hasOpenBucket()).toBe(false);
  });

  it('drops the oldest bucket when the pending queue overflows, and counts it', () => {
    const classifier = new SnapClassifier();
    const gap = SILENCE_WINDOW_MS + 100;
    // One more bucket than the cap allows, with no flush in between.
    for (let i = 0; i <= MAX_PENDING_BUCKETS; i += 1) {
      classifier.onJump(2000, i * 1000, 1000 + i * gap);
    }
    const events = classifier.flush(1000 + (MAX_PENDING_BUCKETS + 1) * gap);

    expect(events).toHaveLength(MAX_PENDING_BUCKETS);
    expect(classifier.droppedBucketCount()).toBe(1);
    // The oldest one is the one that went.
    expect(events[0]?.positionMs).toBe(1000);
  });

  it('ignores non-finite inputs instead of poisoning a bucket', () => {
    const classifier = new SnapClassifier();
    classifier.onJump(Number.NaN, 10_000, 1000);
    classifier.onJump(5000, Number.POSITIVE_INFINITY, 1000);

    expect(classifier.hasOpenBucket()).toBe(false);
    expect(classifier.flush(1000 + SILENCE_WINDOW_MS)).toEqual([]);
  });

  it('reports nothing at all when the stream is quiet', () => {
    const classifier = new SnapClassifier();
    expect(classifier.flush(50_000)).toEqual([]);
    expect(classifier.droppedBucketCount()).toBe(0);
  });

  it('never lets two songs share a bucket', () => {
    // Reporting is deferred, so a jump near the end of one track can still be
    // waiting when the next begins. Averaging them would leave a position
    // belonging to a track nobody can name — and downstream that position is
    // compared against a duration.
    const classifier = new SnapClassifier();
    classifier.onJump(3000, 200_000, 1000, 'song-a');
    classifier.onJump(4000, 2_000, 1100, 'song-b');

    const events = classifier.flush(1100 + SILENCE_WINDOW_MS);
    expect(events).toHaveLength(2);
    expect(events.map((e) => e.trackKey)).toEqual(['song-a', 'song-b']);
    expect(events[0]?.positionMs).toBe(200_000);
  });

  it('carries the track it belonged to, not the one playing when it drains', () => {
    const classifier = new SnapClassifier();
    classifier.onJump(5000, 150_000, 1000, 'song-a');
    const events = classifier.flush(1000 + SILENCE_WINDOW_MS);
    expect(events[0]?.trackKey).toBe('song-a');
  });
});
