/**
 * Pure settings logic — no Electron/fs imports so every rule is unit-testable.
 * Persistence lives in settings.ts; this file owns validation and math.
 */
import {
  DEFAULT_EFFECT_LEVEL,
  DEFAULT_THEME_SCOPE,
  parseEffectLevel,
  parseThemeScope,
  type EffectLevel,
  type ThemeScope,
} from '../shared/effect-level.js';
import { normalizeServerUrl } from './kashi-server-logic.js';
import { DEFAULT_BOX_SCALE, parseBoxScale, type BoxScale } from '../shared/box-zone.js';
import { DEFAULT_TEXT_SCALE, parseTextScale, type TextScale } from '../shared/text-scale.js';
import {
  BASE_LEAD_MS,
  DEFAULT_TIMING_OFFSET_MS,
  appliedLeadMs,
  clampTimingOffset,
  formatTimingOffset,
} from '../shared/timing-offset.js';

export { DEFAULT_TEXT_SCALE, TEXT_SCALES, TEXT_SCALE_FACTORS, parseTextScale } from '../shared/text-scale.js';
export type { TextScale } from '../shared/text-scale.js';

export { DEFAULT_BOX_SCALE, parseBoxScale } from '../shared/box-zone.js';

export {
  BASE_LEAD_MS,
  DEFAULT_TIMING_OFFSET_MS,
  TIMING_OFFSET_MAX_ABS,
  appliedLeadMs,
  clampTimingOffset,
  formatTimingOffset,
} from '../shared/timing-offset.js';

export const OPACITY_PRESETS = [0, 0.1, 0.2, 0.4, 0.6, 0.8, 0.9] as const;
export const OPACITY_MIN = 0;
// 0.9 keeps a hint of see-through at full dark (field turu 3: "komple siyah
// olmasın ama daha koyu bir seçenek olmalı" — ladder is the user's own).
export const OPACITY_MAX = 0.9;
/** One Ctrl+scroll notch. */
export const OPACITY_STEP = 0.02;
export const DEFAULT_BOX_ALPHA = 0.1;

/**
 * The user's lyric timing offset, ms on top of the invisible BASE_LEAD_MS
 * (shared/timing-offset.ts): positive = lyrics fire EARLIER (karaoke UX
 * convention — perception lags the ear by ~100 ms; Caner: "hafif geç
 * hissettiriyor", 2026-07-12). 0 = the base alone. Applied in the renderer
 * frame loop; per-song data never changes.
 */
export const TIMING_OFFSET_PRESETS = [
  -250, -200, -150, -100, -50, 0, 50, 100, 150, 200, 250,
] as const;
/** One Ctrl+Shift+scroll notch (matches the prompt's spinner step). */
export const TIMING_OFFSET_STEP_MS = 10;

/** Minimum part of the window that must stay on a screen to trust saved bounds. */
const MIN_VISIBLE_WIDTH = 120;
const MIN_VISIBLE_HEIGHT = 60;

export interface WindowBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface StoredSettings {
  schema_version: 1;
  box_alpha: number;
  window_bounds: WindowBounds | null;
  /** kashi-server base URL (hand-edited for now — settings UI comes later). */
  server_url: string | null;
  /** API key for the server ("ksh_..."); meaningless without server_url. */
  server_api_key: string | null;
  /**
   * An lrclib-compatible mirror on the user's own network (LyricFix): asked
   * first, with a short timeout, before lrclib.net. Independent of the server
   * — serverless mode can use one too. null = lrclib.net only.
   */
  lyrics_mirror_url: string | null;
  /**
   * The user's timing offset in ms on top of BASE_LEAD_MS (positive =
   * earlier, 0 = the base). A new key, not timing_offset_ms: up to 0.30.x
   * that key held the APPLIED lead (stock 200), and this key's presence is
   * what marks a file as migrated (parseTimingOffsetRel).
   */
  timing_offset_rel_ms: number;
  /** Effect engine level (Faz 4): off | simple | full. */
  effect_level: EffectLevel;
  /**
   * How much of the album palette themes the box (Faz 4 saha turu). Two
   * scopes since client review E.3; a stored `fixed-bg` / `fixed-text` reads
   * as `full` (parseThemeScope).
   */
  theme_scope: ThemeScope;
  /**
   * Send diagnostics to the configured server (Faz 6.7 P2). Meaningless
   * without server_url + server_api_key: it is the user's OWN server, so the
   * default is on and the tray carries the opt-out.
   */
  telemetry_enabled: boolean;
  /**
   * Warm the next track's lyrics while the current one plays (LyricFix round
   * 3): one server request per upcoming track, plus one mirror/lrclib.net
   * lookup for the very next one. Always on, with no switch in the UI (owner,
   * 2026-10-26); `false` in the settings file is a hidden escape hatch.
   */
  prefetch_next_track: boolean;
  /**
   * Lyric text size and box size, as three presets each (Faz 7 P3). Presets
   * rather than free values: every box size has to keep enough margin for the
   * particle layer to fade out in, and a slider would let a user pick a
   * geometry where effects get cut off against the window edge.
   */
  text_scale: TextScale;
  box_scale: BoxScale;
}

