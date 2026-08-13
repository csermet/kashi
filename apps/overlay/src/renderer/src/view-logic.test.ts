import { describe, expect, it } from 'vitest';
import {
  INTERLUDE_GAP_MS,
  WHEEL_PX_PER_STEP,
  accumulateWheel,
  deriveView,
  findActiveWord,
  findDisplayLine,
  shouldAnimateLineChange,
  viewsEqual,
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
    expect(view).toEqual({ boxVisible: false, lineText: '', lineDim: false, searchVisible: false, interlude: false, lineAdlib: false, linePlain: false, lineUncertain: false });
  });

  it('shows the active lyric line bright', () => {
    const view = deriveView({ ...base, hasLines: true, activeText: 'Never gonna give you up' });
    expect(view).toEqual({
      boxVisible: true,
      lineText: 'Never gonna give you up',
      lineDim: false,
      searchVisible: false,
      interlude: false,
      lineAdlib: false,
      linePlain: true,
      lineUncertain: false,
    });
  });

  it('shows the ANIMATED interlude mark when no line is held', () => {
    const view = deriveView({ ...base, hasLines: true, activeText: null });
    expect(view.lineText).toBe('♪');
    expect(view.interlude).toBe(true);
    expect(view.boxVisible).toBe(true);
  });

  it('a held/active line is never marked as interlude', () => {
    expect(deriveView({ ...base, hasLines: true, activeText: 'text' }).interlude).toBe(false);
  });

  it('marks the line as ad-lib only for an ACTIVE line with the flag', () => {
    const active = deriveView({ ...base, hasLines: true, activeText: 'Oh-ooh', activeAdlib: true });
    expect(active.lineAdlib).toBe(true);
    // Interlude (no active line) never styles as ad-lib, whatever the flag.
    const interlude = deriveView({ ...base, hasLines: true, activeText: null, activeAdlib: true });
    expect(interlude.lineAdlib).toBe(false);
    // Old docs / lrclib: flag absent -> false.
    expect(deriveView({ ...base, hasLines: true, activeText: 'x' }).lineAdlib).toBe(false);
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
      interlude: false,
      lineAdlib: false,
      linePlain: true,
      lineUncertain: false,
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
      interlude: false,
      lineAdlib: false,
      linePlain: true,
      lineUncertain: false,
    });
  });
});

describe('shouldAnimateLineChange', () => {
  const view = (lineText: string, over: Record<string, unknown> = {}) => ({
    boxVisible: true,
    lineText,
    lineDim: false,
    searchVisible: false,
    interlude: false,
    lineAdlib: false,
    linePlain: true,
    lineUncertain: false,
    ...over,
  });

  it('arms only on a REAL line change at simple/full', () => {
    expect(shouldAnimateLineChange(view('a'), view('b'), 'simple')).toBe(true);
    expect(shouldAnimateLineChange(view('a'), view('b'), 'full')).toBe(true);
  });

  it('never on first paint, off level, interlude, hidden box or same text', () => {
    expect(shouldAnimateLineChange(null, view('b'), 'full')).toBe(false);
    expect(shouldAnimateLineChange(view('a'), view('b'), 'off')).toBe(false);
    expect(shouldAnimateLineChange(view('a'), view('♪', { interlude: true }), 'full')).toBe(false);
    expect(shouldAnimateLineChange(view('a'), view('b', { boxVisible: false }), 'full')).toBe(
      false,
    );
    expect(shouldAnimateLineChange(view('a'), view('a'), 'full')).toBe(false);
  });
});

