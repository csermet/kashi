/**
 * Auto-enqueue gate (plan R-9): after the server answers 404 for the CURRENT
 * track, the track earns an ingest job only once the listener has accumulated
 * >= 20 s of ACTUAL PLAYING time on it. Radio-zapping never enqueues; a pause
 * FREEZES the accumulator (in-track pauses don't reset intent); a track
 * change resets everything. Fires at most once per track.
 *
 * Pure state machine driven by injected timestamps — fully unit-testable.
 */

export const ENQUEUE_AFTER_MS = 20_000;

type State =
  | { kind: 'idle' }
  | {
      kind: 'armed';
      key: string;
      accumulatedMs: number;
      playing: boolean;
      lastTickAt: number;
    }
  | { kind: 'fired'; key: string };

export class EnqueueGate {
  private state: State = { kind: 'idle' };

  /** New track: everything resets (the 404 must belong to THIS track). */
  trackChanged(): void {
    this.state = { kind: 'idle' };
  }

  /** The server answered 404 for `key` — start counting listening time. */
  serverMiss(key: string, now: number, playing: boolean): void {
    if (this.state.kind === 'fired' && this.state.key === key) return;
    this.state = { kind: 'armed', key, accumulatedMs: 0, playing, lastTickAt: now };
  }

  playback(isPlaying: boolean, now: number): void {
    if (this.state.kind !== 'armed') return;
    this.accumulate(now);
    this.state.playing = isPlaying;
  }

  private accumulate(now: number): void {
    if (this.state.kind !== 'armed') return;
    if (this.state.playing) {
      this.state.accumulatedMs += Math.max(0, now - this.state.lastTickAt);
    }
    this.state.lastTickAt = now;
  }

  /** Returns the key exactly once when the threshold is crossed. */
  tick(now: number): string | null {
    if (this.state.kind !== 'armed') return null;
    this.accumulate(now);
    if (this.state.accumulatedMs >= ENQUEUE_AFTER_MS) {
      const key = this.state.key;
      this.state = { kind: 'fired', key };
      return key;
    }
    return null;
  }

  get armed(): boolean {
    return this.state.kind === 'armed';
  }
}
