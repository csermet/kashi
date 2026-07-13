import { describe, expect, it } from 'vitest';
import {
  clampTimingOffset,
  timingOffsetLabel,
  TIMING_OFFSET_MAX_ABS,
  TIMING_OFFSET_PRESETS,
  DEFAULT_BOX_ALPHA,
  DEFAULT_SETTINGS,
  DEFAULT_TIMING_OFFSET_MS,
  OPACITY_MAX,
  OPACITY_PRESETS,
  adjustAlpha,
  adjustTimingOffset,
  clampAlpha,
  isPositionVisible,
  nearestPresetIndex,
  parseSettings,
  presetLabel,
  sanitizeDeltaSteps,
} from './settings-logic.js';

describe('clampAlpha', () => {
  it('clamps into [0, max] and rounds to 2 decimals', () => {
    expect(clampAlpha(-1)).toBe(0);
    expect(clampAlpha(0.123456)).toBe(0.12);
    expect(clampAlpha(9)).toBe(OPACITY_MAX);
  });

  it('falls back to the default on non-finite input', () => {
    expect(clampAlpha(Number.NaN)).toBe(DEFAULT_BOX_ALPHA);
    expect(clampAlpha(Number.POSITIVE_INFINITY)).toBe(DEFAULT_BOX_ALPHA);
  });
});

describe('sanitizeDeltaSteps', () => {
  it('coerces IPC garbage to 0', () => {
    expect(sanitizeDeltaSteps('3')).toBe(0);
    expect(sanitizeDeltaSteps(undefined)).toBe(0);
    expect(sanitizeDeltaSteps(Number.NaN)).toBe(0);
  });

  it('truncates and caps magnitude at 5', () => {
    expect(sanitizeDeltaSteps(1.9)).toBe(1);
    expect(sanitizeDeltaSteps(-1.9)).toBe(-1);
    expect(sanitizeDeltaSteps(1000)).toBe(5);
    expect(sanitizeDeltaSteps(-1000)).toBe(-5);
  });
});

describe('adjustAlpha', () => {
  it('moves by whole steps and clamps at the edges', () => {
    expect(adjustAlpha(0.1, 1)).toBeCloseTo(0.12);
    expect(adjustAlpha(0.1, -1)).toBeCloseTo(0.08);
    expect(adjustAlpha(0.01, -5)).toBe(0);
    expect(adjustAlpha(0.85, 5)).toBe(OPACITY_MAX);
  });

  it('recovers from a corrupt current value instead of propagating NaN', () => {
    expect(adjustAlpha(Number.NaN, 1)).toBeCloseTo(DEFAULT_BOX_ALPHA + 0.02);
  });
});

describe('adjustTimingOffset', () => {
  it('moves in 10 ms steps and clamps at ±max', () => {
    expect(adjustTimingOffset(0, 1)).toBe(10);
    expect(adjustTimingOffset(120, -3)).toBe(90);
    expect(adjustTimingOffset(TIMING_OFFSET_MAX_ABS - 10, 5)).toBe(TIMING_OFFSET_MAX_ABS);
    expect(adjustTimingOffset(-TIMING_OFFSET_MAX_ABS, -1)).toBe(-TIMING_OFFSET_MAX_ABS);
  });

  it('sanitizes IPC garbage on both axes', () => {
    expect(adjustTimingOffset(Number.NaN, 2)).toBe(DEFAULT_TIMING_OFFSET_MS + 20);
    expect(adjustTimingOffset(100, Number.NaN)).toBe(100);
  });
});

describe('presets', () => {
  it('labels 0 as Off and the rest as percentages', () => {
    expect(presetLabel(0)).toBe('Off');
    expect(presetLabel(0.05)).toBe('5%');
    expect(presetLabel(0.3)).toBe('30%');
    expect(presetLabel(0.8)).toBe('80%');
  });

  it('matches presets with tolerance and reports custom values as -1', () => {
    expect(nearestPresetIndex(0.1)).toBe(OPACITY_PRESETS.indexOf(0.1));
    expect(nearestPresetIndex(0.101)).toBe(OPACITY_PRESETS.indexOf(0.1));
    expect(nearestPresetIndex(0.12)).toBe(-1);
  });
});

describe('isPositionVisible', () => {
  const primary = { workArea: { x: 0, y: 0, width: 1920, height: 1040 } };
  const secondary = { workArea: { x: 1920, y: 0, width: 1280, height: 1024 } };
  const win = { width: 560, height: 180 };

  it('accepts bounds well inside a display', () => {
    expect(isPositionVisible({ x: 100, y: 100, ...win }, [primary])).toBe(true);
  });

  it('accepts bounds on a secondary display', () => {
    expect(isPositionVisible({ x: 2000, y: 300, ...win }, [primary, secondary])).toBe(true);
  });

  it('rejects bounds on an unplugged display', () => {
    // Window saved on the secondary, which is now gone.
    expect(isPositionVisible({ x: 2000, y: 300, ...win }, [primary])).toBe(false);
  });

  it('rejects bounds with only a sliver visible', () => {
    // 100 px visible < the 120 px minimum.
    expect(isPositionVisible({ x: -460, y: 100, ...win }, [primary])).toBe(false);
    // Fully above the work area except 20 px.
    expect(isPositionVisible({ x: 100, y: -160, ...win }, [primary])).toBe(false);
  });

  it('accepts bounds straddling the display edge with enough visible', () => {
    expect(isPositionVisible({ x: -200, y: 100, ...win }, [primary])).toBe(true);
  });

  it('rejects everything when there are no displays', () => {
    expect(isPositionVisible({ x: 0, y: 0, ...win }, [])).toBe(false);
  });
});

