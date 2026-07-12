/**
 * Pure effect-engine logic (Faz 4): palette → CSS variables, beat usability,
 * and the frame-loop beat cursor. DOM-free so every rule is unit-tested; the
 * class/variable writes live in main.ts.
 */
import type { EffectLevel, ThemeScope } from '../../shared/effect-level.js';

/** Shapes as they arrive over IPC — untrusted, everything optional. */
export interface PaletteLike {
  primary?: unknown;
  secondary?: unknown;
  background?: unknown;
  text?: unknown;
  accent?: unknown;
}

export interface BeatsLike {
  bpm?: unknown;
  confidence?: unknown;
  times_ms?: unknown;
  downbeat_indices?: unknown;
}

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/**
 * Defaults equal the pre-Faz-4 look (white text, the box's dark blue-black):
 * a missing/partial/invalid palette falls back per field and the overlay
 * renders exactly as v0.1.x did.
 */
export const DEFAULT_PALETTE_VARS: Readonly<Record<string, string>> = {
  '--kashi-primary': '#ffffff',
  '--kashi-secondary': '#ffffff',
  '--kashi-bg-rgb': '8, 10, 18',
  '--kashi-text': '#ffffff',
  '--kashi-accent': '#ffffff',
};

/**
 * Lyric text sits on a mostly-dark translucent box over arbitrary desktop
 * content — a near-black primary/text color would be unreadable. Colors used
 * for TEXT need at least this relative luminance or they fall back to white.
 * (0.15 keeps saturated mid-tones like #e84545 while rejecting #1a1a2e.)
 */
export const TEXT_LUMINANCE_FLOOR = 0.15;
/**
 * Field feedback (2026-07-12): light album backgrounds made lyrics unreadable
 * and even the valid ones read too bright. The box background is CLAMPED
 * dark — any palette background above this luminance is darkened onto it.
 */
export const BG_MAX_LUMINANCE = 0.1;
/**
 * Readability rule: text-carrying colors must reach this WCAG contrast ratio
 * against the (clamped) background color or they fall back to white. 3:1 is
 * the large-text threshold — the lyric is 28 px bold and additionally backed
 * by a text-shadow.
 */
export const TEXT_CONTRAST_MIN = 3;

function channel(hex: string, index: number): number {
  return Number.parseInt(hex.slice(1 + index * 2, 3 + index * 2), 16);
}

