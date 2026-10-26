/**
 * LRCLIB client (plan R-5 etiquette):
 *  - meaningful User-Agent
 *  - positive cache: forever (records are effectively immutable for our use)
 *  - negative cache: 7 days (don't re-ask on every listen)
 *  - in-flight coalescing: one request per track no matter how many callers
 *  - exact /api/get first, /api/search fallback (best duration match)
 *  - "Artist - Topic" → "Artist" normalization (YTM Topic channels)
 *
 * Aborted lookups are NOT negative-cached — only a genuine miss is.
 *
 * Known trade-off: in-flight coalescing shares ONE request among concurrent
 * callers, bound to the FIRST caller's AbortSignal — if that caller aborts,
 * sharers reject too. Acceptable for the overlay's single-caller wiring; add
 * refcounting before introducing a second caller.
 */
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { KASHI_VERSION } from '../shared/version.js';
import type { SourceCooldown } from './source-cooldown.js';

export interface TrackQuery {
  title: string;
  artist: string;
  album?: string;
  duration_ms?: number;
}

export interface LyricLine {
  start_ms: number;
  end_ms: number;
  text: string;
}

export type LyricsResult =
  | {
      found: true;
      source: 'lrclib';
      sourceId: number;
      lines: LyricLine[];
      /**
       * The MATCHED RECORD's own duration, in ms — the only evidence we get
       * about which EDIT of the song these stamps were written against.
       *
       * Field bug (Caner, 2026-08-13): "sözler inanılmaz kaymış, sanki başka
       * şarkı ile karışıyor". It is another edit: nightcore, sped-up, extended,
       * a live take. The duration filter was the ONLY thing standing between
       * the screen and a foreign clock — and the duration-less retry below
       * removes exactly that filter. Carrying the record's duration up is what
       * lets the caller notice. null = lrclib had none to give.
       */
      recordDurationMs: number | null;
    }
  | { found: false };

/**
 * lrclib could not answer — as opposed to answering "not found".
 *
 * Field outage (2026-10-15/16): lrclib re-tuned its edge rate limiter and
 * answered 429 and 503 at a few requests per HOUR, interleaved with 200s. A
 * bare Error made every one of those look like a dead network, the ladder gave
 * up after 8 s, and the song played out with an error on screen. The kind and
 * the server's own wait hint are what let a caller come back at the right
 * moment instead of never.
 *
 *  - busy:    429 (rate limited) or 503 (ServerOverloaded). lrclib's docs say
 *             a client MUST honour Retry-After or risk a temporary ban.
 *  - network: timeout, DNS, refused connection, any other non-OK status.
 */
export class LrclibUnavailableError extends Error {
  constructor(
    readonly kind: 'busy' | 'network',
    /** The server's Retry-After, in ms from now; null when it sent none. */
    readonly retryAfterMs: number | null,
    message: string,
  ) {
    super(message);
    this.name = 'LrclibUnavailableError';
  }
}

/**
 * Retry-After as ms from `nowMs`: delta-seconds or an HTTP-date. Garbage and
 * dates in the past give null — a wait we cannot read is no wait at all, and
 * the caller's own back-off still applies.
 */
export function parseRetryAfter(value: string | null, nowMs: number): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Math.min(Number(trimmed) * 1000, MAX_RETRY_AFTER_MS);
  const at = Date.parse(trimmed);
  if (Number.isNaN(at) || at <= nowMs) return null;
  return Math.min(at - nowMs, MAX_RETRY_AFTER_MS);
}

/**
 * A ceiling on any wait a server can impose. Past 2^31 ms a timer no longer
 * fits and Node fires it after 1 ms — an absurd Retry-After would turn into a
 * busy loop. An hour is longer than any real lrclib hint and still recovers
 * within a listening session.
 */
export const MAX_RETRY_AFTER_MS = 60 * 60 * 1000;

/** 400/422: the request itself is malformed (e.g. a 61-minute mix's duration).
 * Asking again cannot help, so it is a miss — the server's twin
 * (_PERMANENT_HTTP_STATUS in pipeline/lrclib.py) decides the same way. */
const BAD_REQUEST_STATUS = new Set([400, 422]);

