import { describe, expect, it } from 'vitest';
import { WHEEL_PX_PER_STEP, accumulateWheel } from './wheel.js';

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
