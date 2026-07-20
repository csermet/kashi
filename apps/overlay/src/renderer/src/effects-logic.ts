/**
 * Pure effect-engine logic (Faz 4): palette → CSS variables, beat usability,
 * and the frame-loop beat cursor. DOM-free so every rule is unit-tested; the
 * class/variable writes live in main.ts.
 */
import type { EffectLevel, ThemeScope } from '../../shared/effect-level.js';
import {
  NEUTRAL_BG_TRIPLET,
  bestHueDonor,
  hasUsableHue,
  hexToOklch,
  toneAccent,
  toneBackground,
  toneFx,
  tonePrimary,
  toneSecondary,
  type Oklch,
} from './color-tone.js';

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

function parseSlot(value: unknown): Oklch | null {
  const hex = validHex(value);
  return hex === null ? null : hexToOklch(hex);
}

/**
 * Map an (untrusted) palette onto the CSS variables the stylesheet consumes,
 * through the TONE-MAPPING pipeline (color-tone.ts): only the HUE of each
 * extracted color survives; brightness/saturation render at fixed bands, so
 * a theme can never be muddy, washed-out, or unreadable (field turu 2).
 *
 * Rules preserved from 0.2.3: every input validated against #rrggbb (R-7);
 * background emitted as an "r, g, b" triplet composed with the user's box
 * alpha; `scope` pins color groups; base text stays white (differentiation
 * from the primary is guaranteed by construction: ΔE_OK ≥ ~0.19). The WCAG
 * check remains as a backstop that provably never fires with today's bands.
 */
