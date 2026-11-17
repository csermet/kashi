/**
 * Dev-only renderer drawings (the fx debug bounds, the chain-latency marker)
 * must be impossible to ship by accident: the build folds them from an
 * environment variable, and only the exact string "1" turns one on.
 */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
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

// ---- the packer's guard looks at what it packed (build/after-pack.cjs) ----
interface PackContext {
  electronPlatformName: string;
  appOutDir: string;
  packager: { appInfo: { productFilename: string } };
}
const afterPackHook = createRequire(import.meta.url)('../../build/after-pack.cjs') as {
  default: (context: PackContext) => Promise<void>;
  packedDevOnlyBuild: (resources: string) => string | null;
  resourcesDir: (context: PackContext) => string;
  DEV_ONLY_FLAGS: string[];
};

describe('afterPack refuses a packed dev-only renderer', () => {
  const saved = { ...process.env };
  const dirs: string[] = [];
  afterEach(() => {
    process.env = { ...saved };
    for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
  });
  /** A linux-shaped pack: <out>/resources/app.asar holding `html` among other bytes. */
  function packWith(html: string, layout: 'asar' | 'dir' = 'asar'): PackContext {
    const out = mkdtempSync(join(tmpdir(), 'kashi-pack-'));
    dirs.push(out);
    const resources = join(out, 'resources');
    const page = Buffer.concat([Buffer.from([0, 1, 2, 3]), Buffer.from(html), Buffer.from('\u0000tail')]);
    if (layout === 'asar') {
      mkdirSync(resources, { recursive: true });
      writeFileSync(join(resources, 'app.asar'), page);
    } else {
      mkdirSync(join(resources, 'app', 'out', 'renderer'), { recursive: true });
      writeFileSync(join(resources, 'app', 'out', 'renderer', 'index.html'), page);
    }
    return { electronPlatformName: 'linux', appOutDir: out, packager: { appInfo: { productFilename: 'Kashi' } } };
  }
  const marked = (flags: string) => `<head>\n    <meta name="kashi-dev-only-build" content="${flags}" />`;
  const clean = () => {
    for (const flag of afterPackHook.DEV_ONLY_FLAGS) delete process.env[flag];
  };

  it('fails a pack whose renderer carries the tag, from a clean shell', async () => {
    clean();
    await expect(afterPackHook.default(packWith(marked('KASHI_LATENCY_MARKER')))).rejects.toThrow(
      /built with KASHI_LATENCY_MARKER on/,
    );
    await expect(afterPackHook.default(packWith(marked('KASHI_FX_DEBUG'), 'dir'))).rejects.toThrow(
      /built with KASHI_FX_DEBUG on/,
    );
  });

  it('passes a normal pack, and fails one with nothing to look at', async () => {
    clean();
    await expect(afterPackHook.default(packWith('<head><title>Kashi</title>'))).resolves.toBeUndefined();
    const empty = mkdtempSync(join(tmpdir(), 'kashi-pack-'));
    dirs.push(empty);
    await expect(
      afterPackHook.default({ electronPlatformName: 'linux', appOutDir: empty, packager: { appInfo: { productFilename: 'Kashi' } } }),
    ).rejects.toThrow(/nothing to check/);
  });

  it('still refuses the packer environment first', async () => {
    clean();
    process.env['KASHI_FX_DEBUG'] = '1';
    await expect(afterPackHook.default(packWith('<head>'))).rejects.toThrow(/KASHI_FX_DEBUG=1 is set/);
  });

  it('looks inside the .app bundle on macOS', () => {
    expect(
      afterPackHook.resourcesDir({ electronPlatformName: 'darwin', appOutDir: '/o', packager: { appInfo: { productFilename: 'Kashi' } } }),
    ).toBe(join('/o', 'Kashi.app', 'Contents', 'Resources'));
    expect(
      afterPackHook.resourcesDir({ electronPlatformName: 'win32', appOutDir: '/o', packager: { appInfo: { productFilename: 'Kashi' } } }),
    ).toBe(join('/o', 'resources'));
  });

  it('the build tags exactly the pages of a dev-only build, with the flags the hook knows', async () => {
    vi.resetModules();
    const config = (await import('../../electron.vite.config.js')) as unknown as {
      DEV_ONLY_FLAGS: readonly string[];
      markDevOnlyBuild: () => { transformIndexHtml: (html: string) => string };
      default: { renderer: { plugins: { name: string }[] } };
    };
    expect([...config.DEV_ONLY_FLAGS]).toEqual(afterPackHook.DEV_ONLY_FLAGS);
    // The renderer build runs it.
    expect(config.default.renderer.plugins.map((plugin) => plugin.name)).toContain('kashi-mark-dev-only-build');
    const page = '<!doctype html>\n<html>\n  <head>\n    <title>Kashi</title>\n  </head>\n</html>';
    clean();
    expect(config.markDevOnlyBuild().transformIndexHtml(page)).toBe(page);
    process.env['KASHI_LATENCY_MARKER'] = 'true'; // only "1" turns a flag on
    expect(config.markDevOnlyBuild().transformIndexHtml(page)).toBe(page);
    process.env['KASHI_LATENCY_MARKER'] = '1';
    const tagged = config.markDevOnlyBuild().transformIndexHtml(page);
    expect(tagged).toContain('<meta name="kashi-dev-only-build" content="KASHI_LATENCY_MARKER" />');
    // What the build writes is what the hook finds.
    const resources = mkdtempSync(join(tmpdir(), 'kashi-pack-'));
    dirs.push(resources);
    writeFileSync(join(resources, 'app.asar'), tagged);
    expect(afterPackHook.packedDevOnlyBuild(resources)).toBe('KASHI_LATENCY_MARKER');
  });
});
