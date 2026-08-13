/**
 * Position extrapolation clock (plan R-1).
 *
 * The extension reports positions at ~4 Hz (`timeupdate`). Word/line highlighting
 * needs a continuous position, so between reports we extrapolate:
 *
 *   estimated = anchor_position + (now - anchor_time) × playback_rate   (while playing)
 *
 * Each report is compared against the current estimate:
 *   |delta| <  IGNORE_BELOW_MS     → ignore (network/timeupdate jitter)
 *   |delta| <= SEEK_SNAP_ABOVE_MS  → slew: blend the correction in over
 *                                    SLEW_DURATION_MS so the highlight never
 *                                    visibly jumps (covers buffering stalls too)
 *   |delta| >  SEEK_SNAP_ABOVE_MS  → snap (a real seek; explicit `seek`
 *                                    messages snap regardless of delta)
 *
 * Report latency is compensated using `captured_at` (epoch ms stamped in the
 * content script at capture time): by arrival the media has already advanced
 * by (now_epoch - captured_at) × rate.
 *
 * Reports without `playback_rate` (seek / playback_state messages) keep the
 * clock's current rate instead of resetting it to 1×.
 *
 * Rendering runs on a monotonic clock (performance.now) so NTP adjustments of
 * the wall clock cannot warp playback; the wall clock is only used to measure
 * report latency. Both clocks are injectable for tests.
 */

export const IGNORE_BELOW_MS = 30;
export const SEEK_SNAP_ABOVE_MS = 1500;
export const SLEW_DURATION_MS = 250;

export interface PositionReport {
  position_ms: number;
  /** Omitted by seek/playback_state messages → the current rate is kept. */
  playback_rate?: number;
  is_playing: boolean;
  /** Epoch ms stamped at capture time in the content script. */
  captured_at: number;
}

interface Slew {
  startedAt: number;
  deltaMs: number;
}

export class PositionClock {
  private anchorPositionMs = 0;
  private anchorAtMono = 0;
  private rate = 1;
  private playing = false;
  private hasAnchor = false;
  private slew: Slew | null = null;

  constructor(
    private readonly nowMono: () => number = () => performance.now(),
    private readonly nowEpoch: () => number = () => Date.now(),
    /**
     * Called when the clock snaps WITHOUT a seek to explain it (Faz 6.7 P2).
     * That jump is the signature of the timing bug this phase chased: the
     * position we were told is nowhere near the one we projected. Purely an
     * observer — the clock behaves identically whether anyone listens.
     */
    private readonly onUnexplainedJump?: (info: { deltaMs: number; positionMs: number }) => void,
  ) {}

  /** Feed a position/seek/playback_state report. `isSeek` forces a snap. */
  update(report: PositionReport, isSeek = false): void {
    const mono = this.nowMono();
    const rate = report.playback_rate ?? this.rate;
    // Clamp: a snapshot replayed minutes after capture must not catapult the
    // position forward by the whole gap (transport latency is what we model).
    const latencyMs = Math.min(5000, Math.max(0, this.nowEpoch() - report.captured_at));
    const compensated = report.position_ms + (report.is_playing ? latencyMs * rate : 0);

    if (!this.hasAnchor || isSeek) {
      this.setAnchor(compensated, mono, rate, report.is_playing);
      return;
    }

    // Rate/play-state transitions re-anchor from the current estimate so the
    // position never jumps when only the state changed.
    if (report.is_playing !== this.playing || rate !== this.rate) {
      const current = this.positionAt(mono);
      const delta = compensated - current;
      if (Math.abs(delta) > SEEK_SNAP_ABOVE_MS) {
        this.setAnchor(compensated, mono, rate, report.is_playing);
      } else {
        // Absorb any in-flight slew into the anchor (continuity), then correct.
        this.reAnchorAt(current, mono);
        this.rate = rate;
        this.playing = report.is_playing;
        if (Math.abs(delta) >= IGNORE_BELOW_MS) this.startSlew(delta, mono);
      }
      return;
    }

    const delta = compensated - this.positionAt(mono);
    if (Math.abs(delta) < IGNORE_BELOW_MS) return;
    if (Math.abs(delta) <= SEEK_SNAP_ABOVE_MS) {
      // Absorb the in-flight slew's applied portion into the anchor so the
      // estimate stays continuous; the fresh report supersedes its remainder.
      this.reAnchorAt(this.positionAt(mono), mono);
      this.startSlew(delta, mono);
      return;
    }
    // A jump this large with no seek behind it is worth reporting.
    this.onUnexplainedJump?.({ deltaMs: Math.round(delta), positionMs: report.position_ms });
    this.setAnchor(compensated, mono, rate, report.is_playing); // real seek → snap
  }

  /** Current estimated position in ms (never negative). */
  positionAt(mono: number = this.nowMono()): number {
    if (!this.hasAnchor) return 0;
    const elapsed = this.playing ? (mono - this.anchorAtMono) * this.rate : 0;
    return Math.max(0, this.anchorPositionMs + elapsed + this.slewContribution(mono));
  }

  get isPlaying(): boolean {
    return this.playing;
  }

  /**
   * False until the first report lands — and the difference matters, because
   * `positionAt()` answers 0 either way.
   *
   * A caller that cannot tell "the track is at the start" from "nobody has told
   * us anything yet" paints the first line of the song and leaves it up until a
   * real report arrives, then loses it in a jump. That is the second half of
   * the 2026-08-13 field case: while the anchor guard was (correctly) holding
   * back the previous track's playhead, the screen had already committed to
   * line one of a song that was 337 s from where it claimed to be.
   */
  get anchored(): boolean {
    return this.hasAnchor;
  }

  /** Forget everything (track change). */
  reset(): void {
    this.hasAnchor = false;
    this.slew = null;
    this.playing = false;
    this.rate = 1;
  }

  private setAnchor(positionMs: number, mono: number, rate: number, playing: boolean): void {
    this.anchorPositionMs = positionMs;
    this.anchorAtMono = mono;
    this.rate = rate;
    this.playing = playing;
    this.hasAnchor = true;
    this.slew = null;
  }

  /** Re-anchor at the given estimate, absorbing any in-flight slew. */
  private reAnchorAt(positionMs: number, mono: number): void {
    this.anchorPositionMs = positionMs;
    this.anchorAtMono = mono;
    this.slew = null;
  }

  private startSlew(deltaMs: number, mono: number): void {
    this.slew = { startedAt: mono, deltaMs };
  }

  private slewContribution(mono: number): number {
    if (!this.slew) return 0;
    const progress = Math.min(1, (mono - this.slew.startedAt) / SLEW_DURATION_MS);
    return this.slew.deltaMs * progress;
  }
}
