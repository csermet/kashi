import { describe, expect, it } from 'vitest';
import { computeFxTintVars, FX_BASE_COLORS } from './effects-logic.js';
import {
  BURST_PARTICLES,
  EDGE_FADE_PX,
  insideBox,
  makeRandom,
  MAX_LIFE_S,
  parseTintColor,
  particleAlpha,
  pointOnPerimeter,
  planEmission,
  shouldMountLayer,
  stepParticle,
  type Particle,
  type Rect,
} from './fx-particles-logic.js';

// The 0.15.0 geometry: 800×460 window, box zone 560×180 at (120,160).
const BOX: Rect = { x: 120, y: 160, width: 560, height: 180 };
const W = 800;
const H = 460;

const particle = (over: Partial<Particle> = {}): Particle => ({
  x: 400,
  y: 60,
  vx: 0,
  vy: 0,
  age: 0.2,
  life: 1,
  size: 8,
  rotation: 0,
  spin: 0,
  ...over,
});

describe('shouldMountLayer', () => {
  it('exists only at hype — the other levels stay pixel-identical', () => {
    expect(shouldMountLayer('hype', false)).toBe(true);
    for (const level of ['off', 'simple', 'full']) {
      expect(shouldMountLayer(level, false), level).toBe(false);
    }
  });

  it('never mounts under reduced motion, whatever the level', () => {
    expect(shouldMountLayer('hype', true)).toBe(false);
  });
});

describe('planEmission — the whole outline, not one spot', () => {
  it('emits the whole budget from ALL FOUR edges (the field complaint)', () => {
    expect(planEmission(BOX, makeRandom(9))).toHaveLength(BURST_PARTICLES);
    // A burst appearing in one arbitrary place reads as detached from the
    // line that caused it. Every side must be represented in every burst.
    const sides = { top: 0, right: 0, bottom: 0, left: 0 };
    for (const p of planEmission(BOX, makeRandom(9))) {
      if (Math.abs(p.y - BOX.y) < 0.01) sides.top++;
      else if (Math.abs(p.y - (BOX.y + BOX.height)) < 0.01) sides.bottom++;
      else if (Math.abs(p.x - BOX.x) < 0.01) sides.left++;
      else sides.right++;
    }
    for (const [side, n] of Object.entries(sides)) {
      expect(n, `no particles on the ${side} edge`).toBeGreaterThan(0);
    }
  });

  it('starts every particle ON the edge, never inside the box (DG6)', () => {
    for (const p of planEmission(BOX, makeRandom(2))) {
      const onEdge =
        Math.abs(p.x - BOX.x) < 0.01 ||
        Math.abs(p.x - (BOX.x + BOX.width)) < 0.01 ||
        Math.abs(p.y - BOX.y) < 0.01 ||
        Math.abs(p.y - (BOX.y + BOX.height)) < 0.01;
      expect(onEdge).toBe(true);
    }
  });

  it('pushes outward, so nothing drifts across the lyrics', () => {
    for (const p of planEmission(BOX, makeRandom(6))) {
      if (Math.abs(p.y - BOX.y) < 0.01) expect(p.vy).toBeLessThan(0);
      if (Math.abs(p.y - (BOX.y + BOX.height)) < 0.01) expect(p.vy).toBeGreaterThan(0);
      if (Math.abs(p.x - BOX.x) < 0.01) expect(p.vx).toBeLessThan(0);
      if (Math.abs(p.x - (BOX.x + BOX.width)) < 0.01) expect(p.vx).toBeGreaterThan(0);
    }
  });

  it('stays NEAR the box — the other half of the complaint', () => {
    // Particles were landing a long way from the line they belonged to. The
    // margin is 120-160px; nothing should travel much past it.
    for (const p of planEmission(BOX, makeRandom(8))) {
      while (stepParticle(p, 1 / 60)) {
        /* run it to death */
      }
      const dx = Math.max(BOX.x - p.x, p.x - (BOX.x + BOX.width), 0);
      const dy = Math.max(BOX.y - p.y, p.y - (BOX.y + BOX.height), 0);
      // 120px is the narrowest margin (sides and bottom): nothing may leave
      // the band the effect is supposed to live in.
      expect(Math.max(dx, dy)).toBeLessThan(120);
    }
  });

  it('gives everything a bounded, finite life', () => {
    for (const p of planEmission(BOX, makeRandom(11))) {
      expect(p.life).toBeGreaterThan(0);
      expect(p.life).toBeLessThanOrEqual(MAX_LIFE_S);
    }
  });

  it('is deterministic for a seed', () => {
    expect(planEmission(BOX, makeRandom(42))).toEqual(planEmission(BOX, makeRandom(42)));
  });

  it('almost every particle is actually seen', () => {
    let visible = 0;
    let total = 0;
    for (let seed = 1; seed <= 20; seed++) {
      for (const p of planEmission(BOX, makeRandom(seed))) {
        total++;
        while (stepParticle(p, 1 / 60)) {
          if (!insideBox(p.x, p.y, BOX) && particleAlpha(p, W, H) > 0.05) {
            visible++;
            break;
          }
        }
      }
    }
    expect(visible / total).toBeGreaterThan(0.9);
  });
});

