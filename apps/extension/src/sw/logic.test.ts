import { describe, expect, it } from 'vitest';
import { backoffDelayMs, selectActiveTab } from './logic.js';

describe('backoffDelayMs', () => {
  const noJitter = () => 0.5; // random()=0.5 → jitter factor 1.0

  it('follows the 1→2→5→10→30 s ladder and caps at 30 s', () => {
    expect(backoffDelayMs(0, noJitter)).toBe(1000);
    expect(backoffDelayMs(1, noJitter)).toBe(2000);
    expect(backoffDelayMs(2, noJitter)).toBe(5000);
    expect(backoffDelayMs(3, noJitter)).toBe(10_000);
    expect(backoffDelayMs(4, noJitter)).toBe(30_000);
    expect(backoffDelayMs(99, noJitter)).toBe(30_000);
  });

  it('applies ±20% jitter', () => {
    expect(backoffDelayMs(0, () => 1)).toBe(1200);
    expect(backoffDelayMs(0, () => 0)).toBe(800);
  });

  it('clamps negative attempts', () => {
    expect(backoffDelayMs(-5, noJitter)).toBe(1000);
  });
});

describe('selectActiveTab', () => {
  it('returns null for no tabs', () => {
    expect(selectActiveTab({})).toBeNull();
  });

  it('prefers a playing tab over a more recent paused one', () => {
    expect(
      selectActiveTab({
        1: { isPlaying: true, lastEventAt: 100 },
        2: { isPlaying: false, lastEventAt: 999 },
      }),
    ).toBe(1);
  });

  it('audible beats everything (ground truth over self-reports)', () => {
    expect(
      selectActiveTab({
        1: { isPlaying: true, lastEventAt: 999 },
        2: { isPlaying: false, lastEventAt: 100, audible: true },
      }),
    ).toBe(2);
    expect(
      selectActiveTab({
        1: { isPlaying: true, lastEventAt: 100, audible: true },
        2: { isPlaying: true, lastEventAt: 999, audible: false },
      }),
    ).toBe(1);
  });

  it('breaks ties by most recent event (no incumbent)', () => {
    expect(
      selectActiveTab({
        1: { isPlaying: true, lastEventAt: 100 },
        2: { isPlaying: true, lastEventAt: 200 },
      }),
    ).toBe(2);
    expect(
      selectActiveTab({
        3: { isPlaying: false, lastEventAt: 300 },
        4: { isPlaying: false, lastEventAt: 250 },
      }),
    ).toBe(3);
  });

  it('is sticky: an equal-score newcomer cannot steal the seat', () => {
    const tabs = {
      1: { isPlaying: true, lastEventAt: 100, audible: true },
      2: { isPlaying: true, lastEventAt: 999, audible: true }, // newer, same score
    };
    expect(selectActiveTab(tabs, 1)).toBe(1);
    // ...and no flip-flop as events alternate:
    tabs[1].lastEventAt = 1000;
    expect(selectActiveTab(tabs, 1)).toBe(1);
    tabs[2].lastEventAt = 1001;
    expect(selectActiveTab(tabs, 1)).toBe(1);
  });

  it('switches when the incumbent actually stops', () => {
    expect(
      selectActiveTab(
        {
          1: { isPlaying: false, lastEventAt: 999, audible: false }, // stopped
          2: { isPlaying: true, lastEventAt: 100, audible: true },
        },
        1,
      ),
    ).toBe(2);
  });

  it('falls back to normal selection when the incumbent is gone', () => {
    expect(
      selectActiveTab({ 2: { isPlaying: true, lastEventAt: 100, audible: true } }, 1),
    ).toBe(2);
  });
});
