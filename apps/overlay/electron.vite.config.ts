import { resolve } from 'node:path';
import { defineConfig } from 'electron-vite';
import type { Plugin } from 'vite';

// Dev keeps style-src 'unsafe-inline' (vite HMR injects styles via JS); the
// PRODUCTION bundle links a real stylesheet, so the token is dropped at
// build time — packaged apps ship the strict CSP (Faz 5 P5, R-9).
const tightenCsp = (): Plugin => ({
  name: 'kashi-tighten-csp',
  apply: 'build',
  transformIndexHtml(html) {
    return html.replace(" 'unsafe-inline'", '');
  },
});

export default defineConfig({
  main: {
    build: {
      // Workspace packages must be BUNDLED: externalized they resolve to TS
      // source at runtime and crash Electron's ESM loader on first launch.
      // Real deps (ws) stay external — correct for the main process.
      externalizeDeps: { exclude: ['@kashi/protocol'] },
    },
  },
  preload: {
    // Sandboxed renderers cannot load ESM preload scripts — force CJS output.
    build: {
      externalizeDeps: { exclude: ['@kashi/protocol'] },
      rollupOptions: {
        output: { format: 'cjs', entryFileNames: '[name].cjs' },
      },
    },
  },
  renderer: {
    plugins: [tightenCsp()],
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
