/**
 * Every decision the particle layer makes, with no WebGL and no DOM in sight
 * (Faz 6.7 P5). The adapter around Pixi (`fx-canvas.ts`) owns objects and
 * frames; this file owns rules, so the rules can be tested and argued about.
 *
 * Two constraints shaped all of it. The spike measured that particle COUNT is
 * essentially free at this scale — both machines sat at their panel's refresh
 * with 300 of them — so the budget here is not "how many" but "how long is
 * anything moving at all": a laptop battery pays for a full-screen composite
 * that never idles, not for the arithmetic. And Caner's boundary rule: the
 * effect must stay inside its corner of the screen without ever drawing a
 * line to say so.
 */

import type { ParticleShape } from './fx-textures.js';

/** Where the box lives inside the window; particles must not cover it (DG6). */
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  /**
   * Seconds lived and seconds allotted — alpha is derived from the ratio.
   * `age` starts NEGATIVE for a staggered emission: the particle exists in the
   * list (so the ticker keeps running for it) but has not been born yet.
   */
  age: number;
  life: number;
  size: number;
  rotation: number;
  spin: number;
  /** px/s². Per particle, because archetypes disagree about which way is down. */
  gravity: number;
  /** Which texture to draw. The adapter maps this to a Pixi texture. */
  shape: ParticleShape;
}

/**
 * Gravity in px/s². Deliberately weak: the emission surrounds the box, and
 * anything stronger drags the whole ring into the bottom margin within a
 * frame or two — the shape is the point, and the margin below the box is
 * only 120px deep.
 */
export const GRAVITY = 40;
/** Drag per second — motion settles instead of sliding forever. */
export const DRAG = 0.86;
/**
 * How far from the window edge particles start fading. Nothing draws a
 * boundary; the boundary is simply where things have already faded to
 * nothing, so the effect reads as "it lives here" rather than "it is clipped".
 */
export const EDGE_FADE_PX = 96;
/**
 * One activation's worth, spread over the whole outline. Higher than the
 * point-burst design needed: the same count spread around ~1500px of edge
 * reads as sparse, and the spike measured that count is nearly free.
 */
export const BURST_PARTICLES = 72;
/**
 * The GENERIC profile's lifetime cap. It used to be the whole story ("nothing
 * outlives this, so a stopped song leaves no residue"); archetypes broke that
 * — poison and love deliberately linger, and with their emission stagger the
 * real worst case is `maxParticleLifetimeMs()`, about 3 s.
 */
export const MAX_LIFE_S = 1.4;

/**
 * Deterministic per-burst jitter. Math.random would make the tests describe
 * nothing; a seeded sequence keeps them honest and costs the same.
 */
export function makeRandom(seed: number): () => number {
  let state = seed >>> 0 || 1;
  return () => {
    // xorshift32 — small, fast, good enough for spark directions.
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return ((state >>> 0) % 100_000) / 100_000;
  };
}

export type EdgeName = 'top' | 'right' | 'bottom' | 'left';

export const ALL_EDGES: readonly EdgeName[] = ['top', 'right', 'bottom', 'left'];

/**
 * What a category's particles DO. Faz 6.7 shipped the engine with one
 * behaviour for all 24 tags; Caner's note was that the categories should not
 * all look the same ("poison should spill over the edges and drip"). The
 * engine did not need replacing — it needed the numbers taken out of it.
 *
 * `direction` is a mix, not a mode: `normal` is how much of the launch follows
 * the edge's outward normal, `down` is how much follows gravity's axis. Poison
 * leaves the edge and then sinks (0.55 / 0.5); love leaves and rises
 * (0.3 / -0.8). One formula covers every archetype we wanted.
 */
export interface EmissionProfile {
  /** Which edges of the box emit. Fewer edges = a direction you can read. */
  edges: readonly EdgeName[];
  direction: { normal: number; down: number };
  speed: readonly [min: number, max: number];
  /** Sideways jitter along the edge, px/s. */
  drift: number;
  gravity: number;
  life: readonly [min: number, max: number];
  count: number;
  size: readonly [min: number, max: number];
  spin: number;
  /**
   * Spreads births over this many ms instead of all at once. A puff that
   * appears whole is an explosion; one that keeps arriving is smoke.
   */
  emissionSpanMs: number;
  shapes: readonly ParticleShape[];
}

/**
 * The behaviour every tag had before archetypes, kept exact: the same random
 * draws in the same order, so the existing bursts are unchanged and the 16
 * categories without a hero archetype still look like themselves.
 */
