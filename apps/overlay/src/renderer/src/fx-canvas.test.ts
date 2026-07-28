/**
 * The Pixi adapter, against a fake Pixi and a hand-built DOM.
 *
 * This file exists because of how Faz 6.7 went. The particle layer shipped
 * with green unit tests and did not run at all: Pixi generates its shader
 * code at runtime, the strict CSP refused it, and the single catch line made
 * "the layer is refused" look identical to "nothing is triggering". Every
 * rule was tested; the thing that owns the objects was not.
 *
 * So the adapter gets tests too. Not for the physics — that is
 * fx-particles-logic's job — but for the promises this class makes: it loads
 * the CSP-safe entry point, it goes fully idle, it does not cover the text,
 * it respects its budget out loud, and it can be rebuilt after a context
 * loss. None of those can be checked without standing in for Pixi.
 *
 * No jsdom: the surface actually used is small enough to build honestly, and
 * a fake we wrote is a fake we understand.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ARCHETYPE_PROFILES } from './fx-particles-logic.js';
import { PARTICLE_SHAPES } from './fx-textures.js';

// --- the fake Pixi -------------------------------------------------------

class FakeTicker {
  started = false;
  startCount = 0;
  private callbacks: Array<(t: { deltaMS: number }) => void> = [];
  add(cb: (t: { deltaMS: number }) => void): void {
    this.callbacks.push(cb);
  }
  start(): void {
    this.started = true;
    this.startCount += 1;
  }
  stop(): void {
    this.started = false;
  }
  /** Drives the layer the way a real ticker would. */
  advance(ms: number): void {
    for (const cb of this.callbacks) cb({ deltaMS: ms });
  }
}

class FakeContainer {
  children: FakeSprite[] = [];
  addChild(child: FakeSprite): void {
    this.children.push(child);
  }
}

class FakeSprite {
  anchor = { set: () => {} };
  x = 0;
  y = 0;
  position = {
    set: (x: number, y: number) => {
      this.x = x;
      this.y = y;
    },
  };
  visible = true;
  alpha = 1;
  tint = 0;
  width = 0;
  height = 0;
  rotation = 0;
  destroyed = false;
  constructor(readonly texture: unknown) {}
  destroy(): void {
    this.destroyed = true;
  }
}

let initShouldThrow = false;

class FakeApplication {
  ticker = new FakeTicker();
  stage = new FakeContainer();
  canvas = makeElement('canvas');
  renderer = { width: 800, height: 460, type: 'webgl' };
  destroyed = false;
  async init(): Promise<void> {
    if (initShouldThrow) throw new Error('no WebGL here');
  }
  render(): void {}
  destroy(): void {
    this.destroyed = true;
  }
}

const pixiModule = {
  Application: FakeApplication,
  Container: FakeContainer,
  Sprite: FakeSprite,
  Graphics: class {
    rect() {
      return this;
    }
    stroke() {
      return this;
    }
  },
  Texture: { from: (source: unknown) => ({ source }) },
};

vi.mock('pixi.js', () => pixiModule);
// The entry point named for what it avoids. Importing it is the whole reason
// the layer runs under `default-src 'self'` with no unsafe-eval.
vi.mock('pixi.js/unsafe-eval', () => ({ loaded: true }));

// --- the hand-built DOM --------------------------------------------------

interface FakeElement {
  tagName: string;
  id: string;
  style: Record<string, string>;
  isConnected: boolean;
  width: number;
  height: number;
  listeners: Map<string, (event: { preventDefault(): void }) => void>;
  addEventListener(type: string, cb: (event: { preventDefault(): void }) => void): void;
  remove(): void;
  getContext(kind: string): unknown;
}

function makeElement(tagName: string): FakeElement {
  const element: FakeElement = {
    tagName,
    id: '',
    style: {},
    isConnected: false,
    width: 0,
    height: 0,
    listeners: new Map(),
    addEventListener(type, cb) {
      element.listeners.set(type, cb);
    },
    remove() {
      element.isConnected = false;
      prepended = prepended.filter((el) => el !== element);
    },
    // Enough 2D context for the procedural painters: they draw paths and
    // gradients and never read anything back.
    getContext: (kind: string) =>
      kind === '2d'
        ? {
            fillStyle: '',
            beginPath: () => {},
            moveTo: () => {},
            lineTo: () => {},
            bezierCurveTo: () => {},
            closePath: () => {},
            fill: () => {},
            fillRect: () => {},
            clearRect: () => {},
            createRadialGradient: () => ({ addColorStop: () => {} }),
          }
        : null,
  };
  return element;
}

let prepended: FakeElement[] = [];

beforeEach(() => {
  prepended = [];
  initShouldThrow = false;
  globalThis.document = {
    createElement: (tag: string) => makeElement(tag),
    body: {
      prepend: (element: FakeElement) => {
        element.isConnected = true;
        prepended.push(element);
      },
    },
  } as unknown as Document;
  globalThis.window = { devicePixelRatio: 2 } as unknown as Window & typeof globalThis;
});

