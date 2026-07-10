/**
 * Pure logic for the kashi-server client: document -> IPC payload mapping,
 * the word->line quality gate, URL/cache-name normalization. DOM/net-free so
 * every rule is unit-tested.
 */

export interface ServerWord {
  start_ms: number;
  end_ms: number;
  text: string;
}

export interface ServerLine {
  start_ms: number;
  end_ms: number;
  text: string;
  words?: ServerWord[];
}

export interface ServerPalette {
  source?: string;
  primary?: string;
  secondary?: string;
  background?: string;
  text?: string;
  accent?: string;
}

export interface ServerBeats {
  bpm: number;
  confidence?: number;
  times_ms: number[];
  downbeat_indices?: number[];
}

/** What lookupLyrics forwards to the renderer for a server hit. */
export interface ServerLyricsFound {
  found: true;
  source: 'kashi-server';
  sync: 'word' | 'line';
  qualityScore: number;
  lines: ServerLine[];
  /** Passed through for Faz 4 (renderer ignores them until then). */
  palette?: ServerPalette;
  beats?: ServerBeats;
}

export type ServerLyricsResult = ServerLyricsFound | { found: false } | { error: true };

/**
 * Clients fall back to LINE rendering below this quality (schema contract).
 * The gate lives HERE, in main — the renderer stays dumb (plan R-F3-7).
 */
export const QUALITY_GATE = 0.5;

export function normalizeServerUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s]+$/.test(trimmed)) return null;
  return trimmed;
}

/** Path-safe cache file name (server ids are [A-Za-z0-9_-], but be defensive). */
export function cacheFileName(sourceType: string, sourceId: string): string {
  const safe = (v: string) => v.replace(/[^A-Za-z0-9_-]/g, (c) => `%${c.charCodeAt(0).toString(16)}`);
  return `${safe(sourceType)}_${safe(sourceId)}_v1.json`;
}

function isMs(v: unknown): v is number {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0;
}

/**
 * Map a processed-track.v1 document onto the IPC payload.
 * Returns null for anything malformed — the caller treats that as an error
 * and does NOT cache it (a bad document must not wedge a track forever).
 */
export function mapDocument(doc: unknown): ServerLyricsFound | null {
  if (typeof doc !== 'object' || doc === null) return null;
  const d = doc as Record<string, unknown>;
  if (d['schema_version'] !== 1) return null;
  const sync = d['sync'];
  if (sync !== 'word' && sync !== 'line') return null;
  const alignment = d['alignment'] as Record<string, unknown> | undefined;
  const quality = typeof alignment?.['quality_score'] === 'number' ? alignment['quality_score'] : 0;
  if (!Array.isArray(d['lines'])) return null;

  const lines: ServerLine[] = [];
  for (const raw of d['lines'] as unknown[]) {
    const line = raw as Record<string, unknown>;
    if (!isMs(line['start_ms']) || !isMs(line['end_ms']) || typeof line['text'] !== 'string') {
      return null;
    }
    const mapped: ServerLine = {
      start_ms: line['start_ms'],
      end_ms: line['end_ms'],
      text: line['text'],
    };
    if (Array.isArray(line['words']) && line['words'].length > 0) {
      const words: ServerWord[] = [];
      for (const rawWord of line['words'] as unknown[]) {
        const word = rawWord as Record<string, unknown>;
        if (!isMs(word['start_ms']) || !isMs(word['end_ms']) || typeof word['text'] !== 'string') {
          return null;
        }
        words.push({ start_ms: word['start_ms'], end_ms: word['end_ms'], text: word['text'] });
      }
      mapped.words = words;
    }
    lines.push(mapped);
  }

  let effectiveSync: 'word' | 'line' = sync;
  let effectiveLines = lines;
  if (sync === 'word' && quality < QUALITY_GATE) {
    // Low-confidence word timings read as jitter — degrade to line mode by
    // STRIPPING the words (never blended, single decision point).
    effectiveSync = 'line';
    effectiveLines = lines.map(({ words: _words, ...rest }) => rest);
  }

  const payload: ServerLyricsFound = {
    found: true,
    source: 'kashi-server',
    sync: effectiveSync,
    qualityScore: quality,
    lines: effectiveLines,
  };
  if (typeof d['palette'] === 'object' && d['palette'] !== null) {
    payload.palette = d['palette'] as ServerPalette;
  }
  if (typeof d['beats'] === 'object' && d['beats'] !== null) {
    payload.beats = d['beats'] as ServerBeats;
  }
  return payload;
}
