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
import {
  BOX_SCALES,
  BOX_ZONE,
  BOX_ZONE_PRESETS,
  boxZoneFor,
  WINDOW_HEIGHT,
  WINDOW_WIDTH,
} from '../shared/box-zone.js';
import { EDGE_FADE_PX } from '../renderer/src/fx-particles-logic.js';

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

describe('style contract: the box zone matches the window (Faz 6.7 P4 / Faz 7 P3)', () => {
  it('#stage padding is exactly the margin the DEFAULT zone leaves around the box', () => {
    // Two files have to agree or the box drifts from where main thinks it is:
    // main hit-tests and migrates positions against BOX_ZONE, the renderer
    // lays the box out with padding. This nails them together. The stylesheet
    // states the DEFAULT geometry; the other scales are applied at runtime.
    const padding = css.match(/#stage \{[^}]*padding: (\d+)px (\d+)px (\d+)px;/);
    expect(padding, '#stage padding').not.toBeNull();
    const [top, side, bottom] = padding!.slice(1).map(Number) as [number, number, number];

    expect({ top, side, bottom }).toEqual({
      top: BOX_ZONE.y,
      side: BOX_ZONE.x,
      bottom: WINDOW_HEIGHT - BOX_ZONE.y - BOX_ZONE.height,
    });
    expect(BOX_ZONE.x * 2 + BOX_ZONE.width).toBe(WINDOW_WIDTH); // horizontally centered
  });

  it('every box scale stays centred, inside the window, with room to fade', () => {
    // A box the user can resize must not push particles against the window
    // edge: below EDGE_FADE_PX of margin they would be cut off mid-flight
    // instead of fading, which is exactly the visible boundary Caner ruled out.
    const centre = { x: BOX_ZONE.x + BOX_ZONE.width / 2, y: BOX_ZONE.y + BOX_ZONE.height / 2 };
    for (const scale of BOX_SCALES) {
      const zone = BOX_ZONE_PRESETS[scale];
      expect(zone.x * 2 + zone.width, `${scale} not centred horizontally`).toBe(WINDOW_WIDTH);
      expect(
        { x: zone.x + zone.width / 2, y: zone.y + zone.height / 2 },
        `${scale} moved the centre`,
      ).toEqual(centre);
      const margins = [
        zone.x,
        zone.y,
        WINDOW_WIDTH - zone.x - zone.width,
        WINDOW_HEIGHT - zone.y - zone.height,
      ];
      for (const margin of margins) {
        expect(margin, `${scale} leaves only ${margin}px of margin`).toBeGreaterThanOrEqual(
          EDGE_FADE_PX,
        );
      }
    }
  });

  it('the default scale IS the shipped zone — an untouched install cannot move', () => {
    expect(boxZoneFor('medium')).toEqual(BOX_ZONE);
    expect(boxZoneFor(undefined)).toEqual(BOX_ZONE);
    expect(boxZoneFor('not-a-scale')).toEqual(BOX_ZONE);
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

describe('style contract: no invented hues (Faz 6.7 field bug)', () => {
  it('never mixes two colours in OKLCH', () => {
    // OKLCH interpolates the hue ANGLE, so blending distant hues rotates
    // through colours neither input contains — a green palette mixed with a
    // pink tint came out gold and stayed there. Oklab has no hue axis to
    // travel along; sRGB is fine too. This rule only bans the trap.
    const offenders = css.match(/color-mix\(\s*in\s+oklch[^)]*\)/g) ?? [];
    expect(offenders).toEqual([]);
  });
});

describe('style contract: the DOM burst pool stays removed (Faz 7 P2)', () => {
  const rendererSource = ['main.ts', 'effects-logic.ts']
    .map((file) => readFileSync(fileURLToPath(new URL(`../renderer/src/${file}`, import.meta.url)), 'utf8'))
    .join('\n');

  it('no CSS rule is left behind from the old pool', () => {
    // 0.4.0 scattered twelve absolutely-positioned spans INSIDE the box for
    // explosion and electric. The Pixi layer does that job for all 24
    // categories now, outside the box, so the pool went. A leftover rule
    // would still take part in the cascade.
    const strays = selectorLines(css).filter(
      (line) => line.includes('fx-burst') || line.includes('bursting') || /\.fx-particle\b/.test(line),
    );
    expect(strays).toEqual([]);
    expect(css).not.toContain('kashi-fx-burst');
  });

  it('no code path still reaches for it', () => {
    for (const token of ['FX_BURST_TAGS', 'ensureBurstPool', 'triggerBurst']) {
      expect(rendererSource, `${token} survived the removal`).not.toContain(token);
    }
  });
});

describe('style contract: every fx archetype is renderable (Faz 7 P2)', () => {
  it('maps only real categories, and only shapes the atlas can draw', async () => {
    // The archetype table is the one place where the lexicon, the texture
    // atlas and the physics have to agree. A tag that is not in the lexicon
    // would never fire; a shape the atlas cannot draw would silently emit
    // nothing at all.
    const { FX_ARCHETYPES, FX_BASE_COLORS, resolveFxProfile } = await import(
      '../renderer/src/effects-logic.js'
    );
    const { PARTICLE_SHAPES } = await import('../renderer/src/fx-textures.js');

    for (const [tag, entry] of FX_ARCHETYPES) {
      expect(FX_BASE_COLORS, `${tag} is not a lexicon category`).toHaveProperty(tag);
      if (entry.shape) expect(PARTICLE_SHAPES).toContain(entry.shape);
    }
    // And every category — hero or not — resolves to a drawable profile.
    for (const tag of Object.keys(FX_BASE_COLORS)) {
      const profile = resolveFxProfile(tag);
      expect(profile.shapes.length, tag).toBeGreaterThan(0);
      for (const shape of profile.shapes) expect(PARTICLE_SHAPES, `${tag}/${shape}`).toContain(shape);
    }
  });
});