interface LrclibRecord {
  id: number;
  syncedLyrics: string | null;
  duration: number | null;
  instrumental?: boolean;
}

/**
 * Kashi's registered LRCLIB application id (client-side model, registered by
 * the owner on 2026-10-26). Public by design: lrclib's docs call it "not a
 * secret, safe to ship in a client". Sent to lrclib.net only, never to a
 * mirror. Twin of LRCLIB_APP_ID in the server's pipeline/lrclib.py.
 */
export const LRCLIB_APP_ID = 'yLeWPTPmzzUBKKvZroldkf_vtRR2fCDuKJzP6jMhKg8';

export interface LrclibClientOptions {
  cacheDir: string;
  userAgent?: string;
  /**
   * `Lrclib-App-Id` to send (lrclib.net only). If lrclib answers 401 to it
   * (revoked or unknown), the client drops it for the rest of the process,
   * says so once, and repeats that request without it: anonymous access keeps
   * working.
   */
  appId?: string;
  fetchFn?: typeof fetch;
  nowFn?: () => number;
  negativeTtlMs?: number;
  /** Per-request timeout (exact-get and search each get their own budget). */
  requestTimeoutMs?: number;
  baseUrl?: string;
  log?: (line: string) => void;
  /**
   * Back-off shared by every lookup of this source. Consulted only when the
   * disk cache has no answer: cached lyrics stay instant through an outage,
   * and only a response from the network clears it.
   */
  cooldown?: SourceCooldown;
}

const NEGATIVE_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Which lookup ladder produced a MISS. Bump it whenever a rung is added,
 * removed or reordered.
 *
 * Field bug (Caner, 2026-08-13): the multi-artist rung shipped in 0.27.3 and
 * changed nothing for Mad Love — the very track it was built from. The log gave
 * it away by its timing: server 404, then "duration-scoped lookup missed" 9 ms
 * later and "no synced lyrics" 6 ms after that. Nothing reaches lrclib in 9 ms.
 * The track had failed the day before, the miss was cached for seven days, and
 * the new rung never ran.
 *
 * So a negative is only honoured while it describes a ladder that still exists.
 * Positives are deliberately NOT versioned: an lrclib record is immutable for
 * our purposes (the header contract), so a better ladder cannot improve an
 * answer that was already right, and re-fetching every stored hit on each bump
 * would be a lot of traffic for a service that asks us to be gentle.
 *
 * An entry with no version predates this rule and counts as stale — which is
 * exactly the state the 0.27.3 rung needs to escape.
 *
 * 3: degenerate records are skipped and the ladder moves on (LyricFix P4).
 */
const LOOKUP_LADDER_VERSION = 3;
const REQUEST_TIMEOUT_MS = 12_000; // field data: cold lrclib responses can exceed 8s
const SEARCH_DURATION_TOLERANCE_S = 3;
const LAST_LINE_FALLBACK_MS = 5000;

/**
 * Degenerate-record gate (LyricFix P4). lrclib records can rot: id 1009665
 * "Never Gonna Give You Up" is now the single line "probe", and a "Party Rock
 * Anthem" record is one "day day day" line. Both parse as synced lyrics. The
 * server archive (339 documents, 2026-10-22): stamped lines p2 = 20; words per
 * minute p2 = 22.7, p5 = 37.4, so these floors sit far below any real song.
 * Twin of is_degenerate_synced in the server's pipeline/lrclib.py: keep the
 * numbers identical.
 */
const MIN_SYNCED_LINES = 4;
const MIN_WORDS_PER_MINUTE = 8;
/** Below a minute the rate is too noisy to judge (a short intro edit). */
const WORDS_PER_MINUTE_MIN_DURATION_S = 60;
const WORD = /[\p{L}\p{N}_]+/gu;
const LRC_STAMP = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;

/**
 * True for stamped lyric texts too thin to be a song: fewer than
 * MIN_SYNCED_LINES lines, or (a record of a minute or more) fewer than
 * MIN_WORDS_PER_MINUTE words per minute of the record's own duration.
 */
