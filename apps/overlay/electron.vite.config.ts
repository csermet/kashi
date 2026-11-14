import { resolve } from 'node:path';
import { defineConfig } from 'electron-vite';
import type { Plugin } from 'vite';
import { buildFlag } from './src/shared/build-flags';

// Dev keeps style-src 'unsafe-inline' (vite HMR injects styles via JS); the
// PRODUCTION bundle links a real stylesheet, so the token is dropped at
// build time — packaged apps ship the strict CSP (Faz 5 P5, R-9).
//
// Only the CSP meta's content is rewritten. A plain first-match replace used to
// hit the explanatory HTML comment above it, which names the same token — so
// from 2026-07-13 to 2026-10 every packaged build shipped the loose policy
// while this file said otherwise. The build now fails if the token survives.
const CSP_META = /(<meta\s+http-equiv="Content-Security-Policy"\s+content=")([^"]*)(")/;
const tightenCsp = (): Plugin => ({
  name: 'kashi-tighten-csp',
  apply: 'build',
  transformIndexHtml(html, ctx) {
    if (!CSP_META.test(html)) throw new Error(`${ctx.filename}: no CSP meta to tighten`);
    const out = html.replace(
      CSP_META,
      (_match, head: string, policy: string, tail: string) =>
        head + policy.replaceAll(" 'unsafe-inline'", '') + tail,
    );
    if (CSP_META.exec(out)?.[2]?.includes('unsafe-inline')) {
      throw new Error(`${ctx.filename}: 'unsafe-inline' survived the production CSP`);
    }
    return out;
  },
});

export default defineConfig({
  main: {
    build: {
      // Workspace packages must be BUNDLED: externalized they resolve to TS
      // source at runtime and crash Electron's ESM loader on first launch.
      // Real deps (ws) stay external — correct for the main process.
      // @kashi/view is a devDependency (bundled by default) and listed anyway:
      // main imports its document mapper and settings vocabulary.
      externalizeDeps: { exclude: ['@kashi/protocol', '@kashi/view'] },
    },
  },
  preload: {
    // Sandboxed renderers cannot load ESM preload scripts — force CJS output.
    build: {
      externalizeDeps: { exclude: ['@kashi/protocol', '@kashi/view'] },
      rollupOptions: {
        output: { format: 'cjs', entryFileNames: '[name].cjs' },
      },
    },
  },
  renderer: {
    plugins: [tightenCsp()],
    // Dev-only boundary visualizer for the particle layer (Faz 6.7 P5). The
    // renderer is sandboxed and has no process.env, so the switch is folded in
    // at build time: `KASHI_FX_DEBUG=1 pnpm dev` draws the box and window
    // bounds, and a normal build folds it to `false` so the outline cannot be
    // drawn in a shipped app.
    // `KASHI_LATENCY_MARKER=1 electron-vite build` (the chain-latency
    // measurement, src/renderer/src/latency-marker.ts) draws a black/white
    // code block in the window's top-left corner on every word activation, for
    // a screen recording to read. Folded to `false` in every normal build.
    define: {
      __KASHI_FX_DEBUG__: JSON.stringify(buildFlag(process.env['KASHI_FX_DEBUG'])),
      __KASHI_LATENCY_MARKER__: JSON.stringify(buildFlag(process.env['KASHI_LATENCY_MARKER'])),
    },
    build: {
      rollupOptions: {
        input: {
          // Multi-page: the overlay itself + the tiny prompt windows.
          index: resolve(__dirname, 'src/renderer/index.html'),
          'timing-offset': resolve(__dirname, 'src/renderer/timing-offset.html'),
          'server-settings': resolve(__dirname, 'src/renderer/server-settings.html'),
        },
      },
    },
  },
});
