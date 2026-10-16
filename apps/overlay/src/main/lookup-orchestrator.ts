/**
 * Lyrics lookup ladder, dependency-injected so the whole flow is unit-testable:
 *
 *   server (when configured) → hit: single source of truth, never blended
 *   with lrclib and never replaced by it (R-8) → genuine 404: arm the >=20 s enqueue gate and
 *   fall through → error: fall through WITHOUT arming (R-9) →
 *   lrclib with transient-failure retries and a duration-less second try.
 *
 * LyricFix (2026-10): the server gets a head start, not a monopoly. If it has
 * not answered within `serverHeadStartMs` the lrclib rung starts alongside it
 * — a first listen must show plain lyrics in seconds, and a late server
 * document still replaces them (R-8 is about what WINS, not what runs). And
 * the lrclib rung no longer gives up after its quick tries: while the track is
 * current it keeps asking on a widening schedule that honours Retry-After,
 * so lyrics arrive mid-song when lrclib comes back.
 *
 * Staleness is guarded twice: the per-lookup AbortController (a newer track
 * aborts the old lookup) and the isCurrent(key) check on every response.
 */
import { classifyEdit } from './edit-check.js';
import type { LyricLine } from './lrclib.js';
import type { TrackInfo } from '@kashi/protocol';
import type { ServerLyricsResult } from './kashi-server-logic.js';
import {
  isRetryable,
  retryDelayMs,
  shouldUpgrade,
  enrichmentKeys,
  type DisplayedLyrics,
} from './server-retry-logic.js';

export interface LrclibQuery {
  title: string;
  artist: string;
  album?: string;
  duration_ms?: number;
}

export interface LookupDeps {
  /** null = serverless mode (v0.1.11 behavior, byte-for-byte — R-F3-8). */
  getProcessed:
    | ((type: string, id: string, signal: AbortSignal) => Promise<ServerLyricsResult>)
    | null;
  /**
   * `lines`/`recordDurationMs` are optional so a fake in a test that only
   * cares about the ladder need not invent them — but when they ARE present
   * the different-edit check reads them, so an integration-shaped fake gets
   * the real behavior.
   */
  getLyrics: (
    query: LrclibQuery,
    signal: AbortSignal,
  ) => Promise<{
    found: boolean;
    lines?: readonly LyricLine[];
    sourceId?: number;
    recordDurationMs?: number | null;
    /** Which source answered (the mirror chain); diagnostics only. */
    via?: string;
  }>;
  /** Emit a kashi:lyrics payload (already carries the track key). */
  send: (payload: { key: string } & Record<string, unknown>) => void;
  /** Genuine server 404 for the CURRENT track — arm the enqueue gate. */
  onServerMiss: (key: string, track: TrackInfo) => void;
  /** Word-sync server hit — the only publishable moment (Faz 5 P6). */
  onServerWordHit?: (key: string, source: { type: string; id: string }) => void;
  /**
   * The server's own measurement of this track's audio. Worth learning even
   * from a stale cache hit: the number describes the audio the server probed,
   * and it is the only one that never came through the page.
   */
  onServerDuration?: (key: string, durationMs: number) => void;
  isCurrent: (key: string) => boolean;
  /**
   * Whether the track is actually playing. A track paused overnight during an
   * outage must not keep asking lrclib every two minutes; the background
   * retries wait for playback instead. Omitted = always playing.
   */
  isPlaying?: () => boolean;
  log: (line: string) => void;
  /** Retry delays for transient lrclib failures (timeout/network). */
  retryDelaysMs?: number[];
  /**
   * Waits between the background retries that follow the quick ones, while
   * the track is still playing. Omitted = 15 s doubling to a 120 s cap, for as
   * long as the track is current; a finite list (tests) ends with the old
   * error screen.
   */
  patientDelaysMs?: readonly number[];
  /** How long the server may answer alone before lrclib starts alongside it. */
  serverHeadStartMs?: number;
  /** Self-heal ladder delays; injectable so a test need not wait ten seconds. */
  serverRetryDelaysMs?: readonly number[];
  /** Clock for the latency diagnostics. */
  now?: () => number;
  /** Optional diagnostics sink (Faz 6.7 P2). Never awaited, never throws —
   * the ladder's behavior must not depend on whether anyone is listening. */
  emit?: (kind: 'lyrics_outcome', payload: Record<string, unknown>) => void;
}

