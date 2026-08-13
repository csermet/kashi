/**
 * What we know about a video's real duration, remembered across plays.
 *
 * Field bug (Caner, 2026-08-13): YTM announced Hey Mama with the PREVIOUS
 * video's duration (366451 ms instead of 193000). 0.1.14 closed that at the
 * source — a duration the page cannot prove is fresh is no longer announced at
 * all — which turns a wrong number into NO number. Mid-session that is now the
 * common case, and no number disables both defenses this app has against
 * another edit's stamps: lrclib's duration filter on the way in (`?duration=`,
 * and the ±3 s pick inside search) and `classifyEdit` on the way out, which
 * returns 'unverifiable' without a track duration and lets the record through.
 *
 * A video id is IDENTITY — one id, one audio stream; a clip, a song and a lyric
 * video are separate ids. A duration is an OBSERVATION of that audio, and the
 * field bug was using the observation where identity was needed. This is the
 * other half of the fix: learn the duration once, key it by the identity, and
 * use it whenever an observation is missing or provably carried over.
 *
 * Not a cache of a remote fact — a learned one, so every rule here is about
 * trust rather than freshness:
 *
 *  - the SERVER's number comes from ffprobe on the audio itself, so it outranks
 *    anything the page says;
 *  - an ANNOUNCED number is the page's own report, trustworthy since ext 0.1.14
 *    because the extension only forwards a duration whose `durationchange`
 *    landed after the id changed;
 *  - a CONTRADICTION is not automatically an error. YouTube Studio can trim a
 *    video that is already published: the id, its URL and its stats stay, the
 *    audio gets shorter (support.google.com/youtube/answer/9057455), and
 *    Content ID's "trim out segment" does the same. Rare — but a table that
 *    could never change its mind would keep a dead value forever, so a
 *    contradiction that repeats wins.
 *
 * Deliberately a SECOND-level gating value, not a timing axis. Every consumer
 * is coarse (lrclib takes whole seconds, the edit check ±5 s, the overshoot
 * guard ±2 s); the <100 ms accuracy target belongs to the lyric stamps, and no
 * decision here should ever be read as being about milliseconds.
 */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { EDIT_DURATION_TOLERANCE_MS } from './edit-check.js';

/** Where a remembered duration came from. 'server' outranks 'announce'. */
export type DurationSource = 'server' | 'announce';

export interface LedgerEntry {
  ms: number;
  source: DurationSource;
  /** Last write or re-confirmation — the eviction order, nothing else. */
  at: number;
  /** A contradicting value waiting for a second, agreeing sighting. */
  pending?: { ms: number; seen: number };
}

/**
 * How many agreeing sightings a contradiction needs before it replaces what we
 * remember. Two, because one is exactly what the bug this file exists for looks
 * like: a single announce carrying somebody else's number.
 */
export const CONTRADICTION_CONFIRMATIONS = 2;

/** Nothing we play is a day long; past this the reading is broken, not long. */
export const MAX_PLAUSIBLE_DURATION_MS = 24 * 60 * 60 * 1000;

/** A listening history, not an archive — oldest entries fall off the end. */
export const MAX_ENTRIES = 2000;

const FILE_NAME = 'duration-ledger.json';
const DEFAULT_FLUSH_DELAY_MS = 2000;

export type DurationVerdict =
  /** The page reported it and nothing contradicts it: use and learn. */
  | 'observed'
  /**
   * The page reported it, but it is byte-identical to the duration of the track
   * that just ended — the signature of the carry-over bug. Nothing better is
   * known, so it still gets used (a missing duration is what let the wrong edit
   * through in the first place), but it must NOT be learned.
   */
  | 'observed-suspect'
  /** No usable observation, or a carried-over one we can replace: use the table. */
  | 'remembered'
  /** Nobody knows yet. */
  | 'unknown';

export interface DurationDecision {
  /** What every consumer downstream should use (undefined = still unknown). */
  ms: number | undefined;
  verdict: DurationVerdict;
}

