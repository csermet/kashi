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
  | { found: true; source: 'lrclib'; sourceId: number; lines: LyricLine[] }
  | { found: false };

interface LrclibRecord {
  id: number;
  syncedLyrics: string | null;
  duration: number | null;
  instrumental?: boolean;
}

export interface LrclibClientOptions {
  cacheDir: string;
  userAgent?: string;
  fetchFn?: typeof fetch;
  nowFn?: () => number;
  negativeTtlMs?: number;
  baseUrl?: string;
  log?: (line: string) => void;
}

const NEGATIVE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const SEARCH_DURATION_TOLERANCE_S = 3;
const LAST_LINE_FALLBACK_MS = 5000;

type CacheEntry =
  | { found: true; sourceId: number; lines: LyricLine[] }
  | { found: false; at: number };

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
    if (cached) {
      if (cached.found) {
        return { found: true, source: 'lrclib', sourceId: cached.sourceId, lines: cached.lines };
      }
      if (this.now() - cached.at < (this.opts.negativeTtlMs ?? NEGATIVE_TTL_MS)) {
        return { found: false };
      }
    }

    // Fall through to search when the exact hit exists but carries no synced
    // lyrics (plain-only/instrumental records) — a synced variant may exist.
    const exact = await this.exactGet(query, signal);
    const record = exact?.syncedLyrics ? exact : await this.search(query, signal);

    if (!record?.syncedLyrics) {
      await this.writeCache(key, { found: false, at: this.now() });
      return { found: false };
    }

    const lines = parseLrc(record.syncedLyrics, query.duration_ms);
    if (lines.length === 0) {
      await this.writeCache(key, { found: false, at: this.now() });
      return { found: false };
    }

    await this.writeCache(key, { found: true, sourceId: record.id, lines });
    return { found: true, source: 'lrclib', sourceId: record.id, lines };
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
    if (!resp.ok) throw new Error(`lrclib /api/get failed: ${resp.status}`);
    return (await resp.json()) as LrclibRecord;
  }

  private async search(
    query: TrackQuery,
    signal?: AbortSignal,
  ): Promise<LrclibRecord | null> {
    const params = new URLSearchParams({
      track_name: query.title,
      artist_name: normalizeArtist(query.artist),
    });
    const resp = await this.request(`/api/search?${params}`, signal);
    if (!resp.ok) throw new Error(`lrclib /api/search failed: ${resp.status}`);
    const candidates = ((await resp.json()) as LrclibRecord[]).filter(
      (c) => c.syncedLyrics,
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

  private request(path: string, signal?: AbortSignal): Promise<Response> {
    const fetchFn = this.opts.fetchFn ?? fetch;
    return fetchFn(`${this.opts.baseUrl ?? 'https://lrclib.net'}${path}`, {
      signal: signal ?? null,
      headers: {
        'User-Agent':
          this.opts.userAgent ?? 'kashi/0.1.0 (+https://github.com/csermet/kashi)',
      },
    });
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

/** Strip YTM "Topic" channel suffix: "Rick Astley - Topic" → "Rick Astley". */
export function normalizeArtist(artist: string): string {
  return artist.replace(/\s*-\s*Topic\s*$/i, '').trim();
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
    const lastMatch = matches[matches.length - 1];
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
