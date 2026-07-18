/**
 * Perceptual tone mapping for album-palette theming (overlay 0.2.4).
 *
 * Design statement: HUE IS DATA, TONE IS DESIGN. The server's median-cut
 * palette tells us which hue belongs to a track; how bright and how saturated
 * that hue renders is decided HERE, at fixed tonal bands — so an extracted
 * color can never be muddy, washed-out, or indistinguishable from the white
 * base text (the three 2026-07-12/13 field failures: harsh red, dirty gold,
 * near-white pink). Pure module, no dependencies; runs once per track.
 *
 * Color space: OKLab/OKLCh (Björn Ottosson's matrices) — perceptually uniform
 * lightness, so a band like L=0.82 looks equally bright at every hue.
 */

export interface Oklch {
  L: number;
  C: number;
  /** Hue angle in radians (atan2 output). */
  h: number;
}

// --- Tonal bands (values measured/validated in the 0.2.4 plan) ---

/** Active word: bright but below white text. 0.80 (field turu 3: 0.82 read
 * too pastel — a touch lower buys visibly richer chroma at every hue). */
export const PRIMARY_L = 0.8;
/**
 * Deterministic rescue for hues whose sRGB gamut is too narrow at 0.82
 * (blue, h≈267°, reaches only C≈0.095): at L=0.75 EVERY hue reaches
 * C ≥ 0.128 (measured) ≥ the 0.12 floor, so one fallback always succeeds.
 */
export const PRIMARY_L_FALLBACK = 0.75;
/** Differentiation floor vs white base text (also brightens muddy golds). */
export const PRIMARY_C_MIN = 0.12;
/** Tames neon/harsh saturation — raised from 0.19 (field turu 3: "çok safe"). */
export const PRIMARY_C_MAX = 0.24;
/** Beat glow: dimmer than the word so the pulse reads as light, not text. */
export const ACCENT_L = 0.67;
export const ACCENT_C_MIN = 0.12;
/** Glow may run richer than text (field turu 3 richer bands). */
export const ACCENT_C_MAX = 0.24;
/** Dim sibling of the primary — the sustain-sweep gradient tail band. */
export const SECONDARY_L = 0.74;
/** The tail keeps the hue but recedes (× the primary's achieved chroma). */
export const SECONDARY_C_FACTOR = 0.85;
/** Dark but hue-visible; WCAG luminance ≈ 0.008, far under the 0.1 clamp. */
export const BG_L = 0.2;
/** A tint, never a colored box. */
export const BG_C_MAX = 0.05;
/**
 * Below this chroma the hue angle is 8-bit quantization NOISE (measured:
 * true grays ≤ 0.011; the faintest real field hue, washed pink #f2dee3,
 * is 0.023) — tone-mapping noise would invent a random color.
 */
export const NEUTRAL_C_THRESHOLD = 0.02;
/** A hue must be this confident before it themes NEUTRAL slots (donor pass). */
export const DONOR_C_MIN = 0.04;
/** #161616 — gray at the bg band, C=0: the honest dark for fully-B&W art. */
export const NEUTRAL_BG_TRIPLET = '22, 22, 22';

const GAMUT_EPSILON = 1e-4;
const GAMUT_CLIP_ITERATIONS = 24;

// --- sRGB <-> linear ---

function srgbToLinear(c8: number): number {
  const s = c8 / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function linearToSrgb(l: number): number {
  const clamped = Math.min(1, Math.max(0, l));
  const v = clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055;
  return Math.round(Math.min(1, Math.max(0, v)) * 255);
}

// --- linear RGB <-> OKLab (exact Ottosson matrices) ---

type LinearRgb = [number, number, number];

function linearRgbToOklab(r: number, g: number, b: number): { L: number; a: number; b: number } {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return {
    L: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  };
}

function oklchToLinearRgb(L: number, C: number, h: number): LinearRgb {
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ];
}

function inGamut(rgb: LinearRgb): boolean {
  return rgb.every((v) => v >= -GAMUT_EPSILON && v <= 1 + GAMUT_EPSILON);
}

// --- Public conversions ---

export function hexToOklch(hex: string): Oklch {
  const r = Number.parseInt(hex.slice(1, 3), 16);
  const g = Number.parseInt(hex.slice(3, 5), 16);
  const b = Number.parseInt(hex.slice(5, 7), 16);
  const lab = linearRgbToOklab(srgbToLinear(r), srgbToLinear(g), srgbToLinear(b));
  return { L: lab.L, C: Math.hypot(lab.a, lab.b), h: Math.atan2(lab.b, lab.a) };
}

function rgbToHex(rgb: LinearRgb): string {
  const h = (v: number) => linearToSrgb(v).toString(16).padStart(2, '0');
  return `#${h(rgb[0])}${h(rgb[1])}${h(rgb[2])}`;
}

