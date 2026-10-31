// Perceptual audit: what the overlay shows at given player positions.
//
// sim  (default) — the renderer's own pure functions (findDisplayLine,
//        findActiveWord, the fill plan, the fx index) imported from src/, run
//        on the document exactly as the main process maps it (mapDocument).
// real — the BUILT renderer (out/renderer) in Chromium behind a window.kashi
//        shim; one frozen frame per time (seek, paused), a PNG of #lyric-box,
//        and the DOM state of every word. The sim state is computed too and
//        the two must agree, or the script exits 1.
//
// Usage (Node >= 22.18; `real` inside the Playwright image, see
// apps/server/audit/README.md):
//   node scripts/audit-strip.mjs --doc <doc.json> [--id <video id>] --times 203060,203260
//        [--offset 200] [--level hype] [--mode sim|real] [--out frames.json] [--png-dir dir]
// <doc.json> is a processed-track document, or a [{id, doc}] dump (then --id picks one).
// Times are PLAYER positions (the raw clock); words render at time + offset.
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { createServer } from 'node:http';
import { register } from 'node:module';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

// src/ imports siblings as './x.js' while only x.ts exists; Node's type
// stripping does not remap that, so fall back to .ts on a miss.
register(
  'data:text/javascript,' +
    encodeURIComponent(`
export async function resolve(specifier, context, next) {
  try { return await next(specifier, context); }
  catch (err) {
    if (err?.code === 'ERR_MODULE_NOT_FOUND' && specifier.endsWith('.js'))
      return next(specifier.slice(0, -3) + '.ts', context);
    throw err;
  }
}`),
);

const HERE = dirname(fileURLToPath(import.meta.url));
const OVERLAY = resolve(HERE, '..');
const src = (p) => pathToFileURL(join(OVERLAY, 'src', p)).href;
const { mapDocument } = await import(src('main/kashi-server-logic.ts'));
const { DEFAULT_SETTINGS } = await import(src('main/settings-logic.ts'));
const { findDisplayLine, findActiveWord } = await import(src('renderer/src/view-logic.ts'));
const { planFillsByLineIdentity, planWordFills, fillProgress, buildFxIndex } = await import(
  src('renderer/src/effects-logic.ts')
);

// main.ts FX_FIRED_HOLD_MS: at hype an fx word stays lit this long after it fires.
const FX_FIRED_HOLD_MS = 700;

const { values: args } = parseArgs({
  options: {
    doc: { type: 'string' },
    id: { type: 'string' },
    times: { type: 'string' },
    offset: { type: 'string', default: '200' },
    level: { type: 'string', default: 'hype' },
    mode: { type: 'string', default: 'sim' },
    out: { type: 'string' },
    'png-dir': { type: 'string' },
    'keep-transitions': { type: 'boolean', default: false },
  },
});
if (!args.doc || !args.times) {
  console.error('need --doc and --times');
  process.exit(2);
}

function loadDoc(path, id) {
  const raw = JSON.parse(readFileSync(path, 'utf8'));
  if (!Array.isArray(raw)) return raw;
  const hit = raw.find((x) => x.id === id);
  if (!hit?.doc) throw new Error(`no document for ${id} in ${path}`);
  return hit.doc;
}

const doc = loadDoc(args.doc, args.id);
const videoId = doc.track?.source?.id ?? args.id;
const key = `${doc.track?.source?.type ?? 'youtube'}:${videoId}`;
const mapped = mapDocument(doc);
if (!mapped?.found) throw new Error('mapDocument rejected the document');
const lines = mapped.lines;
const offset = Number(args.offset);
const level = args.level;
const times = args.times.split(',').map((t) => Math.round(Number(t)));

const sharedPlans = planFillsByLineIdentity(lines, level);
const fxIndex = level === 'hype' ? buildFxIndex(mapped.fx, lines) : new Map();

