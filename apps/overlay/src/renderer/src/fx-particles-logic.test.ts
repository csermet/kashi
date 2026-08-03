import { describe, expect, it } from 'vitest';
import { PARTICLE_BANDS } from './color-tone.js';
import { FX_ARCHETYPES, resolveFxProfile } from './effects-logic.js';
import { SHAPE_PEAK_ALPHA } from './fx-textures.js';
import { computeFxTintVars, FX_BASE_COLORS } from './effects-logic.js';
import { PARTICLE_SHAPES } from './fx-textures.js';
import {
  ARCHETYPE_PROFILES,
  BURST_PARTICLES,
  EDGE_FADE_PX,
  GENERIC_PROFILE,
  GRAVITY,
  insideBox,
  makeRandom,
  maxParticleLifetimeMs,
  MAX_LIFE_S,
  parseTintColor,
  particleAlpha,
  particleSize,
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
  gravity: GRAVITY,
  shape: 'spark',
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


describe('archetype profiles', () => {
  /** Runs a burst to completion, sampling every particle each frame. */
  function simulate(profile = GENERIC_PROFILE, seed = 7) {
    const particles = planEmission(BOX, makeRandom(seed), profile);
    const samples: Array<{ x: number; y: number; alpha: number }> = [];
    const live = [...particles];
    for (let frame = 0; frame < 400 && live.length > 0; frame++) {
      for (let i = live.length - 1; i >= 0; i--) {
        const p = live[i]!;
        if (!stepParticle(p, 1 / 60)) {
          live.splice(i, 1);
          continue;
        }
        samples.push({ x: p.x, y: p.y, alpha: particleAlpha(p, W, H) });
      }
    }
    return { particles, samples };
  }

  it('every profile draws only shapes the texture atlas can supply', () => {
    for (const [name, profile] of Object.entries(ARCHETYPE_PROFILES)) {
      expect(profile.shapes.length, name).toBeGreaterThan(0);
      for (const shape of profile.shapes) {
        expect(PARTICLE_SHAPES, `${name}/${shape}`).toContain(shape);
      }
    }
  });

  it('every archetype is actually SEEN — the mask must not eat a category', () => {
    // The guard that matters, and the one this package originally lacked.
    // Three archetypes shipped past review with physics that sent particles
    // INTO the box, where the DG6 mask hides them: love rose from the floor
    // through 180px of box, poison sank through it from the top, and shine
    // hovered on the boundary flickering. Every one of them passed a
    // "position envelope" test, because the particles went exactly where the
    // profile said — they just could never be drawn.
    for (const [name, profile] of Object.entries(ARCHETYPE_PROFILES)) {
      const particles = planEmission(BOX, makeRandom(7), profile);
      let neverSeen = 0;
      for (const p of particles) {
        let seen = false;
        while (stepParticle(p, 1 / 60)) {
          // Inflate by the DRAWN size, exactly as fx-canvas does. Measuring
          // birth size let a growing archetype pass the guard while spending
          // most of its life hidden under the box — the geometric half of the
          // poison bug walked straight through this line.
          if (!insideBox(p.x, p.y, BOX, particleSize(p) / 2) && particleAlpha(p, W, H) > 0.05) {
            seen = true;
            break;
          }
        }
        if (!seen) neverSeen += 1;
      }
      expect(neverSeen / particles.length, `${name}: particles that are never drawn`).toBeLessThan(
        0.1,
      );
    }
  });

  it('never draws anything outside the window', () => {
    for (const profile of [GENERIC_PROFILE, ...Object.values(ARCHETYPE_PROFILES)]) {
      const { samples } = simulate(profile);
      const visibleOutside = samples.filter(
        (s) => s.alpha > 0.01 && (s.x < 0 || s.y < 0 || s.x > W || s.y > H),
      );
      expect(visibleOutside).toEqual([]);
    }
  });

  it('fall pours down the sides instead of hiding behind the box', () => {
    // A top-edge fall would spend most of its life inside the box, where the
    // DG6 mask hides it. The side emission is what keeps the descent on screen.
    // Birth positions from a fresh plan — simulate() mutates as it runs.
    for (const p of planEmission(BOX, makeRandom(7), ARCHETYPE_PROFILES.fall)) {
      const onASide =
        Math.abs(p.x - BOX.x) < 0.01 || Math.abs(p.x - (BOX.x + BOX.width)) < 0.01;
      expect(onASide, `born at ${p.x},${p.y}`).toBe(true);
    }
    const { samples } = simulate(ARCHETYPE_PROFILES.fall);
    const hidden = samples.filter((s) => s.alpha > 0.01 && insideBox(s.x, s.y, BOX));
    expect(hidden.length / samples.length).toBeLessThan(0.05);
  });

  it('gives each archetype the direction its category means', () => {
    // Sampling the LAUNCH velocity alone tests `direction.down` and nothing
    // else — an archetype's gravity could be inverted and this would stay
    // green. What a category means is where its particles END UP, so measure
    // net displacement over the profile's own lifetime.
    const netDrift = (profile: typeof GENERIC_PROFILE) => {
      const particles = planEmission(BOX, makeRandom(3), profile);
      const start = particles.map((p) => ({ x: p.x, y: p.y }));
      for (const p of particles) {
        while (stepParticle(p, 1 / 60)) {
          /* to death */
        }
      }
      return particles.reduce((sum, p, i) => sum + (p.y - start[i]!.y), 0) / particles.length;
    };

    // Poison sinks and money/water pour downward; love rises.
    expect(netDrift(ARCHETYPE_PROFILES.smoke), 'poison should sink').toBeGreaterThan(20);
    expect(netDrift(ARCHETYPE_PROFILES.fall), 'money/water should fall').toBeGreaterThan(20);
    expect(netDrift(ARCHETYPE_PROFILES.drift), 'love should rise').toBeLessThan(-20);
  });

  it('staggers births only where a profile asked for it', () => {
    const staggered = planEmission(BOX, makeRandom(5), ARCHETYPE_PROFILES.smoke);
    expect(staggered.some((p) => p.age < 0)).toBe(true);

    const instant = planEmission(BOX, makeRandom(5), ARCHETYPE_PROFILES.burst);
    expect(instant.every((p) => p.age === 0)).toBe(true);
  });

  it('an unborn particle keeps the ticker alive but draws nothing', () => {
    const p = particle({ age: -0.2, life: 1, x: 400, y: 60 });
    expect(particleAlpha(p, W, H)).toBe(0);
    expect(stepParticle(p, 1 / 60)).toBe(true); // still in the list
    expect(p.x).toBe(400); // and it has not moved
  });

  it('bounds how long a burst can linger, stagger included', () => {
    // The "a stopped song leaves no residue" contract, now profile-aware.
    // Asserted against the exact number the profiles imply rather than a wide
    // window: a range from 1.4s to 3.2s is loose enough to swallow the
    // stagger term itself, so dropping it would leave this green.
    const worst = [GENERIC_PROFILE, ...Object.values(ARCHETYPE_PROFILES)].reduce(
      (max, p) => Math.max(max, p.emissionSpanMs + p.life[1] * 1000),
      0,
    );
    expect(maxParticleLifetimeMs()).toBe(worst);

    // And the stagger is genuinely part of it — the longest profile staggers,
    // so lifetime alone is a strictly smaller number.
    const lifeOnly = [GENERIC_PROFILE, ...Object.values(ARCHETYPE_PROFILES)].reduce(
      (max, p) => Math.max(max, p.life[1] * 1000),
      0,
    );
    expect(maxParticleLifetimeMs()).toBeGreaterThan(lifeOnly);
  });

  it('keeps the generic profile exactly as it shipped', () => {
    // The 16 categories without a hero archetype must not change at all.
    expect(GENERIC_PROFILE.count).toBe(BURST_PARTICLES);
    expect(GENERIC_PROFILE.gravity).toBe(GRAVITY);
    expect(GENERIC_PROFILE.life[1]).toBe(MAX_LIFE_S);
    expect(GENERIC_PROFILE.emissionSpanMs).toBe(0);
    expect(GENERIC_PROFILE.direction).toEqual({ normal: 1, down: 0 });
    // The two archetype-only shapes stay out of the uncategorised look.
    expect(GENERIC_PROFILE.shapes).not.toContain('heart');
    expect(GENERIC_PROFILE.shapes).not.toContain('disc');
  });
});

describe('no archetype is an order of magnitude fainter than the rest', () => {
  /**
   * What a particle actually puts on screen is roughly its band's lightness
   * times its texture's peak alpha. Poison shipped at 0.44 × 0.35 = 0.154
   * against spark's 0.93 — a sixth — and the field verdict was that it simply
   * is not there, while every existing guard stayed green: they check hue
   * separation, position envelopes and DG6, none of which can see "too faint".
   *
   * HONEST LIMITS: this is a proxy. It ignores blend mode, overlap build-up,
   * particle count, motion, edge fade and whatever desktop shows through a
   * transparent overlay. It catches order-of-magnitude washouts, which is the
   * failure that reached the user; it does not certify that anything looks
   * good. The eye pass remains the judge, and any of these numbers may be
   * revised in the same package that changes them.
   */
  const FAINTEST_ALLOWED_RATIO = 0.3;

  const effectivePeak = (tag: string): number => {
    const profile = resolveFxProfile(tag);
    const band = PARTICLE_BANDS[FX_ARCHETYPES.get(tag)!.archetype]!;
    const alpha = Math.max(...profile.shapes.map((shape) => SHAPE_PEAK_ALPHA[shape]));
    return band.L * alpha;
  };

  it('every hero archetype clears a fraction of the brightest one', () => {
    const peaks = new Map([...FX_ARCHETYPES.keys()].map((tag) => [tag, effectivePeak(tag)]));
    const brightest = Math.max(...peaks.values());
    for (const [tag, peak] of peaks) {
      expect(
        peak / brightest,
        `${tag} draws at ${peak.toFixed(3)} against ${brightest.toFixed(3)}`,
      ).toBeGreaterThanOrEqual(FAINTEST_ALLOWED_RATIO);
    }
  });

});
