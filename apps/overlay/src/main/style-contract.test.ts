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