export const GENERIC_PROFILE: EmissionProfile = {
  edges: ALL_EDGES,
  direction: { normal: 1, down: 0 },
  speed: [22, 70],
  drift: 30,
  gravity: GRAVITY,
  life: [0.6, MAX_LIFE_S],
  count: BURST_PARTICLES,
  size: [5, 14],
  spin: 5,
  emissionSpanMs: 0,
  // The four shapes that shipped in 0.16.x, deliberately NOT the two added for
  // archetypes: the 16 categories without a hero archetype must keep looking
  // exactly as they do today. Hearts belong to love, not to "uncategorised".
  shapes: ['spark', 'droplet', 'star', 'smoke'],
};

export type ArchetypeName = 'burst' | 'spark' | 'fall' | 'smoke' | 'twinkle' | 'drift';

/**
 * The six hero behaviours. Numbers here are a starting point for Caner's eye
 * pass, not a result — an archetype he dislikes gets revised or deleted in
 * this same package (the nightcore prototype set the precedent).
 */
export const ARCHETYPE_PROFILES: Record<ArchetypeName, EmissionProfile> = {
  /** explosion, fire — the whole outline throws itself outward, fast. */
  burst: {
    edges: ALL_EDGES,
    direction: { normal: 1, down: 0.1 },
    speed: [70, 150],
    drift: 45,
    gravity: 60,
    life: [0.45, 0.95],
    count: 88,
    size: [6, 17],
    spin: 6,
    emissionSpanMs: 0,
    shapes: ['spark', 'smoke'],
  },
  /** electric — small, very fast, gone almost immediately. */
  spark: {
    edges: ALL_EDGES,
    direction: { normal: 1, down: 0 },
    speed: [130, 260],
    drift: 95,
    gravity: 15,
    life: [0.22, 0.5],
    count: 74,
    size: [3, 9],
    spin: 14,
    emissionSpanMs: 60,
    shapes: ['spark'],
  },
  /**
   * money, water — pours down the SIDES.
   *
   * The obvious reading (emit from the top edge, fall past the box) is the one
   * that cannot work: the box is 180px tall and particles inside it are hidden
   * by the DG6 mask, so a top-edge fall spends most of its life invisible and
   * reads as motes blinking out. Leaving from the left and right edges keeps
   * the whole descent on screen.
   */
  fall: {
    edges: ['left', 'right'],
    direction: { normal: 0.45, down: 1 },
    speed: [35, 85],
    drift: 12,
    gravity: 240,
    life: [0.9, 1.7],
    count: 44,
    size: [7, 16],
    spin: 3,
    emissionSpanMs: 280,
    shapes: ['droplet'],
  },
  /**
   * poison — spills over the edges and sinks. Caner's own example.
   *
   * Sides and floor only: a top-edge puff barely rises (0.5 down cancels most
   * of 0.55 normal) and then sinks through the whole box, so it is hidden for
   * most of its life and pops back into view underneath — the "motes blinking
   * out" artifact. Spilling over the sides and dripping off the bottom is
   * also the closer reading of what was asked for.
   */
  smoke: {
    edges: ['left', 'right', 'bottom'],
    direction: { normal: 0.55, down: 0.5 },
    speed: [16, 44],
    drift: 18,
    gravity: 120,
    life: [1.2, 2.3],
    count: 52,
    size: [15, 32],
    spin: 1.2,
    emissionSpanMs: 420,
    shapes: ['smoke'],
  },
  /**
   * shine — a scatter of glints that arrive over half a second.
   *
   * Slow, but decidedly outward and with NO gravity: at 0.28 normal against
   * gravity 6 the top-edge glints netted about 4px of travel, which left them
   * sitting on the mask boundary flickering in and out. They need only a
   * little authority, as long as it never brings them back.
   */
  twinkle: {
    edges: ALL_EDGES,
    direction: { normal: 0.6, down: 0 },
    speed: [10, 32],
    drift: 10,
    gravity: 0,
    life: [0.7, 1.5],
    count: 58,
    size: [4, 12],
    spin: 2,
    emissionSpanMs: 620,
    shapes: ['star'],
  },
  /**
   * love — a few hearts rise past the box.
   *
   * NOT from the bottom edge, however natural that reads: rising from below
   * means rising THROUGH the box, and the DG6 mask hides every one of those
   * frames. Measured, a heart climbs ~110px in its longest life against a
   * 180px box, so it would live and die without ever being drawn. The sides
   * and the top are the edges where "up" is also "visible".
   */
  drift: {
    edges: ['left', 'right', 'top'],
    direction: { normal: 0.3, down: -0.8 },
    speed: [24, 52],
    drift: 14,
    gravity: -20,
    life: [1.4, 2.5],
    count: 24,
    size: [12, 24],
    spin: 1,
    emissionSpanMs: 520,
    shapes: ['heart'],
  },
};

