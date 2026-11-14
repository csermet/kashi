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
import { BOX_CHROME, BOX_SCALES, EDGE_FADE_PX } from '@kashi/view';
import {
  BOX_ZONE,
  BOX_ZONE_PRESETS,
  boxZoneFor,
  geometryVars,
  WINDOW_HEIGHT,
  WINDOW_WIDTH,
} from '../shared/box-zone.js';

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

describe('style contract: the box zone matches the window (Faz 6.7 P4 / Faz 7 P3)', () => {
  it('the stage is padded by the host geometry, and that geometry IS the zone', () => {
    // main hit-tests and migrates positions against BOX_ZONE; the renderer lays
    // the box out with the stage padding the host derives from the same zone
    // (geometryVars). Until client review E.4 the stylesheet carried its own
    // copy of the default padding; now it only reads the variable.
    expect(css).toMatch(/#stage \{[^}]*padding: var\(--kashi-stage-padding, 0\);/);
    for (const scale of BOX_SCALES) {
      const zone = BOX_ZONE_PRESETS[scale];
      const vars = geometryVars(scale);
      const [top, side, bottom] = (vars['--kashi-stage-padding'] ?? '')
        .split(' ')
        .map((v) => Number.parseFloat(v));
      expect({ top, side, bottom }, scale).toEqual({
        top: zone.y,
        side: zone.x,
        bottom: WINDOW_HEIGHT - zone.y - zone.height,
      });
      expect(vars['--kashi-box-max-width'], scale).toBe(`${zone.width - 60}px`);
    }
    expect(geometryVars(undefined)).toEqual(geometryVars('medium'));
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

  it("the stylesheet's chrome fallbacks ARE medium's chrome — default look pinned", () => {
    // Box size was invisible for a whole phase because only the wrap width
    // changed (field report 2026-08-12). The chrome makes it visible, and
    // this pins the two places that must agree: an untouched install (CSS
    // fallbacks) and the runtime's medium preset render identically.
    const m = BOX_CHROME.medium;
    expect(css).toContain(`padding: var(--kashi-box-pad, ${m.padY}px ${m.padX}px);`);
    expect(css).toContain(`border-radius: var(--kashi-box-radius, ${m.radius}px);`);
  });

  it('every chrome step is visibly monotone — no two presets render alike', () => {
    const { small, medium, large } = BOX_CHROME;
    for (const key of ['padY', 'padX', 'radius'] as const) {
      expect(small[key], key).toBeLessThan(medium[key]);
      expect(medium[key], key).toBeLessThan(large[key]);
    }
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

describe('style contract: every fx archetype is renderable (Faz 7 P2)', () => {
  it('maps only real categories, and only shapes the atlas can draw', async () => {
    // The archetype table is the one place where the lexicon, the texture
    // atlas and the physics have to agree. A tag that is not in the lexicon
    // would never fire; a shape the atlas cannot draw would silently emit
    // nothing at all.
    const { FX_ARCHETYPES, FX_BASE_COLORS, resolveFxProfile } = await import('@kashi/view');
    const { PARTICLE_SHAPES } = await import('@kashi/view');

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

describe('style contract: an untimed line wears the theme (Faz 7 P8b)', () => {
  const mainSource = readFileSync(
    fileURLToPath(new URL('../../../../packages/view/src/lyrics-view.ts', import.meta.url)),
    'utf8',
  );

  it('the plain class is decided by the view, never by the span cache', () => {
    // The regression this locks: `wordSpans` describes the line painted LAST
    // time — it is cleared further down the same function and rebuilt later in
    // the frame — so keying the class off it meant a plain line following a
    // word-synced one never got the class and rendered stock white.
    expect(mainSource).toContain("classList.toggle('plain', view.linePlain)");
    expect(mainSource, 'the stale-span condition came back').not.toMatch(
      /toggle\(\s*'plain',\s*wordSpans/,
    );
  });

  it('the rule reads its own variable, not a mix that resolves to white', () => {
    // `color-mix(in oklab, --kashi-primary 55%, --kashi-text)` was the old
    // tint. `--kashi-text` is pinned white by design, so the mix landed at
    // L≈0.89 with a tenth of the chroma — and on every path where the primary
    // itself falls back to white (no palette, fx off, theme none, neutral art,
    // WCAG backstop) it WAS white.
    const start = css.indexOf('.lyric.plain');
    expect(start, '.lyric.plain rule is missing').toBeGreaterThan(-1);
    // Bounded at the closing brace: a fixed-width slice spills into the NEXT
    // rule and would assert against whatever happens to follow.
    const block = css.slice(start, css.indexOf('}', start) + 1);
    expect(block).toContain('var(--kashi-plain');
    expect(block).not.toContain('--kashi-text');
  });
});

describe('style contract: a rescued line is softened, never removed (Faz 8.1)', () => {
  const mainSource = readFileSync(
    fileURLToPath(new URL('../../../../packages/view/src/lyrics-view.ts', import.meta.url)),
    'utf8',
  );

  it('the flag reaches a pixel at all', () => {
    // The whole defect this closes: the server has written lines[].uncertain
    // since pipeline 2.19.0 and no client read it, so the arbiter's verdict
    // was invisible in the field. If this class stops being applied, the
    // signal is silently half-delivered again.
    expect(mainSource).toContain("classList.toggle('uncertain', view.lineUncertain)");
    const start = css.indexOf('.lyric.uncertain');
    expect(start, '.lyric.uncertain rule is missing').toBeGreaterThan(-1);
    const block = css.slice(start, css.indexOf('}', start) + 1);
    expect(block).toContain('--kashi-uncertain-fade');
  });

  it('softens without hiding: no display/visibility escape hatch', () => {
    // Caner's rule for this change was explicit — de-emphasise, no deleting,
    // no hiding. A later "just skip the doubtful lines" would land here.
    const start = css.indexOf('.lyric.uncertain');
    const block = css.slice(start, css.indexOf('}', start) + 1);
    for (const banned of ['display:', 'visibility:', 'content:']) {
      expect(block, `${banned} in the uncertain rule`).not.toContain(banned);
    }
  });

  it('line fades MULTIPLY instead of racing on specificity', () => {
    // Two independent reasons to soften one line (ad-lib text, rescued
    // timings) can both be true — `adlib` comes from the text, `uncertain`
    // from line QA's rescue path. If each declared its own `opacity`, the
    // more specific selector would win outright and one signal would vanish:
    // `body.fx-simple .lyric.adlib` (0,3,1) beats `.lyric.uncertain` (0,2,0),
    // so an ad-lib line that was ALSO rescued would have shown no softening
    // beyond the ad-lib's own. The factor variables make it a product.
    const base = css.slice(css.indexOf('.lyric {'), css.indexOf('}', css.indexOf('.lyric {')) + 1);
    expect(base).toContain('--kashi-adlib-fade');
    expect(base).toContain('--kashi-uncertain-fade');
    // Both default to 1: a line wearing neither class is pixel-identical to
    // the pre-Faz-8.1 stylesheet, which had no opacity on .lyric at all.
    expect(base).toContain('var(--kashi-adlib-fade, 1)');
    expect(base).toContain('var(--kashi-uncertain-fade, 1)');
    // And the ad-lib rule must contribute a FACTOR, not an opacity of its own.
    const adlibStart = css.indexOf('body.fx-simple .lyric.adlib');
    expect(adlibStart, 'the ad-lib rule moved').toBeGreaterThan(-1);
    const adlib = css.slice(adlibStart, css.indexOf('}', adlibStart) + 1);
    expect(adlib).toContain('--kashi-adlib-fade');
    expect(adlib, 'the ad-lib rule went back to a bare opacity').not.toMatch(/(^|[^-])opacity:/);
  });
});

describe('style contract: particle alphas have one source (Faz 7 P8b)', () => {
  const textures = readFileSync(
    fileURLToPath(new URL('../../../../packages/view/src/fx-textures.ts', import.meta.url)),
    'utf8',
  );

  it('the painters draw the declared peak, not a copy of it', () => {
    // The visibility guard in fx-particles-logic.test.ts multiplies each
    // archetype's band by its texture's peak alpha. That only means anything
    // if the painter uses the same number — a hardcoded literal would let the
    // guard certify a value nothing draws.
    expect(textures).toContain('SHAPE_PEAK_ALPHA.smoke');
    expect(textures, 'a painter hardcoded its alpha again').not.toMatch(
      /rgba\(255,\s*255,\s*255,\s*0\.\d+\)/,
    );
  });
});

describe('style contract: the fx-fired hold (Faz 9, field report 2026-08-12)', () => {
  it('the held word changes colour and glow but never geometry', () => {
    // "The effect comes but you can't see why": a clipped word span can flash
    // past in 150 ms while its burst lives a second, so the triggering word
    // is held lit. It must not transform — a held word shifting the line
    // would move the ACTIVE word that follows it, which is the one being
    // read. Colour and shadow only.
    const rule = css.match(/body\.fx-hype \.word\.fx-fired:not\(\.word-fill\) \{([^}]*)\}/);
    expect(rule, 'fx-fired rule').not.toBeNull();
    const body = rule![1]!;
    expect(body).toContain('color:');
    expect(body).toContain('text-shadow:');
    expect(body).not.toContain('transform');
    expect(body).not.toContain('font-size');
    expect(body).not.toContain('margin');
    expect(body).not.toContain('padding');
    // 2026-10 review: the hype contract never interpolates text-shadow values
    // or a per-word colour — the glow snaps with the class.
    expect(body).not.toContain('transition');
  });

  it('a word still being swept is not held lit (2026-10 review)', () => {
    // .word-fill clears text-shadow for its transparent-glyph gradient, but a
    // 0,3,1 fx-fired rule outranks that 0,2,1 reset: the swept word got a
    // 12 px glow through transparent glyphs, muddying it and hiding the sweep.
    const firedSelectors = selectorLines(css).filter((line) => line.includes('.fx-fired'));
    expect(firedSelectors.length).toBeGreaterThan(0);
    for (const line of firedSelectors) expect(line).toContain(':not(.word-fill)');
  });

  it('the hold is hype-only — quieter levels stay quiet', () => {
    const strays = selectorLines(css).filter(
      (line) => line.includes('fx-fired') && !line.startsWith('body.fx-hype'),
    );
    expect(strays).toEqual([]);
  });
});

describe('style contract: one sweep, the same at every sweeping level (client review E.3)', () => {
  /** The rule body whose selector list names `selector` exactly. */
  function ruleBody(selector: string): string {
    const noComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
    const hit = noComments.split('}').find((block) =>
      (block.split('{')[0] ?? '')
        .split(',')
        .map((s) => s.trim())
        .includes(selector),
    );
    return (hit?.split('{')[1] ?? '').replace(/\s+/g, ' ');
  }
  /** The gradient's arguments, split on top-level commas. */
  function stops(body: string): string[] {
    const inner = /background-image: linear-gradient\((.*)\);/.exec(body)?.[1] ?? '';
    const parts: string[] = [];
    let depth = 0;
    let current = '';
    for (const ch of inner) {
      if (ch === '(') depth += 1;
      if (ch === ')') depth -= 1;
      if (ch === ',' && depth === 0) {
        parts.push(current.trim());
        current = '';
      } else {
        current += ch;
      }
    }
    parts.push(current.trim());
    return parts;
  }

  it('head, boundary and tail are pinned at every sweeping level', () => {
    // A held note sweeps through --kashi-fill like every other sweep. The head
    // carries the fx word's own colour first (--fx-color, 2026-08-13), the
    // unsung tail rests on the dim theme tone; the boundary is the fill value.
    for (const level of ['fx-simple', 'fx-full', 'fx-hype']) {
      expect(stops(ruleBody(`body.${level} .word-fill`)), level).toEqual([
        '90deg',
        'var(--fx-color, var(--kashi-primary, #fff)) calc(var(--kashi-fill, 0) * 100%)',
        'color-mix(in srgb, var(--fx-color, var(--kashi-secondary, #fff)) 55%, transparent) calc(var(--kashi-fill, 0) * 100%)',
      ]);
    }
  });
});