describe('parseSettings', () => {
  it('returns defaults for corrupt JSON', () => {
    expect(parseSettings('{oops')).toEqual(DEFAULT_SETTINGS);
    expect(parseSettings('null')).toEqual(DEFAULT_SETTINGS);
    expect(parseSettings('"str"')).toEqual(DEFAULT_SETTINGS);
  });

  it('parses a round-tripped settings file', () => {
    const stored = {
      schema_version: 1,
      box_alpha: 0.2,
      window_bounds: { x: 10, y: 20, width: 560, height: 180 },
      server_url: 'http://cnr-intel:8080',
      server_api_key: 'ksh_' + 'a'.repeat(32),
      timing_offset_ms: 100,
      effect_level: 'full',
      theme_scope: 'fixed-text',
    };
    expect(parseSettings(JSON.stringify(stored))).toEqual(stored);
  });

  it('normalizes the server fields (hand-edited JSON is untrusted)', () => {
    const parsed = parseSettings(
      JSON.stringify({ server_url: 'http://cnr-intel:8080///', server_api_key: '  ksh_x  ' }),
    );
    expect(parsed.server_url).toBe('http://cnr-intel:8080');
    expect(parsed.server_api_key).toBe('ksh_x');
    const bad = parseSettings(JSON.stringify({ server_url: 'not a url', server_api_key: '' }));
    expect(bad.server_url).toBeNull();
    expect(bad.server_api_key).toBeNull();
  });

  it('clamps out-of-range alpha and drops malformed bounds', () => {
    const parsed = parseSettings(
      JSON.stringify({ box_alpha: 7, window_bounds: { x: 'a', y: 0, width: 1, height: 1 } }),
    );
    expect(parsed.box_alpha).toBe(OPACITY_MAX);
    expect(parsed.window_bounds).toBeNull();
  });

  it('drops zero/negative-sized bounds', () => {
    const parsed = parseSettings(
      JSON.stringify({ window_bounds: { x: 0, y: 0, width: 0, height: 180 } }),
    );
    expect(parsed.window_bounds).toBeNull();
  });

  it('preserves unknown fields through a round-trip (rollback-safe)', () => {
    // A newer kashi wrote font_size; this build must not strip it on save.
    const parsed = parseSettings(
      JSON.stringify({ box_alpha: 0.05, font_size: 32, window_bounds: null }),
    );
    expect(parsed.box_alpha).toBe(0.05);
    expect((parsed as unknown as Record<string, unknown>)['font_size']).toBe(32);
    const roundTripped = parseSettings(JSON.stringify(parsed));
    expect((roundTripped as unknown as Record<string, unknown>)['font_size']).toBe(32);
  });
});

describe('clampTimingOffset', () => {
  it('accepts presets, rounds, clamps to +/-500, defaults garbage to Off', () => {
    expect(clampTimingOffset(100)).toBe(100);
    expect(clampTimingOffset(-100)).toBe(-100);
    expect(clampTimingOffset(100.6)).toBe(101);
    expect(clampTimingOffset(9999)).toBe(500);
    expect(clampTimingOffset(-9999)).toBe(-500);
    expect(clampTimingOffset('50')).toBe(0);
    expect(clampTimingOffset(Number.NaN)).toBe(0);
    expect(clampTimingOffset(undefined)).toBe(0);
  });

  it('missing field in an old settings file parses to Off', () => {
    expect(parseSettings(JSON.stringify({ box_alpha: 0.2 })).timing_offset_ms).toBe(0);
  });
});

describe('timingOffsetLabel', () => {
  it('labels the direction in UX terms', () => {
    expect(timingOffsetLabel(0)).toBe('Off');
    expect(timingOffsetLabel(100)).toBe('+100 ms (earlier)');
    expect(timingOffsetLabel(-50)).toBe('-50 ms (later)');
  });
});

describe('theme_scope setting', () => {
  it('defaults to full and round-trips', () => {
    expect(DEFAULT_SETTINGS.theme_scope).toBe('full');
    expect(parseSettings(JSON.stringify({ theme_scope: 'fixed-bg' })).theme_scope).toBe('fixed-bg');
    expect(parseSettings(JSON.stringify({ theme_scope: 'renkler' })).theme_scope).toBe('full');
  });
});

describe('effect_level setting', () => {
  it('defaults to simple and round-trips through parseSettings', () => {
    expect(DEFAULT_SETTINGS.effect_level).toBe('simple');
    expect(parseSettings(JSON.stringify({ effect_level: 'full' })).effect_level).toBe('full');
    expect(parseSettings(JSON.stringify({ effect_level: 'off' })).effect_level).toBe('off');
    // Garbage/missing → default (tolerant parse, hand-edited files).
    expect(parseSettings(JSON.stringify({ effect_level: 'ULTRA' })).effect_level).toBe('simple');
    expect(parseSettings(JSON.stringify({ box_alpha: 0.2 })).effect_level).toBe('simple');
  });
});

describe('TIMING_OFFSET_PRESETS', () => {
  it('is the symmetric -250..+250 ladder in 50 ms steps, sorted, with Off', () => {
    expect([...TIMING_OFFSET_PRESETS]).toEqual([
      -250, -200, -150, -100, -50, 0, 50, 100, 150, 200, 250,
    ]);
    // Every preset survives the clamp unchanged (list stays inside ±MAX_ABS).
    for (const preset of TIMING_OFFSET_PRESETS) {
      expect(clampTimingOffset(preset)).toBe(preset);
    }
  });
});