function isUsable(ms: number | undefined): ms is number {
  return (
    typeof ms === 'number' &&
    Number.isFinite(ms) &&
    ms > 0 &&
    ms <= MAX_PLAUSIBLE_DURATION_MS
  );
}

function agrees(a: number, b: number): boolean {
  return Math.abs(a - b) <= EDIT_DURATION_TOLERANCE_MS;
}

/**
 * Which duration should this track be judged by?
 *
 * The carry-over test is EXACT equality against the previous track's duration,
 * and that is deliberate on both counts:
 *
 *  - exact, because `video.duration` is a constant per media resource and the
 *    extension rounds it the same way every time (`Math.round(d * 1000)`), so a
 *    genuine carry-over reproduces the previous number bit for bit. A tolerance
 *    here would instead start convicting neighbouring tracks that merely happen
 *    to be about as long;
 *  - a suspicion rather than proof, because two different videos CAN share a
 *    ms-identical duration — the same master delivered twice (album plus
 *    single), or a clean/explicit pair where the censor only mutes. So it never
 *    discards the number on its own; it only declines to pin it, and prefers a
 *    remembered value when there is one.
 */
export function resolveDuration(
  observedMs: number | undefined,
  rememberedMs: number | undefined,
  previousTrackMs: number | undefined,
): DurationDecision {
  const remembered = isUsable(rememberedMs) ? rememberedMs : undefined;
  if (!isUsable(observedMs)) {
    return remembered === undefined
      ? { ms: undefined, verdict: 'unknown' }
      : { ms: remembered, verdict: 'remembered' };
  }
  const carriedOver = isUsable(previousTrackMs) && observedMs === previousTrackMs;
  if (carriedOver) {
    // A remembered value was learned for THIS id, so it wins over a number that
    // still belongs to the track that just ended.
    if (remembered !== undefined && !agrees(remembered, observedMs)) {
      return { ms: remembered, verdict: 'remembered' };
    }
    return { ms: observedMs, verdict: 'observed-suspect' };
  }
  // A fresh observation that disagrees with the table is NEWS, not an error:
  // since 0.1.14 the page only reports a duration it can prove arrived after
  // the id changed, and the table may be describing audio that has since been
  // trimmed. `learn` decides how much confirmation that takes.
  return { ms: observedMs, verdict: 'observed' };
}

export interface DurationLedgerOptions {
  /** Same cache root as the other learned stores (one file, not one per key). */
  cacheDir: string;
  nowFn?: () => number;
  log?: (line: string) => void;
  /** Write debounce; 0 writes synchronously-ish on every change (tests). */
  flushDelayMs?: number;
}

export type LearnOutcome =
  /** Written: first sighting, a server number, or a confirmed contradiction. */
  | 'learned'
  /** Agreed with what we had (freshens the entry, may upgrade its source). */
  | 'confirmed'
  /** Contradicted what we had and is waiting for a second sighting. */
  | 'pending'
  /** Unusable number — nothing happened. */
  | 'ignored';

export class DurationLedger {
  private readonly entries = new Map<string, LedgerEntry>();
  private flushTimer: NodeJS.Timeout | null = null;
  private dirty = false;

  constructor(private readonly opts: DurationLedgerOptions) {}

  /** Best-effort: a ledger that cannot be read just starts empty. */
  async load(): Promise<void> {
    try {
      const raw = await readFile(join(this.opts.cacheDir, FILE_NAME), 'utf8');
      const parsed = JSON.parse(raw) as unknown;
      if (typeof parsed !== 'object' || parsed === null) return;
      const entries = (parsed as { entries?: unknown }).entries;
      if (typeof entries !== 'object' || entries === null) return;
      for (const [key, value] of Object.entries(entries as Record<string, unknown>)) {
        const entry = value as Partial<LedgerEntry>;
        // Validate on the way in: a hand-edited or half-written file must not
        // put a bogus duration in charge of which lyrics get shown.
        if (!isUsable(entry.ms)) continue;
        if (entry.source !== 'server' && entry.source !== 'announce') continue;
        if (typeof entry.at !== 'number' || !Number.isFinite(entry.at)) continue;
        this.entries.set(key, { ms: entry.ms, source: entry.source, at: entry.at });
      }
      this.opts.log?.(`[ledger] ${this.entries.size} remembered durations`);
    } catch {
      // no file yet, or unreadable — both mean "learn from scratch"
    }
  }

