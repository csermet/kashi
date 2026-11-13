// Golden frames and dwell: the BUILT renderer, headless, on committed
// synthetic documents (test/golden/runs.json).
//
// For every run and time point the harness seeks the renderer (paused, on a
// virtual clock: scripts/lib/page-shim.mjs) and reads the DOM: which line is
// up, which word is active, which are sung, each sweeping word's fill, the fx
// mark and the line/box classes. That state must equal the committed golden
// exactly, and the renderer's own pure functions (scripts/lib/sim.mjs) must
// agree with it. A few times also keep a picture, compared with the tolerance
// below. Per document it reports how long every word stays LIT (the renderer
// keeps a word lit until the next word starts) and asserts the counts of
// words lit under 120 ms and under 180 ms against pinned numbers: a change
// that rushes words past the reader fails here even when every position
// looks right ("guard visibility, not position", olu-fikirler).
//
// Runs inside mcr.microsoft.com/playwright:v1.63.0-noble (its Chromium and
// fonts are what the PNGs were taken with); see golden-frames.sh. No
// dependency beyond Node itself.
//
//   node scripts/golden-frames.mjs [--renderer out/renderer] [--update]
//        [--only <run>] [--report-dir /tmp/kashi-golden-report] [--chromium <path>]
//        [--runs <runs.json>]   (another run list, for experiments; docs stay in test/golden)
//
// --update rewrites test/golden/expected.json and test/golden/png/*.png from
// the current build: review that diff like code.
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { Browser, IMAGE_CHROMIUM } from './lib/cdp.mjs';
import { PAGE_INIT_SOURCE } from './lib/page-shim.mjs';
import { comparePixels, decodePng } from './lib/png.mjs';
import { loadRendererSrc } from './lib/renderer-src.mjs';
import { dwellSummary, simContext, simulateAt, wordLitTimes } from './lib/sim.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OVERLAY = resolve(HERE, '..');
const GOLDEN = join(OVERLAY, 'test/golden');

// Picture tolerance. A pixel counts as different when any channel moved by
// more than CHANNEL_TOLERANCE; a picture fails when more than
// MAX_DIFFERING_SHARE of its pixels differ. Measured 2026-11-13 in the pinned
// image (box crops of 182-418 x 58 px):
//   - a repeat run: 0 pixels differ in all 13 pictures (max channel delta 0);
//   - the active word moving one word on: 6.0 % of the box;
//   - the fill dialect switched (themed <-> neutral, removed since): 4.3-5.2 %;
//   - a sweep advanced by 5 % of one word: 0.12-0.15 %; by 2 %: 0-0.08 %.
// Exact fill values are the DOM golden's job (compared to the digit); the
// pictures guard what the DOM cannot see — colours, gradients, the tints,
// layout. 0.1 % catches every class above, a 5 % sweep step included, and
// leaves room for anti-aliasing noise from another CPU under the same
// software rasteriser (12-24 pixels per box), which this host cannot measure.
const CHANNEL_TOLERANCE = 8;
const MAX_DIFFERING_SHARE = 0.001;

const VIEWPORT = { width: 800, height: 460, deviceScaleFactor: 1 };

const { values: args } = parseArgs({
  options: {
    renderer: { type: 'string', default: join(OVERLAY, 'out/renderer') },
    chromium: { type: 'string', default: IMAGE_CHROMIUM },
    update: { type: 'boolean', default: false },
    only: { type: 'string' },
    runs: { type: 'string', default: join(GOLDEN, 'runs.json') },
    'report-dir': { type: 'string', default: '/tmp/kashi-golden-report' },
  },
});

const started = Date.now();
const R = await loadRendererSrc(OVERLAY);
const spec = JSON.parse(readFileSync(args.runs, 'utf8'));
const runs = spec.runs.filter((run) => !args.only || run.name === args.only);
const expectedPath = join(GOLDEN, 'expected.json');
const expected = existsSync(expectedPath) ? JSON.parse(readFileSync(expectedPath, 'utf8')) : null;
if (!expected && !args.update) {
  console.error('no test/golden/expected.json: run once with --update and review it');
  process.exit(2);
}

const failures = [];
const fail = (line) => failures.push(line);

// ---- the built renderer, served same-origin (the build CSP refuses inline styles)
const root = resolve(args.renderer);
if (!existsSync(join(root, 'index.html'))) {
  console.error(`no built renderer at ${root} (pnpm --filter kashi-overlay build)`);
  process.exit(2);
}
// Transitions and animations run on the compositor clock, which the virtual
// clock does not drive: off, so a frame shows the settled state. The
// particle canvas is hidden in pictures (its sprites move on their own
// ticker; the words are DOM and are what is compared). Background: YouTube
// Music's dark page, which is what sits behind the transparent window.
const GOLDEN_CSS =
  '*,*::before,*::after{transition:none!important;animation:none!important}' +
  'html,body{background:#030303!important}canvas{visibility:hidden!important}';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json' };