export function isDegenerateLines(texts: string[], durationS: number | null): boolean {
  if (texts.length < MIN_SYNCED_LINES) return true;
  if (durationS === null || durationS < WORDS_PER_MINUTE_MIN_DURATION_S) return false;
  const words = texts.reduce((sum, text) => sum + (text.match(WORD)?.length ?? 0), 0);
  return words / (durationS / 60) < MIN_WORDS_PER_MINUTE;
}

/** The gate on a raw LRC text: one entry per stamped line that carries text,
 * however many stamps it repeats (the server counts lines the same way). */
export function isDegenerateSynced(lrc: string, durationS: number | null): boolean {
  const texts: string[] = [];
  for (const rawLine of lrc.split(/\r?\n/)) {
    const matches = [...rawLine.matchAll(LRC_STAMP)];
    const last = matches.at(-1);
    if (!last) continue;
    const text = rawLine.slice((last.index ?? 0) + last[0].length).trim();
    if (text) texts.push(text);
  }
  return isDegenerateLines(texts, durationS);
}

/** Records a lookup threw away as degenerate. A miss that only had those is
 * not cached: lrclib may repair the record any day. */
interface LadderNotes {
  rejected: number;
}

type CacheEntry =
  | { found: true; sourceId: number; lines: LyricLine[]; recordDurationMs?: number | null }
  | { found: false; at: number; ladder?: number };

export class LrclibClient {
  private readonly inFlight = new Map<string, Promise<LyricsResult>>();

  constructor(private readonly opts: LrclibClientOptions) {}

  async getLyrics(query: TrackQuery, signal?: AbortSignal): Promise<LyricsResult> {
    const key = this.cacheKey(query);
    const existing = this.inFlight.get(key);
    if (existing) return existing;

    const promise = this.lookup(query, key, signal).finally(() => {
      this.inFlight.delete(key);
    });
    this.inFlight.set(key, promise);
    return promise;
  }

  private async lookup(
    query: TrackQuery,
    key: string,
    signal?: AbortSignal,
  ): Promise<LyricsResult> {
    const cached = await this.readCache(key);
    const answer = cached ? this.cachedAnswer(cached) : null;
    if (answer) return answer;
    const { cooldown } = this.opts;
    const wait = cooldown?.remainingMs() ?? 0;
    if (wait > 0) {
      // No request: this source said "busy" moments ago, and asking again
      // before the wait it named is what earns a ban.
      throw new LrclibUnavailableError('busy', wait, `cooling down for ${wait} ms`);
    }
    let record: LrclibRecord | null;
    const notes: LadderNotes = { rejected: 0 };
    try {
      record = await this.findRecord(query, signal, notes);
    } catch (err) {
      // Only a real outage counts: an abort (ours, or a coalesced caller's)
      // says nothing about the source.
      if (err instanceof LrclibUnavailableError) cooldown?.noteFailure(err.retryAfterMs);
      throw err;
    }
    cooldown?.noteSuccess();
    return this.settle(key, query, record, notes);
  }

  /** What a cache entry answers on its own: a hit, or a negative entry that is
   * still fresh for THIS ladder. null = go ask lrclib. */
  private cachedAnswer(cached: CacheEntry): LyricsResult | null {
    if (cached.found) {
      // Positives are kept forever, so one stored before the record gate
      // existed would show "probe" forever: a degenerate one is asked again.
      const durationS = cached.recordDurationMs == null ? null : cached.recordDurationMs / 1000;
      if (isDegenerateLines(cached.lines.map((line) => line.text), durationS)) return null;
      return {
        found: true,
        source: 'lrclib',
        sourceId: cached.sourceId,
        lines: cached.lines,
        // Entries written before the edit check shipped carry no duration.
        // undefined -> null: "lrclib gave none" and "we never stored one"
        // are the same thing to the caller, and both mean unverifiable.
        recordDurationMs: cached.recordDurationMs ?? null,
      };
    }
    if (
      cached.ladder === LOOKUP_LADDER_VERSION &&
      this.now() - cached.at < (this.opts.negativeTtlMs ?? NEGATIVE_TTL_MS)
    ) {
      return { found: false };
    }
    return null;
  }

