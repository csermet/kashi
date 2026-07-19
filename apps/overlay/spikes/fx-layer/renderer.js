/**
 * fx-layer spike renderer: identical drift motion in two engines (PixiJS v8
 * ParticleContainer vs plain canvas 2D), with a p95 frame-time HUD. The 5-min
 * soak result feeds the P3 GO/NO-GO gate (see README + the research doc).
 *
 * Dev-only file, CommonJS on purpose (nodeIntegration lets require() resolve
 * pixi.js without any bundling).
 */
/* eslint-disable */
'use strict';

const params = new URLSearchParams(location.search);
const MODE = params.get('mode') === 'canvas' ? 'canvas' : 'pixi';
const COUNT = Math.max(1, Math.min(20000, parseInt(params.get('n') || '300', 10) || 300));
const SOAK_MS = 5 * 60 * 1000;

const W = () => window.innerWidth;
const H = () => window.innerHeight;

// --- shared motion model: sakura-style drift (fall + sway + slow spin) ---
function makeParticles(count) {
  const parts = [];
  for (let i = 0; i < count; i += 1) {
    parts.push({
      x: Math.random() * W(),
      y: Math.random() * H(),
      vy: 28 + Math.random() * 46, // px/s fall
      sway: 18 + Math.random() * 30, // px sway amplitude
      phase: Math.random() * Math.PI * 2,
      freq: 0.4 + Math.random() * 0.7, // sway Hz
      size: 2.5 + Math.random() * 3.5,
    });
  }
  return parts;
}

function step(parts, dtS, tS) {
  for (const p of parts) {
    p.y += p.vy * dtS;
    if (p.y > H() + 8) {
      p.y = -8;
      p.x = Math.random() * W();
    }
    p.drawX = p.x + Math.sin(tS * p.freq * 2 * Math.PI + p.phase) * p.sway;
  }
}

// --- metrics: rolling p95 over ~10s + worst frame + soak summary ---
const deltas = [];
let frames = 0;
let worstMs = 0;
const startedAt = performance.now();
let soakDone = false;
const hud = document.getElementById('hud');

function recordFrame(deltaMs) {
  frames += 1;
  if (deltaMs > worstMs) worstMs = deltaMs;
  deltas.push(deltaMs);
  if (deltas.length > 600) deltas.shift();
}

function p95() {
  if (deltas.length === 0) return 0;
  const sorted = [...deltas].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))];
}

let engineLabel = MODE;
setInterval(() => {
  const elapsedS = (performance.now() - startedAt) / 1000;
  const p = p95();
  const fps = deltas.length ? Math.round(1000 / (deltas.reduce((a, b) => a + b, 0) / deltas.length)) : 0;
  if (!soakDone && elapsedS * 1000 >= SOAK_MS) {
    soakDone = true;
    hud.classList.add('done');
    console.log(
      `SOAK COMPLETE engine=${engineLabel} n=${COUNT} p95=${p.toFixed(1)}ms worst=${worstMs.toFixed(1)}ms frames=${frames}`,
    );
  }
  hud.textContent =
    `kashi fx-layer spike  [${engineLabel}]  particles=${COUNT}\n` +
    `p95(10s)=${p.toFixed(1)} ms   fps~${fps}   worst=${worstMs.toFixed(1)} ms\n` +
    `elapsed=${Math.floor(elapsedS / 60)}:${String(Math.floor(elapsedS % 60)).padStart(2, '0')}` +
    (soakDone ? '   ✔ 5-min soak DONE (keep it running for the checklist)' : '   (soak: 5 min)') +
    `\nGO gate @300: p95 < 16.7 ms AND process CPU < 10%`;
}, 500);

// --- engines ---
async function runPixi() {
  const pixi = require('pixi.js');
  const app = new pixi.Application();
  await app.init({ backgroundAlpha: 0, resizeTo: window, antialias: false });
  document.body.appendChild(app.canvas);

  const texture = app.renderer.generateTexture(
    new pixi.Graphics().circle(0, 0, 4).fill({ color: 0xffe4f1, alpha: 0.9 }),
  );
  const parts = makeParticles(COUNT);

  // ParticleContainer + Particle is the v8 fast path; fall back to plain
  // sprites if a minor rework moves the API (the spike must keep measuring).
  let sprites;
  if (typeof pixi.ParticleContainer === 'function' && typeof pixi.Particle === 'function') {
    const pc = new pixi.ParticleContainer({
      dynamicProperties: { position: true, rotation: false, uvs: false, color: false },
    });
    sprites = parts.map((p) => {
      const particle = new pixi.Particle({ texture, x: p.x, y: p.y });
      pc.addParticle(particle);
      return particle;
    });
    app.stage.addChild(pc);
  } else {
    engineLabel = 'pixi-sprite-fallback';
    sprites = parts.map((p) => {
      const s = new pixi.Sprite(texture);
      s.position.set(p.x, p.y);
      app.stage.addChild(s);
      return s;
    });
  }

  let last = performance.now();
  app.ticker.add(() => {
    const now = performance.now();
    const dt = now - last;
    last = now;
    recordFrame(dt);
    step(parts, dt / 1000, now / 1000);
    for (let i = 0; i < parts.length; i += 1) {
      sprites[i].x = parts[i].drawX;
      sprites[i].y = parts[i].y;
    }
  });
}

function runCanvas() {
  const canvas = document.createElement('canvas');
  document.body.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  const resize = () => {
    canvas.width = W();
    canvas.height = H();
  };
  resize();
  window.addEventListener('resize', resize);

  const parts = makeParticles(COUNT);
  let last = performance.now();
  const frame = (now) => {
    const dt = now - last;
    last = now;
    recordFrame(dt);
    step(parts, dt / 1000, now / 1000);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = 'rgba(255, 228, 241, 0.9)';
    for (const p of parts) {
      ctx.beginPath();
      ctx.arc(p.drawX, p.y, p.size, 0, Math.PI * 2);
      ctx.fill();
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

if (MODE === 'pixi') {
  runPixi().catch((err) => {
    hud.textContent = `pixi init FAILED: ${err?.message ?? err}\n(that itself is a spike datapoint — note it)`;
    console.error(err);
  });
} else {
  runCanvas();
}