/**
 * The longest a particle can still be on screen after its burst started —
 * emission stagger plus lifetime, over every profile. The old flat MAX_LIFE_S
 * no longer answers this on its own, and the "a stopped song leaves no
 * residue" contract is stated in terms of this number.
 */
export function maxParticleLifetimeMs(): number {
  const profiles = [GENERIC_PROFILE, ...Object.values(ARCHETYPE_PROFILES)];
  return Math.max(...profiles.map((p) => p.emissionSpanMs + p.life[1] * 1000));
}

/**
 * One activation's worth of particles, emitted from the ENTIRE box edge.
 *
 * The first design fired them from the triggering word, pushed out to the
 * nearest edge. In the field that read as a puff appearing in one arbitrary
 * spot, often far from the word that caused it — Caner's verdict was that a
 * single small origin looks worse than no effect at all. The box radiating
 * along its whole outline says the same thing without picking a corner: it
 * belongs to the line, not to one point on it.
 *
 * Emission points are STRATIFIED around the perimeter rather than random, so
 * every burst is evenly distributed instead of clumping by luck. Velocity is
 * the outward normal plus a little sideways jitter, and slow enough that the
 * particles live in the margin around the box instead of flying off it.
 */
export function planEmission(
  box: Rect,
  random: () => number,
  profile: EmissionProfile = GENERIC_PROFILE,
): Particle[] {
  const spans = edgeSpans(box, profile.edges);
  const emitting = spans.reduce((sum, span) => sum + span.length, 0);
  const particles: Particle[] = [];
  if (emitting <= 0) return particles;

  const { count, direction, drift: driftScale, shapes } = profile;
  const [speedMin, speedMax] = profile.speed;
  const [lifeMin, lifeMax] = profile.life;
  const [sizeMin, sizeMax] = profile.size;

  for (let i = 0; i < count; i++) {
    // Stratified: one particle per equal slice, jittered inside its slice.
    // Slices are cut over the EMITTING edges only, so a two-edge archetype is
    // as evenly spread along those two as the generic one is around all four.
    const t = ((i + random()) / count) * emitting;
    const [x, y, nx, ny] = pointOnPerimeter(distanceOnBox(t, spans), box);
    const speed = speedMin + random() * (speedMax - speedMin);
    // Tangent = normal rotated 90°, so the jitter slides along the edge.
    const drift = (random() - 0.5) * driftScale;
    particles.push({
      x,
      y,
      vx: nx * speed * direction.normal + -ny * drift,
      vy: ny * speed * direction.normal + nx * drift + speed * direction.down,
      // Negative age = staggered birth; see Particle.age.
      age: profile.emissionSpanMs > 0 ? -(random() * profile.emissionSpanMs) / 1000 : 0,
      life: lifeMin + random() * (lifeMax - lifeMin),
      size: sizeMin + random() * (sizeMax - sizeMin),
      rotation: random() * Math.PI * 2,
      spin: (random() - 0.5) * profile.spin,
      gravity: profile.gravity,
      // Assigned in a SECOND pass below, not here.
      shape: shapes[0]!,
    });
  }
  // The shape draw has to come after ALL the particle draws: that is the
  // order 0.16.x used (planEmission ran to completion, then the adapter drew
  // one shape per particle). Interleaving it shifts every subsequent random
  // value, which silently rescatters the 16 categories that are supposed to
  // look untouched — 71 of 72 particles moved, with every formula intact.
  for (const particle of particles) {
    particle.shape = shapes[Math.floor(random() * shapes.length) % shapes.length] ?? shapes[0]!;
  }
  return particles;
}

interface EdgeSpan {
  /** Where this edge starts along the full outline walk. */
  offset: number;
  length: number;
}

/** The emitting edges as spans of the full top→right→bottom→left walk. */
function edgeSpans(box: Rect, edges: readonly EdgeName[]): EdgeSpan[] {
  const { width: w, height: h } = box;
  const geometry: Record<EdgeName, EdgeSpan> = {
    top: { offset: 0, length: w },
    right: { offset: w, length: h },
    bottom: { offset: w + h, length: w },
    left: { offset: 2 * w + h, length: h },
  };
  return edges.map((edge) => geometry[edge]).filter((span) => span.length > 0);
}