  /** The lookup ladder: exact get, search, then the primary-artist rung. */
  private async findRecord(
    query: TrackQuery,
    signal: AbortSignal | undefined,
    notes: LadderNotes,
  ): Promise<LrclibRecord | null> {
    // Fall through to search when the exact hit exists but carries no usable
    // synced lyrics (plain-only, instrumental, degenerate): a real synced
    // variant may exist.
    const exact = await this.exactGet(query, signal);
    const record = this.usable(exact, notes) ? exact : await this.search(query, signal, notes);
    if (record?.syncedLyrics) return record;

    // Multi-artist rung. Runs ONLY when the joined string actually splits, and
    // keeps the duration filter — so it can recover a track the conjunction
    // hid without opening the door the duration-less retry opens (another
    // edit's stamps). The primary artist is enough: lrclib credits it either
    // alone or first.
    const [primary] = splitArtists(normalizeArtist(query.artist));
    if (!primary) return record;
    const scoped = { ...query, artist: primary };
    const retry = await this.exactGet(scoped, signal);
    const rescued = this.usable(retry, notes) ? retry : await this.search(scoped, signal, notes);
    if (rescued?.syncedLyrics) {
      this.opts.log?.(`[lrclib] "${query.artist}" missed; "${primary}" hit`);
    }
    return rescued;
  }

  /** A record whose synced lyrics the ladder may use; notes the rejects. */
  private usable(record: LrclibRecord | null, notes: LadderNotes): record is LrclibRecord {
    if (!record?.syncedLyrics) return false;
    if (isDegenerateSynced(record.syncedLyrics, record.duration)) {
      notes.rejected += 1;
      return false;
    }
    return true;
  }

  /** Parse the record and cache the verdict, except a miss made of rejects. */
  private async settle(
    key: string,
    query: TrackQuery,
    record: LrclibRecord | null,
    notes: LadderNotes,
  ): Promise<LyricsResult> {
    if (!record?.syncedLyrics) {
      if (notes.rejected > 0) {
        this.opts.log?.(
          `[lrclib] "${query.artist} - ${query.title}": only degenerate records (${notes.rejected}); miss not cached`,
        );
        return { found: false };
      }
      await this.writeCache(key, { found: false, at: this.now(), ladder: LOOKUP_LADDER_VERSION });
      return { found: false };
    }

    const lines = parseLrc(record.syncedLyrics, query.duration_ms);
    if (lines.length === 0) {
      await this.writeCache(key, { found: false, at: this.now(), ladder: LOOKUP_LADDER_VERSION });
      return { found: false };
    }

    const recordDurationMs = record.duration == null ? null : Math.round(record.duration * 1000);
    await this.writeCache(key, { found: true, sourceId: record.id, lines, recordDurationMs });
    return { found: true, source: 'lrclib', sourceId: record.id, lines, recordDurationMs };
  }

  private async exactGet(
    query: TrackQuery,
    signal?: AbortSignal,
  ): Promise<LrclibRecord | null> {
    const params = new URLSearchParams({
      track_name: query.title,
      artist_name: normalizeArtist(query.artist),
    });
    if (query.album) params.set('album_name', query.album);
    if (query.duration_ms) {
      params.set('duration', String(Math.round(query.duration_ms / 1000)));
    }

    const resp = await this.request(`/api/get?${params}`, signal);
    if (resp.status === 404) return null;
    if (BAD_REQUEST_STATUS.has(resp.status)) return null;
    if (!resp.ok) throw this.unavailable(resp, '/api/get');
    return (await resp.json()) as LrclibRecord;
  }