afterEach(() => {
  vi.clearAllMocks();
});

async function freshCanvas(log: string[] = []) {
  const { FxCanvas } = await import('./fx-canvas.js');
  const box = { x: 120, y: 160, width: 560, height: 180 };
  return { layer: new FxCanvas(box, (line) => log.push(line)), box, log };
}

describe('FxCanvas — the adapter, not the physics', () => {
  it('loads the CSP-safe entry point alongside pixi itself', async () => {
    // The 6.7 failure in one assertion: without this import the renderer runs
    // into `new Function` under a policy that forbids it, and everything up
    // to that point — types, lint, unit tests — stays green.
    const unsafeEval = await import('pixi.js/unsafe-eval');
    expect(unsafeEval).toBeDefined();

    const { layer, log } = await freshCanvas();
    await layer.burst(0xff0000);
    expect(log.some((line) => line.includes('pixi module loaded'))).toBe(true);
    expect(log.some((line) => line.includes(`textures=${PARTICLE_SHAPES.length}`))).toBe(true);
  });

  it('puts the canvas UNDER the box and out of the pointer path', async () => {
    const { layer } = await freshCanvas();
    await layer.burst(0x00ff00);
    const canvas = prepended[0]!;
    expect(canvas.id).toBe('fx-canvas');
    expect(canvas.style['zIndex']).toBe('0');
    expect(canvas.style['pointerEvents']).toBe('none');
    void layer;
  });

  it('emits sprites in the tint and shapes the profile asked for', async () => {
    const { layer } = await freshCanvas();
    await layer.burst(0x123456, undefined, ARCHETYPE_PROFILES.drift);

    const textures = texturesOf(layer);
    expect([...textures.keys()].sort()).toEqual([...PARTICLE_SHAPES].sort());

    const sprites = stageOf(layer);
    expect(sprites.length).toBe(ARCHETYPE_PROFILES.drift.count);
    const heart = textures.get('heart');
    for (const sprite of sprites) {
      expect(sprite.tint).toBe(0x123456);
      // love is hearts and NOTHING else — identity, not just "some texture".
      expect(sprite.texture).toBe(heart);
    }

    // And a profile with a pool draws from that pool only.
    const { layer: second } = await freshCanvas();
    await second.burst(0xffffff, undefined, ARCHETYPE_PROFILES.burst);
    const pool = ARCHETYPE_PROFILES.burst.shapes.map((s) => texturesOf(second).get(s));
    for (const sprite of stageOf(second)) expect(pool).toContain(sprite.texture);
  });

  it('starts the ticker on the first burst and stops dead when the last particle dies', async () => {
    // The battery contract: a full-screen composite that never idles costs a
    // laptop even while it draws nothing.
    const { layer } = await freshCanvas();
    expect(layer.isIdle()).toBe(true);

    await layer.burst(0xffffff, undefined, ARCHETYPE_PROFILES.burst);
    const app = appOf(layer);
    expect(app.ticker.started).toBe(true);
    expect(app.canvas.style['visibility']).toBe('visible');
    expect(layer.isIdle()).toBe(false);

    // Run past the longest life in the profile.
    for (let i = 0; i < 200; i++) app.ticker.advance(16);

    expect(app.ticker.started).toBe(false);
    expect(app.canvas.style['visibility']).toBe('hidden');
    expect(layer.isIdle()).toBe(true);
  });

  it('never draws a particle that is over the text (DG6)', async () => {
    const { layer, box } = await freshCanvas();
    await layer.burst(0xffffff, undefined, ARCHETYPE_PROFILES.smoke);
    const app = appOf(layer);

    // Walk the whole burst; at every frame, anything the adapter marked
    // visible has to be clear of the box — by the SPRITE's extent, not its
    // centre, which is why smoke (32px) forced the inflated mask.
    for (let frame = 0; frame < 200; frame++) {
      app.ticker.advance(16);
      for (const sprite of stageOf(layer)) {
        if (!sprite.visible || sprite.destroyed) continue;
        const half = sprite.width / 2;
        const overlapsText =
          sprite.x >= box.x - half &&
          sprite.x <= box.x + box.width + half &&
          sprite.y >= box.y - half &&
          sprite.y <= box.y + box.height + half;
        expect(overlapsText, `sprite at ${sprite.x},${sprite.y} is over the lyrics`).toBe(false);
      }
    }
  });

  it('says so when the budget trims a burst instead of thinning it quietly', async () => {
    const { layer, log } = await freshCanvas();
    // Fill the layer well past the ceiling with long-lived particles.
    for (let i = 0; i < 8; i++) await layer.burst(0xffffff, undefined, ARCHETYPE_PROFILES.smoke);

    const budgetLines = log.filter((line) => line.includes('budget'));
    expect(budgetLines).toHaveLength(1);
    expect(budgetLines[0]).toMatch(/emitting \d+ of \d+/);
  });

  it('gives up rebuilding a renderer this machine will not provide, loudly', async () => {
    initShouldThrow = true;
    const { layer, log } = await freshCanvas();
    for (let i = 0; i < 5; i++) await layer.burst(0xffffff);

    // Two attempts, then it stops asking — and it never pretended to work.
    // The 6.7 lesson in one place: a refused setup has to be loud enough to
    // diagnose AND has to stop retrying, or "the layer is refused" reads as
    // "nothing is triggering" once per fx word forever.
    const failures = log.filter((line) => line.includes('fx layer unavailable'));
    expect(failures).toHaveLength(2);
    expect(failures[1]).toContain('giving up for this session');
    expect(failures[0]).toContain('no WebGL here');
    expect(layer.isIdle()).toBe(true);
  });

  it('takes the whole layer down on destroy, canvas included', async () => {
    const { layer } = await freshCanvas();
    await layer.burst(0xffffff);
    expect(prepended).toHaveLength(1);

    layer.destroy();
    expect(prepended).toHaveLength(0);
    expect(layer.isIdle()).toBe(true);

    // A destroyed layer stays destroyed: a burst in flight must not resurrect it.
    await layer.burst(0xffffff);
    expect(prepended).toHaveLength(0);
  });

  it('lets a rebuilt layer speak again after a context loss', async () => {
    const { layer, log } = await freshCanvas();
    await layer.burst(0xffffff);
    const first = appOf(layer);
    expect(log.filter((l) => l.includes('first emission'))).toHaveLength(1);

    // The browser takes the context away; the layer drops everything.
    first.canvas.listeners.get('webglcontextlost')?.({ preventDefault: () => {} });
    await layer.burst(0xffffff);

    // Rebuilt from scratch, and its one-shot diagnostics are armed again —
    // otherwise the budget line would stay suppressed for the whole session.
    expect(log.filter((l) => l.includes('first emission'))).toHaveLength(2);
  });
});

