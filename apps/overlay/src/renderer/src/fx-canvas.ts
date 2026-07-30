/**
 * The Pixi adapter (Faz 6.7 P5): objects, frames and GPU state. Every rule it
 * follows lives in fx-particles-logic.ts, so this file stays small enough to
 * read and the rules stay testable without a GPU.
 *
 * Three contracts it exists to keep:
 *
 * 1. It must not exist outside hype. off/simple/full are pixel-identical to
 *    their pre-hype selves by contract, so the canvas is created on the first
 *    hype activation and destroyed the moment the level drops. No element, no
 *    influence.
 * 2. It must go completely idle. The spike found the constraint is not
 *    framerate but battery: a full-screen composite that never stops costs a
 *    laptop even when it draws nothing. So the ticker stops and the canvas is
 *    hidden the instant the last particle dies.
 * 3. It must never cover the lyrics (DG6). The canvas sits UNDER the box in
 *    z-order and particles inside the box rect are skipped — two independent
 *    guarantees, because one of them being wrong should not put anything on
 *    top of the text.
 */
import type { Application, Container, Sprite, Texture } from 'pixi.js';
import {
  GENERIC_PROFILE,
  insideBox,
  makeRandom,
  maxParticleLifetimeMs,
  particleAlpha,
  particleSize,
  planEmission,
  stepParticle,
  type EmissionProfile,
  type Particle,
  type Rect,
} from './fx-particles-logic.js';
import { drawParticleCanvas, PARTICLE_SHAPES, type ParticleShape } from './fx-textures.js';

/**
 * Build-time switch (electron.vite.config.ts): `KASHI_FX_DEBUG=1 pnpm dev`
 * outlines the protected box and the window bounds. A normal build folds the
 * constant to `false`, so the call becomes `if (false)` and the outline can
 * never be drawn in a shipped app. The function body still sits in the bundle
 * — electron-vite does not minify this output — but it is unreachable, which
 * is the property that matters.
 */
declare const __KASHI_FX_DEBUG__: boolean;
const DEBUG = typeof __KASHI_FX_DEBUG__ !== 'undefined' && __KASHI_FX_DEBUG__;


/**
 * Dev-only: outlines the protected box and the window bounds. Module scope
 * rather than a method so the class stays about particles; the single call
 * site is behind a constant the build folds to false.
 */
function drawDebugBounds(
  app: Application,
  pixi: typeof import('pixi.js'),
  box: Rect,
  log: (line: string) => void,
): void {
  const g = new pixi.Graphics();
  g.rect(box.x, box.y, box.width, box.height).stroke({ width: 1, color: 0xff0000, alpha: 0.5 });
  g.rect(0, 0, app.renderer.width, app.renderer.height).stroke({
    width: 1,
    color: 0x00ff00,
    alpha: 0.4,
  });
  app.stage.addChild(g);
  log('fx layer debug bounds drawn (KASHI_FX_DEBUG)');
}

/** Give up after this many setup failures (the error will not change). */
const MAX_SETUP_FAILURES = 2;

/**
 * Ceiling on live sprites across ALL bursts. The spike measured that count is
 * nearly free at this scale (300 sprites held the panel's refresh rate), so
 * this is a runaway guard, not a performance budget — the longest archetypes
 * now outlive their own burst interval, which the old flat lifetime made
 * impossible.
 */
const MAX_LIVE_PARTICLES = 400;

/**
 * Longest step a single frame may advance the simulation. Waking from sleep,
 * or a stalled compositor, hands the ticker a delta measured in seconds; at
 * 240px/s that is a particle jumping across the screen instead of moving.
 * Slow motion for one frame is invisible, teleporting is not.
 */
const MAX_FRAME_S = 0.1;

/**
 * Frames after which a burst is definitely over. Derived from the longest
 * profile (stagger + lifetime) with generous slack, so it can only fire when
 * something is genuinely wrong — and counted in frames, not seconds, so the
 * guard survives exactly the bad deltas it guards against.
 */
