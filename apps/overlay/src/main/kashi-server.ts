/**
 * kashi-server HTTP client (overlay main process).
 *
 * The server is OPTIONAL infrastructure: one attempt per track start with a
 * short timeout, disk cache with ETag revalidation, and every failure path
 * degrades silently (the caller falls back to lrclib). With no server
 * configured this module is never constructed — the code path stays
 * byte-for-byte the serverless behavior (plan R-F3-8).
 */
import { mkdir, readFile, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import {
  cacheFileName,
  mapDocument,
  type ServerLyricsResult,
} from './kashi-server-logic.js';

const REQUEST_TIMEOUT_MS = 5_000;
const ENQUEUE_TIMEOUT_MS = 10_000;

interface CacheEntry {
  etag: string | null;
  document: unknown;
}

export interface KashiServerClientOptions {
  baseUrl: string; // already normalized (normalizeServerUrl)
  apiKey: string;
  cacheDir: string;
  fetchFn: typeof fetch;
  log?: (line: string) => void;
}

export interface IngestSource {
  type: string;
  id: string;
}

export class KashiServerClient {
  private readonly log: (line: string) => void;

  constructor(private readonly opts: KashiServerClientOptions) {
    this.log = opts.log ?? (() => {});
  }

  private cachePath(sourceType: string, sourceId: string): string {
    return join(this.opts.cacheDir, cacheFileName(sourceType, sourceId));
  }

  private async readCache(path: string): Promise<CacheEntry | null> {
    try {
      return JSON.parse(await readFile(path, 'utf8')) as CacheEntry;
    } catch {
      return null;
    }
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return { Authorization: `Bearer ${this.opts.apiKey}`, ...extra };
  }

  /** ONE conditional GET per track start — no retry loop (lrclib covers misses). */
  async getProcessed(
    sourceType: string,
    sourceId: string,
    signal?: AbortSignal,
  ): Promise<ServerLyricsResult> {
    const path = this.cachePath(sourceType, sourceId);
    const cached = await this.readCache(path);

    const url =
      `${this.opts.baseUrl}/v1/lyrics/` +
      `${encodeURIComponent(sourceType)}/${encodeURIComponent(sourceId)}`;
    const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
    let response: Response;
    try {
      response = await this.opts.fetchFn(url, {
        headers: this.headers(cached?.etag ? { 'If-None-Match': `"${cached.etag}"` } : {}),
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      });
    } catch (err) {
      return this.staleOrError(cached, `unreachable (${String(err).slice(0, 120)})`);
    }

    if (response.status === 304 && cached) {
      const payload = mapDocument(cached.document);
      return payload ?? { error: true };
    }
    if (response.status === 404) {
      // The track is genuinely unprocessed — a stale cache entry must go too.
      await unlink(path).catch(() => {});
      return { found: false };
    }
    if (!response.ok) {
      return this.staleOrError(cached, `HTTP ${response.status}`);
    }

    let document: unknown;
    try {
      document = await response.json();
    } catch {
      return this.staleOrError(cached, 'invalid JSON body');
    }
    const payload = mapDocument(document);
    if (payload === null) {
      // Malformed document: do NOT cache it, and don't serve a stale one that
      // may be equally broken.
      this.log(`kashi-server: malformed document for ${sourceType}:${sourceId}`);
      return { error: true };
    }

    const etag = response.headers.get('etag')?.replaceAll('"', '').replace(/^W\//, '') ?? null;
    await mkdir(this.opts.cacheDir, { recursive: true }).catch(() => {});
    await writeFile(path, JSON.stringify({ etag, document } satisfies CacheEntry)).catch((err) =>
      this.log(`kashi-server: cache write failed (${String(err).slice(0, 120)})`),
    );
    return payload;
  }

  private staleOrError(cached: CacheEntry | null, reason: string): ServerLyricsResult {
    if (cached) {
      const payload = mapDocument(cached.document);
      if (payload) {
        this.log(`kashi-server: ${reason} — serving the cached document`);
        return payload;
      }
    }
    this.log(`kashi-server: ${reason} — falling back`);
    return { error: true };
  }

  /** Operator-approved contribute-back (Faz 5 P6). The server gates hard
   * (409 disabled / 422 not-publishable) — this only carries the intent. A
   * 202 reports the LEDGER state (queued/published/dry_run/failed), not a
   * blanket "accepted" (reviewer: honesty over optimism). */
  async requestPublish(source: IngestSource): Promise<string> {
    try {
      const response = await this.opts.fetchFn(`${this.opts.baseUrl}/v1/publish-requests`, {
        method: 'POST',
        headers: this.headers({ 'content-type': 'application/json' }),
        body: JSON.stringify({ source }),
        signal: AbortSignal.timeout(ENQUEUE_TIMEOUT_MS),
      });
      if (response.status === 202) {
        try {
          const body = (await response.json()) as { status?: unknown };
          return typeof body.status === 'string' ? body.status : 'accepted';
        } catch {
          return 'accepted';
        }
      }
      if (response.status === 409) return 'disabled';
      if (response.status === 422) return 'rejected';
      if (response.status === 404) return 'not_found';
      return 'error';
    } catch {
      return 'error';
    }
  }

  /** Fire-and-forget ingest; errors are logged, never surfaced (R-9 gate fires it). */
  async enqueue(source: IngestSource, hints: Record<string, unknown>): Promise<void> {
    try {
      const response = await this.opts.fetchFn(`${this.opts.baseUrl}/v1/ingest`, {
        method: 'POST',
        headers: this.headers({ 'content-type': 'application/json' }),
        body: JSON.stringify({ source, hints }),
        signal: AbortSignal.timeout(ENQUEUE_TIMEOUT_MS),
      });
      this.log(
        response.ok || response.status === 202
          ? `kashi-server: enqueued ${source.type}:${source.id}`
          : `kashi-server: enqueue rejected (HTTP ${response.status})`,
      );
    } catch (err) {
      this.log(`kashi-server: enqueue failed (${String(err).slice(0, 120)})`);
    }
  }
}
