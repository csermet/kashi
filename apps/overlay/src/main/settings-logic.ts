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

export const OPACITY_PRESETS = [0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8] as const;
export const OPACITY_MIN = 0;
// 0.8 keeps a hint of see-through at full dark (user: "daha koyu olabilmeli").
export const OPACITY_MAX = 0.8;
/** One Ctrl+scroll notch. */
export const OPACITY_STEP = 0.02;
export const DEFAULT_BOX_ALPHA = 0.1;

/**
 * Global lyric timing offset, ms ADDED to the estimated position: positive =
 * lyrics fire EARLIER (karaoke UX convention — perception lags the ear by
 * ~100 ms; Caner: "hafif geç hissettiriyor", 2026-07-12). Applied in the
 * renderer frame loop; per-song data never changes.
 */
export const TIMING_OFFSET_PRESETS = [
  -250, -200, -150, -100, -50, 0, 50, 100, 150, 200, 250,
] as const;
export const TIMING_OFFSET_MAX_ABS = 500;
export const DEFAULT_TIMING_OFFSET_MS = 0;

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
  /** Lyric timing offset in ms (positive = earlier). */
  timing_offset_ms: number;
  /** Effect engine level (Faz 4): off | simple | full. */
  effect_level: EffectLevel;
  /** How much of the album palette themes the box (Faz 4 saha turu). */
  theme_scope: ThemeScope;
}

export const DEFAULT_SETTINGS: StoredSettings = {
  schema_version: 1,
  box_alpha: DEFAULT_BOX_ALPHA,
  window_bounds: null,
  server_url: null,
  server_api_key: null,
  timing_offset_ms: DEFAULT_TIMING_OFFSET_MS,
  effect_level: DEFAULT_EFFECT_LEVEL,
  theme_scope: DEFAULT_THEME_SCOPE,
};

/** Integer ms in [-500, 500]; garbage → 0 (Off). */
export function clampTimingOffset(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DEFAULT_TIMING_OFFSET_MS;
  return Math.max(-TIMING_OFFSET_MAX_ABS, Math.min(TIMING_OFFSET_MAX_ABS, Math.round(value)));
}

export function timingOffsetLabel(offsetMs: number): string {
  if (offsetMs === 0) return 'Off';
  return offsetMs > 0 ? `+${offsetMs} ms (earlier)` : `${offsetMs} ms (later)`;
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

export function presetLabel(alpha: number): string {
  return alpha === 0 ? 'Off' : `${Math.round(alpha * 100)}%`;
}

/** Index into OPACITY_PRESETS matching alpha (±0.005), or -1 (custom value). */
export function nearestPresetIndex(alpha: number): number {
  return OPACITY_PRESETS.findIndex((preset) => Math.abs(preset - alpha) < 0.005);
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

/** Tolerant parse (corrupt/hand-edited/future file must never crash startup). */
export function parseSettings(raw: string): StoredSettings {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
  if (typeof data !== 'object' || data === null) return { ...DEFAULT_SETTINGS };
  const record = data as Record<string, unknown>;

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
    timing_offset_ms: clampTimingOffset(record['timing_offset_ms']),
    effect_level: parseEffectLevel(record['effect_level']),
    theme_scope: parseThemeScope(record['theme_scope']),
  };
}
