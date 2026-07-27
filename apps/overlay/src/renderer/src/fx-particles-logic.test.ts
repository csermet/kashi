import { describe, expect, it } from 'vitest';
import {
  BURST_PARTICLES,
  EDGE_FADE_PX,
  insideBox,
  makeRandom,
  MAX_LIFE_S,
  particleAlpha,
  planBurst,
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

describe('planBurst', () => {
  it('aims away from the box, so nothing is launched across the lyrics', () => {
    const random = makeRandom(7);
    // Above the box: every particle should be heading upward, away from it.
    const up = planBurst(400, 80, BOX, random);
    expect(up).toHaveLength(BURST_PARTICLES);
    expect(up.every((p) => p.vy < 0)).toBe(true);

    // Below the box: the same rule mirrored.
    const down = planBurst(400, 400, BOX, makeRandom(7));
    expect(down.every((p) => p.vy > 0)).toBe(true);
  });

  it('spreads sideways when the origin is beside the box', () => {
    const left = planBurst(40, 250, BOX, makeRandom(3));
    expect(left.every((p) => p.vx < 0)).toBe(true);
  });

  it('falls back to straight up when the origin IS the box centre', () => {
    const p = planBurst(BOX.x + BOX.width / 2, BOX.y + BOX.height / 2, BOX, makeRandom(1));
    expect(p.every((q) => q.vy < 0)).toBe(true);
  });

  it('gives everything a bounded, finite life', () => {
    for (const p of planBurst(400, 80, BOX, makeRandom(11))) {
      expect(p.life).toBeGreaterThan(0);
      expect(p.life).toBeLessThanOrEqual(MAX_LIFE_S);
    }
  });

  it('is deterministic for a seed — the same burst twice is the same burst', () => {
    expect(planBurst(400, 80, BOX, makeRandom(42))).toEqual(
      planBurst(400, 80, BOX, makeRandom(42)),
    );
  });
});

describe('stepParticle', () => {
  it('reports death exactly once the life is spent', () => {
    const p = particle({ age: 0.9, life: 1 });
    expect(stepParticle(p, 0.05)).toBe(true);
    expect(stepParticle(p, 0.05)).toBe(false);
  });

  it('every particle dies within MAX_LIFE_S — a paused song leaves no residue', () => {
    const alive = planBurst(400, 80, BOX, makeRandom(5));
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
