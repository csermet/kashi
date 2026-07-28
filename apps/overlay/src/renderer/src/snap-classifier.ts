/**
 * Tells a user's seek apart from a real timing anomaly.
 *
 * The clock reports every unexplained jump it takes (position-clock.ts). In the
 * field that produced 120 `unexplained_snap` events against 21 songs, and the
 * shape of the data gave the game away: 86 of them landed within two seconds of
 * the previous one. They were not 120 faults, they were a handful of drags.
 *
 * The cause is a race in the source data, not in the clock. While the progress
 * bar is being dragged, YouTube Music keeps firing `timeupdate` (~4 Hz), and
 * each of those arrives as a plain position report with a large delta and
 * nothing to explain it. The one message that DOES explain it — `seeked` — only
 * arrives when the drag ends. So the explanation always trails the evidence.
 *
 * This collects jumps into a bucket, waits for the stream to go quiet, and asks
 * once: did a seek show up? One drag becomes one event, labelled honestly.
 *
 * The clock's behaviour is untouched — this only classifies what gets reported.
 */

/**
 * How long the jumps have to stop before a bucket is judged. Anchored to the
 * LAST jump, not the first: a six-second drag has to stay one bucket, otherwise
 * the middle of the drag would be sealed as "unexplained" before the `seeked`
 * that explains it ever arrives.
 */
export const SILENCE_WINDOW_MS = 2000;

/**
 * A seek can also arrive just BEFORE its jumps — clicking (rather than
 * dragging) the progress bar snaps `currentTime` first, and the position
 * reports catch up after. A seek this recent still explains a new bucket.
 */
export const SEEK_GRACE_MS = 2000;

/**
 * Sealed-but-unsent buckets are capped. A guard with no budget can do more
 * damage than what it guards against (Faz 6.7 lesson), so the overflow drops
 * the oldest and counts it rather than growing without limit. In practice the
 * queue never exceeds one or two: sealing takes 2 s, draining runs at 1 Hz.
 */
export const MAX_PENDING_BUCKETS = 8;

export type SnapReason = 'user_seek' | 'unexplained_snap';

export interface SnapEvent {
  reason: SnapReason;
  /** The last jump in the bucket — where the position actually ended up. */
  deltaMs: number;
  positionMs: number;
  /** How many raw jumps collapsed into this one event (for the log line). */
  jumpCount: number;
  /**
   * Which track the jump belonged to. Reporting is deferred by up to a few
   * seconds, so by the time an event is sent the song may have changed —
   * and a position from one track measured against another's duration is
   * worse than no measurement at all.
   */
  trackKey: string | null;
}

interface Bucket {
  lastAtMono: number;
  jumpCount: number;
  deltaMs: number;
  positionMs: number;
  sawSeek: boolean;
  trackKey: string | null;
}

export class SnapClassifier {
  private open: Bucket | null = null;
  private sealed: Bucket[] = [];
  private lastSeekAtMono = Number.NEGATIVE_INFINITY;
  private droppedBuckets = 0;

  /** A jump the clock could not explain by itself. */
  onJump(deltaMs: number, positionMs: number, atMono: number, trackKey: string | null = null): void {
    if (!Number.isFinite(deltaMs) || !Number.isFinite(positionMs)) return;

    // A long enough gap means the previous bucket is a separate incident — and
    // so does a different song: two tracks' jumps must never average into one
    // event, or the surviving position belongs to a track nobody can name.
    if (
      this.open &&
      (atMono - this.open.lastAtMono >= SILENCE_WINDOW_MS || this.open.trackKey !== trackKey)
    ) {
      this.seal();
    }
    if (!this.open) {
      this.open = {
        lastAtMono: atMono,
        jumpCount: 0,
        deltaMs,
        positionMs,
        sawSeek: atMono - this.lastSeekAtMono < SEEK_GRACE_MS,
        trackKey,
      };
    }
    this.open.lastAtMono = atMono;
    this.open.jumpCount += 1;
    this.open.deltaMs = deltaMs;
    this.open.positionMs = positionMs;
  }

  /** A real seek message arrived — it explains the jumps around it. */
  onSeek(atMono: number): void {
    this.lastSeekAtMono = atMono;
    if (this.open) this.open.sawSeek = true;
  }

  /**
   * Seals whatever has gone quiet and hands back the events to report. Called
   * off the render path (the 1 Hz repaint hook), never per frame.
   *
   * The timeout is unconditional: no sequence of inputs can hold a bucket open
   * forever, so nothing can be suppressed permanently.
   */
  flush(atMono: number): SnapEvent[] {
    if (this.open && atMono - this.open.lastAtMono >= SILENCE_WINDOW_MS) {
      this.seal();
    }
    if (this.sealed.length === 0) return [];
    const events = this.sealed.map(
      (bucket): SnapEvent => ({
        reason: bucket.sawSeek ? 'user_seek' : 'unexplained_snap',
        deltaMs: bucket.deltaMs,
        positionMs: bucket.positionMs,
        jumpCount: bucket.jumpCount,
        trackKey: bucket.trackKey,
      }),
    );
    this.sealed = [];
    return events;
  }

  /** Buckets lost to the cap. Non-zero means something pathological upstream. */
  droppedBucketCount(): number {
    return this.droppedBuckets;
  }

  /** Test/diagnostic hook: is a bucket still collecting? */
  hasOpenBucket(): boolean {
    return this.open !== null;
  }

  private seal(): void {
    if (!this.open) return;
    this.sealed.push(this.open);
    this.open = null;
    while (this.sealed.length > MAX_PENDING_BUCKETS) {
      this.sealed.shift();
      this.droppedBuckets += 1;
    }
  }
}