const MAX_BURST_FRAMES = Math.ceil((maxParticleLifetimeMs() / 1000) * 240) * 3;

interface Live {
  particle: Particle;
  sprite: Sprite;
}

export class FxCanvas {
  private app: Application | null = null;
  private layer: Container | null = null;
  private pixi: typeof import('pixi.js') | null = null;
  private textures = new Map<ParticleShape, Texture>();
  private live: Live[] = [];
  private seed = 1;
  private starting = false;
  /** Set by destroy(): an in-flight init must not resurrect a dead layer. */
  private disposed = false;
  private loggedFirstBurst = false;
  private loggedBudget = false;
  /** Frames since the last burst — drives the "nothing lives forever" backstop. */
  private framesSinceBurst = 0;
  private failures = 0;

  /**
   * `fallbackBox` is the RESERVED zone — the fixed rect the window layout
   * keeps free for the lyric box. The box itself shrinks to its content (a
   * one-line lyric is far shorter than three), so emitting from the zone left
   * a visible empty band between the text and its own effect. Each burst
   * passes the box's REAL rect; the zone is only the answer before anything
   * has measured it.
   */
  private box: Rect;

  constructor(
    fallbackBox: Rect,
    private readonly log: (line: string) => void = () => {},
  ) {
    this.box = fallbackBox;
  }

  /**
   * Updates the rect used before a burst has measured the real box. Only the
   * fallback: every burst still passes the box's actual rect, so this matters
   * for the very first one after a box-size change.
   */
  setFallbackBox(box: Rect): void {
    this.box = box;
  }

  /** True while anything is animating — the battery contract's test hook. */
  isIdle(): boolean {
    return this.live.length === 0 && this.app?.ticker.started !== true;
  }

  /** Tears everything down for good: level dropped, reduced motion, shutdown. */
  destroy(): void {
    this.disposed = true;
    this.teardown();
  }

  /**
   * Drops the GPU-backed half. Everything here — textures, sprites, the
   * renderer — belongs to one WebGL context, so when that context goes the
   * whole set goes with it and the next burst rebuilds from scratch.
   */
  private teardown(): void {
    this.live = [];
    // A rebuild after context loss is a fresh layer: its diagnostics must be
    // able to speak again, or the one-shot budget line stays suppressed for
    // the rest of the session on the very path that exists to be noticed.
    this.loggedBudget = false;
    this.loggedFirstBurst = false;
    const app = this.app;
    this.app = null;
    this.layer = null;
    this.textures.clear();
    this.pixi = null;
    if (app) {
      app.canvas.remove();
      app.destroy(true, { children: true, texture: true });
    }
  }

