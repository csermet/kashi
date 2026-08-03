/**
 * Procedurally drawn particle textures (Faz 6.7 P5).
 *
 * Nothing is vendored. Caner's call, and it happens to be the right one
 * twice over: a spark drawn from a radial gradient weighs nothing in the
 * bundle and carries no licence, while every "free particle pack" worth
 * having is share-alike — which the commercial direction cannot take.
 *
 * All four shapes are drawn WHITE. Pixi tints a sprite for free, so one
 * white texture serves all 24 fx categories in their own colour; baking the
 * colour in would mean 24 uploads and a colour that cannot follow the album
 * palette.
 */

export type ParticleShape = 'spark' | 'droplet' | 'star' | 'smoke' | 'heart' | 'disc';

/**
 * Peak alpha each shape paints at its centre. Declared once and consumed by
 * the painters below, so a visibility guard can assert against the number that
 * is actually drawn rather than a copy that drifts away from it.
 */
export const SHAPE_PEAK_ALPHA: Record<ParticleShape, number> = {
  spark: 1,
  droplet: 0.92,
  star: 0.95,
  smoke: 0.55,
  heart: 0.9,
  disc: 0.95,
};

export const PARTICLE_SHAPES: readonly ParticleShape[] = [
  'spark',
  'droplet',
  'star',
  'smoke',
  'heart',
  'disc',
];

/** Texture edge in px. Small: these are scaled down, never up. */
const SIZE = 64;

function radialAlpha(
  ctx: CanvasRenderingContext2D,
  stops: Array<[number, number]>,
): CanvasGradient {
  const gradient = ctx.createRadialGradient(SIZE / 2, SIZE / 2, 0, SIZE / 2, SIZE / 2, SIZE / 2);
  for (const [offset, alpha] of stops) {
    gradient.addColorStop(offset, `rgba(255,255,255,${alpha})`);
  }
  return gradient;
}

function drawSpark(ctx: CanvasRenderingContext2D): void {
  // A hot core with a short tail: the classic ember read.
  ctx.fillStyle = radialAlpha(ctx, [
    [0, 1],
    [0.35, 0.75],
    [1, 0],
  ]);
  ctx.fillRect(0, 0, SIZE, SIZE);
}

function drawDroplet(ctx: CanvasRenderingContext2D): void {
  ctx.fillStyle = `rgba(255,255,255,${SHAPE_PEAK_ALPHA.droplet})`;
  ctx.beginPath();
  // Teardrop: a circle with a point pulled up out of it.
  ctx.moveTo(SIZE / 2, 6);
  ctx.bezierCurveTo(SIZE * 0.82, SIZE * 0.42, SIZE * 0.78, SIZE * 0.86, SIZE / 2, SIZE - 6);
  ctx.bezierCurveTo(SIZE * 0.22, SIZE * 0.86, SIZE * 0.18, SIZE * 0.42, SIZE / 2, 6);
  ctx.fill();
}

function drawStar(ctx: CanvasRenderingContext2D): void {
  // Four-point sparkle — two crossed lens flares, not a five-point cartoon.
  ctx.fillStyle = `rgba(255,255,255,${SHAPE_PEAK_ALPHA.star})`;
  const c = SIZE / 2;
  ctx.beginPath();
  for (let i = 0; i < 4; i++) {
    const angle = (Math.PI / 2) * i;
    const tipX = c + Math.cos(angle) * c;
    const tipY = c + Math.sin(angle) * c;
    const waistX = c + Math.cos(angle + Math.PI / 4) * c * 0.16;
    const waistY = c + Math.sin(angle + Math.PI / 4) * c * 0.16;
    if (i === 0) ctx.moveTo(tipX, tipY);
    else ctx.lineTo(tipX, tipY);
    ctx.lineTo(waistX, waistY);
  }
  ctx.closePath();
  ctx.fill();
}

function drawSmoke(ctx: CanvasRenderingContext2D): void {
  // Soft and hollow, so overlapping puffs build up instead of flat-filling.
  // The peak used to be 0.35, which multiplied against poison's dark band and
  // normal compositing put it an order of magnitude under every other
  // archetype — the field verdict was that poison simply is not there. Same
  // gradient shape, both stops scaled together: still the faintest texture by
  // a wide margin, which is the matte character, but present.
  ctx.fillStyle = radialAlpha(ctx, [
    [0, SHAPE_PEAK_ALPHA.smoke],
    [0.5, SHAPE_PEAK_ALPHA.smoke * 0.62],
    [1, 0],
  ]);
  ctx.fillRect(0, 0, SIZE, SIZE);
}

function drawHeart(ctx: CanvasRenderingContext2D): void {
  // Two lobes over a point. Drawn a touch soft-edged rather than as a crisp
  // emoji silhouette — at 12-22 px on a transparent overlay a hard outline
  // reads as clip-art, a slightly diffuse one reads as a mote of light.
  ctx.fillStyle = `rgba(255,255,255,${SHAPE_PEAK_ALPHA.heart})`;
  const c = SIZE / 2;
  ctx.beginPath();
  ctx.moveTo(c, SIZE * 0.86);
  ctx.bezierCurveTo(SIZE * 0.06, SIZE * 0.55, SIZE * 0.16, SIZE * 0.12, c, SIZE * 0.32);
  ctx.bezierCurveTo(SIZE * 0.84, SIZE * 0.12, SIZE * 0.94, SIZE * 0.55, c, SIZE * 0.86);
  ctx.fill();
}

function drawDisc(ctx: CanvasRenderingContext2D): void {
  // A filled round mote with a soft rim: the general-purpose "solid speck".
  // Coins read as this plus the tag's own colour, which the tint supplies.
  ctx.fillStyle = radialAlpha(ctx, [
    [0, 0.95],
    [0.62, 0.9],
    [0.86, 0.45],
    [1, 0],
  ]);
  ctx.fillRect(0, 0, SIZE, SIZE);
}

const PAINTERS: Record<ParticleShape, (ctx: CanvasRenderingContext2D) => void> = {
  spark: drawSpark,
  droplet: drawDroplet,
  star: drawStar,
  smoke: drawSmoke,
  heart: drawHeart,
  disc: drawDisc,
};

/**
 * Draws one shape onto a fresh canvas. Returns null when the 2D context is
 * unavailable — the caller then simply has no layer, which is a duller app,
 * never a broken one.
 */
export function drawParticleCanvas(shape: ParticleShape): HTMLCanvasElement | null {
  const canvas = document.createElement('canvas');
  canvas.width = SIZE;
  canvas.height = SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.clearRect(0, 0, SIZE, SIZE);
  PAINTERS[shape](ctx);
  return canvas;
}
