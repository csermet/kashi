/**
 * Dev-only renderer drawings (the fx debug bounds, the chain-latency marker)
 * must be impossible to ship by accident: the build folds them from an
 * environment variable, and only the exact string "1" turns one on.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildFlag } from '../shared/build-flags.js';

describe('build flags', () => {
  it('only "1" turns a flag on', () => {
    expect(buildFlag('1')).toBe(true);
    for (const value of [undefined, '', '0', 'true', 'yes', ' 1', '1 ', 'on']) {
      expect(buildFlag(value)).toBe(false);
    }
  });

  describe('electron.vite.config.ts', () => {
    const saved = { ...process.env };
    afterEach(() => {
      process.env = { ...saved };
      vi.resetModules();
    });

    async function rendererDefine(): Promise<Record<string, string>> {
      vi.resetModules();
      const config = (await import('../../electron.vite.config.js')).default as {
        renderer: { define: Record<string, string> };
      };
      return config.renderer.define;
    }

    it('folds the latency marker and the fx debug bounds to false in a normal build', async () => {
      delete process.env['KASHI_LATENCY_MARKER'];
      delete process.env['KASHI_FX_DEBUG'];
      const define = await rendererDefine();
      expect(define['__KASHI_LATENCY_MARKER__']).toBe('false');
      expect(define['__KASHI_FX_DEBUG__']).toBe('false');
    });

    it('turns the marker on only for KASHI_LATENCY_MARKER=1', async () => {
      process.env['KASHI_LATENCY_MARKER'] = 'true';
      expect((await rendererDefine())['__KASHI_LATENCY_MARKER__']).toBe('false');
      process.env['KASHI_LATENCY_MARKER'] = '1';
      expect((await rendererDefine())['__KASHI_LATENCY_MARKER__']).toBe('true');
    });
  });
});
