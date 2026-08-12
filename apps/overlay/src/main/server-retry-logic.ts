/**
 * Self-heal schedule for a server lookup that failed (Faz 6.7 P3).
 *
 * The server gets ONE conditional GET per track start — a deliberate rule, so
 * a slow server never delays lyrics. The cost showed up in the field: when
 * that attempt timed out, lrclib's plain text stayed on screen for the WHOLE
 * song even though the server answered fine seconds later, because nothing
 * ever asked again (the Danza Kuduro case). One attempt is the right rule for
 * the critical path and the wrong rule for the three minutes after it.
 *
 * So the ladder keeps its single blocking attempt and a background probe
 * retries on a widening schedule. Pure here; the timers live in the
 * orchestrator, where the track's AbortController already owns cancellation.
 */
import type { ServerLyricsResult } from './kashi-server-logic.js';

/**
 * Widening, and it ends. Five attempts span about three minutes — roughly a
 * song — after which either the server is genuinely down or this track is
 * nearly over, and both mean the same thing: stop asking.
 */
export const RETRY_DELAYS_MS = [10_000, 20_000, 40_000, 60_000, 60_000] as const;

/** Delay before attempt N (0-based), or null once the schedule is spent. */
export function retryDelayMs(attempt: number): number | null {
  return RETRY_DELAYS_MS[attempt] ?? null;
}

/** What the user is currently reading, as far as the upgrade rule cares. */
export interface DisplayedLyrics {
  source: 'kashi-server' | 'lrclib' | 'none';
  sync: 'word' | 'line';
  qualityScore?: number;
  /**
   * Which enrichment blocks the displayed document actually carries.
   *
   * Quality score alone cannot answer "is this one richer?": a reprocess that
   * adds effects without touching alignment leaves the number identical, so a
   * server-to-server comparison would decline the upgrade and the user would
   * keep an unthemed document all song.
   */
  enrichment?: readonly string[];
}

/** The enrichment blocks a result brings, as a stable set of names. */
export function enrichmentKeys(incoming: ServerLyricsResult): string[] {
  if (!('found' in incoming) || !incoming.found) return [];
  const keys: string[] = [];
  if (incoming.palette) keys.push('palette');
  if (incoming.beats) keys.push('beats');
  if (incoming.fx) keys.push('fx');
  if (incoming.fx?.select) keys.push('fx.select');
  if (incoming.energy) keys.push('energy');
  if (incoming.sections?.length) keys.push('sections');
  if (incoming.alignment) keys.push('alignment');
  return keys.sort();
}

/**
 * Should a late server document replace what is already on screen?
 *
 * Swapping lyrics mid-song is a visible event, so it has to buy something.
 * Word timing over line timing always does — that is the whole point of the
 * server. Past that the bar is "measurably better", never "different":
 * re-rendering the same lines at the same quality is pure flicker.
 */
export function shouldUpgrade(current: DisplayedLyrics, incoming: ServerLyricsResult): boolean {
  if (!('found' in incoming) || !incoming.found) return false;
  if (current.source === 'none') return true;
  if (incoming.sync === 'word' && current.sync === 'line') return true;
  if (incoming.sync === 'line' && current.sync === 'word') return false;
  // Line over line looks like a wash on the text alone — but a server
  // document also carries the palette, beats, fx tags, energy curve, sections
  // and the nightcore speed factor, none of which lrclib has. Losing all of
  // that is not "no difference" at hype level, so the swap is worth it when
  // the enrichment is actually there, and declined when it is not.
  if (current.source === 'lrclib') return carriesEnrichment(incoming);
  // Server over server. This used to compare quality scores and refuse a
  // lower one — reasonable-looking, and wrong: the score ranks real accuracy
  // at Spearman +0.24 (measured 2026-08-12), so a reprocessed document with
  // BETTER timings and a lower score was pinned out for the whole song.
  // The trustworthy signal is already in the transport: every request
  // revalidates with If-None-Match, so a `fresh` result means the server has
  // deliberately produced a different document than this client knew — the
  // server is the sole author of its documents, and its newest version is
  // authoritative regardless of what the score thinks of it. A 304 (not
  // fresh) is byte-identical to what the cache held, and re-rendering an
  // identical document is the flicker this rule exists to prevent. A stale
  // fallback is the cache itself — never an upgrade.
  if (current.source !== 'kashi-server') return false;
  return incoming.fresh === true;
}

/** Does this document bring anything lrclib cannot? */
function carriesEnrichment(doc: {
  palette?: unknown;
  beats?: unknown;
  fx?: unknown;
  energy?: unknown;
  sections?: unknown;
  alignment?: unknown;
}): boolean {
  return Boolean(doc.palette ?? doc.beats ?? doc.fx ?? doc.energy ?? doc.sections ?? doc.alignment);
}

/** Only an error is worth asking again about — a 404 is an answer. */
export function isRetryable(result: ServerLyricsResult): boolean {
  return 'error' in result;
}