  /**
   * Fires one burst at a window-space point. Creates the layer on first use —
   * an overlay that never reaches hype never pays for Pixi at all.
   */
  async burst(colour: number, box?: Rect, profile: EmissionProfile = GENERIC_PROFILE): Promise<void> {
    if (this.disposed) return;
    // One layout read on an edge that fires at most once per line — the same
    // budget the old word-rect read used.
    if (box) this.box = box;
    const app = await this.ensureApp();
    if (!app || !this.layer || this.disposed) return;

    // Archetypes made bursts longer-lived (poison lingers over two seconds),
    // so overlapping ones can now stack in a way the single 1.4s behaviour
    // never could. The cap trims the new burst rather than the running ones,
    // and says so once — a silently thinned effect is the harder bug.
    const room = Math.max(0, MAX_LIVE_PARTICLES - this.live.length);
    const random = makeRandom((this.seed = (this.seed * 1_664_525 + 1_013_904_223) >>> 0));
    const planned = planEmission(this.box, random, profile);
    if (room < planned.length && !this.loggedBudget) {
      // Once per layer: a thinned burst and a skipped one are the same class
      // of problem, and a quietly thinned effect is the harder one to notice.
      this.loggedBudget = true;
      this.log(
        `fx layer: particle budget ${MAX_LIVE_PARTICLES} reached,` +
          ` emitting ${room} of ${planned.length}`,
      );
    }
    if (room === 0) return;
    this.framesSinceBurst = 0;
    for (const particle of planned.slice(0, room)) {
      const texture = this.textures.get(particle.shape);
      if (!texture || !this.pixi) continue;
      const sprite = new this.pixi.Sprite(texture);
      sprite.anchor.set(0.5);
      sprite.tint = colour;
      sprite.alpha = 0;
      sprite.width = particle.size;
      sprite.height = particle.size;
      // Additive compositing where the category is LIGHT (fire, electricity).
      // One GPU state flag per sprite — no shader codegen, so it stays inside
      // the strict CSP the layer already runs under.
      if (profile.blend === 'add') sprite.blendMode = 'add';
      this.layer.addChild(sprite);
      this.live.push({ particle, sprite });
    }
    if (this.live.length > 0 && !app.ticker.started) {
      app.canvas.style.visibility = 'visible';
      app.ticker.start();
    }
    if (!this.loggedFirstBurst) {
      this.loggedFirstBurst = true;
      this.log(
        `fx layer: first emission -> ${this.live.length} sprite(s),` +
          ` canvas ${app.canvas.isConnected ? 'in DOM' : 'DETACHED'}`,
      );
    }
  }

  private async ensureApp(): Promise<Application | null> {
    if (this.app) return this.app;
    if (this.starting || this.disposed) return null; // a second burst during init just misses
    // Setup failed repeatedly: stop rebuilding a renderer this environment
    // will not give us. One retry covers a transient; a wall of identical
    // errors helps nobody and costs a module init per fx word.
    if (this.failures >= MAX_SETUP_FAILURES) return null;
    this.starting = true;
    try {
      // Loaded HERE, not at module scope: Pixi is over a megabyte, and an
      // overlay that never reaches hype should never parse a byte of it.
      // Pixi generates its shader/uniform sync code with `new Function()`,
      // which our CSP forbids and should keep forbidding (R-7). The
      // `unsafe-eval` entry point is named for the thing it AVOIDS: importing
      // it swaps that codegen for polyfills, so the layer runs under the
      // strict policy instead of asking for an exception.
      const [pixi] = await Promise.all([import('pixi.js'), import('pixi.js/unsafe-eval')]);
      this.pixi = pixi;
      this.log('fx layer: pixi module loaded (CSP-safe codegen installed)');
      const app = new pixi.Application();
      await app.init({
        backgroundAlpha: 0,
        antialias: true,
        resizeTo: window,
        autoStart: false, // idle until something actually moves
        // Without these the backing store stays at CSS pixels and the sprites
        // render soft beside crisp DOM text — very visible on a Retina panel
        // or Windows display scaling.
        resolution: window.devicePixelRatio || 1,
        autoDensity: true,
      });
      // Two awaits happened. The user can have left hype in that time, and
      // an orphaned canvas at simple level would break pixel identity — the
      // one contract this layer must never cost anything.
      if (this.disposed) {
        app.destroy(true, { children: true, texture: true });
        return null;
      }
      app.canvas.id = 'fx-canvas';
      // Under the box in z-order: even if the mask were wrong, the lyrics
      // would still be drawn on top of the particles.
      Object.assign(app.canvas.style, {
        position: 'fixed',
        inset: '0',
        zIndex: '0',
        pointerEvents: 'none',
        visibility: 'hidden',
      });
      document.body.prepend(app.canvas);

      // A lost context invalidates every texture and sprite we hold. Rather
      // than nurse a half-dead renderer back to life, drop the whole layer:
      // the next fx word rebuilds it, textures included, through the same
      // path that built it the first time. preventDefault() is what makes the
      // browser willing to hand the context back at all.
      app.canvas.addEventListener('webglcontextlost', (event) => {
        event.preventDefault();
        this.log('fx layer: WebGL context lost — dropping the layer, next burst rebuilds');
        this.teardown();
      });

      for (const shape of PARTICLE_SHAPES) {
        const canvas = drawParticleCanvas(shape);
        if (canvas) this.textures.set(shape, pixi.Texture.from(canvas));
      }
      // Every step of this path used to fail silently, which is how a layer
      // that never appears looks exactly like a layer with nothing to do.
      this.log(
        `fx layer ready: renderer=${app.renderer.type} ${app.renderer.width}x${app.renderer.height}` +
          ` textures=${this.textures.size}/${PARTICLE_SHAPES.length}`,
      );
      const layer = new pixi.Container();
      app.stage.addChild(layer);
      app.ticker.add(({ deltaMS }) => this.frame(deltaMS / 1000));
      if (DEBUG) drawDebugBounds(app, pixi, this.box, this.log);

      this.app = app;
      this.layer = layer;
      return app;
    } catch (err) {
      // No WebGL, a lost context at startup, anything: the overlay is duller
      // and still works. Never let decoration take the app down.
      this.failures++;
      this.log(
        `fx layer unavailable (${String(err).slice(0, 140)})` +
          (this.failures >= MAX_SETUP_FAILURES ? ' — giving up for this session' : ''),
      );
      return null;
    } finally {
      this.starting = false;
    }
  }

