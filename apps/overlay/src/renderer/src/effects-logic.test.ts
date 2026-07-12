import { describe, expect, it } from 'vitest';
import {
  DEFAULT_EFFECT_LEVEL,
  effectLevelLabel,
  parseEffectLevel,
} from '../../shared/effect-level.js';
import {
  BEAT_IDLE,
  BEAT_RESYNC_JUMP_MS,
  BEAT_WINDOW_AFTER_MS,
  BEAT_WINDOW_BEFORE_MS,
  BeatCursor,
  DEFAULT_PALETTE_VARS,
  TEXT_LUMINANCE_FLOOR,
  beatsUsable,
  fillProgress,
  isFillWord,
  paletteToCssVars,
  relativeLuminance,
} from './effects-logic.js';

describe('parseEffectLevel', () => {
  it('accepts the three levels and defaults everything else', () => {
    expect(parseEffectLevel('off')).toBe('off');
    expect(parseEffectLevel('simple')).toBe('simple');
    expect(parseEffectLevel('full')).toBe('full');
    expect(parseEffectLevel('FULL')).toBe(DEFAULT_EFFECT_LEVEL);
    expect(parseEffectLevel(2)).toBe(DEFAULT_EFFECT_LEVEL);
    expect(parseEffectLevel(undefined)).toBe(DEFAULT_EFFECT_LEVEL);
  });

  it('labels levels for the tray', () => {
    expect(effectLevelLabel('off')).toBe('Off');
    expect(effectLevelLabel('simple')).toBe('Simple');
    expect(effectLevelLabel('full')).toBe('Full');
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

  it('maps a valid palette onto the variables', () => {
    const vars = paletteToCssVars(RICK);
    expect(vars['--kashi-primary']).toBe('#e84545');
    expect(vars['--kashi-secondary']).toBe('#f5d76e');
    expect(vars['--kashi-bg-rgb']).toBe('26, 26, 46'); // hex → decimal triplet
    expect(vars['--kashi-text']).toBe('#ffffff');
    expect(vars['--kashi-accent']).toBe('#903749');
  });

  it('no palette → the defaults (= the pre-Faz-4 look)', () => {
    expect(paletteToCssVars(undefined)).toEqual(DEFAULT_PALETTE_VARS);
  });

  it('falls back per field on invalid colors (untrusted IPC never reaches CSS)', () => {
    const vars = paletteToCssVars({
      primary: 'red',
      secondary: '#12345',
      background: 'url(javascript:x)',
      text: 42,
      accent: '#903749',
    });
    expect(vars['--kashi-primary']).toBe(DEFAULT_PALETTE_VARS['--kashi-primary']);
    expect(vars['--kashi-secondary']).toBe(DEFAULT_PALETTE_VARS['--kashi-secondary']);
    expect(vars['--kashi-bg-rgb']).toBe(DEFAULT_PALETTE_VARS['--kashi-bg-rgb']);
    expect(vars['--kashi-text']).toBe(DEFAULT_PALETTE_VARS['--kashi-text']);
    expect(vars['--kashi-accent']).toBe('#903749'); // the one valid field survives
  });

  it('floors near-black TEXT colors to stay readable, keeps mid-tones', () => {
    expect(relativeLuminance('#1a1a2e')).toBeLessThan(TEXT_LUMINANCE_FLOOR);
    expect(relativeLuminance('#e84545')).toBeGreaterThan(TEXT_LUMINANCE_FLOOR);
    const vars = paletteToCssVars({ primary: '#1a1a2e', text: '#0a0a0a' });
    expect(vars['--kashi-primary']).toBe('#ffffff');
    expect(vars['--kashi-text']).toBe('#ffffff');
    // The floor applies to text-carrying colors only — bg keeps dark values.
    expect(paletteToCssVars({ background: '#0a0a0a' })['--kashi-bg-rgb']).toBe('10, 10, 10');
  });
});

describe('sustained fill', () => {
  const SHORT = { start_ms: 1000, end_ms: 1400 };
  const LONG = { start_ms: 1000, end_ms: 2200 }; // ≥ 800 ms hold

  it('eligible for long-held words and any ad-lib line word, never at off', () => {
    expect(isFillWord(LONG, false, 'simple')).toBe(true);
    expect(isFillWord(LONG, false, 'full')).toBe(true);
    expect(isFillWord(SHORT, false, 'simple')).toBe(false);
    expect(isFillWord(SHORT, true, 'simple')).toBe(true); // ad-lib line
    expect(isFillWord(LONG, true, 'off')).toBe(false);
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
