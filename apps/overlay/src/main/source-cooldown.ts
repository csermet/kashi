/**
 * Consecutive-failure back-off for one lyrics source: base, 2×base, 4×base …
 * up to the cap, never shorter than the wait the server itself asked for. One
 * real answer clears it.
 *
 * The client consults it AFTER its disk cache and BEFORE the network
 * (lrclib.ts): a cooling-down source must not hide lyrics that are already on
 * disk, and a disk answer must not clear a wait the server asked for — only a
 * response that actually came back over the wire proves the source is up.
 */
export class SourceCooldown {
  private failures = 0;
  private until = 0;

  constructor(
    private readonly baseMs: number,
    private readonly capMs: number,
    private readonly now: () => number = Date.now,
  ) {}

  /** ms until the source may be asked again; 0 = now. */
  remainingMs(): number {
    return Math.max(0, this.until - this.now());
  }

  noteFailure(retryAfterMs: number | null): void {
    this.failures++;
    const backoff = Math.min(this.baseMs * 2 ** (this.failures - 1), this.capMs);
    this.until = this.now() + Math.max(backoff, retryAfterMs ?? 0);
  }

  noteSuccess(): void {
    this.failures = 0;
    this.until = 0;
  }
}
