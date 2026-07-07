import { defineConfig } from 'electron-vite';

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
  renderer: {},
});