function toHex(r: number, g: number, b: number): string {
  const h = (v: number) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** WCAG relative luminance of a #rrggbb color (0 = black, 1 = white). */
export function relativeLuminance(hex: string): number {
  const linear = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return (
    0.2126 * linear(channel(hex, 0)) +
    0.7152 * linear(channel(hex, 1)) +
    0.0722 * linear(channel(hex, 2))
  );
}

/** WCAG contrast ratio between two colors (1..21). */
export function contrastRatio(hexA: string, hexB: string): number {
  const a = relativeLuminance(hexA);
  const b = relativeLuminance(hexB);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/**
 * Darken a color until its luminance is at most BG_MAX_LUMINANCE, keeping the
 * hue (channels scale together). Deterministic and testable.
 */
export function clampBackground(hex: string): string {
  let r = channel(hex, 0);
  let g = channel(hex, 1);
  let b = channel(hex, 2);
  let out = toHex(r, g, b);
  for (let i = 0; i < 16 && relativeLuminance(out) > BG_MAX_LUMINANCE; i += 1) {
    r *= 0.85;
    g *= 0.85;
    b *= 0.85;
    out = toHex(r, g, b);
  }
  return out;
}

function validHex(value: unknown): string | null {
  return typeof value === 'string' && HEX_COLOR.test(value) ? value : null;
}

/**
 * Text-carrying colors pass BOTH readability rules or fall back to white:
 * the absolute luminance floor (the box is translucent — the desktop behind
 * it is arbitrary) and the contrast ratio against the box's own color.
 */
function textSafeHex(value: unknown, bgHex: string): string | null {
  const hex = validHex(value);
  if (hex === null) return null;
  if (relativeLuminance(hex) < TEXT_LUMINANCE_FLOOR) return null;
  if (contrastRatio(hex, bgHex) < TEXT_CONTRAST_MIN) return null;
  return hex;
}

const DEFAULT_BG_HEX = '#080a12'; // the stock box color (8, 10, 18)

/**
 * Map an (untrusted) palette onto the CSS variables the stylesheet consumes.
 * Every color is validated against #rrggbb — IPC payloads never reach CSS
 * unchecked (R-7). The background is clamped dark, text-carrying colors must
 * clear the readability rules, and `scope` decides how much of the palette
 * applies at all (field feedback: colors can be pinned per group). The
 * background becomes an "r, g, b" triplet so the stylesheet can compose it
 * with the user's box alpha (which stays untouched by theming).
 */
export function paletteToCssVars(
  palette: PaletteLike | undefined,
  scope: ThemeScope = 'full',
): Record<string, string> {
  const vars = { ...DEFAULT_PALETTE_VARS };
  if (!palette || scope === 'none') return vars;

  let bgHex = DEFAULT_BG_HEX;
  const background = validHex(palette.background);
  if (background && scope === 'full') {
    bgHex = clampBackground(background);
    vars['--kashi-bg-rgb'] = `${channel(bgHex, 0)}, ${channel(bgHex, 1)}, ${channel(bgHex, 2)}`;
  }

  if (scope === 'full' || scope === 'fixed-bg') {
    const text = textSafeHex(palette.text, bgHex);
    if (text) vars['--kashi-text'] = text;
  }

  // Effect colors (active word / glow) theme in every non-none scope.
  const primary = textSafeHex(palette.primary, bgHex);
  if (primary) vars['--kashi-primary'] = primary;
  const secondary = validHex(palette.secondary);
  if (secondary) vars['--kashi-secondary'] = secondary;
  const accent = validHex(palette.accent);
  if (accent) vars['--kashi-accent'] = accent;
  return vars;
}

/**
 * Sustained-fill (Faz 4 "ooh-ooh" aesthetics): long-held words sweep left to
 * right continuously instead of the discrete word jump. Held this long =
 * "sustained".
 */
export const FILL_MIN_WORD_DURATION_MS = 800;
/** Mid-line sustained words only sweep in runs at least this long. */
export const FILL_MIN_RUN = 2;

/**
 * Which words of the ACTIVE line sweep (field feedback 2026-07-12: per-word
 * alternation between sweep and pop reads as random). Line-level plan:
 *   - ad-lib line → every word sweeps (one coherent gesture);
 *   - otherwise a sustained LAST word sweeps (line-end hold), and mid-line
 *     sustained words sweep only as a consecutive run of >= FILL_MIN_RUN —
 *     an isolated long word mid-line pops like its neighbours.
 * Computed once per line (span build time), not per frame.
 */
export function planWordFills(
  words: readonly { start_ms: number; end_ms: number }[],
  lineAdlib: boolean,
  level: EffectLevel,
): boolean[] {
  if (level === 'off' || words.length === 0) return words.map(() => false);
  if (lineAdlib) return words.map(() => true);
  const sustained = words.map((w) => w.end_ms - w.start_ms >= FILL_MIN_WORD_DURATION_MS);
  const plan = words.map(() => false);
  let runStart = -1;
  for (let i = 0; i <= sustained.length; i += 1) {
    if (i < sustained.length && sustained[i]) {
      if (runStart < 0) runStart = i;
      continue;
    }
    if (runStart >= 0) {
      const runEnd = i - 1; // inclusive
      const runLength = i - runStart;
      // A run counts when it is long enough, or when it reaches the line end.
      if (runLength >= FILL_MIN_RUN || runEnd === sustained.length - 1) {
        for (let j = runStart; j <= runEnd; j += 1) plan[j] = true;
      }
      runStart = -1;
    }
  }
  return plan;
}

/** 0..1 progress of the sweep across the word at clock position `pos`. */
export function fillProgress(word: { start_ms: number; end_ms: number }, pos: number): number {
  const span = word.end_ms - word.start_ms;
  if (span <= 0) return 1;
  return Math.min(1, Math.max(0, (pos - word.start_ms) / span));
}

export const BEAT_CONFIDENCE_GATE = 0.5;
/** A beat is "active" inside [t-30, t+60] ms — a ~90 ms pulse per beat. */
export const BEAT_WINDOW_BEFORE_MS = 30;
export const BEAT_WINDOW_AFTER_MS = 60;
/**
 * A forward jump beyond this re-seeks by binary search instead of scanning.
 * Above the clock's seek-snap threshold (1500 ms) so slew never triggers it.
 */
export const BEAT_RESYNC_JUMP_MS = 2000;

/** Beat pulse only at `full`, only with a usable, confident grid. */
export function beatsUsable(level: EffectLevel, beats: BeatsLike | undefined): boolean {
  if (level !== 'full' || !beats) return false;
  const times = beats.times_ms;
  if (!Array.isArray(times) || times.length === 0) return false;
  if (!times.every((t) => typeof t === 'number' && Number.isFinite(t))) return false;
  // Missing confidence → conservative off (the schema always writes it today).
  return typeof beats.confidence === 'number' && beats.confidence >= BEAT_CONFIDENCE_GATE;
}

export interface BeatFrame {
  active: boolean;
  down: boolean;
}

export const BEAT_IDLE: BeatFrame = { active: false, down: false };

/**
 * Monotonic beat cursor: O(1) per frame while the position moves forward
 * (the common case), binary-search re-seek on seeks/backward jumps. Class
 * removal is position math too — no setTimeout in the render path.
 */
export class BeatCursor {
  private readonly times: number[];
  private readonly downs: Set<number>;
  /** Index of the first beat whose window has not fully passed. */
  private idx = 0;
  private lastPos = Number.NEGATIVE_INFINITY;

  constructor(times: number[], downbeatIndices: readonly number[] = []) {
    this.times = times;
    this.downs = new Set(downbeatIndices);
  }

  frame(pos: number): BeatFrame {
    if (pos < this.lastPos || pos - this.lastPos > BEAT_RESYNC_JUMP_MS) {
      this.reseek(pos);
    }
    this.lastPos = pos;
    while (this.idx < this.times.length && (this.times[this.idx] ?? 0) + BEAT_WINDOW_AFTER_MS < pos) {
      this.idx += 1;
    }
    const t = this.times[this.idx];
    if (t === undefined) return BEAT_IDLE;
    const active = pos >= t - BEAT_WINDOW_BEFORE_MS && pos <= t + BEAT_WINDOW_AFTER_MS;
    if (!active) return BEAT_IDLE;
    return { active: true, down: this.downs.has(this.idx) };
  }

  private reseek(pos: number): void {
    let lo = 0;
    let hi = this.times.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if ((this.times[mid] ?? 0) + BEAT_WINDOW_AFTER_MS < pos) lo = mid + 1;
      else hi = mid;
    }
    this.idx = lo;
  }
}