const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/__golden.css') {
    res.writeHead(200, { 'content-type': 'text/css' });
    return res.end(GOLDEN_CSS);
  }
  const file = join(root, url.pathname === '/' ? 'index.html' : url.pathname);
  if (!file.startsWith(root) || !existsSync(file)) {
    res.writeHead(404);
    return res.end();
  }
  res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' });
  res.end(readFileSync(file));
});
await new Promise((ok) => server.listen(0, '127.0.0.1', ok));
const base = `http://127.0.0.1:${server.address().port}`;

const browser = await Browser.launch({ executable: args.chromium });
const { product } = await browser.send('Browser.getVersion');

const actual = { generated_by: 'scripts/golden-frames.mjs', chromium: product, runs: {}, dwell: {} };
const pictures = new Map(); // file name -> PNG bytes

try {
  for (const run of runs) {
    actual.runs[run.name] = await renderRun(run);
  }
} finally {
  await browser.close();
  server.close();
}

// Twin runs: the same document and times under settings that may only change
// paint (the theme scope) must reach the same DOM state, frame by frame.
for (const [a, b] of spec.twins ?? []) {
  const left = actual.runs[a];
  const right = actual.runs[b];
  if (!left || !right) continue;
  left.frames.forEach((frame, i) => {
    if (JSON.stringify(frame) !== JSON.stringify(right.frames[i])) {
      fail(`twins ${a} / ${b} @${frame.pos}: DOM state differs`);
    }
  });
}

// ---- compare or write
if (args.update) {
  const pngDir = join(GOLDEN, 'png');
  if (!args.only) rmSync(pngDir, { recursive: true, force: true });
  mkdirSync(pngDir, { recursive: true });
  for (const [name, bytes] of pictures) writeFileSync(join(pngDir, name), bytes);
  const merged = args.only && expected ? { ...expected, runs: { ...expected.runs, ...actual.runs }, dwell: { ...expected.dwell, ...actual.dwell } } : actual;
  merged.chromium = product;
  writeFileSync(expectedPath, JSON.stringify(merged, null, 1) + '\n');
  console.log(`updated ${expectedPath} and ${pictures.size} picture(s)`);
} else {
  compareAll();
}

for (const [name, d] of Object.entries(actual.dwell)) {
  const s = d.summary;
  console.log(
    `dwell ${name}: ${s.words} words, lit < 120 ms: ${s.lit_lt_120}, < 180 ms: ${s.lit_lt_180}, ` +
      `never lit: ${s.never_lit}, median ${s.median_lit_ms} ms`,
  );
}
const seconds = ((Date.now() - started) / 1000).toFixed(1);
if (failures.length > 0) {
  const dir = args['report-dir'];
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'actual.json'), JSON.stringify(actual, null, 1) + '\n');
  for (const [name, bytes] of pictures) writeFileSync(join(dir, name), bytes);
  console.error(failures.join('\n'));
  console.error(`golden frames: ${failures.length} failure(s) in ${seconds} s; actual state and pictures in ${dir}`);
  process.exit(1);
}
const frameCount = Object.values(actual.runs).reduce((n, r) => n + r.frames.length, 0);
console.log(`golden frames: ${runs.length} runs, ${frameCount} frames, ${pictures.size} pictures, all as expected (${seconds} s, ${product})`);

// --------------------------------------------------------------------------

