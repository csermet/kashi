/**
 * The view must run on any host: it imports nothing from Electron or Node
 * (client review E.4). This walks every non-test module of the package and
 * checks what it imports at RUNTIME — type-only imports are erased and may
 * name the workspace's contract packages.
 */
import { describe, expect, it } from 'vitest';

/** Every source file of the package, as text (vite's raw import). */
const SOURCES = import.meta.glob<string>(['./*.ts', '!./*.test.ts', '!./*.d.ts'], {
  query: '?raw',
  import: 'default',
  eager: true,
});

/** Runtime third-party imports the view may make: the lazily loaded particle renderer. */
const RUNTIME_ALLOWED = new Set(['pixi.js', 'pixi.js/unsafe-eval']);
/** Type-only imports may also name the contracts (erased from every bundle). */
const TYPE_ONLY_ALLOWED = new Set([...RUNTIME_ALLOWED, '@kashi/protocol', '@kashi/schemas']);
const NODE_BUILTINS = new Set([
  'assert', 'buffer', 'child_process', 'crypto', 'dgram', 'dns', 'events', 'fs', 'fs/promises',
  'http', 'http2', 'https', 'module', 'net', 'os', 'path', 'process', 'readline', 'stream',
  'timers', 'tls', 'tty', 'url', 'util', 'v8', 'vm', 'worker_threads', 'zlib',
]);

interface ImportUse {
  specifier: string;
  typeOnly: boolean;
}

/** Static, side-effect, re-export and dynamic imports of one module. */
export function importsOf(source: string): ImportUse[] {
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const uses: ImportUse[] = [];
  // Names hold no quote or semicolon, so a side-effect import cannot be
  // swallowed into the next statement.
  const statement = /^\s*(import|export)\s+(type\s+)?([^'";]*?)\s+from\s+['"]([^'"]+)['"]/gm;
  for (const m of code.matchAll(statement)) {
    // `import { type A, type B }` is type-only as well.
    const names = m[3] ?? '';
    const allTypes =
      /^\{[\s\S]*\}$/.test(names.trim()) &&
      names
        .replace(/[{}]/g, '')
        .split(',')
        .map((n) => n.trim())
        .filter(Boolean)
        .every((n) => /^type\s/.test(n));
    uses.push({ specifier: m[4] ?? '', typeOnly: Boolean(m[2]) || allTypes });
  }
  for (const m of code.matchAll(/^\s*import\s+['"]([^'"]+)['"]/gm)) {
    uses.push({ specifier: m[1] ?? '', typeOnly: false });
  }
  for (const m of code.matchAll(/\bimport\(\s*['"]([^'"]+)['"]\s*\)/g)) {
    uses.push({ specifier: m[1] ?? '', typeOnly: false });
  }
  for (const m of code.matchAll(/\brequire\(\s*['"]([^'"]+)['"]\s*\)/g)) {
    uses.push({ specifier: m[1] ?? '', typeOnly: false });
  }
  return uses;
}

/** Why a specifier is not allowed in the view, or null. */
export function forbidden({ specifier, typeOnly }: ImportUse): string | null {
  if (specifier.startsWith('./')) return null;
  if (specifier.startsWith('../')) return 'reaches outside the package';
  if (specifier === 'electron' || specifier.startsWith('electron/')) return 'Electron';
  if (specifier.startsWith('node:') || NODE_BUILTINS.has(specifier)) return 'Node';
  const allowed = typeOnly ? TYPE_ONLY_ALLOWED : RUNTIME_ALLOWED;
  return allowed.has(specifier) ? null : 'not on the allow-list';
}

describe('the view imports nothing from Electron or Node', () => {
  it('scans every module of the package', () => {
    const files = Object.keys(SOURCES);
    // A glob that matched nothing would make the next test vacuous.
    for (const expected of ['./index.ts', './lyrics-view.ts', './fx-canvas.ts', './document.ts']) {
      expect(files, expected).toContain(expected);
    }
    expect(files.some((f) => f.endsWith('.test.ts'))).toBe(false);
  });

  it('every runtime import is relative or allowed, and none is Electron or Node', () => {
    const offenders: string[] = [];
    for (const [file, source] of Object.entries(SOURCES)) {
      for (const use of importsOf(source)) {
        const why = forbidden(use);
        if (why) offenders.push(`${file}: '${use.specifier}'${use.typeOnly ? ' (type)' : ''} — ${why}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('the scanner sees each form it has to (so the check above has teeth)', () => {
    const sample = [
      "import { readFileSync } from 'node:fs';",
      "import type { App } from 'electron';",
      "import 'path';",
      "export { a } from 'os';",
      "const m = await import('child_process');",
      "import { type A, type B } from '@kashi/protocol';",
      "import { mapDocument } from '@kashi/schemas';",
      "import { x } from './local.js';",
    ].join('\n');
    expect(importsOf(sample).map((u) => [u.specifier, forbidden(u)])).toEqual([
      ['node:fs', 'Node'],
      ['electron', 'Electron'],
      ['os', 'Node'],
      ['@kashi/protocol', null],
      ['@kashi/schemas', 'not on the allow-list'],
      ['./local.js', null],
      ['path', 'Node'],
      ['child_process', 'Node'],
    ]);
  });
});