type LrclibAnswer = Awaited<ReturnType<LookupDeps['getLyrics']>>;

const DEFAULT_RETRY_DELAYS_MS = [0, 2000, 6000];
const PATIENT_FIRST_MS = 15_000;
const PATIENT_CAP_MS = 120_000;
/** The server answers from the LAN or its disk cache in well under a second;
 * past this it is timing out (5 s budget), and the screen should not wait. */
const DEFAULT_SERVER_HEAD_START_MS = 1500;

/** The wait an lrclib failure asked for (Retry-After, or the shared cooldown
 * that stands in for it); 0 when it named none. Duck-typed so this module
 * stays free of the client's I/O imports. */
function retryAfterMsOf(err: unknown): number {
  if (typeof err === 'object' && err !== null && 'retryAfterMs' in err) {
    const value = (err as { retryAfterMs: unknown }).retryAfterMs;
    if (typeof value === 'number' && value > 0) return value;
  }
  return 0;
}

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    // 'abort' never fires again on an already-aborted signal, so without this
    // the caller waits out the full delay before noticing it was cancelled.
    if (signal.aborted) return resolve();
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    // The listener goes with the timer: background retries can sleep on one
    // signal for hours, and listeners must not pile up on it.
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

export class LookupOrchestrator {
  private abort: AbortController | null = null;
  /** What the current track is showing — the self-heal upgrade rule reads it. */
  private displayed: { key: string; state: DisplayedLyrics } | null = null;

  constructor(private readonly deps: LookupDeps) {}

  /** Abort the in-flight lookup (track change, source gone, shutdown). */
  cancel(): void {
    this.abort?.abort();
  }

  async lookup(key: string, track: TrackInfo): Promise<void> {
    this.abort?.abort();
    const abort = new AbortController();
    this.abort = abort;
    const query: LrclibQuery = {
      title: track.title,
      artist: track.artist,
      album: track.album,
      duration_ms: track.duration_ms,
    };

    this.deps.send({ key, searching: true });
    const startedAt = this.now();

    // One lrclib rung per lookup, however many paths ask for it.
    let rung: Promise<void> | null = null;
    const startLrclib = (): Promise<void> =>
      (rung ??= this.lrclibRung(key, track, query, abort, startedAt));

    if (!this.deps.getProcessed) return startLrclib();

    const server = this.tryServer(key, track, abort, startedAt);
    const headStart = this.deps.serverHeadStartMs ?? DEFAULT_SERVER_HEAD_START_MS;
    const timer = new AbortController();
    const first = await Promise.race([
      server,
      abortableSleep(headStart, AbortSignal.any([abort.signal, timer.signal])).then(
        () => 'head-start-over' as const,
      ),
    ]);
    timer.abort();
    if (abort.signal.aborted) return;
    if (first === 'head-start-over') {
      this.deps.log(`server silent after ${headStart} ms for ${key} — lrclib starts alongside`);
      void startLrclib();
      // A late hit replaces whatever lrclib put up (showServerHit); a late
      // 404 or error has nothing to add — the rung is already running.
      await server;
      return startLrclib();
    }
    if (first) return; // server hit (or stale)
    return startLrclib();
  }

  /**
   * lrclib, until it answers or the track moves on: the quick tries first,
   * then background retries on a widening schedule. No wait is ever shorter
   * than the one the failure asked for.
   */
  private async lrclibRung(
    key: string,
    track: TrackInfo,
    query: LrclibQuery,
    abort: AbortController,
    startedAt: number,
  ): Promise<void> {
    const quick = this.deps.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
    let lastError: unknown = null;
    let patient = false;
    for (let attempt = 0; ; attempt++) {
      const delay =
        attempt < quick.length
          ? (quick[attempt] ?? 0)
          : this.patientDelay(attempt - quick.length);
      if (delay === null) break;
      const wait = Math.max(delay, retryAfterMsOf(lastError));
      // Say so once the quick tries are spent — or sooner, when lrclib itself
      // asked for a wait long enough that plain dots would look like a hang.
      if (!patient && (attempt >= quick.length || wait >= PATIENT_FIRST_MS)) {
        patient = true;
        this.announceRetrying(key, lastError);
      }
      await abortableSleep(wait, abort.signal);
      if (abort.signal.aborted || !this.deps.isCurrent(key)) return; // superseded
      if (this.serverShown(key)) return; // a server document healed the track
      if (patient && this.deps.isPlaying && !this.deps.isPlaying()) {
        continue; // paused: the schedule keeps widening, no request goes out
      }
      try {
        const result = await this.fetchCheckedLyrics(key, query, abort);
        if (result === null) return; // stale response guard (R-9)
        this.showLrclib(key, track, attempt, result, startedAt, patient);
        return;
      } catch (err) {
        if (abort.signal.aborted) return;
        lastError = err;
        this.deps.log(`lyrics lookup failed (attempt ${attempt + 1}): ${err}`);
      }
    }
    // Only a finite patient schedule gets here.
    // error !== genuine miss — renderer shows a different message.
    if (this.deps.isCurrent(key) && !this.serverShown(key)) {
      this.markEmpty(key);
      this.deps.send({ key, found: false, error: true });
    }
  }

