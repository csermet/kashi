import { describe, expect, it } from 'vitest';
import {
  DEFAULT_EFFECT_LEVEL,
  effectLevelLabel,
  parseEffectLevel,
  parseThemeScope,
} from '../../shared/effect-level.js';
import {
  BEAT_IDLE,
  BEAT_RESYNC_JUMP_MS,
  BEAT_WINDOW_AFTER_MS,
  BEAT_WINDOW_BEFORE_MS,
  BG_MAX_LUMINANCE,
  BeatCursor,
  DEFAULT_PALETTE_VARS,
  FX_BURST_TAGS,
  TEXT_CONTRAST_MIN,
  beatsUsable,
  buildFxIndex,
  clampBackground,
  computeFxTintVars,
  FX_BASE_COLORS,
  energyAt,
  inSection,
  quantizedEnergy,
  contrastRatio,
  fillProgress,
  paletteToCssVars,
  planWordFills,
  relativeLuminance,
} from './effects-logic.js';
import { NEUTRAL_BG_TRIPLET, hexToOklch, hueDistance } from './color-tone.js';

describe('parseEffectLevel', () => {
  it('accepts the four levels and defaults everything else', () => {
    expect(parseEffectLevel('off')).toBe('off');
    expect(parseEffectLevel('simple')).toBe('simple');
    expect(parseEffectLevel('full')).toBe('full');
    expect(parseEffectLevel('hype')).toBe('hype');
    expect(parseEffectLevel('FULL')).toBe(DEFAULT_EFFECT_LEVEL);
    expect(parseEffectLevel(2)).toBe(DEFAULT_EFFECT_LEVEL);
    expect(parseEffectLevel(undefined)).toBe(DEFAULT_EFFECT_LEVEL);
    // A `full` user must see zero change from the Faz 6 upgrade.
    expect(DEFAULT_EFFECT_LEVEL).toBe('simple');
  });

  it('labels levels for the tray', () => {
    expect(effectLevelLabel('off')).toBe('Off');
    expect(effectLevelLabel('simple')).toBe('Simple');
    expect(effectLevelLabel('full')).toBe('Full');
    expect(effectLevelLabel('hype')).toBe('Hype');
  });

  it('parses theme scopes, defaulting garbage to full', () => {
    expect(parseThemeScope('fixed-bg')).toBe('fixed-bg');
    expect(parseThemeScope('fixed-text')).toBe('fixed-text');
    expect(parseThemeScope('none')).toBe('none');
    expect(parseThemeScope('ULTRA')).toBe('full');
    expect(parseThemeScope(undefined)).toBe('full');
  });
});