export const DEFAULT_SETTINGS: StoredSettings = {
  schema_version: 1,
  box_alpha: DEFAULT_BOX_ALPHA,
  window_bounds: null,
  server_url: null,
  server_api_key: null,
  lyrics_mirror_url: null,
  timing_offset_rel_ms: DEFAULT_TIMING_OFFSET_MS,
  effect_level: DEFAULT_EFFECT_LEVEL,
  theme_scope: DEFAULT_THEME_SCOPE,
  telemetry_enabled: true,
  prefetch_next_track: true,
  text_scale: DEFAULT_TEXT_SCALE,
  box_scale: DEFAULT_BOX_SCALE,
};

/**
 * The stored user offset. Up to 0.30.x the file held the APPLIED lead as
 * timing_offset_ms (stock +200); it migrates ONCE to the user's number
 * (200 → 0, 250 → +50). Once timing_offset_rel_ms exists it alone is read, so
 * a second start never subtracts the base again — even though the file keeps
 * a timing_offset_ms (serializeSettings writes it back for rollbacks).
 */
export function parseTimingOffsetRel(record: Record<string, unknown>): number {
  if (record['timing_offset_rel_ms'] !== undefined) {
    return clampTimingOffset(record['timing_offset_rel_ms']);
  }
  const legacy = record['timing_offset_ms'];
  // Absent or garbage: 0.30.x fell back to its stock +200 — the base, i.e. 0.
  if (typeof legacy !== 'number' || !Number.isFinite(legacy)) return DEFAULT_TIMING_OFFSET_MS;
  // 0.30.x clamped the applied value to ±500 first; keep what it played.
  return clampTimingOffset(clampTimingOffset(legacy) - BASE_LEAD_MS);
}

export function timingOffsetLabel(offsetMs: number): string {
  if (offsetMs === 0) return '0 ms (default)';
  return `${formatTimingOffset(offsetMs)} (${offsetMs > 0 ? 'earlier' : 'later'})`;
}

/** Clamp to [OPACITY_MIN, OPACITY_MAX], 2 decimals; garbage → default. */
export function clampAlpha(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_BOX_ALPHA;
  const clamped = Math.min(OPACITY_MAX, Math.max(OPACITY_MIN, value));
  return Math.round(clamped * 100) / 100;
}

/**
 * IPC payloads are untrusted (R-7): coerce to a small integer step count.
 * Anything non-finite → 0; magnitude capped so a burst can't teleport alpha.
 */
export function sanitizeDeltaSteps(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0;
  return Math.max(-5, Math.min(5, Math.trunc(value)));
}

export function adjustAlpha(current: number, deltaSteps: number): number {
  return clampAlpha(clampAlpha(current) + sanitizeDeltaSteps(deltaSteps) * OPACITY_STEP);
}

/** The offset twin of adjustAlpha: Ctrl+Shift+scroll live tuning (Faz 4.5). */
export function adjustTimingOffset(current: number, deltaSteps: number): number {
  return clampTimingOffset(
    clampTimingOffset(current) + sanitizeDeltaSteps(deltaSteps) * TIMING_OFFSET_STEP_MS,
  );
}

export function presetLabel(alpha: number): string {
  return alpha === 0 ? 'Off' : `${Math.round(alpha * 100)}%`;
}

/** Index into OPACITY_PRESETS matching alpha (±0.005), or -1 (custom value). */
export function nearestPresetIndex(alpha: number): number {
  return OPACITY_PRESETS.findIndex((preset) => Math.abs(preset - alpha) < 0.005);
}

/** The pre-0.9.0 window size — the SENTINEL for legacy saved bounds
 * (persistWindowBounds always writes the real window size, so an exact
 * 560×180 can only come from a ≤0.8.x install). */
/**
 * Every window size this app has shipped, with where the BOX ZONE sat inside
 * it. The zone is the box's real home — the window only ever grew around it —
 * so a saved position migrates by the difference between two zone origins and
 * the box lands on the same pixel it was parked on.
 *
 * A size is a SENTINEL: persistWindowBounds always writes the real window
 * size, so an exact match can only come from the build that shipped it.
 */
export const KNOWN_WINDOWS = [
  { width: 560, height: 180, zoneX: 0, zoneY: 0 }, // ≤0.8.x — the box WAS the window
  { width: 640, height: 300, zoneX: 40, zoneY: 120 }, // 0.9.0–0.14.x
  { width: 800, height: 460, zoneX: 120, zoneY: 160 }, // 0.15.0–
] as const;

/**
 * Shifts bounds saved by an older build so the BOX stays put while the window
 * grows around it. Anything unrecognized passes through with the size pinned:
 * stored width/height could be hand-edited, and a position-only move across a
 * Windows DPI boundary rescales the window (the drag-path trap).
 */