// --- reaching into the layer without exporting its internals -------------

function appOf(layer: unknown): FakeApplication {
  return (layer as { app: FakeApplication }).app;
}

function stageOf(layer: unknown): FakeSprite[] {
  return (layer as { layer: FakeContainer }).layer.children;
}

function texturesOf(layer: unknown): Map<string, unknown> {
  return (layer as { textures: Map<string, unknown> }).textures;
}

describe('FxCanvas — frame deltas cannot be trusted', () => {
  it('a stutter must not teleport particles across the box', async () => {
    // The dangerous delta is not the huge one — a multi-second gap kills the
    // burst outright, which is fine, the song moved on. It is the half-second
    // stall (a compositor hiccup, a heavy tab) where particles SURVIVE: at
    // spark speeds that is over a hundred pixels covered in a single frame,
    // seen as the whole burst jumping sideways at once.
    const { layer } = await freshCanvas();
    await layer.burst(0xffffff, undefined, ARCHETYPE_PROFILES.spark);
    const app = appOf(layer);

    app.ticker.advance(16);
    const before = stageOf(layer).map((s) => ({ x: s.x, y: s.y }));
    app.ticker.advance(500);

    let checked = 0;
    for (const [i, sprite] of stageOf(layer).entries()) {
      if (sprite.destroyed) continue;
      checked += 1;
      const moved = Math.hypot(sprite.x - before[i]!.x, sprite.y - before[i]!.y);
      // One clamped step's worth of travel, not half a second of it.
      expect(moved, `sprite ${i} jumped ${Math.round(moved)}px in one frame`).toBeLessThan(40);
    }
    // The assertion above is only worth anything if particles actually lived.
    expect(checked).toBeGreaterThan(0);
  });

  it('a poisoned delta cannot make a particle immortal', async () => {
    // Death is `age >= life`; NaN makes that comparison false forever, so the
    // particle never dies and the ticker never stops — an invisible battery
    // leak. The clamp turns the bad delta into no movement at all.
    const { layer } = await freshCanvas();
    await layer.burst(0xffffff, undefined, ARCHETYPE_PROFILES.spark);
    const app = appOf(layer);

    app.ticker.advance(Number.NaN);
    app.ticker.advance(Number.POSITIVE_INFINITY);
    for (let i = 0; i < 200; i++) app.ticker.advance(16);

    expect(layer.isIdle()).toBe(true);
    expect(app.ticker.started).toBe(false);
  });

  it('clears a burst that outlived its bound, and says so', async () => {
    // Backstop for whatever the clamp did not foresee. Driven with a zero
    // delta so ages never advance: the particles would otherwise live forever.
    const { layer, log } = await freshCanvas();
    await layer.burst(0xffffff, undefined, ARCHETYPE_PROFILES.drift);
    const app = appOf(layer);

    for (let i = 0; i < 3000; i++) app.ticker.advance(0);

    expect(layer.isIdle()).toBe(true);
    expect(log.some((line) => line.includes('outlived their bound'))).toBe(true);
  });
});