  private async search(
    query: TrackQuery,
    signal: AbortSignal | undefined,
    notes: LadderNotes,
  ): Promise<LrclibRecord | null> {
    const params = new URLSearchParams({
      track_name: query.title,
      artist_name: normalizeArtist(query.artist),
    });
    const resp = await this.request(`/api/search?${params}`, signal);
    if (BAD_REQUEST_STATUS.has(resp.status)) return null;
    if (!resp.ok) throw this.unavailable(resp, '/api/search');
    const candidates = ((await resp.json()) as LrclibRecord[]).filter((c) =>
      this.usable(c, notes),
    );
    if (candidates.length === 0) return null;

    const wantedS = query.duration_ms ? query.duration_ms / 1000 : null;
    if (wantedS === null) return candidates[0] ?? null;

    let best: LrclibRecord | null = null;
    let bestDiff = Number.POSITIVE_INFINITY;
    for (const candidate of candidates) {
      if (candidate.duration == null) continue;
      const diff = Math.abs(candidate.duration - wantedS);
      if (diff < bestDiff) {
        best = candidate;
        bestDiff = diff;
      }
    }
    return bestDiff <= SEARCH_DURATION_TOLERANCE_S ? best : null;
  }

  /** Set once lrclib rejected the app id: anonymous from then on. */
  private appIdRejected = false;

  private async request(path: string, signal?: AbortSignal): Promise<Response> {
    const appId = this.appIdRejected ? undefined : this.opts.appId;
    const resp = await this.send(path, appId, signal);
    if (resp.status === 401 && appId !== undefined) {
      this.appIdRejected = true;
      this.opts.log?.('[lrclib] app id rejected (401) — continuing without it');
      return this.send(path, undefined, signal);
    }
    return resp;
  }

  private async send(path: string, appId: string | undefined, signal?: AbortSignal): Promise<Response> {
    const fetchFn = this.opts.fetchFn ?? fetch;
    // Each request gets its own timeout budget — a single slow lrclib response
    // must not eat the whole lookup (exact-get + search are sequential).
    const timeout = AbortSignal.timeout(this.opts.requestTimeoutMs ?? REQUEST_TIMEOUT_MS);
    const headers: Record<string, string> = {
      'User-Agent':
        this.opts.userAgent ?? `kashi/${KASHI_VERSION} (+https://github.com/csermet/kashi)`,
    };
    if (appId !== undefined) headers['Lrclib-App-Id'] = appId;
    try {
      return await fetchFn(`${this.opts.baseUrl ?? 'https://lrclib.net'}${path}`, {
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
        headers,
      });
    } catch (err) {
      // The CALLER's abort is a cancellation, not an outage: it must stay
      // recognisable so nothing backs off or retries on its account.
      if (signal?.aborted) throw err;
      throw new LrclibUnavailableError('network', null, `lrclib ${path} unreachable: ${String(err)}`);
    }
  }

  private unavailable(resp: Response, endpoint: string): LrclibUnavailableError {
    const busy = resp.status === 429 || resp.status === 503;
    return new LrclibUnavailableError(
      busy ? 'busy' : 'network',
      parseRetryAfter(resp.headers.get('Retry-After'), this.now()),
      `lrclib ${endpoint} failed: ${resp.status}`,
    );
  }

  private cacheKey(query: TrackQuery): string {
    const durationS = query.duration_ms ? Math.round(query.duration_ms / 1000) : '';
    const raw = [
      normalizeArtist(query.artist).toLowerCase(),
      query.title.toLowerCase(),
      query.album?.toLowerCase() ?? '',
      durationS,
    ].join('|');
    return createHash('sha256').update(raw).digest('hex').slice(0, 24);
  }

  private async readCache(key: string): Promise<CacheEntry | null> {
    try {
      const raw = await readFile(join(this.opts.cacheDir, `${key}.json`), 'utf8');
      return JSON.parse(raw) as CacheEntry;
    } catch {
      return null;
    }
  }

  private async writeCache(key: string, entry: CacheEntry): Promise<void> {
    try {
      await mkdir(this.opts.cacheDir, { recursive: true });
      await writeFile(
        join(this.opts.cacheDir, `${key}.json`),
        JSON.stringify(entry),
        'utf8',
      );
    } catch (err) {
      this.opts.log?.(`[lrclib] cache write failed: ${String(err)}`);
    }
  }

  private now(): number {
    return (this.opts.nowFn ?? Date.now)();
  }
}

/**
 * Strip YTM "Topic" channel suffix: "Rick Astley - Topic" → "Rick Astley".
 *
 * Twin of the server's _TOPIC_SUFFIX (lrclib.py). The (?<!\s) lookbehind lets
 * a match start only at the beginning of a whitespace run: the same matches
 * (fuzz-verified), but a long run that never reaches "Topic" is scanned once
 * instead of once per position — without it 40 000 spaces took 3.5 s on the
 * main process.
 */
