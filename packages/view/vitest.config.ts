import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
    // The style contracts read view.css as text (?raw); vitest blanks CSS it
    // does not process.
    css: { include: [/view\.css/] },
  },
});