  /** null = stop; omitted schedule = 15 s doubling to a 120 s cap, forever. */
  private patientDelay(index: number): number | null {
    const schedule = this.deps.patientDelaysMs;
    if (schedule) return schedule[index] ?? null;
    return Math.min(PATIENT_FIRST_MS * 2 ** index, PATIENT_CAP_MS);
  }

  /** The quick tries are spent: say so once, and keep the screen honest. */
  private announceRetrying(key: string, lastError: unknown): void {
    if (!this.deps.isCurrent(key) || this.serverShown(key)) return;
    this.deps.log(`lrclib unavailable for ${key} — retrying in the background`);
    this.deps.emit?.('lyrics_outcome', {
      source: 'error',
      retry_after_ms: retryAfterMsOf(lastError),
    });
    this.markEmpty(key);
    this.deps.send({ key, searching: true, retrying: true });
  }

  /**
   * Record the empty screen: the network event that timed the server out
   * usually takes lrclib with it, and that is exactly when a late server
   * document is worth the most. Without this the probe would find no
   * 'displayed' state and decline to fill a screen showing an error.
   */
  private markEmpty(key: string): void {
    if (this.displayed?.key === key) return;
    this.displayed = { key, state: { source: 'none', sync: 'line' } };
  }

  private serverShown(key: string): boolean {
    return this.displayed?.key === key && this.displayed.state.source === 'kashi-server';
  }

  private now(): number {
    return (this.deps.now ?? Date.now)();
  }

  /** The server rung. True when the lookup is finished (a hit, or stale);
   * false to fall through to lrclib (a 404, or an error with a probe started). */
  private async tryServer(
    key: string,
    track: TrackInfo,
    abort: AbortController,
    startedAt: number,
  ): Promise<boolean> {
    if (!this.deps.getProcessed) return false;
    const result = await this.deps.getProcessed(track.source.type, track.source.id, abort.signal);
    if (abort.signal.aborted || !this.deps.isCurrent(key)) return true; // stale (R-9)
    if ('found' in result && result.found) {
      this.showServerHit(key, track, result, abort, startedAt);
      return true;
    }
    if ('found' in result && !result.found) {
      // Genuinely unprocessed: arm the >=20 s listening gate (R-9), then let
      // the lrclib flow below fill the screen in the meantime.
      this.deps.onServerMiss(key, track);
      this.deps.log(`server 404: ${key} — lrclib fallback + enqueue gate armed`);
    } else {
      this.deps.log(`server error for ${key} — lrclib fallback, probing in the background`);
      this.deps.emit?.('lyrics_outcome', { source: 'server-error' });
      // Do NOT await: the ladder must reach lrclib now. The probe rides the
      // same AbortController, so a track change kills it.
      void this.selfHeal(key, track, abort).catch(() => {});
    }
    return false;
  }