/** The renderer's state at player position `t`, from its own functions. */
function simulate(t) {
  const pos = t + offset;
  const lineIndex = lines.length > 0 ? findDisplayLine(lines, pos) : -1;
  const line = lineIndex >= 0 ? lines[lineIndex] : undefined;
  const words = line?.words ?? [];
  const state = { t, pos, line: lineIndex, text: line?.text ?? null, active: -1, sung: [], fill: {}, fx_fired: null };
  if (!line || words.length === 0) return state;
  const active = findActiveWord(words, pos);
  state.active = active;
  state.sung = words.map((_, i) => i).filter((i) => i < active);
  const plan = sharedPlans[lineIndex] ?? planWordFills(words, line.adlib === true, level);
  if (active >= 0 && plan[active]) {
    let runStart = active;
    while (runStart > 0 && plan[runStart - 1]) runStart -= 1;
    for (let i = runStart; i < active; i += 1) state.fill[i] = 1;
    state.fill[active] = Number(fillProgress(words[active], pos).toFixed(3));
  }
  // The latest fx word fired on this line, held FX_FIRED_HOLD_MS (forward play).
  for (const hit of fxIndex.get(lineIndex) ?? []) {
    const w = words[hit.word];
    if (w && hit.word <= active && pos - w.start_ms < FX_FIRED_HOLD_MS) state.fx_fired = hit.word;
  }
  return state;
}

const result = {
  id: videoId,
  pipeline_version: doc.pipeline_version,
  offset_ms: offset,
  level,
  mode: args.mode,
  frames: times.map((t) => {
    const state = simulate(t);
    return { ...state, words: (state.line >= 0 ? (lines[state.line].words ?? []) : []).map((w) => w.text) };
  }),
};

if (args.mode === 'real') {
  result.real = await realFrames();
  const mismatches = [];
  result.frames.forEach((f, i) => {
    const r = result.real.frames[i];
    const same =
      r.visible === (f.line >= 0) &&
      (f.line < 0 || (r.text === f.text && r.active === f.active && r.sung.join() === f.sung.join()));
    if (!same) mismatches.push({ t: f.t, sim: { text: f.text, active: f.active, sung: f.sung }, real: r });
  });
  result.mismatches = mismatches;
}

const out = JSON.stringify(result, null, 1);
if (args.out) writeFileSync(args.out, out + '\n');
else console.log(out);
if (result.mismatches?.length) {
  console.error(`sim and real disagree on ${result.mismatches.length} frame(s)`);
  process.exit(1);
}