describe('pointOnPerimeter', () => {
  it('walks top, right, bottom, left and wraps', () => {
    expect(pointOnPerimeter(0, BOX)).toEqual([BOX.x, BOX.y, 0, -1]);
    expect(pointOnPerimeter(BOX.width, BOX)).toEqual([BOX.x + BOX.width, BOX.y, 1, 0]);
    const perimeter = 2 * (BOX.width + BOX.height);
    expect(pointOnPerimeter(perimeter, BOX)).toEqual(pointOnPerimeter(0, BOX));
    expect(pointOnPerimeter(-1, BOX)).toEqual(pointOnPerimeter(perimeter - 1, BOX));
  });
});

describe('stepParticle', () => {
  it('reports death exactly once the life is spent', () => {
    const p = particle({ age: 0.9, life: 1 });
    expect(stepParticle(p, 0.05)).toBe(true);
    expect(stepParticle(p, 0.05)).toBe(false);
  });

  it('every particle dies within MAX_LIFE_S — a paused song leaves no residue', () => {
    const alive = planEmission(BOX, makeRandom(5));
    let steps = 0;
    let remaining = alive;
    while (remaining.length > 0 && steps < 1000) {
      remaining = remaining.filter((p) => stepParticle(p, 1 / 60));
      steps++;
    }
    expect(remaining).toHaveLength(0);
    expect(steps / 60).toBeLessThanOrEqual(MAX_LIFE_S + 0.05);
  });

  it('pulls downward and slows down (gravity plus drag)', () => {
    const p = particle({ vx: 100, vy: 0, age: 0 });
    stepParticle(p, 0.1);
    expect(p.vy).toBeGreaterThan(0);
    expect(p.vx).toBeLessThan(100);
  });
});

describe('particleAlpha — the invisible boundary', () => {
  it('is fully faded AT the window edge, so no line is ever drawn', () => {
    expect(particleAlpha(particle({ x: 0, y: 200 }), W, H)).toBe(0);
    expect(particleAlpha(particle({ x: W, y: 200 }), W, H)).toBe(0);
    expect(particleAlpha(particle({ y: 0 }), W, H)).toBe(0);
    expect(particleAlpha(particle({ y: H }), W, H)).toBe(0);
  });

  it('fades gradually across the band rather than snapping', () => {
    // A long life isolates the EDGE term from the age term.
    const at = (x: number) => particleAlpha(particle({ x, y: 200, age: 0.1, life: 10 }), W, H);
    const near = at(EDGE_FADE_PX / 4);
    const mid = at(EDGE_FADE_PX / 2);
    const inside = at(EDGE_FADE_PX * 2);
    expect(near).toBeLessThan(mid);
    expect(mid).toBeLessThan(inside);
    expect(inside).toBeGreaterThan(0.9);
  });

  it('fades in at birth and out at death', () => {
    expect(particleAlpha(particle({ age: 0 }), W, H)).toBe(0);
    expect(particleAlpha(particle({ age: 0.999, life: 1 }), W, H)).toBeLessThan(0.05);
  });

  it('is never negative or above one, wherever the particle is', () => {
    for (const x of [-500, 0, 400, 5000]) {
      for (const age of [0, 0.5, 5]) {
        const a = particleAlpha(particle({ x, age, life: 1 }), W, H);
        expect(a).toBeGreaterThanOrEqual(0);
        expect(a).toBeLessThanOrEqual(1);
      }
    }
  });
});

describe('insideBox (DG6)', () => {
  it('protects the whole box rect, edges included', () => {
    expect(insideBox(400, 250, BOX)).toBe(true);
    expect(insideBox(BOX.x, BOX.y, BOX)).toBe(true);
    expect(insideBox(BOX.x + BOX.width, BOX.y + BOX.height, BOX)).toBe(true);
  });

  it('lets the margins through — that is where the effect lives', () => {
    expect(insideBox(400, 80, BOX)).toBe(false); // above
    expect(insideBox(400, 400, BOX)).toBe(false); // below
    expect(insideBox(40, 250, BOX)).toBe(false); // left gutter
    expect(insideBox(760, 250, BOX)).toBe(false); // right gutter
  });
});

describe('parseTintColor — every category wears its own colour', () => {
  it('turns a tint variable into the integer Pixi wants', () => {
    expect(parseTintColor('#4cd964')).toBe(0x4cd964);
    expect(parseTintColor('  #FF6FA5  ')).toBe(0xff6fa5);
  });

  it('falls back to white rather than to nothing', () => {
    expect(parseTintColor(undefined)).toBe(0xffffff);
    expect(parseTintColor('oklch(0.8 0.2 140)')).toBe(0xffffff);
    expect(parseTintColor('')).toBe(0xffffff);
  });

  it('all 24 lexicon categories resolve to a REAL colour, not the fallback', () => {
    // The silent failure this guards: if the tint pipeline ever stopped
    // emitting hex, every particle would quietly turn white and no test or
    // log would complain — the effect would just look broken to one person.
    const vars = computeFxTintVars('#3aa0ff', 'full');
    const tags = Object.keys(FX_BASE_COLORS);
    expect(tags.length).toBe(24);
    for (const tag of tags) {
      expect(parseTintColor(vars[`--fx-tint-${tag}`]), tag).not.toBe(0xffffff);
    }
  });
});

