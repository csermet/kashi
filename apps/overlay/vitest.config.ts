import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // scripts/: the pure halves of the measurement tools (chain-latency).
    include: ['src/**/*.test.ts', 'scripts/**/*.test.mjs'],
    environment: 'node',
  },
});
