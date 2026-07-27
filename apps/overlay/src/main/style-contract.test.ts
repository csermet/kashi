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

  it("the ring's pre-drawn base rule (no 'ambient' in its selector) is hype-scoped too", () => {
    // The base `#lyric-box::before` layer slips past the substring net above
    // (reviewer nit) — guard it explicitly.
    const offenders = selectorLines(css).filter(
      (line) =>
        line.includes('lyric-box') &&
        line.includes('::before') &&
        !line.startsWith('body.fx-hype'),
    );
    expect(offenders).toEqual([]);
  });

  it('the ambient rules exist (the guard must be guarding something)', () => {
    expect(css).toContain('#lyric-box.fx-ambient::before');
    expect(css).toContain('#lyric-box.ambient-flash::before');
  });
});

describe('style contract: every FX category has a live CSS tint rule', () => {
  it('FX_BASE_COLORS keys and the .fx-<tag> --fx-color block stay in lockstep', async () => {
    // The v1.2 categories shipped colors + icons but NO CSS mapping — the
    // words rendered stock while the ambient ring showed the hue (reviewer
    // violation). This pins the two lists together forever.
    const { FX_BASE_COLORS } = await import('../renderer/src/effects-logic.js');
    for (const tag of Object.keys(FX_BASE_COLORS)) {
      expect(css, tag).toContain(
        `body.fx-hype .word.fx-word.fx-${tag} { --fx-color: var(--fx-tint-${tag},`,
      );
    }
  });
});

describe('style contract: nightcore stays hype-scoped (Faz 6.5 P5)', () => {
  it('every selector naming nightcore starts with body.fx-hype', () => {
    const offenders = selectorLines(css).filter(
      (line) => line.includes('nightcore') && !line.startsWith('body.fx-hype'),
    );
    expect(offenders).toEqual([]);
  });

  it('the nightcore rules exist', () => {
    expect(css).toContain('body.fx-hype.nightcore .lyric');
  });
});

describe('style contract: the removed icon stage stays removed (Faz 6.7 P4)', () => {
  it('no rule mentions the stage band or the park spot', () => {
    // 0.9.0 put icons outside the box by dropping them from a top band and
    // parking one in the left gutter. Caner's field verdict was that both
    // read as plain, so they were removed rather than tuned — the particle
    // layer replaces them. A stray rule would be dead weight that still
    // participates in the cascade.
    const strays = selectorLines(css).filter(
      (line) =>
        line.includes('fx-stage') || line.includes('stage-slot') || line.includes('fx-park'),
    );
    expect(strays).toEqual([]);
    expect(css).not.toContain('kashi-stage-drop');
  });
});

describe('style contract: the box zone matches the window (Faz 6.7 P4)', () => {
  const geometry = readFileSync(
    fileURLToPath(new URL('../shared/box-zone.ts', import.meta.url)),
    'utf8',
  );

  it('#stage padding is exactly the margin BOX_ZONE leaves around the box', () => {
    // Two files have to agree or the box drifts from where main thinks it is:
    // main hit-tests and migrates positions against BOX_ZONE, the renderer
    // lays the box out with padding. This nails them together.
    const zone = geometry.match(
      /BOX_ZONE: BoxRect = \{ x: (\d+), y: (\d+), width: (\d+), height: (\d+) \}/,
    );
    const size = geometry.match(
      /WINDOW_WIDTH = (\d+);\nexport const WINDOW_HEIGHT = (\d+);/,
    );
    expect(zone, 'BOX_ZONE literal').not.toBeNull();
    expect(size, 'WINDOW_* literals').not.toBeNull();
    const [x, y, width, height] = zone!.slice(1).map(Number) as [number, number, number, number];
    const [windowWidth, windowHeight] = size!.slice(1).map(Number) as [number, number];

    const padding = css.match(/#stage \{[^}]*padding: (\d+)px (\d+)px (\d+)px;/);
    expect(padding, '#stage padding').not.toBeNull();
    const [top, side, bottom] = padding!.slice(1).map(Number) as [number, number, number];

    expect({ top, side, bottom }).toEqual({
      top: y,
      side: x,
      bottom: windowHeight - y - height,
    });
    expect(x * 2 + width).toBe(windowWidth); // the box is horizontally centered
  });
});

describe('style contract: particles never cover the text (Faz 6.7 P5)', () => {
  it('the lyric box declares a stacking order above the canvas', () => {
    // The canvas is prepended to <body> at z-index 0. Tree order alone would
    // paint the box on top, but DG6 is load-bearing enough to state outright.
    const box = css.match(/#lyric-box \{[^}]*\}/)?.[0];
    expect(box, '#lyric-box block').toBeDefined();
    expect(box).toContain('z-index: 1');
  });
});
