import { describe, expect, it } from 'vitest';
import { coerceDiagnosticFields, MAX_DIAGNOSTIC_FIELDS } from './diagnostic-fields.js';

describe('coerceDiagnosticFields (preload, before IPC)', () => {
  it('keeps flat names with finite numbers or short words', () => {
    expect(coerceDiagnosticFields({ scope: 'track', reports: 12, slew_max_ms: 45.5 })).toEqual({
      scope: 'track',
      reports: 12,
      slew_max_ms: 45.5,
    });
  });

  it('drops nested values, non-finite numbers, long or odd strings and odd names', () => {
    expect(
      coerceDiagnosticFields({
        nested: { a: 1 },
        list: [1, 2],
        nan: Number.NaN,
        inf: Number.POSITIVE_INFINITY,
        title: 'Some Song Title',
        url: 'https://x',
        long_word: 'abcdefghijklmnopq', // 17 characters
        word: 'abcdefghijklmnop', // 16: kept
        'Bad-Name': 1,
        flag: true,
        empty: null,
      }),
    ).toEqual({ word: 'abcdefghijklmnop' });
  });

  it('is undefined for anything that is not a plain object', () => {
    for (const raw of [undefined, null, 3, 'x', [1, 2]]) expect(coerceDiagnosticFields(raw)).toBeUndefined();
  });

  it('carries at most 32 fields', () => {
    const many = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`f${i}`, i]));
    const kept = coerceDiagnosticFields(many) ?? {};
    expect(Object.keys(kept)).toHaveLength(MAX_DIAGNOSTIC_FIELDS);
    expect(MAX_DIAGNOSTIC_FIELDS).toBe(32);
    expect(kept['f31']).toBe(31);
    expect(kept['f32']).toBeUndefined();
  });
});