  /** One lrclib answer for the CURRENT track (null when it went stale):
   * duration-scoped first, unscoped once on a miss, and a different-edit
   * record treated as a miss. */
  private async fetchCheckedLyrics(
    key: string,
    query: LrclibQuery,
    abort: AbortController,
  ): Promise<LrclibAnswer | null> {
    let result = await this.deps.getLyrics(query, abort.signal);
    // A cancelled lookup (settings re-init) may share the key with its
    // replacement, so the key check alone cannot tell them apart.
    if (abort.signal.aborted || !this.deps.isCurrent(key)) return null; // stale (R-9)
    if (!result.found && query.duration_ms) {
      // The reported duration can be transiently WRONG during YTM's
      // auto-advance (MSE mid-transition) — a bad duration rejects every
      // candidate, so retry once without it before giving up.
      this.deps.log(
        `duration-scoped lookup missed (duration_ms=${query.duration_ms}), retrying without duration`,
      );
      result = await this.deps.getLyrics({ ...query, duration_ms: undefined }, abort.signal);
      if (abort.signal.aborted || !this.deps.isCurrent(key)) return null;
    }
    if (result.found) {
      // The unscoped retry above removes the ONLY filter that keeps another
      // edit's stamps off the screen, so whatever came back gets checked
      // against this track before it is allowed to drive a clock.
      const verdict = classifyEdit(
        query.duration_ms,
        result.recordDurationMs ?? null,
        result.lines ?? [],
      );
      if (verdict === 'different-edit') {
        this.deps.log(
          `lrclib record ${result.sourceId} is a different edit` +
            ` (record ${result.recordDurationMs ?? 'sure yok'}ms vs track ${query.duration_ms}ms)` +
            ' — discarded, wrong stamps are worse than none',
        );
        this.deps.emit?.('lyrics_outcome', { source: 'different-edit' });
        // Treated as a miss, not as a display: the enqueue gate is already
        // armed, so the server will align this track against its OWN audio
        // and the screen fills properly a few minutes later.
        result = { found: false };
      }
    }
    return result;
  }

  private showServerHit(
    key: string,
    track: TrackInfo,
    result: Extract<ServerLyricsResult, { found: true }>,
    abort: AbortController,
    startedAt: number,
  ): void {
    const stale = result.stale === true;
    this.deps.log(
      `server hit: ${key} sync=${result.sync} quality=${result.qualityScore}` +
        (stale ? ' (from cache — the live request failed)' : ''),
    );
    this.deps.emit?.('lyrics_outcome', {
      source: 'kashi-server',
      sync: result.sync,
      quality: result.qualityScore,
      latency_ms: this.now() - startedAt,
      ...(stale ? { stale: true } : {}),
    });
    if (result.sync === 'word') {
      this.deps.onServerWordHit?.(key, { type: track.source.type, id: track.source.id });
    }
    if (result.trackDurationMs) this.deps.onServerDuration?.(key, result.trackDurationMs);
    this.displayed = {
      key,
      state: { source: 'kashi-server', sync: result.sync, qualityScore: result.qualityScore },
    };
    this.deps.send({ key, ...result });
    // A cache fallback filled the screen, but the server was never
    // actually reached. Keep probing: the document on disk may be several
    // reprocesses behind, and nothing else will ever ask again.
    if (stale) void this.selfHeal(key, track, abort).catch(() => {});
  }

  /** Publish an lrclib answer (or a genuine miss) for the current track —
   * unless the probe already healed it with a server document. */
  private showLrclib(
    key: string,
    track: TrackInfo,
    attempt: number,
    result: LrclibAnswer,
    startedAt: number,
    recovered: boolean,
  ): void {
    if (!result.found) {
      this.deps.log(
        `no synced lyrics: "${track.artist} - ${track.title}"` +
          ` (duration_ms=${track.duration_ms ?? 'yok'})`,
      );
    }
    const latencyMs = this.now() - startedAt;
    if (recovered) this.deps.log(`lrclib answered ${key} after ${Math.round(latencyMs / 1000)} s of retrying`);
    if (this.serverShown(key)) {
      // The probe already healed this track while lrclib was still
      // retrying. A server document is the single source of truth (R-8);
      // publishing lrclib over it would be a visible downgrade.
      this.deps.log(`lrclib answered after the server probe healed ${key} — discarded`);
      return;
    }
    // Counted only once it is what the user sees: an answer discarded under a
    // server document would report two outcomes for one lookup.
    this.deps.emit?.('lyrics_outcome', {
      source: result.found ? 'lrclib' : 'none',
      attempt: attempt + 1,
      latency_ms: latencyMs,
      ...(result.via ? { via: result.via } : {}),
      ...(recovered ? { recovered_after_s: Math.round(latencyMs / 1000) } : {}),
    });
    this.displayed = {
      key,
      state: { source: result.found ? 'lrclib' : 'none', sync: 'line' },
    };
    this.deps.send({ key, ...result });
  }