/**
 * Largest in-gamut chroma ≤ C at (L, h), by bisection — deterministic, fixed
 * cost (C = 0 is always in gamut for L in [0, 1], so it always terminates).
 * The ACHIEVED chroma is load-bearing: tonePrimary compares it against
 * PRIMARY_C_MIN to decide the L-drop fallback.
 */
function clipChroma(L: number, C: number, h: number): { rgb: LinearRgb; c: number } {
  const rgb = oklchToLinearRgb(L, C, h);
  if (inGamut(rgb)) return { rgb, c: C };
  let lo = 0;
  let hi = C;
  for (let i = 0; i < GAMUT_CLIP_ITERATIONS; i += 1) {
    const mid = (lo + hi) / 2;
    if (inGamut(oklchToLinearRgb(L, mid, h))) lo = mid;
    else hi = mid;
  }
  return { rgb: oklchToLinearRgb(L, lo, h), c: lo };
}

/** Test hook: the achieved chroma after gamut clipping. */
export function clipChromaC(L: number, C: number, h: number): number {
  return clipChroma(L, C, h).c;
}

export function oklchToHex(L: number, C: number, h: number): string {
  return rgbToHex(clipChroma(L, C, h).rgb);
}

// --- Donor pass ---

export function hasUsableHue(c: Oklch): boolean {
  return c.C >= NEUTRAL_C_THRESHOLD;
}

/**
 * The most chromatic slot (C ≥ DONOR_C_MIN) donates its hue+chroma to
 * neutral slots — a B&W cover with one gold accent themes coherently gold
 * instead of falling apart into grays. Max-C wins; iteration order breaks
 * exact ties deterministically.
 */
export function bestHueDonor(slots: readonly (Oklch | null)[]): Oklch | null {
  let best: Oklch | null = null;
  for (const slot of slots) {
    if (slot && slot.C >= DONOR_C_MIN && (best === null || slot.C > best.C)) {
      best = slot;
    }
  }
  return best;
}

// --- Per-slot tone mappers ---

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** Active-word color: band-mapped, with the guaranteed L-drop rescue. */
export function tonePrimary(c: Oklch): { hex: string; c: number; h: number } {
  const target = clamp(c.C, PRIMARY_C_MIN, PRIMARY_C_MAX);
  let clipped = clipChroma(PRIMARY_L, target, c.h);
  if (clipped.c < PRIMARY_C_MIN - 1e-6) {
    clipped = clipChroma(PRIMARY_L_FALLBACK, target, c.h);
  }
  return { hex: rgbToHex(clipped.rgb), c: clipped.c, h: c.h };
}

/** Gradient tail: a dim of the PRIMARY's hue (never its own extraction). */
export function toneSecondary(primaryC: number, h: number): string {
  return oklchToHex(SECONDARY_L, primaryC * SECONDARY_C_FACTOR, h);
}

export function toneAccent(c: Oklch): string {
  return oklchToHex(ACCENT_L, clamp(c.C, ACCENT_C_MIN, ACCENT_C_MAX), c.h);
}

/** Background tint as a hex; the caller renders the "r, g, b" triplet. */
export function toneBackground(c: Oklch): string {
  return oklchToHex(BG_L, Math.min(c.C, BG_C_MAX), c.h);
}

/** Circular hue distance in RADIANS (0..π) — Oklch.h is atan2 output. */
export function hueDistance(a: number, b: number): number {
  const d = Math.abs(a - b) % (2 * Math.PI);
  return d > Math.PI ? 2 * Math.PI - d : d;
}

/**
 * FX category tint (Faz 6 field round 2): the category color must read as
 * ITS OWN color — "love is pink, toxic is green" — and must stay visibly
 * distinct from the album theme. Hue comes from the CATEGORY (data);
 * lightness/chroma render in a fixed readable band (design). When the
 * theme primary already sits on the category's hue (a green album playing
 * a "toxic" line), same-band rendering would disappear into the theme —
 * so the tint is pushed apart on the LIGHTNESS axis instead (lighter over
 * a dark primary, darker over a bright one), keeping the hue semantic.
 */
export const FX_TINT_L = 0.78;
/** ~40° in radians: closer than this = the tint would vanish into the theme. */
export const FX_HUE_CLASH_RAD = (40 * Math.PI) / 180;

export function toneFx(category: Oklch, primary: Oklch | null): string {
  const c = clamp(category.C < 0.12 ? 0.16 : category.C, 0.14, 0.22);
  let L = FX_TINT_L;
  if (primary && hueDistance(category.h, primary.h) < FX_HUE_CLASH_RAD) {
    L = primary.L >= 0.72 ? 0.6 : 0.88;
  }
  return oklchToHex(L, c, category.h);
}