  /** The remembered duration for a track key, if we have a settled one. */
  durationFor(key: string): number | undefined {
    return this.entries.get(key)?.ms;
  }

  /** What this track should be judged by, given what the page just said. */
  resolve(
    key: string,
    observedMs: number | undefined,
    previousTrackMs: number | undefined,
  ): DurationDecision {
    return resolveDuration(observedMs, this.durationFor(key), previousTrackMs);
  }

  learn(key: string, ms: number | undefined, source: DurationSource): LearnOutcome {
    if (!isUsable(ms)) return 'ignored';
    const now = this.now();
    const entry = this.entries.get(key);
    if (!entry) {
      this.write(key, { ms, source, at: now });
      return 'learned';
    }
    if (agrees(entry.ms, ms)) {
      entry.at = now;
      delete entry.pending; // the contradiction did not hold up
      // ffprobe measured the audio; keep its exact number and its rank.
      if (source === 'server' && entry.source === 'announce') {
        entry.source = 'server';
        entry.ms = ms;
      }
      this.dirty = true;
      this.scheduleFlush();
      return 'confirmed';
    }
    if (source === 'server') {
      // The audio itself disagrees with what the page told us. No confirmation
      // ritual: one measurement outranks any number of page reports.
      this.write(key, { ms, source, at: now });
      return 'learned';
    }
    // Neither branch below schedules a write: `pending` is evidence we are
    // still collecting, and `flush` strips it on the way out, so a write here
    // would rewrite the file byte for byte to record nothing.
    if (entry.pending && agrees(entry.pending.ms, ms)) {
      entry.pending.seen++;
      if (entry.pending.seen >= CONTRADICTION_CONFIRMATIONS) {
        this.write(key, { ms, source, at: now });
        this.opts.log?.(`[ledger] ${key}: ${entry.ms}ms -> ${ms}ms (confirmed)`);
        return 'learned';
      }
      return 'pending';
    }
    entry.pending = { ms, seen: 1 };
    return 'pending';
  }

  /** Write whatever is buffered right now (app quit). */
  async flush(): Promise<void> {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    if (!this.dirty) return;
    this.dirty = false;
    const entries: Record<string, LedgerEntry> = {};
    for (const [key, entry] of this.entries) {
      // `pending` is in-session evidence, not knowledge: persisting it would let
      // one stale announce today plus one tomorrow overwrite a good value.
      entries[key] = { ms: entry.ms, source: entry.source, at: entry.at };
    }
    try {
      await mkdir(this.opts.cacheDir, { recursive: true });
      await writeFile(
        join(this.opts.cacheDir, FILE_NAME),
        JSON.stringify({ v: 1, entries }),
        'utf8',
      );
    } catch (err) {
      this.opts.log?.(`[ledger] write failed: ${String(err)}`);
    }
  }

  private write(key: string, entry: LedgerEntry): void {
    this.entries.delete(key); // re-insert last: Map order carries the LRU
    this.entries.set(key, entry);
    this.evict();
    this.dirty = true;
    this.scheduleFlush();
  }

  private evict(): void {
    while (this.entries.size > MAX_ENTRIES) {
      const oldest = this.entries.keys().next();
      if (oldest.done) return;
      this.entries.delete(oldest.value);
    }
  }

  private scheduleFlush(): void {
    const delay = this.opts.flushDelayMs ?? DEFAULT_FLUSH_DELAY_MS;
    if (this.flushTimer) return; // one pending write coalesces every change
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      void this.flush();
    }, delay);
    // A learned duration must never hold the app open at quit time.
    this.flushTimer.unref?.();
  }

  private now(): number {
    return (this.opts.nowFn ?? Date.now)();
  }
}