async function realFrames() {
  const pwPath = process.env.PLAYWRIGHT_CORE ?? join(OVERLAY, '../../scratch/ext-gapless/node_modules/playwright-core/index.mjs');
  const { chromium } = await import(pathToFileURL(pwPath).href);
  const root = join(OVERLAY, 'out/renderer');
  const indexHtml = readFileSync(join(root, 'index.html'));
  const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json' };
  // Transitions run on the compositor clock, which a frozen player clock does
  // not stop; served same-origin because the build CSP refuses inline styles.
  // The window is transparent; behind it on the owner's screen is YouTube
  // Music's dark page, so frames are taken over that colour, not white.
  const KILL =
    '*,*::before,*::after{transition:none!important;animation:none!important}' +
    'html,body{background:#030303!important}';
  const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/__audit.css') {
      res.writeHead(200, { 'content-type': 'text/css' });
      return res.end(KILL);
    }
    const file = join(root, url.pathname === '/' ? 'index.html' : url.pathname);
    if (!file.startsWith(root) || !existsSync(file)) {
      res.writeHead(404);
      return res.end();
    }
    res.writeHead(200, { 'content-type': types[extname(file)] ?? 'application/octet-stream' });
    res.end(readFileSync(file));
  });
  await new Promise((ok) => server.listen(0, '127.0.0.1', ok));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 800, height: 460 }, deviceScaleFactor: 2 });
  const logs = [];
  page.on('console', (m) => logs.push(m.text()));
  page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
  await page.addInitScript(() => {
    const cbs = {};
    const on = (name) => (cb) => {
      (cbs[name] ??= []).push(cb);
      return () => {};
    };
    window.__kashiPush = (name, payload) => (cbs[name] ?? []).forEach((cb) => cb(payload));
    window.__kashiLog = [];
    const noop = () => {};
    window.kashi = {
      onTrack: on('track'), onLyrics: on('lyrics'), onPlayback: on('playback'),
      onSettings: on('settings'), onConnection: on('connection'), onSourceGone: on('sourceGone'),
      setInteractive: noop, dragStart: noop, dragEnd: noop, adjustOpacity: noop,
      adjustTimingOffset: noop, openMenu: noop, reportAnomaly: noop,
      log: (line) => window.__kashiLog.push(String(line)),
    };
  });
  await page.goto(base + '/');
  if (!args['keep-transitions']) await page.addStyleTag({ url: base + '/__audit.css' });
  const settings = { ...DEFAULT_SETTINGS, effect_level: level, timing_offset_ms: offset };
  const pick = ({ box_alpha, timing_offset_ms, effect_level, theme_scope, fill_style, text_scale, box_scale }) =>
    ({ box_alpha, timing_offset_ms, effect_level, theme_scope, fill_style, text_scale, box_scale });
  const title = doc.track?.title ?? '';
  const artist = doc.track?.artist ?? '';
  await page.evaluate(
    ({ settings, key, title, artist, mapped }) => {
      window.__kashiPush('settings', settings);
      window.__kashiPush('connection', { connected: true });
      window.__kashiPush('track', { key, track: { title, artist } });
      window.__kashiPush('lyrics', { key, ...mapped });
    },
    { settings: pick(settings), key, title, artist, mapped },
  );
  const pngDir = args['png-dir'];
  if (pngDir) mkdirSync(pngDir, { recursive: true });
  const frames = [];
  let seq = 0;
  for (const t of times) {
    await page.evaluate(
      ({ t, seq }) => {
        window.__kashiPush('playback', {
          type: 'seek', seq, sent_at: Date.now(), tab_id: 1, position_ms: t, is_playing: false, captured_at: Date.now(),
        });
        return new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok)));
      },
      { t, seq: (seq += 1) },
    );
    await page.waitForTimeout(50);
    const state = await page.evaluate(() => {
      const box = document.getElementById('lyric-box');
      const line = document.getElementById('lyric-line');
      const spans = [...line.querySelectorAll('span.word')];
      const idx = (cls) => spans.map((s, i) => (s.classList.contains(cls) ? i : -1)).filter((i) => i >= 0);
      return {
        visible: !box.classList.contains('hidden') && !line.classList.contains('interlude') && spans.length + (line.textContent ?? '').trim().length > 0 && !line.classList.contains('dim'),
        text: spans.length ? spans.map((s) => s.textContent).join(' ') : (line.textContent ?? '').trim(),
        active: idx('word-active')[0] ?? -1,
        sung: idx('word-sung'),
        fill: Object.fromEntries(spans.map((s, i) => [i, s.style.getPropertyValue('--kashi-fill')]).filter(([, v]) => v !== '')),
        fx_fired: idx('fx-fired')[0] ?? null,
        line_classes: line.className,
        box_classes: box.className,
      };
    });
    if (pngDir) await page.locator('#lyric-box').screenshot({ path: join(pngDir, `frame-${t}.png`) });
    frames.push({ t, ...state });
  }
  const kashiLog = await page.evaluate(() => window.__kashiLog);
  // Pixi loads lazily at hype; it only draws particles, never the words.
  const fxCanvas = await page.evaluate(() => (document.querySelector('canvas') ? 'mounted' : 'absent'));
  const bodyClasses = await page.evaluate(() => document.body.className);
  await browser.close();
  server.close();
  return {
    build_index_sha256: createHash('sha256').update(indexHtml).digest('hex').slice(0, 16),
    fx_canvas: fxCanvas,
    body_classes: bodyClasses,
    log: [...kashiLog, ...logs],
    frames,
  };
}