describe('watchdogShouldReset', () => {
  it('trips on a playing clock starved of positions past the 60 s threshold', () => {
    expect(watchdogShouldReset(true, false, 60_001)).toBe(true);
  });

  it('does not trip below the threshold (a seek/buffer stall is not a dead source)', () => {
    // 10 s used to trip here and wipe the rich document; 60 s tolerates the
    // MSE buffer (Caner field bug, RE700X flaky WiFi).
    expect(watchdogShouldReset(true, false, 30_000)).toBe(false);
    expect(watchdogShouldReset(true, false, 59_999)).toBe(false);
  });

  it('does not trip while paused (no extrapolation, nothing to kill)', () => {
    expect(watchdogShouldReset(false, false, 120_000)).toBe(false);
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

describe('findActiveWord', () => {
  const words = [
    { start_ms: 1000, end_ms: 1400, text: 'a' },
    { start_ms: 1500, end_ms: 2000, text: 'b' },
    { start_ms: 2600, end_ms: 3000, text: 'c' },
  ];

  it('returns -1 before the first word', () => {
    expect(findActiveWord(words, 0)).toBe(-1);
    expect(findActiveWord(words, 999)).toBe(-1);
  });

  it('finds the covering word', () => {
    expect(findActiveWord(words, 1000)).toBe(0);
    expect(findActiveWord(words, 1700)).toBe(1);
    expect(findActiveWord(words, 2600)).toBe(2);
  });

  it('keeps the previous word lit through inter-word gaps (no flicker)', () => {
    expect(findActiveWord(words, 2300)).toBe(1); // between b.end and c.start
  });

  it('keeps the last word lit after the line ends', () => {
    expect(findActiveWord(words, 99_000)).toBe(2);
  });

  it('handles empty input', () => {
    expect(findActiveWord([], 1000)).toBe(-1);
  });
});

describe('findDisplayLine', () => {
  const lines = [
    { start_ms: 1000, end_ms: 4000 },   // line 0
    { start_ms: 5000, end_ms: 8000 },   // line 1 (1 s gap: HOLD)
    { start_ms: 30_000, end_ms: 33_000 }, // line 2 (22 s break: interlude)
  ];

  it('is -1 during the intro, until the first line leads in', () => {
    expect(findDisplayLine(lines, 0)).toBe(-1);
    expect(findDisplayLine(lines, 499)).toBe(-1);
    expect(findDisplayLine(lines, 999)).toBe(0); // inside the lead-in
  });

  it('returns the covering line', () => {
    expect(findDisplayLine(lines, 2000)).toBe(0);
    expect(findDisplayLine(lines, 31_000)).toBe(2);
  });

  it('HOLDS the previous line through a short gap (no ♪ flash)', () => {
    expect(findDisplayLine(lines, 4400)).toBe(0); // between 0 and 1, pre-lead
  });

  it('hands over to the next line during its lead-in', () => {
    // The whole point: the text lands BEFORE its first word lights, so the
    // reader sees the word unlit and then watches it light.
    expect(findDisplayLine(lines, 4499)).toBe(0);
    expect(findDisplayLine(lines, 4500)).toBe(1); // 500 ms early
    expect(findDisplayLine(lines, 5000)).toBe(1);
  });

  it('borrows the lead from SILENCE only — never from a line still singing', () => {
    // Back-to-back lines: cutting line 0 short to preview line 1 would drop a
    // word that is still being sung. The gap is what pays for the lead.
    const tight = [
      { start_ms: 1000, end_ms: 4000 },
      { start_ms: 4000, end_ms: 8000 },
    ];
    expect(findDisplayLine(tight, 3900)).toBe(0);
    expect(findDisplayLine(tight, 3999)).toBe(0);
    expect(findDisplayLine(tight, 4000)).toBe(1);
  });

  it('shrinks the lead to fit a narrow gap instead of skipping it', () => {
    const narrow = [
      { start_ms: 1000, end_ms: 4000 },
      { start_ms: 4300, end_ms: 8000 }, // 300 ms gap: lead is 300, not 500
    ];
    expect(findDisplayLine(narrow, 3999)).toBe(0);
    expect(findDisplayLine(narrow, 4000)).toBe(1);
  });

  it('does not flicker on a gap too small to read', () => {
    const hair = [
      { start_ms: 1000, end_ms: 4000 },
      { start_ms: 4050, end_ms: 8000 }, // 50 ms < LINE_LEAD_MIN_MS
    ];
    expect(findDisplayLine(hair, 4000)).toBe(0);
    expect(findDisplayLine(hair, 4049)).toBe(0);
    expect(findDisplayLine(hair, 4050)).toBe(1);
  });

  it('leads out of a long interlude too', () => {
    expect(findDisplayLine(lines, 29_499)).toBe(-1); // still ♪
    expect(findDisplayLine(lines, 29_500)).toBe(2);
  });

  it('shows the interlude during a long instrumental break', () => {
    expect(findDisplayLine(lines, 9000)).toBe(-1);  // 8s..30s break
    expect(findDisplayLine(lines, 29_000)).toBe(-1);
  });

  it('holds the last line briefly after the song, then interludes', () => {
    expect(findDisplayLine(lines, 33_000 + INTERLUDE_GAP_MS - 1)).toBe(2);
    expect(findDisplayLine(lines, 33_000 + INTERLUDE_GAP_MS + 1)).toBe(-1);
  });

  it('handles empty input', () => {
    expect(findDisplayLine([], 1000)).toBe(-1);
  });
});

describe('plain lines wear the theme, and know it from their own data', () => {
  const base = {
    adActive: false,
    hasLines: true,
    statusText: '',
    statusDim: false,
    searching: false,
  };

  it('marks a line plain exactly when it has no word clock', () => {
    expect(deriveView({ ...base, activeText: 'sung', activeHasWords: true }).linePlain).toBe(false);
    expect(deriveView({ ...base, activeText: 'unsung', activeHasWords: false }).linePlain).toBe(
      true,
    );
    // Absent flag = no word clock known = plain (serverless/lrclib documents).
    expect(deriveView({ ...base, activeText: 'unsung' }).linePlain).toBe(true);
    // The ♪ owns its own styling; an ad shows nothing at all.
    expect(deriveView({ ...base, activeText: null }).linePlain).toBe(false);
    expect(deriveView({ ...base, adActive: true, activeText: 'x' }).linePlain).toBe(false);
  });

  it('answers for the CURRENT line, not the one painted before it', () => {
    // THE field bug: the renderer asked its word-span array, which at repaint
    // time still described the PREVIOUS line. A plain line arriving after a
    // word-synced one was therefore never marked plain and stayed stock white
    // — measured across the archive as half the visual surface on some songs.
    const sung = deriveView({ ...base, activeText: 'sung', activeHasWords: true });
    const plainAfterSung = deriveView({ ...base, activeText: 'spoken', activeHasWords: false });
    const sungAfterPlain = deriveView({ ...base, activeText: 'sung again', activeHasWords: true });
    expect(sung.linePlain).toBe(false);
    expect(plainAfterSung.linePlain).toBe(true);
    expect(sungAfterPlain.linePlain).toBe(false);
    // Stateless: the same input twice must answer the same, or the derivation
    // is carrying exactly the memory that caused the bug.
    expect(deriveView({ ...base, activeText: 'spoken', activeHasWords: false })).toEqual(
      plainAfterSung,
    );
  });

  it('softens a rescued line, and only while it still has a word clock', () => {
    // Faz 8.1: the server has written lines[].uncertain since pipeline 2.19.0
    // and nothing read it, so the arbiter's "the anchor proposes, the audio
    // disposes" verdict was invisible in the field. It is a statement ABOUT
    // word timings, so it only means anything while they are on screen.
    expect(
      deriveView({ ...base, activeText: 'shifted', activeHasWords: true, activeUncertain: true })
        .lineUncertain,
    ).toBe(true);
    // Word clock gone (line mode / QA dropped the words): the line already
    // wears .plain, and fading it further would say the same thing twice.
    expect(
      deriveView({ ...base, activeText: 'shifted', activeHasWords: false, activeUncertain: true })
        .lineUncertain,
    ).toBe(false);
    // A confident line is never softened; nor is the ♪ or an ad.
    expect(
      deriveView({ ...base, activeText: 'sure', activeHasWords: true }).lineUncertain,
    ).toBe(false);
    expect(
      deriveView({ ...base, activeText: null, activeHasWords: true, activeUncertain: true })
        .lineUncertain,
    ).toBe(false);
    expect(
      deriveView({ ...base, adActive: true, activeText: 'x', activeHasWords: true, activeUncertain: true })
        .lineUncertain,
    ).toBe(false);
  });

  it('never removes anything — a rescued line keeps its text and its box', () => {
    // The rule Caner set for this change: de-emphasise, no deleting, no
    // hiding. Pin it so a future "just skip uncertain lines" cannot land
    // quietly.
    const sure = deriveView({ ...base, activeText: 'to fight', activeHasWords: true });
    const rescued = deriveView({
      ...base,
      activeText: 'to fight',
      activeHasWords: true,
      activeUncertain: true,
    });
    expect(rescued.lineText).toBe(sure.lineText);
    expect(rescued.boxVisible).toBe(true);
    expect({ ...rescued, lineUncertain: false }).toEqual(sure);
  });

  it('softening composes with the ad-lib flag instead of replacing it', () => {
    // Both can be true on one line: `adlib` is derived from the TEXT, while
    // `uncertain` comes from line QA's rescue path — nothing couples them.
    const view = deriveView({
      ...base,
      activeText: 'Oh-ooh, whoa-oh',
      activeHasWords: true,
      activeAdlib: true,
      activeUncertain: true,
    });
    expect(view.lineAdlib).toBe(true);
    expect(view.lineUncertain).toBe(true);
  });

  it('compares every field, so no future one is silently exempt', () => {
    const view = deriveView({ ...base, activeText: 'sung', activeHasWords: true });
    expect(viewsEqual(view, { ...view })).toBe(true);
    expect(viewsEqual(null, view)).toBe(false);
    for (const key of Object.keys(view) as (keyof typeof view)[]) {
      const flipped = {
        ...view,
        [key]: typeof view[key] === 'boolean' ? !view[key] : 'other',
      } as typeof view;
      expect(viewsEqual(view, flipped)).toBe(false);
    }
  });
});