const TOPIC_SUFFIX = /(?<!\s)\s*-\s*Topic\s*$/i;

export function normalizeArtist(artist: string): string {
  return artist.replace(TOPIC_SUFFIX, '').trim();
}

/**
 * YTM joins collaborators with a LOCALE conjunction — " ve " on a Turkish UI,
 * " & " / " and " elsewhere — plus commas and feat./ft./x credits. lrclib
 * credits either the primary artist alone or a differently-joined list, so the
 * joined string matches NOTHING structurally.
 *
 * Measured on 2026-08-13, against lrclib itself:
 *   "Sean Paul ve David Guetta" + Mad Love  ->  0 records
 *   "Sean Paul & David Guetta"  + Mad Love  -> 20 records, 19 synced, exact
 *   "Jim Yosef ve Scarlett"     + Carousel  ->  0 records
 *   "Jim Yosef"                 + Carousel  ->  4 records, all synced, exact
 *
 * The pipeline has had `split_artists` since Faz 4 (pipeline/lrclib.py), which
 * is why these tracks resolve once the SERVER processes them — but the overlay
 * fallback is what fills the screen on first listen, and it never learned.
 *
 * Returns [] for a single-artist string: the caller gates an extra request on
 * "there is actually something to split", so an ordinary track costs nothing.
 */
// Twin of the server's _ARTIST_SEPARATORS (lrclib.py) — keep them identical.
// The lookbehinds only let a match start at the beginning of a whitespace run:
// same splits (fuzz-verified), one scan per run instead of one per position.
const ARTIST_SEPARATORS = /(?<!\s)\s+(?:ve|and|x|feat\.?|ft\.?|&)\s+|(?:(?<!\s)\s+)?,\s*/i;

export function splitArtists(artist: string): string[] {
  const parts = artist
    .split(ARTIST_SEPARATORS)
    .map((p) => p.trim())
    .filter(Boolean);
  return parts.length > 1 ? parts : [];
}

/**
 * Parse LRC text into ms-integer lines. Supports multiple timestamps per line
 * ("[00:12.34][01:02.50]text"). end_ms = next line's start; the last line gets
 * min(start + 5 s, track duration) when the duration is known.
 *
 * Known limitation: "[hh:mm:ss]" (3-field hour format, seen on 1 h+ mixes) is
 * read as mm:ss:frac. Rare enough in LRCLIB data that we accept it for now.
 */
export function parseLrc(lrc: string, durationMs?: number): LyricLine[] {
  const stamped: Array<{ start_ms: number; text: string }> = [];
  const timestamp = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;

  for (const rawLine of lrc.split(/\r?\n/)) {
    const matches = [...rawLine.matchAll(timestamp)];
    if (matches.length === 0) continue;
    const lastMatch = matches.at(-1);
    if (!lastMatch) continue;
    const text = rawLine.slice((lastMatch.index ?? 0) + lastMatch[0].length).trim();
    if (!text) continue;

    for (const m of matches) {
      const minutes = Number(m[1]);
      const seconds = Number(m[2]);
      const fractionRaw = m[3] ?? '0';
      const fractionMs = Math.round(Number(`0.${fractionRaw}`) * 1000);
      stamped.push({ start_ms: (minutes * 60 + seconds) * 1000 + fractionMs, text });
    }
  }

  stamped.sort((a, b) => a.start_ms - b.start_ms);

  return stamped.map((line, i) => {
    const next = stamped[i + 1];
    let end_ms = next
      ? next.start_ms
      : line.start_ms + LAST_LINE_FALLBACK_MS;
    // Clamp the last line to track duration — but only when that leaves the
    // line visible (album-version LRC can outlast a shorter YTM edit).
    if (!next && durationMs && durationMs > line.start_ms) {
      end_ms = Math.min(end_ms, durationMs);
    }
    end_ms = Math.max(end_ms, line.start_ms); // guard degenerate data
    return { start_ms: line.start_ms, end_ms, text: line.text };
  });
}
