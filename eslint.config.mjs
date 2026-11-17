// Minimal flat config (Faz 5 P5, retro 4.5 #13): typescript-eslint
// recommended over the TS workspaces. Python (apps/server) has ruff;
// generated code and build outputs are ignored.
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: [
      '**/dist/**',
      '**/out/**',
      '**/node_modules/**',
      'packages/schemas/src/generated/**',
      'apps/server/**',
      '**/*.mjs',
      '**/*.js',
      '**/*.cjs', // CJS build/config scripts (e.g. electron-builder afterPack)
    ],
  },
  ...tseslint.configs.recommended,
  {
    rules: {
      // The `_`-prefix convention is load-bearing (drift-guard type exports,
      // deliberately unused destructures like `words: _words`).
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', destructuredArrayIgnorePattern: '^_' },
      ],
    },
  },
  {
    // The shared view runs on hosts that are not Electron (client review
    // E.4, packages/view): nothing from Electron or Node in its modules. The
    // package's import-graph.test.ts checks the same over every module.
    files: ['packages/view/src/**/*.ts'],
    ignores: ['packages/view/src/**/*.test.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                'electron', 'electron/*', 'node:*', 'fs', 'fs/*', 'path', 'os', 'crypto',
                'child_process', 'http', 'https', 'net', 'url', 'util', 'stream', 'events',
                'buffer', 'worker_threads', 'zlib', 'module', 'process',
              ],
              message: 'The shared view must run in any browser host: no Electron or Node.',
            },
          ],
        },
      ],
    },
  }
);
