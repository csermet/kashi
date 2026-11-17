/**
 * Pure logic for the kashi-server client: URL/cache-name normalization.
 * DOM/net-free so every rule is unit-tested. The document -> payload mapping
 * and its word->line quality gate moved to the shared view package
 * (@kashi/view/document, client review E.4).
 */

import type { ServerLyricsFound } from '@kashi/view/document';

export type ServerLyricsResult = ServerLyricsFound | { found: false } | { error: true };

export function normalizeServerUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim().replace(/(?<!\/)\/+$/, ''); // one scan per slash run
  if (!/^https?:\/\/[^\s]+$/.test(trimmed)) return null;
  return trimmed;
}

/** Path-safe cache file name (server ids are [A-Za-z0-9_-], but be defensive). */
export function cacheFileName(sourceType: string, sourceId: string): string {
  const safe = (v: string) => v.replace(/[^A-Za-z0-9_-]/g, (c) => `%${c.charCodeAt(0).toString(16)}`);
  return `${safe(sourceType)}_${safe(sourceId)}_v1.json`;
}