export function migrateWindowBounds(
  bounds: WindowBounds,
  windowWidth: number,
  windowHeight: number,
  // Mandatory: a forgotten zone would silently shift every migrated box by
  // the zone offset, and no test would notice.
  zone: { x: number; y: number },
): WindowBounds {
  const previous = KNOWN_WINDOWS.find(
    (w) => w.width === bounds.width && w.height === bounds.height,
  );
  const known = previous && !(bounds.width === windowWidth && bounds.height === windowHeight);
  return {
    x: known ? bounds.x - (zone.x - previous.zoneX) : bounds.x,
    y: known ? bounds.y - (zone.y - previous.zoneY) : bounds.y,
    width: windowWidth,
    height: windowHeight,
  };
}

export interface WorkAreaLike {
  workArea: { x: number; y: number; width: number; height: number };
}

/**
 * Saved bounds are trusted only if a usable chunk of the window still lands on
 * a CURRENT display (monitors get unplugged, resolutions change — R-4);
 * otherwise the caller falls back to the default centered position.
 */
export function isPositionVisible(
  bounds: WindowBounds,
  displays: readonly WorkAreaLike[],
): boolean {
  return displays.some(({ workArea }) => {
    const visibleW =
      Math.min(bounds.x + bounds.width, workArea.x + workArea.width) -
      Math.max(bounds.x, workArea.x);
    const visibleH =
      Math.min(bounds.y + bounds.height, workArea.y + workArea.height) -
      Math.max(bounds.y, workArea.y);
    return visibleW >= MIN_VISIBLE_WIDTH && visibleH >= MIN_VISIBLE_HEIGHT;
  });
}

/**
 * Keys this build has removed, dropped on read so the next save writes the
 * file without them. Everything else unknown survives the round-trip (see
 * parseSettings) — these are the exception because they are not a NEWER
 * build's fields but this one's retired knobs.
 *
 * `fill_style` (client review E.3, 2026-11-14): the sweep had two base
 * dialects, `themed` and `neutral`; only `themed` is left. A stored `neutral`
 * goes silently: the file loses the key and the user sees the themed sweep,
 * which is also what a build from before the setting existed did.
 */
export const REMOVED_SETTINGS_KEYS: readonly string[] = ['fill_style'];

function withoutRemovedKeys(record: Record<string, unknown>): Record<string, unknown> {
  const kept = { ...record };
  for (const key of REMOVED_SETTINGS_KEYS) delete kept[key];
  return kept;
}

/** Tolerant parse (corrupt/hand-edited/future file must never crash startup). */
export function parseSettings(raw: string): StoredSettings {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
  if (typeof data !== 'object' || data === null) return { ...DEFAULT_SETTINGS };
  const record = withoutRemovedKeys(data as Record<string, unknown>);

  const alpha =
    typeof record['box_alpha'] === 'number' ? clampAlpha(record['box_alpha']) : DEFAULT_BOX_ALPHA;

  let bounds: WindowBounds | null = null;
  const rawBounds = record['window_bounds'];
  if (typeof rawBounds === 'object' && rawBounds !== null) {
    const b = rawBounds as Record<string, unknown>;
    const x = b['x'];
    const y = b['y'];
    const width = b['width'];
    const height = b['height'];
    if (
      typeof x === 'number' &&
      typeof y === 'number' &&
      typeof width === 'number' &&
      typeof height === 'number' &&
      [x, y, width, height].every(Number.isFinite) &&
      width > 0 &&
      height > 0
    ) {
      bounds = {
        x: Math.round(x),
        y: Math.round(y),
        width: Math.round(width),
        height: Math.round(height),
      };
    }
  }

  const serverUrl = normalizeServerUrl(record['server_url']);
  const apiKey =
    typeof record['server_api_key'] === 'string' && record['server_api_key'].trim() !== ''
      ? record['server_api_key'].trim()
      : null;

  // Spread the raw record first: fields written by a NEWER kashi must survive
  // a round-trip through this build (read → tweak alpha → save), or a version
  // rollback silently strips them. Known fields are then overridden with
  // their validated values.
  return {
    ...record,
    schema_version: 1,
    box_alpha: alpha,
    window_bounds: bounds,
    server_url: serverUrl,
    server_api_key: apiKey,
    lyrics_mirror_url: normalizeServerUrl(record['lyrics_mirror_url']),
    timing_offset_rel_ms: parseTimingOffsetRel(record),
    effect_level: parseEffectLevel(record['effect_level']),
    theme_scope: parseThemeScope(record['theme_scope']),
    // Only an explicit false opts out — a corrupt or absent value keeps the
    // default rather than silently disabling the channel we debug with.
    telemetry_enabled: record['telemetry_enabled'] !== false,
    prefetch_next_track: record['prefetch_next_track'] !== false,
    text_scale: parseTextScale(record['text_scale']),
    box_scale: parseBoxScale(record['box_scale']),
  };
}

/**
 * The file's text. timing_offset_ms is written back with its 0.30.x meaning
 * (the applied lead), so rolling back to an older build keeps the delay the
 * user actually hears; this build reads it only from a pre-0.31 file.
 */
export function serializeSettings(settings: StoredSettings): string {
  const file = { ...settings, timing_offset_ms: appliedLeadMs(settings.timing_offset_rel_ms) };
  return `${JSON.stringify(file, null, 2)}\n`;
}
