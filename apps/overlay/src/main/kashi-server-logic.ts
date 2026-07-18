/**
 * Pure logic for the kashi-server client: document -> IPC payload mapping,
 * the word->line quality gate, URL/cache-name normalization. DOM/net-free so
 * every rule is unit-tested.
 */

import type {
  BeatsData,
  EnergyData,
  FxData,
  FxLineTag,
  FxWordTag,
  LyricLine,
  PaletteData,
  SectionData,
  WordTiming,
} from '../shared/lyrics.js';

// One source of truth for these shapes: src/shared/lyrics.ts (schema-drift
// guarded). The Server* names stay as aliases for the existing call sites.
export type ServerWord = WordTiming;
export type ServerLine = LyricLine;
export type ServerPalette = PaletteData;
export type ServerBeats = BeatsData;

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
  /** Semantic effect tags (server 2.6.0+, Faz 6) — hype level consumes them. */
  fx?: FxData;
  /** Track-normalized loudness curve + energy-derived sections (2.6.0+). */
  energy?: EnergyData;
  sections?: SectionData[];
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
    if (line['adlib'] === true) mapped.adlib = true;
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
  const fx = mapFx(d['fx'], effectiveSync);
  if (fx) payload.fx = fx;
  const energy = mapEnergy(d['energy']);
  if (energy) payload.energy = energy;
  const sections = mapSections(d['sections']);
  if (sections) payload.sections = sections;
  return payload;
}

/** Tolerant energy parse — enrichment like beats; bad shape → absent. */
export function mapEnergy(raw: unknown): EnergyData | undefined {
  if (typeof raw !== 'object' || raw === null) return undefined;
  const e = raw as Record<string, unknown>;
  const rate = e['rate_hz'];
  if (!Number.isInteger(rate) || (rate as number) < 1 || (rate as number) > 50) return undefined;
  if (!Array.isArray(e['values']) || e['values'].length === 0) return undefined;
  const values: number[] = [];
  for (const v of e['values'] as unknown[]) {
    if (typeof v !== 'number' || !Number.isFinite(v)) return undefined;
    values.push(Math.min(100, Math.max(0, Math.round(v))));
  }
  return { rate_hz: rate as number, values };
}

/** Tolerant sections parse — bad ENTRIES drop, empty → absent. */
export function mapSections(raw: unknown): SectionData[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: SectionData[] = [];
  for (const entry of raw as unknown[]) {
    if (out.length >= 64) break;
    const s = entry as Record<string, unknown>;
    if (
      typeof s === 'object' &&
      s !== null &&
      typeof s['type'] === 'string' &&
      s['type'] !== '' &&
      Number.isInteger(s['start_ms']) &&
      Number.isInteger(s['end_ms']) &&
      (s['start_ms'] as number) >= 0 &&
      (s['end_ms'] as number) > (s['start_ms'] as number)
    ) {
      out.push({ type: s['type'], start_ms: s['start_ms'] as number, end_ms: s['end_ms'] as number });
    }
  }
  return out.length > 0 ? out : undefined;
}

/**
 * Tolerant fx parse (Faz 6): bad ENTRIES are dropped, never the document —
 * fx is enrichment exactly like palette/beats. Word tags are meaningless
 * after the quality gate stripped words (line mode), so they are only kept
 * on word-sync payloads; line theme tags survive either way. Defensive caps
 * mirror the server's own (60 words / 24 lines) against a hostile server.
 */
export function mapFx(raw: unknown, sync: 'word' | 'line'): FxData | undefined {
  if (typeof raw !== 'object' || raw === null) return undefined;
  const f = raw as Record<string, unknown>;
  if (typeof f['lexicon'] !== 'string' || typeof f['engine'] !== 'string') return undefined;
  const out: FxData = { lexicon: f['lexicon'], engine: f['engine'] };
  if (sync === 'word' && Array.isArray(f['words'])) {
    const words: FxWordTag[] = [];
    for (const raw of f['words'] as unknown[]) {
      if (words.length >= 60) break;
      const t = raw as Record<string, unknown>;
      if (
        Number.isInteger(t['line']) &&
        Number.isInteger(t['word']) &&
        (t['line'] as number) >= 0 &&
        (t['word'] as number) >= 0 &&
        typeof t['tag'] === 'string' &&
        t['tag'] !== '' &&
        typeof t['intensity'] === 'number' &&
        Number.isFinite(t['intensity'])
      ) {
        words.push({
          line: t['line'] as number,
          word: t['word'] as number,
          tag: t['tag'],
          intensity: Math.min(1, Math.max(0, t['intensity'])),
        });
      }
    }
    if (words.length > 0) out.words = words;
  }
  if (Array.isArray(f['lines'])) {
    const lineTags: FxLineTag[] = [];
    for (const raw of f['lines'] as unknown[]) {
      if (lineTags.length >= 24) break;
      const t = raw as Record<string, unknown>;
      if (
        Number.isInteger(t['line']) &&
        (t['line'] as number) >= 0 &&
        typeof t['tag'] === 'string' &&
        t['tag'] !== ''
      ) {
        lineTags.push({ line: t['line'] as number, tag: t['tag'] });
      }
    }
    if (lineTags.length > 0) out.lines = lineTags;
  }
  return out.words || out.lines ? out : undefined;
}