async function renderRun(run) {
  const doc = JSON.parse(readFileSync(join(GOLDEN, run.doc), 'utf8'));
  const mapped = R.mapDocument(doc);
  if (!mapped?.found) throw new Error(`${run.doc}: mapDocument rejected it`);
  const settings = { ...R.DEFAULT_SETTINGS, ...run.settings };
  const lead = R.appliedLeadMs(settings.timing_offset_rel_ms);
  const ctx = simContext(R, mapped.lines, settings.effect_level, mapped.fx);
  const docName = run.doc.replace(/^docs\//, '').replace(/\.json$/, '');
  if (!actual.dwell[docName]) {
    const lit = wordLitTimes(R, mapped.lines);
    actual.dwell[docName] = { summary: dwellSummary(lit), lit };
  }

  const page = await browser.newPage(VIEWPORT);
  await page.addInitScript(PAGE_INIT_SOURCE);
  await page.goto(base + '/');
  await page.evaluate(
    () =>
      new Promise((ok, fail) => {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = '/__golden.css';
        link.onload = () => ok(true);
        link.onerror = () => fail(new Error('golden stylesheet refused'));
        document.head.appendChild(link);
      }),
  );
  const key = `${doc.track.source.type}:${doc.track.source.id}`;
  const pick = ({ box_alpha, timing_offset_rel_ms, effect_level, theme_scope, fill_style, text_scale, box_scale }) =>
    ({ box_alpha, timing_offset_rel_ms, effect_level, theme_scope, fill_style, text_scale, box_scale });
  let v = await page.evaluate(() => window.__vt.now());
  v += 100;
  await page.evaluate(
    ({ v, settings, key, title, artist, mapped }) => {
      window.__kashiPush('settings', settings);
      window.__kashiPush('connection', { connected: true });
      window.__kashiPush('track', { key, track: { title, artist } });
      window.__kashiPush('lyrics', { key, ...mapped });
      window.__vt.frame(v);
    },
    { v, settings: pick(settings), key, title: doc.track.title, artist: doc.track.artist, mapped },
  );

  const frames = [];
  let previous = null;
  let seq = 0;
  for (const pos of run.times) {
    // Song time passes between points as it would while playing (forward),
    // so held marks and timers expire on schedule; a backward seek costs 1 s.
    v += previous !== null && pos > previous ? pos - previous : 1000;
    previous = pos;
    const state = await page.evaluate(
      ({ v, t, seq }) => {
        window.__vt.frame(v);
        window.__kashiPush('playback', {
          type: 'seek', seq, sent_at: Date.now(), tab_id: 1, position_ms: t, is_playing: false, captured_at: Date.now(),
        });
        window.__vt.frame(v);
        return window.__readState();
      },
      { v, t: pos - lead, seq: (seq += 1) },
    );
    frames.push({ pos, ...state });

    const sim = simulateAt(R, ctx, pos);
    const simFill = JSON.stringify(sim.fill);
    const realFill = JSON.stringify(Object.fromEntries(Object.entries(state.fill).map(([i, f]) => [i, Number(f)])));
    const agree =
      state.visible === (sim.line >= 0) &&
      (sim.line < 0 ||
        (state.text === sim.text && state.active === sim.active && state.sung.join() === sim.sung.join() && simFill === realFill));
    if (!agree) {
      fail(
        `${run.name} @${pos}: renderer and its pure functions disagree — sim ${JSON.stringify({
          text: sim.text, active: sim.active, sung: sim.sung, fill: sim.fill,
        })} real ${JSON.stringify({ visible: state.visible, text: state.text, active: state.active, sung: state.sung, fill: state.fill })}`,
      );
    }

    if (run.png?.includes(pos)) {
      await page.evaluate(() => window.__vt.painted());
      const rect = await page.evaluate(() => {
        const r = document.getElementById('lyric-box').getBoundingClientRect();
        const x = Math.floor(r.left);
        const y = Math.floor(r.top);
        return { x, y, width: Math.ceil(r.right) - x, height: Math.ceil(r.bottom) - y };
      });
      pictures.set(`${run.name}-${pos}.png`, await page.screenshot(rect));
    }
  }
  const pageErrors = page.logs.filter((l) => l.startsWith('pageerror'));
  for (const line of pageErrors) fail(`${run.name}: ${line}`);
  await page.close();
  return { settings: run.settings, lead_ms: lead, frames, png: (run.png ?? []).map((p) => `${run.name}-${p}.png`) };
}

function compareAll() {
  for (const [name, run] of Object.entries(actual.runs)) {
    const want = expected.runs[name];
    if (!want) {
      fail(`${name}: no golden for this run (--update)`);
      continue;
    }
    if (want.frames.length !== run.frames.length) fail(`${name}: ${run.frames.length} frames, golden has ${want.frames.length}`);
    run.frames.forEach((frame, i) => {
      const golden = want.frames[i];
      if (!golden) return;
      const keys = new Set([...Object.keys(golden), ...Object.keys(frame)]);
      const diffs = [...keys].filter((k) => JSON.stringify(golden[k]) !== JSON.stringify(frame[k]));
      if (diffs.length > 0) {
        fail(
          `${name} @${frame.pos}: ` +
            diffs.map((k) => `${k} ${JSON.stringify(golden[k])} -> ${JSON.stringify(frame[k])}`).join('; '),
        );
      }
    });
    for (const file of run.png) {
      const goldenFile = join(GOLDEN, 'png', file);
      if (!existsSync(goldenFile)) {
        fail(`${file}: no golden picture`);
        continue;
      }
      const result = comparePixels(decodePng(readFileSync(goldenFile)), decodePng(pictures.get(file)), CHANNEL_TOLERANCE);
      if (result.sizeMismatch) fail(`${file}: picture size changed`);
      else if (result.share > MAX_DIFFERING_SHARE) {
        fail(
          `${file}: ${result.differing} pixels differ (${(result.share * 100).toFixed(3)} % > ` +
            `${(MAX_DIFFERING_SHARE * 100).toFixed(3)} %, max channel delta ${result.maxDelta})`,
        );
      }
    }
  }
  for (const [name, d] of Object.entries(actual.dwell)) {
    const want = expected.dwell[name];
    if (!want) {
      fail(`dwell ${name}: no pinned numbers`);
      continue;
    }
    if (JSON.stringify(want.summary) !== JSON.stringify(d.summary)) {
      fail(`dwell ${name}: pinned ${JSON.stringify(want.summary)} -> now ${JSON.stringify(d.summary)}`);
    }
    if (JSON.stringify(want.lit) !== JSON.stringify(d.lit)) {
      fail(`dwell ${name}: per-word lit times changed: ${JSON.stringify(want.lit)} -> ${JSON.stringify(d.lit)}`);
    }
  }
  const stale = existsSync(join(GOLDEN, 'png'))
    ? readdirSync(join(GOLDEN, 'png')).filter((f) => !args.only && !pictures.has(f))
    : [];
  for (const f of stale) fail(`${f}: golden picture no run produces (remove it or --update)`);
}