describe('paletteToCssVars', () => {
  const RICK = {
    primary: '#e84545',
    secondary: '#f5d76e',
    background: '#1a1a2e',
    text: '#ffffff',
    accent: '#903749',
  };

  it('maps a valid palette onto TONE-MAPPED variables (0.2.4)', () => {
    const vars = paletteToCssVars(RICK);
    expect(vars['--kashi-primary']).toBe('#ff847c'); // harsh red → soft coral
    expect(vars['--kashi-secondary']).toBe('#f18981'); // dim of the PRIMARY hue
    expect(vars['--kashi-bg-rgb']).toBe('20, 19, 39'); // bg band dark tint
    expect(vars['--kashi-text']).toBe('#ffffff'); // base text pinned white
    expect(vars['--kashi-accent']).toBe('#d47483'); // accent band
  });

  it('no palette → the defaults (= the pre-Faz-4 look)', () => {
    expect(paletteToCssVars(undefined)).toEqual(DEFAULT_PALETTE_VARS);
  });

  it('invalid colors never reach CSS; the valid hue themes the theme (donor)', () => {
    const vars = paletteToCssVars({
      primary: 'red',
      secondary: '#12345',
      background: 'url(javascript:x)',
      text: 42,
      accent: '#903749',
    });
    // The one valid slot (accent) becomes the hue donor: primary/secondary/bg
    // all render as tone-mapped versions of ITS hue — never raw, never CSS-
    // injectable garbage.
    expect(vars['--kashi-accent']).toBe('#d47483');
    expect(vars['--kashi-primary']).toMatch(/^#[0-9a-f]{6}$/);
    expect(vars['--kashi-primary']).not.toBe(DEFAULT_PALETTE_VARS['--kashi-primary']);
    expect(vars['--kashi-bg-rgb']).toMatch(/^\d+, \d+, \d+$/);
    expect(vars['--kashi-text']).toBe('#ffffff');
  });

  it('dark hued primaries are RESCUED, not whited out (0.2.4 feature)', () => {
    // 0.2.3 dropped #1a1a2e to white via the luminance floor; the tone mapper
    // now re-renders its hue at a readable band instead — that is the point.
    const vars = paletteToCssVars({ primary: '#1a1a2e', text: '#0a0a0a' });
    expect(vars['--kashi-primary']).not.toBe('#ffffff');
    expect(relativeLuminance(vars['--kashi-primary']!)).toBeGreaterThan(0.4);
    expect(vars['--kashi-text']).toBe('#ffffff'); // base text stays pinned
    // A truly neutral background with no donor lands on the honest dark gray.
    expect(paletteToCssVars({ background: '#0a0a0a' })['--kashi-bg-rgb']).toBe(
      NEUTRAL_BG_TRIPLET,
    );
  });
});

describe('sustained fill', () => {
  const LONG = { start_ms: 1000, end_ms: 2200 }; // ≥ 800 ms hold

  // Compact word builder: durations in ms, chained.
  const words = (...durations: number[]) => {
    let t = 0;
    return durations.map((d) => {
      const w = { start_ms: t, end_ms: t + d };
      t += d;
      return w;
    });
  };

  it('ad-lib lines sweep every word; off level never sweeps', () => {
    expect(planWordFills(words(200, 300, 900), true, 'simple')).toEqual([true, true, true]);
    expect(planWordFills(words(200, 300, 900), true, 'off')).toEqual([false, false, false]);
    expect(planWordFills([], true, 'full')).toEqual([]);
  });

  it('a sustained LAST word sweeps alone (line-end hold)', () => {
    expect(planWordFills(words(200, 300, 900), false, 'simple')).toEqual([false, false, true]);
  });

  it('an isolated mid-line sustained word pops like its neighbours', () => {
    // Field feedback: per-word sweep/pop alternation reads as random.
    expect(planWordFills(words(200, 900, 300), false, 'simple')).toEqual([false, false, false]);
  });

  it('mid-line runs of >= 2 sustained words sweep together', () => {
    expect(planWordFills(words(200, 900, 850, 300), false, 'full')).toEqual([
      false,
      true,
      true,
      false,
    ]);
    expect(planWordFills(words(900, 850, 950), false, 'full')).toEqual([true, true, true]);
  });

  it('short lines with no sustained words never sweep', () => {
    expect(planWordFills(words(200, 300, 250), false, 'full')).toEqual([false, false, false]);
  });

  it('progress is clamped 0..1 across the word span', () => {
    expect(fillProgress(LONG, 500)).toBe(0); // before
    expect(fillProgress(LONG, 1000)).toBe(0);
    expect(fillProgress(LONG, 1600)).toBeCloseTo(0.5);
    expect(fillProgress(LONG, 2200)).toBe(1);
    expect(fillProgress(LONG, 9000)).toBe(1); // after
    expect(fillProgress({ start_ms: 1000, end_ms: 1000 }, 1000)).toBe(1); // zero span
  });
});

describe('color rules (field feedback: readability is a RULE, not luck)', () => {
  it('clamps light backgrounds dark, keeping grays gray', () => {
    const clamped = clampBackground('#f5f5f5');
    expect(relativeLuminance(clamped)).toBeLessThanOrEqual(BG_MAX_LUMINANCE);
    // Channels scale together — a gray stays a gray.
    expect(clamped[1]).toBe(clamped[3]);
    // Already-dark backgrounds pass through unchanged.
    expect(clampBackground('#1a1a2e')).toBe('#1a1a2e');
  });

  it('light palette backgrounds land dark in the CSS vars', () => {
    const vars = paletteToCssVars({ background: '#ffffff' });
    expect(vars['--kashi-bg-rgb']).not.toBe('255, 255, 255');
    const [r, g, b] = vars['--kashi-bg-rgb']!.split(',').map((v) => Number(v.trim()));
    const hex = `#${[r, g, b].map((v) => v!.toString(16).padStart(2, '0')).join('')}`;
    expect(relativeLuminance(hex)).toBeLessThanOrEqual(BG_MAX_LUMINANCE);
  });

  it('a dark muddy primary is rescued into a readable band (was: white fallback)', () => {
    // 0.2.3 whited this out; the tone mapper keeps the hue and guarantees
    // the contrast by construction.
    const vars = paletteToCssVars({ background: '#1a1a2e', primary: '#5a2020' });
    expect(vars['--kashi-primary']).not.toBe('#ffffff');
    const [r, g, b] = vars['--kashi-bg-rgb']!.split(',').map((v) => Number(v.trim()));
    const bgHex = `#${[r, g, b].map((v) => v!.toString(16).padStart(2, '0')).join('')}`;
    expect(contrastRatio(vars['--kashi-primary']!, bgHex)).toBeGreaterThanOrEqual(
      TEXT_CONTRAST_MIN,
    );
  });

  it('theme scope pins color groups (field setting)', () => {
    const palette = {
      primary: '#e84545',
      background: '#1a1a2e',
      text: '#f5d76e',
      accent: '#903749',
    };
    const fixedBg = paletteToCssVars(palette, 'fixed-bg');
    expect(fixedBg['--kashi-bg-rgb']).toBe(DEFAULT_PALETTE_VARS['--kashi-bg-rgb']);
    expect(fixedBg['--kashi-text']).toBe('#ffffff'); // base text pinned (0.2.4)
    expect(fixedBg['--kashi-primary']).toBe('#ff847c'); // effect colors themed

    const fixedText = paletteToCssVars(palette, 'fixed-text');
    expect(fixedText['--kashi-bg-rgb']).toBe(DEFAULT_PALETTE_VARS['--kashi-bg-rgb']);
    expect(fixedText['--kashi-text']).toBe(DEFAULT_PALETTE_VARS['--kashi-text']);
    expect(fixedText['--kashi-primary']).toBe('#ff847c'); // effect colors still theme

    expect(paletteToCssVars(palette, 'none')).toEqual(DEFAULT_PALETTE_VARS);
  });
});

describe('beatsUsable', () => {
  const BEATS = { bpm: 113, confidence: 0.9, times_ms: [480, 1010], downbeat_indices: [0] };

  it('requires full level, a non-empty grid and confidence >= 0.5', () => {
    expect(beatsUsable('full', BEATS)).toBe(true);
    expect(beatsUsable('simple', BEATS)).toBe(false);
    expect(beatsUsable('off', BEATS)).toBe(false);
    expect(beatsUsable('full', undefined)).toBe(false);
    expect(beatsUsable('full', { ...BEATS, times_ms: [] })).toBe(false);
    expect(beatsUsable('full', { ...BEATS, confidence: 0.4 })).toBe(false);
    expect(beatsUsable('full', { ...BEATS, confidence: undefined })).toBe(false);
    expect(beatsUsable('full', { ...BEATS, times_ms: [480, 'x'] })).toBe(false);
  });
});

describe('BeatCursor', () => {
  const TIMES = [1000, 1500, 2000, 2500, 3000];

  it('pulses exactly inside [t-30, t+60] (edges inclusive)', () => {
    const cursor = new BeatCursor(TIMES);
    expect(cursor.frame(1000 - BEAT_WINDOW_BEFORE_MS - 1).active).toBe(false);
    expect(cursor.frame(1000 - BEAT_WINDOW_BEFORE_MS).active).toBe(true);
    expect(cursor.frame(1000).active).toBe(true);
    expect(cursor.frame(1000 + BEAT_WINDOW_AFTER_MS).active).toBe(true);
    expect(cursor.frame(1000 + BEAT_WINDOW_AFTER_MS + 1).active).toBe(false);
  });

  it('advances monotonically through the grid', () => {
    const cursor = new BeatCursor(TIMES, [2]); // 2000 is a downbeat
    expect(cursor.frame(900)).toEqual(BEAT_IDLE);
    expect(cursor.frame(1005)).toEqual({ active: true, down: false });
    expect(cursor.frame(1200)).toEqual(BEAT_IDLE); // between beats
    expect(cursor.frame(1500)).toEqual({ active: true, down: false });
    expect(cursor.frame(2010)).toEqual({ active: true, down: true });
    expect(cursor.frame(3200)).toEqual(BEAT_IDLE); // past the last beat
    expect(cursor.frame(9999)).toEqual(BEAT_IDLE); // stays idle at the end
  });

  it('re-seeks on backward jumps (seek back)', () => {
    const cursor = new BeatCursor(TIMES, [0]); // the 1000 ms beat is a downbeat
    cursor.frame(2510); // advance deep into the grid
    expect(cursor.frame(1010)).toEqual({ active: true, down: true });
    expect(cursor.frame(995)).toEqual({ active: true, down: true }); // small back-slew
  });

  it('re-seeks on forward jumps beyond the resync threshold', () => {
    const long = Array.from({ length: 10_000 }, (_, i) => i * 500);
    const cursor = new BeatCursor(long);
    cursor.frame(0);
    // Jump far forward — must land accurately (binary search, not a scan).
    const jump = 4_000_000;
    expect(jump).toBeGreaterThan(BEAT_RESYNC_JUMP_MS);
    expect(cursor.frame(jump).active).toBe(true); // 4_000_000 is on the grid
    expect(cursor.frame(jump + 100).active).toBe(false);
  });

  it('handles a paused position (repeated frames at the same pos)', () => {
    const cursor = new BeatCursor(TIMES);
    expect(cursor.frame(1500).active).toBe(true);
    expect(cursor.frame(1500).active).toBe(true); // idempotent, no advance past
  });
});

describe('buildFxIndex (Faz 6 hype)', () => {
  const lines = [
    {
      start_ms: 0,
      end_ms: 2000,
      text: 'the bomb explodes with love',
      words: [
        { start_ms: 0, end_ms: 400, text: 'the' },
        { start_ms: 400, end_ms: 900, text: 'bomb' },
        { start_ms: 900, end_ms: 1400, text: 'explodes' },
        { start_ms: 1400, end_ms: 1700, text: 'with' },
        { start_ms: 1700, end_ms: 2000, text: 'love' },
      ],
    },
    { start_ms: 2000, end_ms: 4000, text: 'wordless line' },
  ];
  const fx = (words: unknown[]) =>
    ({ lexicon: 'kashi-fx/1.0.0', engine: 'keywords', words }) as never;

  it('keeps ONE winner per line: highest intensity, earliest word on ties', () => {
    const index = buildFxIndex(
      fx([
        { line: 0, word: 4, tag: 'love', intensity: 0.6 },
        { line: 0, word: 1, tag: 'explosion', intensity: 0.9 },
        { line: 0, word: 2, tag: 'explosion', intensity: 0.9 },
      ]),
      lines,
    );
    expect(index.size).toBe(1);
    expect(index.get(0)).toEqual({ word: 1, effect: { tag: 'explosion', intensity: 0.9 } });
  });

  it('drops out-of-range indices and wordless-line tags (quality-gate strips)', () => {
    const index = buildFxIndex(
      fx([
        { line: 0, word: 99, tag: 'love', intensity: 0.6 },
        { line: 1, word: 0, tag: 'love', intensity: 0.6 },
        { line: 7, word: 0, tag: 'love', intensity: 0.6 },
      ]),
      lines,
    );
    expect(index.size).toBe(0);
  });

  it('clamps intensity into [0,1] and tolerates missing fx', () => {
    const index = buildFxIndex(fx([{ line: 0, word: 1, tag: 'fire', intensity: 7 }]), lines);
    expect(index.get(0)!.effect.intensity).toBe(1);
    expect(buildFxIndex(undefined, lines).size).toBe(0);
  });

  it('burst tags are a small fixed set', () => {
    expect(FX_BURST_TAGS.has('explosion')).toBe(true);
    expect(FX_BURST_TAGS.has('love')).toBe(false);
  });
});

describe('energy/section dynamics (Faz 6 P5)', () => {
  const energy = { rate_hz: 2, values: [0, 20, 40, 60, 80, 100] }; // 3s clip

  it('energyAt is O(1) position lookup with edge clamping', () => {
    expect(energyAt(energy, 0)).toBe(0);
    expect(energyAt(energy, 500)).toBe(0.2); // sample 1
    expect(energyAt(energy, 2500)).toBe(1);
    expect(energyAt(energy, 99_000)).toBe(1); // clamps to the last sample
    expect(energyAt(energy, -50)).toBe(0);
    expect(energyAt(undefined, 1000)).toBe(0);
    expect(energyAt({ rate_hz: 0, values: [50] }, 0)).toBe(0);
  });

  it('quantizedEnergy steps by ENERGY_QUANT (style writes only on change)', () => {
    const e = { rate_hz: 2, values: [37] };
    expect(quantizedEnergy(e, 0)).toBeCloseTo(0.35);
    expect(quantizedEnergy(e, 0) === quantizedEnergy(e, 400)).toBe(true);
  });

  it('inSection matches only the asked type inside [start, end)', () => {
    const sections = [
      { type: 'high', start_ms: 10_000, end_ms: 20_000 },
      { type: 'chorus', start_ms: 30_000, end_ms: 40_000 },
    ];
    expect(inSection(sections, 'high', 9_999)).toBe(false);
    expect(inSection(sections, 'high', 10_000)).toBe(true);
    expect(inSection(sections, 'high', 19_999)).toBe(true);
    expect(inSection(sections, 'high', 20_000)).toBe(false);
    expect(inSection(sections, 'high', 35_000)).toBe(false); // other type
    expect(inSection(undefined, 'high', 0)).toBe(false);
  });
});

describe('computeFxTintVars (Faz 6 field round 2)', () => {
  it('emits one var per category; scope none emits nothing (stock contract)', () => {
    const vars = computeFxTintVars('#ff847c', 'full');
    expect(Object.keys(vars).length).toBe(Object.keys(FX_BASE_COLORS).length);
    expect(vars['--fx-tint-poison']).toMatch(/^#[0-9a-f]{6}$/);
    expect(computeFxTintVars('#ff847c', 'none')).toEqual({});
  });

  it('tints are valid hex and keep the category hue (0.7.1 own-vividness)', () => {
    const vars = computeFxTintVars(undefined, 'full');
    for (const [tag, base] of Object.entries(FX_BASE_COLORS)) {
      const tint = vars[`--fx-tint-${tag}`]!;
      expect(tint).toMatch(/^#[0-9a-f]{6}$/);
      expect(hueDistance(hexToOklch(tint).h, hexToOklch(base).h)).toBeLessThan(0.2);
    }
  });
});
