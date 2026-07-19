/**
 * Structural pixel-identity guard (Faz 6.5 P1): every stylesheet rule that
 * touches the ambient ring must be scoped under `body.fx-hype` — off/simple/
 * full render pixel-identical to the pre-hype look by construction, and this
 * test keeps that construction honest as the hype section grows.
 *
 * Lives main-side only because reading a file needs node typings; the
 * renderer TS program stays browser-pure (tsconfig.web.json has no node).
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const css = readFileSync(
  fileURLToPath(new URL('../renderer/src/style.css', import.meta.url)),
  'utf8',
);

/** Selector lines only: strip comments, keep lines opening/continuing rules. */
function selectorLines(source: string): string[] {
  const noComments = source.replace(/\/\*[\s\S]*?\*\//g, '');
  return noComments
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => (line.includes('{') || line.endsWith(',')) && !line.startsWith('@'));
}

describe('style contract: ambient ring stays hype-scoped', () => {
  it('every selector naming ambient starts with body.fx-hype', () => {
    const offenders = selectorLines(css).filter(
      (line) => line.includes('ambient') && !line.startsWith('body.fx-hype'),
    );
    expect(offenders).toEqual([]);
  });

  it('the ambient rules exist (the guard must be guarding something)', () => {
    expect(css).toContain('#lyric-box.fx-ambient::before');
    expect(css).toContain('#lyric-box.ambient-flash::before');
  });
});

describe('style contract: icon stage / park spot (Faz 6.5 P2)', () => {
  // Structural shells may exist outside hype ONLY as these invisible base
  // selectors (position/opacity:0); every visible rule must be hype-scoped.
  const BASE_SHELLS = new Set(['#fx-stage {', '.stage-slot {', '#fx-park {']);

  it('every stage/park selector is hype-scoped or an invisible base shell', () => {
    const offenders = selectorLines(css).filter(
      (line) =>
        (line.includes('fx-stage') || line.includes('stage-slot') || line.includes('fx-park')) &&
        !line.startsWith('body.fx-hype') &&
        !BASE_SHELLS.has(line),
    );
    expect(offenders).toEqual([]);
  });

  it('base shells are invisible and click-through (opacity 0 + no pointer events)', () => {
    for (const shell of ['#fx-stage', '.stage-slot', '#fx-park']) {
      const block = css.match(new RegExp(`^${shell.replace('.', '\\.')} \\{[^}]*\\}`, 'm'))?.[0];
      expect(block, shell).toBeDefined();
      expect(block, shell).toContain('pointer-events: none');
      if (shell !== '#fx-stage') expect(block, shell).toContain('opacity: 0');
    }
  });
});