/** Maps a distance along the emitting edges to one along the whole outline. */
function distanceOnBox(t: number, spans: EdgeSpan[]): number {
  let remaining = t;
  for (const span of spans) {
    if (remaining < span.length) return span.offset + remaining;
    remaining -= span.length;
  }
  const last = spans[spans.length - 1]!;
  return last.offset + last.length;
}

/**
 * Maps a distance along the box outline to a point and its OUTWARD normal,
 * walking top → right → bottom → left.
 */
export function pointOnPerimeter(
  distance: number,
  box: Rect,
): [x: number, y: number, nx: number, ny: number] {
  const { x, y, width: w, height: h } = box;
  const perimeter = 2 * (w + h);
  // Wrap so callers never have to think about it.
  let t = distance % perimeter;
  if (t < 0) t += perimeter;
  if (t < w) return [x + t, y, 0, -1];
  t -= w;
  if (t < h) return [x + w, y + t, 1, 0];
  t -= h;
  if (t < w) return [x + w - t, y + h, 0, 1];
  t -= w;
  return [x, y + h - t, -1, 0];
}

/** Advances one particle by `dt` seconds. Returns false once it is spent. */
export function stepParticle(p: Particle, dt: number): boolean {
  p.age += dt;
  if (p.age >= p.life) return false;
  // Staggered emission: it is in the list, keeping the ticker alive, but it
  // has not been born yet, so it neither moves nor draws (alpha clamps to 0).
  if (p.age < 0) return true;
  p.vy += p.gravity * dt;
  const drag = Math.pow(DRAG, dt);
  p.vx *= drag;
  p.vy *= drag;
  p.x += p.vx * dt;
  p.y += p.vy * dt;
  p.rotation += p.spin * dt;
  return true;
}

/**
 * Alpha from age and distance to the window edge — the two ways a particle
 * disappears. Both are smooth: a particle that reached the boundary has
 * already faded out, so the boundary itself is never visible.
 */
export function particleAlpha(
  p: Particle,
  windowWidth: number,
  windowHeight: number,
  fadePx: number = EDGE_FADE_PX,
): number {
  const lifeLeft = 1 - p.age / p.life;
  if (lifeLeft <= 0) return 0;
  // Not born yet (staggered emission) — present in the list, drawing nothing.
  if (p.age < 0) return 0;
  // Fade in briefly too: particles popping into existence at full opacity
  // read as a glitch rather than a spark.
  const fadeIn = Math.min(1, p.age / 0.08);
  const edge = Math.min(p.x, p.y, windowWidth - p.x, windowHeight - p.y);
  const edgeFade = fadePx <= 0 ? 1 : Math.max(0, Math.min(1, edge / fadePx));
  return Math.max(0, Math.min(1, lifeLeft * fadeIn * edgeFade));
}

/**
 * True when the point sits inside the lyric box (DG6): the text is never
 * competed with. The mask is a plain rect test — the box is a rect, and the
 * cheapest correct answer is the right one on a path that runs per particle.
 *
 * `inflate` accounts for the SPRITE rather than its centre. That did not
 * matter while the largest particle was 14px, but smoke is 32px: testing the
 * centre alone let half a puff bleed over the lyrics, which the z-order hides
 * only as long as the box background is opaque — and its alpha is the user's
 * to set.
 */
export function insideBox(x: number, y: number, box: Rect, inflate = 0): boolean {
  return (
    x >= box.x - inflate &&
    x <= box.x + box.width + inflate &&
    y >= box.y - inflate &&
    y <= box.y + box.height + inflate
  );
}

/**
 * Reads a `--fx-tint-<tag>` custom property into the 0xRRGGBB integer Pixi
 * tints with. White on anything unparseable — a particle in the wrong colour
 * is a smaller loss than no particle, and white reads as "uncategorised"
 * rather than as a bug.
 *
 * It lives here rather than beside the DOM because it is the seam where a
 * silent failure would hide: if the tint pipeline ever stopped emitting hex,
 * every particle would quietly turn white and nothing would break loudly.
 */
export function parseTintColor(value: string | undefined): number {
  const hex = value?.match(/#([0-9a-f]{6})/i)?.[1];
  return hex ? Number.parseInt(hex, 16) : 0xffffff;
}

/**
 * The layer may exist only where it cannot change what the other levels look
 * like. off/simple/full are pixel-identical to their pre-hype selves by
 * contract, and reduced-motion means the user asked for none of this.
 */
export function shouldMountLayer(effectLevel: string, prefersReducedMotion: boolean): boolean {
  return effectLevel === 'hype' && !prefersReducedMotion;
}
