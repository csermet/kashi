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
  }
);
