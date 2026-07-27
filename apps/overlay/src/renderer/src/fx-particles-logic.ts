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

/** Gravity in px/s², gentle: these are sparks, not falling rocks. */
export const GRAVITY = 220;
/** Drag per second — motion settles instead of sliding forever. */
export const DRAG = 0.86;
/**
 * How far from the window edge particles start fading. Nothing draws a
 * boundary; the boundary is simply where things have already faded to
 * nothing, so the effect reads as "it lives here" rather than "it is clipped".
 */
export const EDGE_FADE_PX = 96;
/** One activation's worth. The cap is per-burst, not per-frame — see above. */
export const BURST_PARTICLES = 36;
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
 * A burst at (originX, originY), aimed AWAY from the box.
 *
 * Aiming matters: a symmetric explosion sends half its particles across the
 * lyrics, which DG6 forbids and the mask would eat anyway — visibly, as
 * particles vanishing mid-flight. Biasing the spread outward means the ones
 * that survive are the ones that were always going to be seen.
 */
export function planBurst(
  originX: number,
  originY: number,
  box: Rect,
  random: () => number,
  count: number = BURST_PARTICLES,
): Particle[] {
  const boxCenterX = box.x + box.width / 2;
  const boxCenterY = box.y + box.height / 2;
  // Outward = away from the box centre. Degenerate case (origin exactly at
  // the centre) falls back to straight up, which is never wrong.
  let awayX = originX - boxCenterX;
  let awayY = originY - boxCenterY;
  const length = Math.hypot(awayX, awayY);
  if (length < 1) {
    awayX = 0;
    awayY = -1;
  } else {
    awayX /= length;
    awayY /= length;
  }
  const baseAngle = Math.atan2(awayY, awayX);

  const particles: Particle[] = [];
  for (let i = 0; i < count; i++) {
    // ±60° around the outward direction: wide enough to look like a burst,
    // narrow enough that nothing is launched into the box.
    const angle = baseAngle + (random() - 0.5) * (Math.PI * 2) * (1 / 3);
    const speed = 90 + random() * 210;
    particles.push({
      x: originX,
      y: originY,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      age: 0,
      life: 0.6 + random() * (MAX_LIFE_S - 0.6),
      size: 6 + random() * 10,
      rotation: random() * Math.PI * 2,
      spin: (random() - 0.5) * 6,
    });
  }
  return particles;
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
 * The layer may exist only where it cannot change what the other levels look
 * like. off/simple/full are pixel-identical to their pre-hype selves by
 * contract, and reduced-motion means the user asked for none of this.
 */
export function shouldMountLayer(effectLevel: string, prefersReducedMotion: boolean): boolean {
  return effectLevel === 'hype' && !prefersReducedMotion;
}
