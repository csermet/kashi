import { describe, expect, it } from 'vitest';
import {
  WHEEL_PX_PER_STEP,
  accumulateWheel,
  deriveView,
  watchdogShouldReset,
} from './view-logic.js';

describe('deriveView', () => {
  const base = {
    adActive: false,
    hasLines: false,
    activeText: null,
    statusText: 'Kashi',
    statusDim: true,
    searching: false,
  };

  it('hides the box entirely during ads, whatever else is set', () => {
    const view = deriveView({
      ...base,
      adActive: true,
      hasLines: true,
      activeText: 'la la',
      searching: true,
    });
    expect(view).toEqual({ boxVisible: false, lineText: '', lineDim: false, searchVisible: false });
  });

  it('shows the active lyric line bright', () => {
    const view = deriveView({ ...base, hasLines: true, activeText: 'Never gonna give you up' });
    expect(view).toEqual({
      boxVisible: true,
      lineText: 'Never gonna give you up',
      lineDim: false,
      searchVisible: false,
    });
  });

  it('shows ♪ during intros/instrumental gaps (never a blank box)', () => {
    const view = deriveView({ ...base, hasLines: true, activeText: null });
    expect(view.lineText).toBe('♪');
    expect(view.boxVisible).toBe(true);
  });

  it('lyrics win over a stale searching flag', () => {
    const view = deriveView({ ...base, hasLines: true, activeText: 'line', searching: true });
    expect(view.searchVisible).toBe(false);
  });

  it('shows the idle badge dim with no search row', () => {
    const view = deriveView(base);
    expect(view).toEqual({
      boxVisible: true,
      lineText: 'Kashi',
      lineDim: true,
      searchVisible: false,
    });
  });

  it('shows the track label plus the searching row during lookup', () => {
    const view = deriveView({
      ...base,
      statusText: '♪ Artist — Title',
      statusDim: false,
      searching: true,
    });
    expect(view).toEqual({
      boxVisible: true,
      lineText: '♪ Artist — Title',
      lineDim: false,
      searchVisible: true,
    });
  });
});

describe('watchdogShouldReset', () => {
  it('trips on a playing clock starved of positions', () => {
    expect(watchdogShouldReset(true, false, 10_001)).toBe(true);
  });

  it('does not trip below the threshold', () => {
    expect(watchdogShouldReset(true, false, 9_999)).toBe(false);
  });

  it('does not trip while paused (no extrapolation, nothing to kill)', () => {
    expect(watchdogShouldReset(false, false, 60_000)).toBe(false);
  });

  it('does not trip during a normal-length ad — position silence there is deliberate', () => {
    expect(watchdogShouldReset(true, true, 60_000)).toBe(false);
  });

  it('DOES trip during an "ad" that outlives any real ad break (dead source)', () => {
    // Content script died mid-ad: ad_state=false never arrives — the long
    // leash must still catch it or the overlay stays invisible forever.
    expect(watchdogShouldReset(true, true, 180_001)).toBe(true);
  });

  it('honors custom thresholds', () => {
    expect(watchdogShouldReset(true, false, 5_001, 5_000)).toBe(true);
    expect(watchdogShouldReset(true, true, 5_001, 1_000, 5_000)).toBe(true);
    expect(watchdogShouldReset(true, true, 4_999, 1_000, 5_000)).toBe(false);
  });
});

describe('accumulateWheel', () => {
  it('ignores pure horizontal scroll (deltaY 0) — no accidental dimming', () => {
    expect(accumulateWheel(30, 0, 0)).toEqual({ accumulatedPx: 30, steps: 0 });
  });

  it('converts one classic wheel notch (±100 px) into exactly one step', () => {
    expect(accumulateWheel(0, 100, 0)).toEqual({ accumulatedPx: 0, steps: 1 });
    expect(accumulateWheel(0, -100, 0)).toEqual({ accumulatedPx: 0, steps: -1 });
  });

  it('accumulates touchpad micro-deltas into whole steps with remainder', () => {
    let acc = 0;
    let totalSteps = 0;
    // A two-finger swipe: 30 events of 8 px = 240 px → 2 steps, 40 px left over.
    for (let i = 0; i < 30; i++) {
      const r = accumulateWheel(acc, 8, 0);
      acc = r.accumulatedPx;
      totalSteps += r.steps;
    }
    expect(totalSteps).toBe(2);
    expect(acc).toBe(240 - 2 * WHEEL_PX_PER_STEP);
  });

  it('normalizes line-mode deltas (deltaMode 1)', () => {
    // 5 lines × 20 px = 100 px → one step.
    expect(accumulateWheel(0, 5, 1)).toEqual({ accumulatedPx: 0, steps: 1 });
  });

  it('carries direction changes through the accumulator', () => {
    const up = accumulateWheel(0, -60, 0); // -60 px, no step yet
    expect(up.steps).toBe(0);
    const down = accumulateWheel(up.accumulatedPx, 60, 0); // back to 0
    expect(down).toEqual({ accumulatedPx: 0, steps: 0 });
  });

  it('survives non-finite deltas', () => {
    expect(accumulateWheel(10, Number.NaN, 0)).toEqual({ accumulatedPx: 10, steps: 0 });
  });
});
