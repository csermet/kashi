import { describe, expect, it } from 'vitest';
import {
  BG_MAX_LUMINANCE,
  contrastRatio,
  relativeLuminance,
} from './effects-logic.js';
import {
  ACCENT_C_MAX,
  ACCENT_C_MIN,
  NEUTRAL_C_THRESHOLD,
  PRIMARY_C_MIN,
  PRIMARY_L,
  PRIMARY_L_FALLBACK,
  bestHueDonor,
  clipChromaC,
  hasUsableHue,
  hexToOklch,
  oklchToHex,
  toneAccent,
  toneBackground,
  tonePrimary,
  toneSecondary,
  type Oklch,
} from './color-tone.js';

/** ΔE_OK between two hexes (L,a,b distance). */
function deltaE(hexA: string, hexB: string): number {
  const a = hexToOklch(hexA);
  const b = hexToOklch(hexB);
  const ax = a.C * Math.cos(a.h);
  const ay = a.C * Math.sin(a.h);
  const bx = b.C * Math.cos(b.h);
  const by = b.C * Math.sin(b.h);
  return Math.hypot(a.L - b.L, ax - bx, ay - by);
}

describe('field-failure vectors (plan-verified expected values)', () => {
  it('harsh red #e84545 → soft coral (C tamed, L lifted)', () => {
    expect(tonePrimary(hexToOklch('#e84545')).hex).toBe('#ff847c');
  });

  it('muddy gold #8a7a4a → luminous gold (C boosted to the floor)', () => {
    expect(tonePrimary(hexToOklch('#8a7a4a')).hex).toBe('#dabb5c');
  });

  it('washed pink #f2dee3 keeps its OWN hue (C 0.023 ≥ 0.02) → clear pink', () => {
    const input = hexToOklch('#f2dee3');
    expect(input.C).toBeGreaterThanOrEqual(NEUTRAL_C_THRESHOLD);
    expect(tonePrimary(input).hex).toBe('#fd9cba');
  });

  it('pure gray is NEUTRAL — its hue angle is quantization noise', () => {
    expect(hasUsableHue(hexToOklch('#808080'))).toBe(false);
    // Naively tone-mapping it would invent a random gold; callers must gate
    // on hasUsableHue and fall back (paletteToCssVars does).
  });

  it('neon green #39ff14 is tamed into the band', () => {
    expect(tonePrimary(hexToOklch('#39ff14')).hex).toBe('#4be137');
  });

  it('saturated blue #3b3bee takes the L-fallback path (narrow gamut at 0.82)', () => {
    const input = hexToOklch('#3b3bee');
    expect(clipChromaC(PRIMARY_L, PRIMARY_C_MIN, input.h)).toBeLessThan(PRIMARY_C_MIN);
    const mapped = tonePrimary(input);
    expect(mapped.hex).toBe('#92a8ff');
    expect(mapped.c).toBeGreaterThanOrEqual(PRIMARY_C_MIN - 1e-6);
  });

  it('secondary derives a dim same-hue tail from the primary', () => {
    const primary = tonePrimary(hexToOklch('#e84545'));
    expect(toneSecondary(primary.c, primary.h)).toBe('#f18981');
  });

  it('background band keeps the hue as a dark tint', () => {
    expect(toneBackground(hexToOklch('#1a1a2e'))).toBe('#141327');
  });

  it('accent band renders dimmer than the primary', () => {
    expect(toneAccent(hexToOklch('#903749'))).toBe('#d47483');
  });

  it('roundtrip identity for in-gamut colors', () => {
    for (const hex of ['#e84545', '#f5d76e', '#123456', '#00ff00']) {
      const { L, C, h } = hexToOklch(hex);
      expect(oklchToHex(L, C, h)).toBe(hex);
    }
  });
});

describe('donor pass (B&W-with-one-accent covers)', () => {
  const KESHA: (Oklch | null)[] = [
    hexToOklch('#1e1e1e'), // primary: near-black
    hexToOklch('#c8a24a'), // accent: the gold
    null,
    hexToOklch('#111111'), // background
  ];

  it('picks the most chromatic usable slot', () => {
    const donor = bestHueDonor(KESHA);
    expect(donor).not.toBeNull();
    expect(donor!.C).toBeCloseTo(hexToOklch('#c8a24a').C, 6);
  });

  it('a donated hue themes the neutral primary coherently gold', () => {
    const donor = bestHueDonor(KESHA)!;
    expect(tonePrimary(donor).hex).toBe('#e0b85c');
    expect(toneBackground(donor)).toBe('#1e1400');
  });

  it('returns null when nothing is chromatic enough', () => {
    expect(bestHueDonor([hexToOklch('#808080'), hexToOklch('#111111'), null])).toBeNull();
  });
});

describe('band properties (hold for every mapped color)', () => {
  const HUES = ['#e84545', '#8a7a4a', '#f2dee3', '#39ff14', '#3b3bee', '#903749', '#c8a24a'];

  it('mapped primary vs mapped bg always clears the WCAG backstop', () => {
    for (const hex of HUES) {
      const input = hexToOklch(hex);
      const primary = tonePrimary(input).hex;
      const bg = toneBackground(input);
      expect(contrastRatio(primary, bg)).toBeGreaterThanOrEqual(3);
    }
  });

  it('mapped backgrounds sit far under the luminance clamp', () => {
    for (const hex of HUES) {
      expect(relativeLuminance(toneBackground(hexToOklch(hex)))).toBeLessThanOrEqual(
        BG_MAX_LUMINANCE,
      );
    }
  });

  it('mapped primaries are always visibly distinct from white base text', () => {
    for (const hex of HUES) {
      expect(deltaE(tonePrimary(hexToOklch(hex)).hex, '#ffffff')).toBeGreaterThanOrEqual(0.18);
    }
  });

  it('primary L never leaves its two sanctioned levels', () => {
    for (const hex of HUES) {
      const mapped = hexToOklch(tonePrimary(hexToOklch(hex)).hex);
      const nearBand = Math.abs(mapped.L - PRIMARY_L) < 0.02;
      const nearFallback = Math.abs(mapped.L - PRIMARY_L_FALLBACK) < 0.02;
      expect(nearBand || nearFallback).toBe(true);
    }
  });

  it('accent chroma stays inside its band', () => {
    for (const hex of HUES) {
      const mapped = hexToOklch(toneAccent(hexToOklch(hex)));
      expect(mapped.C).toBeGreaterThanOrEqual(ACCENT_C_MIN - 0.02); // gamut clip tolerance
      expect(mapped.C).toBeLessThanOrEqual(ACCENT_C_MAX + 0.02);
    }
  });
});
