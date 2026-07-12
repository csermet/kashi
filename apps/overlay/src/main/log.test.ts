import { describe, expect, it } from 'vitest';
import { formatLogLine, formatStamp } from './log.js';

describe('log formatting', () => {
  it('stamps HH:MM:SS.mmm with zero padding', () => {
    expect(formatStamp(new Date(2026, 6, 13, 1, 2, 3, 45))).toBe('01:02:03.045');
    expect(formatStamp(new Date(2026, 6, 13, 23, 59, 59, 999))).toBe('23:59:59.999');
  });

  it('aligns component tags into a fixed column', () => {
    const at = new Date(2026, 6, 13, 1, 2, 3, 4);
    expect(formatLogLine('main', 'hello', at)).toBe('01:02:03.004 main     hello');
    expect(formatLogLine('settings', 'x', at)).toBe('01:02:03.004 settings x');
  });
});