  /**
   * Background probe after a server ERROR (Faz 6.7 P3).
   *
   * A timeout used to cost the whole song: lrclib's plain text stayed up
   * until the next track, because the ladder asks the server exactly once.
   * This asks again on a widening schedule and swaps the document in only if
   * it is genuinely richer — a mid-song re-render has to earn itself.
   */
  private async selfHeal(key: string, track: TrackInfo, abort: AbortController): Promise<void> {
    const getProcessed = this.deps.getProcessed;
    if (!getProcessed) return;
    const ladder = this.deps.serverRetryDelaysMs;
    for (let attempt = 0; ; attempt++) {
      const delay = ladder ? (ladder[attempt] ?? null) : retryDelayMs(attempt);
      if (delay === null) {
        this.deps.log(`server probe gave up on ${key} after ${attempt} attempts`);
        return;
      }
      await abortableSleep(delay, abort.signal);
      if (abort.signal.aborted || !this.deps.isCurrent(key)) return;

      const result = await this.probeOnce(getProcessed, key, track, abort);
      if (result === 'stop') return;
      if (result === 'retry') continue;
      this.settleProbe(key, track, attempt, result);
      return;
    }
  }

  /** One self-heal request: 'stop' when the track moved on, 'retry' when the
   * answer is still a failure (an error, or the cache standing in for one). */
  private async probeOnce(
    getProcessed: NonNullable<LookupDeps['getProcessed']>,
    key: string,
    track: TrackInfo,
    abort: AbortController,
  ): Promise<ServerLyricsResult | 'stop' | 'retry'> {
    let result: ServerLyricsResult;
    try {
      result = await getProcessed(track.source.type, track.source.id, abort.signal);
    } catch {
      // The client swallows its own errors; be defensive anyway. Re-check
      // cancellation rather than falling straight into the next sleep.
      if (abort.signal.aborted || !this.deps.isCurrent(key)) return 'stop';
      return 'retry';
    }
    if (abort.signal.aborted || !this.deps.isCurrent(key)) return 'stop';

    if (isRetryable(result)) return 'retry';
    // A stale answer is the SAME failure wearing the cache's clothes: the
    // request did not reach the server. Treating it as an answer would end
    // the ladder on its first rung and leave the old document up for good.
    if ('found' in result && result.found && result.stale) return 'retry';
    return result;
  }

  /** A probe got a real answer: a 404 arms the gate, a richer document replaces
   * the screen, anything else ends the probe quietly. */
  private settleProbe(
    key: string,
    track: TrackInfo,
    attempt: number,
    result: ServerLyricsResult,
  ): void {
    if ('found' in result && !result.found) {
      // A 404 is an answer, not a failure: stop probing and arm the gate
      // the first attempt could not (an error never proves "unprocessed").
      this.deps.log(`server probe: ${key} genuinely unprocessed — enqueue gate armed`);
      this.deps.onServerMiss(key, track);
      return;
    }

    // No recorded state means nothing useful is on screen yet — the ladder
    // is still working, or it died without publishing. Either way an empty
    // screen is the weakest thing the document can be compared against, so
    // that is the default rather than a reason to decline.
    const current: DisplayedLyrics =
      this.displayed?.key === key
        ? this.displayed.state
        : { source: 'none', sync: 'line' };
    if (!shouldUpgrade(current, result)) {
      this.deps.log(`server probe: ${key} recovered but not richer than what is shown`);
      return; // the server answers now; nothing left to heal
    }
    this.publishUpgrade(key, track, attempt, result);
  }

  private publishUpgrade(
    key: string,
    track: TrackInfo,
    attempt: number,
    result: ServerLyricsResult,
  ): void {
    if ('found' in result && result.found) {
      this.deps.log(
        `server probe: upgrading ${key} to sync=${result.sync} on attempt ${attempt + 1}`,
      );
      this.deps.emit?.('lyrics_outcome', {
        source: 'kashi-server',
        sync: result.sync,
        quality: result.qualityScore,
        upgraded: true,
        attempt: attempt + 1,
      });
      if (result.sync === 'word') {
        this.deps.onServerWordHit?.(key, { type: track.source.type, id: track.source.id });
      }
      if (result.trackDurationMs) this.deps.onServerDuration?.(key, result.trackDurationMs);
      this.displayed = {
        key,
        state: {
          source: 'kashi-server',
          sync: result.sync,
          qualityScore: result.qualityScore,
          enrichment: enrichmentKeys(result),
        },
      };
      this.deps.send({ key, ...result });
    }
  }
}