export function paletteToCssVars(
  palette: PaletteLike | undefined,
  scope: ThemeScope = 'full',
): Record<string, string> {
  const vars = { ...DEFAULT_PALETTE_VARS };
  if (!palette || scope === 'none') return vars;

  // Donor pass: the most chromatic slot lends its hue to neutral slots so
  // B&W-with-one-accent covers theme coherently. `text` is excluded — the
  // server only ever emits synthetic #ffffff/#111111 there.
  const slots = {
    primary: parseSlot(palette.primary),
    accent: parseSlot(palette.accent),
    secondary: parseSlot(palette.secondary),
    background: parseSlot(palette.background),
  };
  const donor = bestHueDonor([slots.primary, slots.accent, slots.secondary, slots.background]);
  const resolve = (slot: Oklch | null): Oklch | null =>
    slot && hasUsableHue(slot) ? slot : donor;

  let bgHex: string | null = null;
  if (scope === 'full') {
    const bg = resolve(slots.background);
    bgHex = bg ? toneBackground(bg) : null;
    vars['--kashi-bg-rgb'] = bgHex
      ? `${channel(bgHex, 0)}, ${channel(bgHex, 1)}, ${channel(bgHex, 2)}`
      : NEUTRAL_BG_TRIPLET;
  }

  // Base text is pinned white (see module note) — DEFAULT_PALETTE_VARS value.

  // Effect colors (active word / sweep tail / glow) theme in every non-none
  // scope; a neutral palette with no donor keeps them stock white.
  const primarySource = resolve(slots.primary);
  if (primarySource) {
    const primary = tonePrimary(primarySource);
    vars['--kashi-primary'] = primary.hex;
    vars['--kashi-secondary'] = toneSecondary(primary.c, primary.h);
  }
  const accentSource = resolve(slots.accent);
  if (accentSource) vars['--kashi-accent'] = toneAccent(accentSource);

  // Backstop (belt): measured floor across the bands is ~8:1 — this cannot
  // fire today, but a future band retune must not silently ship unreadable.
  if (bgHex && contrastRatio(vars['--kashi-primary']!, bgHex) < TEXT_CONTRAST_MIN) {
    vars['--kashi-primary'] = DEFAULT_PALETTE_VARS['--kashi-primary']!;
    vars['--kashi-secondary'] = DEFAULT_PALETTE_VARS['--kashi-secondary']!;
  }
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
  if ((level !== 'full' && level !== 'hype') || !beats) return false;
  const times = beats.times_ms;
  if (!Array.isArray(times) || times.length === 0) return false;
  // Monotonicity too: BeatCursor binary-searches this array — unsorted
  // times silently break the pulse (retro 4.5 #13).
  if (
    !times.every(
      (t, i) =>
        typeof t === 'number' && Number.isFinite(t) && (i === 0 || t >= (times[i - 1] as number))
    )
  ) {
    return false;
  }
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

// ---------------------------------------------------------------------------
// Semantic word effects (Faz 6 P4) — consuming the server's fx block.

import type { FxData, LyricLine } from '../../shared/lyrics.js';

export interface FxWordEffect {
  tag: string;
  intensity: number;
}

/** Tags whose activation fires the particle burst (pool in main.ts). */
export const FX_BURST_TAGS: ReadonlySet<string> = new Set(['explosion', 'electric']);

/**
 * fx.words → per-line winner map (line → word → effect).
 *
 * Selectivity is a PRODUCT decision (Caner kararı 8): at most ONE semantic
 * effect per line — the highest intensity wins, earliest word breaks ties —
 * "az ama vurucu", loosened only if the field tour asks for more. Entries
 * with out-of-range indices (quality-gate word strips, stale docs) are
 * dropped here so the renderer never styles a nonexistent span (DG6: a
 * wrong effect is worse than no effect).
 */
export function buildFxIndex(
  fx: FxData | undefined,
  lines: readonly LyricLine[],
): Map<number, { word: number; effect: FxWordEffect }> {
  const index = new Map<number, { word: number; effect: FxWordEffect }>();
  if (!fx?.words) return index;
  for (const tag of fx.words) {
    if (!Number.isInteger(tag.line) || !Number.isInteger(tag.word)) continue;
    const words = lines[tag.line]?.words;
    if (!words || tag.word < 0 || tag.word >= words.length) continue;
    if (typeof tag.tag !== 'string' || !tag.tag) continue;
    const intensity = typeof tag.intensity === 'number' ? Math.min(1, Math.max(0, tag.intensity)) : 0;
    const current = index.get(tag.line);
    if (
      !current ||
      intensity > current.effect.intensity ||
      (intensity === current.effect.intensity && tag.word < current.word)
    ) {
      index.set(tag.line, { word: tag.word, effect: { tag: tag.tag, intensity } });
    }
  }
  return index;
}

/**
 * fx.lines → line-theme map (Faz 6.5 P1, ambient ring). Line-level tags are
 * the embedding layer's THEME verdicts — one per line from the server; if a
 * doc ever carries duplicates the FIRST entry wins (deterministic). Entries
 * with out-of-range indices or empty tags are dropped, same tolerance story
 * as buildFxIndex (enrichment never breaks the document).
 */
export function buildLineThemeIndex(
  fx: FxData | undefined,
  lines: readonly LyricLine[],
): Map<number, string> {
  const index = new Map<number, string>();
  if (!fx?.lines) return index;
  for (const tag of fx.lines) {
    if (!Number.isInteger(tag.line) || tag.line < 0 || tag.line >= lines.length) continue;
    if (typeof tag.tag !== 'string' || !tag.tag) continue;
    if (!index.has(tag.line)) index.set(tag.line, tag.tag);
  }
  return index;
}

/**
 * Ambient ring colors for the ACTIVE line (Faz 6.5 P1): the continuous ring
 * takes the line THEME's tint; the activation flash takes the line's fx
 * WORD tint (the "poison word → green halo" field idea). Since the P4
 * calibration parked the embedding layer (line themes are rare now), the
 * floor FALLS BACK to the fx word's tint — the keyword layer is the
 * precision path, and a poison-word line still gets its green surround.
 * Unknown tags (a newer lexicon than this build) resolve to null — no ring
 * beats a wrong ring (DG6). Pure: main.ts applies at line cadence.
 */
export function ambientColors(
  lineIndex: number,
  themes: ReadonlyMap<number, string>,
  fxIndex: ReadonlyMap<number, { word: number; effect: FxWordEffect }>,
  tintVars: Readonly<Record<string, string>>,
): { ambient: string | null; flash: string | null } {
  if (lineIndex < 0) return { ambient: null, flash: null };
  const theme = themes.get(lineIndex);
  const fxTag = fxIndex.get(lineIndex)?.effect.tag;
  const flash = fxTag ? (tintVars[`--fx-tint-${fxTag}`] ?? null) : null;
  const ambient = theme ? (tintVars[`--fx-tint-${theme}`] ?? null) : flash;
  return { ambient, flash };
}

// ---------------------------------------------------------------------------
// Energy/section dynamics (Faz 6 P5) — precomputed curves, zero live audio.

import type { EnergyData, SectionData } from '../../shared/lyrics.js';

/** Quantization step for the CSS energy var — style writes only on step
 * changes (a few per second), never per frame. */
export const ENERGY_QUANT = 0.05;

/** Track-relative loudness at a position, 0..1 (0 when absent). O(1). */
export function energyAt(energy: EnergyData | undefined, posMs: number): number {
  if (!energy || energy.values.length === 0 || energy.rate_hz <= 0) return 0;
  const idx = Math.floor((Math.max(0, posMs) / 1000) * energy.rate_hz);
  const value = energy.values[Math.min(idx, energy.values.length - 1)] ?? 0;
  return Math.min(100, Math.max(0, value)) / 100;
}

/** Quantized for CSS: 0, 0.05, … 1 — equality-comparable across frames. */
export function quantizedEnergy(energy: EnergyData | undefined, posMs: number): number {
  return Math.round(energyAt(energy, posMs) / ENERGY_QUANT) * ENERGY_QUANT;
}

/** Inside a section of `type` at posMs? Sections are few (v1: a handful of
 * energy-derived "high" blocks) — a linear scan is O(few) per frame. */
export function inSection(
  sections: readonly SectionData[] | undefined,
  type: string,
  posMs: number,
): boolean {
  if (!sections) return false;
  for (const section of sections) {
    if (section.type === type && posMs >= section.start_ms && posMs < section.end_ms) {
      return true;
    }
  }
  return false;
}

/** Section types that drive the high-intensity ramp (Faz 6.5 P5): today's
 * energy-derived "high" blocks plus real "chorus" labels — additive-ready
 * for the structure analysis (P6) before it even ships. */
export const RAMP_SECTION_TYPES: readonly string[] = ['high', 'chorus'];

export function inRampSection(
  sections: readonly SectionData[] | undefined,
  posMs: number,
): boolean {
  return RAMP_SECTION_TYPES.some((type) => inSection(sections, type, posMs));
}

// ---------------------------------------------------------------------------
// Nightcore aesthetics (Faz 6.5 P5) — sped-up documents only.

import type { AlignmentData } from '../../shared/lyrics.js';

/** Below this the speed difference is inaudible tape drift, not nightcore. */
export const NIGHTCORE_MIN_SPEED = 1.05;

export function isNightcore(alignment: AlignmentData | undefined): boolean {
  const speed = alignment?.speed_factor;
  return typeof speed === 'number' && Number.isFinite(speed) && speed >= NIGHTCORE_MIN_SPEED;
}

/** Category base colors — the SEMANTIC hue source ("love is pink, toxic is
 * green"; field round 2). Only the hue survives rendering: toneFx re-renders
 * every tint in a fixed readable band and pushes lightness apart when the
 * album theme already sits on the category's hue. */
export const FX_BASE_COLORS: Readonly<Record<string, string>> = {
  explosion: '#ff8c42',
  fire: '#ff6b35',
  poison: '#4cd964', // green — the field note's canonical example
  love: '#ff6fa5',
  heartbreak: '#7f9cf5',
  water: '#4fc3f7',
  night: '#ffd166',
  shine: '#ffe082',
  dance: '#f06292',
  money: '#66bb6a',
  fly: '#81d4fa',
  speed: '#ffab40',
  electric: '#ffee58',
  cold: '#80deea',
  dark: '#9575cd',
  death: '#b0bec5',
  crown: '#ffd700',
  phone: '#4dd0e1',
  fight: '#ef5350',
  music: '#ce93d8',
  // v1.2 (Faz 6.5 P4): the brown and magenta bands were unused until now.
  drink: '#d4a373', // whiskey amber
  dream: '#d1c4e9', // pale lilac
  space: '#ea80fc', // nebula magenta
  storm: '#78909c', // storm-cloud slate
};

/**
 * Per-tag tint CSS vars, recomputed whenever the palette/scope changes
 * (rare — track changes and settings flips, never per frame). scope "none"
 * returns an empty map: the caller clears the vars and the CSS fallback
 * keeps everything stock (the scope contract).
 */
export function computeFxTintVars(
  primaryHex: string | undefined,
  scope: ThemeScope,
): Record<string, string> {
  if (scope === 'none') return {};
  const primary =
    primaryHex && HEX_COLOR.test(primaryHex) && primaryHex !== '#ffffff'
      ? hexToOklch(primaryHex)
      : null;
  const vars: Record<string, string> = {};
  for (const [tag, hex] of Object.entries(FX_BASE_COLORS)) {
    vars[`--fx-tint-${tag}`] = toneFx(hexToOklch(hex), primary);
  }
  return vars;
}
