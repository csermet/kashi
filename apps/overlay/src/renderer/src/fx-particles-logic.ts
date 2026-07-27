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
  /** Seconds lived and seconds allotted — alpha is derived from the ratio. */
  age: number;
  life: number;
  size: number;
  rotation: number;
  spin: number;
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
/** Nothing lives longer than this, so a stopped song cannot leave residue. */
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
  count: number = BURST_PARTICLES,
): Particle[] {
  const perimeter = 2 * (box.width + box.height);
  const particles: Particle[] = [];
  for (let i = 0; i < count; i++) {
    // Stratified: one particle per equal slice, jittered inside its slice.
    const t = ((i + random()) / count) * perimeter;
    const [x, y, nx, ny] = pointOnPerimeter(t, box);
    const speed = 22 + random() * 48;
    // Tangent = normal rotated 90°, so the jitter slides along the edge.
    const drift = (random() - 0.5) * 30;
    particles.push({
      x,
      y,
      vx: nx * speed + -ny * drift,
      vy: ny * speed + nx * drift,
      age: 0,
      life: 0.6 + random() * (MAX_LIFE_S - 0.6),
      size: 5 + random() * 9,
      rotation: random() * Math.PI * 2,
      spin: (random() - 0.5) * 5,
    });
  }
  return particles;
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
  p.vy += GRAVITY * dt;
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
 */
export function insideBox(x: number, y: number, box: Rect): boolean {
  return x >= box.x && x <= box.x + box.width && y >= box.y && y <= box.y + box.height;
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
