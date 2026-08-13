import { describe, expect, it } from 'vitest';
import { classifyEdit, isDurationCorrection, EDIT_DURATION_TOLERANCE_MS } from './edit-check.js';

const line = (start_ms: number) => ({ start_ms, end_ms: start_ms + 2000, text: 'x' });

describe('classifyEdit', () => {
  it('accepts the record written for this edit', () => {
    expect(classifyEdit(193_000, 193_000, [line(10_000)])).toBe('match');
    expect(classifyEdit(193_000, 195_000, [line(10_000)])).toBe('match');
  });

  it('convicts the field case: Hey Mama stamps against a six-minute duration', () => {
    // The announce carried the PREVIOUS video's duration, the scoped lookup
    // missed, and the unscoped retry returned the real 3:13 record. Whichever
    // side is wrong, these two do not describe the same audio.
    expect(classifyEdit(366_451, 193_000, [line(10_000)])).toBe('different-edit');
  });

  it('convicts a sped-up edit even inside the same title', () => {
    // Nightcore: same words, ~25% shorter. The stamps are uniformly wrong.
    expect(classifyEdit(177_000, 232_000, [line(10_000)])).toBe('different-edit');
  });

  it('holds the tolerance exactly where the server holds it', () => {
    expect(classifyEdit(193_000, 193_000 + EDIT_DURATION_TOLERANCE_MS, [line(1)])).toBe('match');
    expect(classifyEdit(193_000, 193_000 + EDIT_DURATION_TOLERANCE_MS + 1, [line(1)])).toBe(
      'different-edit',
    );
  });

  it('convicts on overrunning stamps when the record carries no duration', () => {
    // The second signal, and the only one available for a duration-less
    // record: a lyric still singing after the audio has stopped.
    expect(classifyEdit(193_000, null, [line(10_000), line(250_000)])).toBe('different-edit');
  });

  it('does not convict a lyric that merely ends early', () => {
    // Instrumental outros, fade-outs, and songs that simply stop singing are
    // normal. Only overrun is evidence.
    expect(classifyEdit(193_000, null, [line(10_000), line(60_000)])).toBe('unverifiable');
  });

  it('refuses to convict when there is nothing to compare', () => {
    // A missing TRACK duration is exactly the auto-advance window that started
    // this whole bug — we cannot tell which side lied, so neither is convicted.
    expect(classifyEdit(undefined, 193_000, [line(1)])).toBe('unverifiable');
    expect(classifyEdit(0, 193_000, [line(1)])).toBe('unverifiable');
    expect(classifyEdit(193_000, null, [])).toBe('unverifiable');
  });

  it('prefers the record duration over the stamps when both are available', () => {
    // A matching duration with stamps past the end means parseLrc's clamp or a
    // trailing credit line — not a different edit. The direct signal wins.
    expect(classifyEdit(193_000, 193_000, [line(250_000)])).toBe('match');
  });
});

describe('isDurationCorrection', () => {
  it('catches the field case: the real duration arrives 11 s late', () => {
    expect(isDurationCorrection(366_451, 193_000)).toBe(true);
  });

  it('ignores the ordinary metadata refresh', () => {
    // YTM re-announces the playing track constantly; only a MATERIAL change
    // may cost the user a re-lookup (and the lyric flash that comes with it).
    expect(isDurationCorrection(193_000, 193_000)).toBe(false);
    expect(isDurationCorrection(193_000, 193_400)).toBe(false);
  });

  it('treats a first duration as news — the lookup that ran had no filter', () => {
    expect(isDurationCorrection(undefined, 193_000)).toBe(true);
    expect(isDurationCorrection(0, 193_000)).toBe(true);
  });

  it('never acts on a refresh that carries no duration', () => {
    // Losing a duration we already have would re-run the lookup UNFILTERED —
    // the exact weakening this whole guard exists to prevent.
    expect(isDurationCorrection(193_000, undefined)).toBe(false);
    expect(isDurationCorrection(193_000, 0)).toBe(false);
  });

  it('holds the same tolerance as the edit check', () => {
    expect(isDurationCorrection(193_000, 193_000 + EDIT_DURATION_TOLERANCE_MS)).toBe(false);
    expect(isDurationCorrection(193_000, 193_000 + EDIT_DURATION_TOLERANCE_MS + 1)).toBe(true);
  });
});
