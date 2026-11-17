/**
 * What the renderer dropped when it went idle, kept so a re-announce of the
 * SAME track can bring it back.
 *
 * The renderer drops to idle on three edges — the data-loss watchdog, a lost
 * source, a lost connection — and idle means "no key, no lines". The main
 * process answers a duplicate announce by re-sending the TRACK, the only
 * signal that can wake an idled renderer, but never the lyrics: those were
 * delivered once, for this key, before the renderer let go of them. The woken
 * renderer then held the key with no lines for the rest of the song
 * (2026-10 review). Re-sending the lyrics from main on every duplicate would
 * fix that and break the normal path instead: re-applying the same payload
 * rebuilds fill plans and the fx index, which blinks effects mid-song.
 *
 * So the renderer keeps what it had. One payload, and any other track
 * invalidates it — a stash is about THIS song or nothing.
 */
export class IdleStash<T> {
  private last: { key: string; payload: T } | null = null;
  private stashed: { key: string; payload: T } | null = null;

  /** A (non-searching) lyrics payload was applied for `key`. */
  remember(key: string, payload: T): void {
    this.last = { key, payload };
  }

  /** The renderer is dropping to idle while it shows `key` (null: nothing). */
  idle(key: string | null): void {
    // Two idles in a row (watchdog, then source-gone) must not lose the stash.
    if (key === null) return;
    this.stashed = this.last?.key === key ? this.last : null;
  }

  /** A new key was announced: this track's stashed payload, at most once. */
  take(key: string): T | null {
    const stashed = this.stashed;
    this.stashed = null;
    return stashed !== null && stashed.key === key ? stashed.payload : null;
  }
}