  private frame(rawDt: number): void {
    const app = this.app;
    if (!app) return;

    // A frame delta is not to be trusted blindly. Waking from sleep hands the
    // ticker a gap measured in seconds, which would teleport every live
    // particle somewhere arbitrary in one step; a non-finite one would poison
    // `age` permanently, and since death is `age >= life`, a poisoned particle
    // never dies and the ticker never stops — the battery contract broken in
    // a way nothing would report.
    const dt = Number.isFinite(rawDt) ? Math.min(Math.max(rawDt, 0), MAX_FRAME_S) : 0;

    // Backstop for anything the clamp cannot foresee: no burst can still be
    // running this long after it started. Counted in FRAMES rather than
    // elapsed time so a bad delta cannot disable the guard that exists to
    // survive bad deltas.
    this.framesSinceBurst += 1;
    if (this.live.length > 0 && this.framesSinceBurst > MAX_BURST_FRAMES) {
      this.log(
        `fx layer: particles outlived their bound (${this.live.length} still alive` +
          ` after ${this.framesSinceBurst} frames) — clearing`,
      );
      for (const entry of this.live) entry.sprite.destroy();
      this.live = [];
    }

    const width = app.renderer.width;
    const height = app.renderer.height;
    const survivors: Live[] = [];
    for (const entry of this.live) {
      if (!stepParticle(entry.particle, dt)) {
        entry.sprite.destroy();
        continue;
      }
      const { x, y } = entry.particle;
      // DG6: the box belongs to the text. Skipped, not clipped — a particle
      // crossing it simply is not drawn for those frames.
      // The DRAWN size, not the birth size: smoke grows to over twice its
      // original width, and measuring the mask against birth let a swollen
      // puff bleed over the lyrics. The adapter test caught this the moment
      // growth existed.
      const drawn = particleSize(entry.particle);
      entry.sprite.visible = !insideBox(x, y, this.box, drawn / 2);
      entry.sprite.position.set(x, y);
      entry.sprite.rotation = entry.particle.rotation;
      entry.sprite.width = drawn;
      entry.sprite.height = drawn;
      entry.sprite.alpha = particleAlpha(entry.particle, width, height);
      survivors.push(entry);
    }
    this.live = survivors;
    if (this.live.length === 0) {
      // One last frame clears the leftovers, THEN everything stops. Rendering
      // an empty scene at 120 Hz is exactly the battery cost the spike warned
      // about.
      app.render();
      app.ticker.stop();
      app.canvas.style.visibility = 'hidden';
    }
  }

}
