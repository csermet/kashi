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
  insideBox,
  makeRandom,
  particleAlpha,
  planBurst,
  stepParticle,
  type Particle,
  type Rect,
} from './fx-particles-logic.js';
import { drawParticleCanvas, PARTICLE_SHAPES, type ParticleShape } from './fx-textures.js';

/** Set KASHI_FX_DEBUG=1 to see the boundary the effect is confined to. */
const DEBUG = Boolean((globalThis as { KASHI_FX_DEBUG?: unknown }).KASHI_FX_DEBUG);

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

  constructor(
    private readonly box: Rect,
    private readonly log: (line: string) => void = () => {},
  ) {}

  /** True while anything is animating — the battery contract's test hook. */
  isIdle(): boolean {
    return this.live.length === 0 && this.app?.ticker.started !== true;
  }

  /** Tears everything down: level dropped, reduced motion, or shutdown. */
  destroy(): void {
    this.live = [];
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
  async burst(x: number, y: number, colour: number): Promise<void> {
    const app = await this.ensureApp();
    if (!app || !this.layer) return;

    const random = makeRandom((this.seed = (this.seed * 1_664_525 + 1_013_904_223) >>> 0));
    for (const particle of planBurst(x, y, this.box, random)) {
      const shape = PARTICLE_SHAPES[Math.floor(random() * PARTICLE_SHAPES.length)] ?? 'spark';
      const texture = this.textures.get(shape);
      if (!texture || !this.pixi) continue;
      const sprite = new this.pixi.Sprite(texture);
      sprite.anchor.set(0.5);
      sprite.tint = colour;
      sprite.alpha = 0;
      sprite.width = particle.size;
      sprite.height = particle.size;
      this.layer.addChild(sprite);
      this.live.push({ particle, sprite });
    }
    if (this.live.length > 0 && !app.ticker.started) {
      app.canvas.style.visibility = 'visible';
      app.ticker.start();
    }
  }

  private async ensureApp(): Promise<Application | null> {
    if (this.app) return this.app;
    if (this.starting) return null; // a second burst during init just misses
    this.starting = true;
    try {
      // Loaded HERE, not at module scope: Pixi is over a megabyte, and an
      // overlay that never reaches hype should never parse a byte of it.
      const pixi = this.pixi ?? (await import('pixi.js'));
      this.pixi = pixi;
      const app = new pixi.Application();
      await app.init({
        backgroundAlpha: 0,
        antialias: true,
        resizeTo: window,
        autoStart: false, // idle until something actually moves
      });
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

      for (const shape of PARTICLE_SHAPES) {
        const canvas = drawParticleCanvas(shape);
        if (canvas) this.textures.set(shape, pixi.Texture.from(canvas));
      }
      const layer = new pixi.Container();
      app.stage.addChild(layer);
      app.ticker.add(({ deltaMS }) => this.frame(deltaMS / 1000));
      if (DEBUG) this.drawDebugBounds(app, pixi);

      this.app = app;
      this.layer = layer;
      return app;
    } catch (err) {
      // No WebGL, a lost context at startup, anything: the overlay is duller
      // and still works. Never let decoration take the app down.
      this.log(`fx layer unavailable (${String(err).slice(0, 100)})`);
      return null;
    } finally {
      this.starting = false;
    }
  }

  private frame(dt: number): void {
    const app = this.app;
    if (!app) return;
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
      entry.sprite.visible = !insideBox(x, y, this.box);
      entry.sprite.position.set(x, y);
      entry.sprite.rotation = entry.particle.rotation;
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

  /** Dev-only: the fade band and the protected box, drawn faintly. */
  private drawDebugBounds(app: Application, pixi: typeof import('pixi.js')): void {
    const g = new pixi.Graphics();
    g.rect(this.box.x, this.box.y, this.box.width, this.box.height).stroke({
      width: 1,
      color: 0xff0000,
      alpha: 0.5,
    });
    g.rect(0, 0, app.renderer.width, app.renderer.height).stroke({
      width: 1,
      color: 0x00ff00,
      alpha: 0.4,
    });
    app.stage.addChild(g);
    this.log('fx layer debug bounds drawn (KASHI_FX_DEBUG)');
  }
}
