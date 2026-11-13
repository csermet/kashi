// Old build against new build of the renderer: the same documents, settings
// and events through both, a full snapshot after every step, compared byte
// for byte after a short, declared normalisation. Built for the packages/view
// extraction (client review step E.4): a refactor is proven by running the
// old and the new side by side, not by reading the diff.
//
// A snapshot is far wider than the golden harness's state: every element under
// <body> (tag, attributes, class order, inline style in order, ~60 computed
// properties, every Kashi custom property as the element resolves it, its
// rect), the box's ::before / ::after rings, <html> and <body> themselves, the
// renderer's log lines since the last step, and a picture of the whole window
// (compared exactly: 0 differing pixels).
//
// Normalisation, and nothing else:
//   - text nodes that are only whitespace are skipped (the old page's markup
//     came from index.html with indentation, the new view builds its DOM);
//   - <html>'s inline style is compared as a sorted map, without
//     --kashi-stage-padding; <div id="stage">'s inline style without padding-*.
//     The window geometry moved from an inline padding to a host-supplied
//     variable — the stage's COMPUTED padding and every rect are still compared;
//   - the particle <canvas> is left out of the tree and hidden in pictures: it
//     loads Pixi asynchronously and runs its own ticker, so when it exists is
//     not deterministic. Its "fx layer" log lines are compared per scenario as
//     a set of kinds (numbers blanked) — a weaker check, declared; the layer's
//     rules have their own unit tests (fx-canvas, fx-particles-logic).
//
// Transitions run at zero duration (their declared durations are still
// compared, in a second pass) and animations are paused at their first
// keyframe, so a snapshot is deterministic; the page runs on the golden
// harness's virtual clock. The one handler on a real-time event, the box
// halo's animationend, is therefore not exercised (nor by the golden harness).
//
//   node scripts/view-differential.mjs --old <renderer dir> --new <renderer dir>
//        [--only <scenario>] [--report-dir /tmp/kashi-view-diff] [--chromium <path>]
//        [--no-pictures]
// Runs in the pinned Playwright image like the golden harness
// (view-differential.sh). Exit 0 = identical, 1 = a difference (first ones
// printed, both snapshots written to the report dir), 2 = setup error.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { crc32, deflateSync } from 'node:zlib';
import { Browser, IMAGE_CHROMIUM } from './lib/cdp.mjs';
import { PAGE_INIT_SOURCE } from './lib/page-shim.mjs';
import { comparePixels, decodePng } from './lib/png.mjs';
import { loadRendererSrc } from './lib/renderer-src.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OVERLAY = resolve(HERE, '..');
const GOLDEN = join(OVERLAY, 'test/golden');
const VIEWPORT = { width: 800, height: 460, deviceScaleFactor: 1 };

const { values: args } = parseArgs({
  options: {
    old: { type: 'string' },
    new: { type: 'string' },
    only: { type: 'string' },
    chromium: { type: 'string', default: IMAGE_CHROMIUM },
    'report-dir': { type: 'string', default: '/tmp/kashi-view-diff' },
    'no-pictures': { type: 'boolean', default: false },
  },
});
if (!args.old || !args.new) {
  console.error('need --old <renderer dir> and --new <renderer dir>');
  process.exit(2);
}
for (const dir of [args.old, args.new]) {
  if (!existsSync(join(dir, 'index.html'))) {
    console.error(`no built renderer at ${dir}`);
    process.exit(2);
  }
}

const started = Date.now();
const R = await loadRendererSrc(OVERLAY);

// ---------------------------------------------------------------- documents

const goldenDoc = (name) => JSON.parse(readFileSync(join(GOLDEN, 'docs', `${name}.json`), 'utf8'));

/** Words spread evenly over [start, start + span) with a small gap after each. */
function words(texts, start, span) {
  const step = Math.floor(span / texts.length);
  return texts.map((text, i) => ({
    text,
    start_ms: start + i * step,
    end_ms: start + i * step + Math.floor(step * 0.85),
  }));
}

/**
 * Everything the golden documents leave out: beats, energy, sections, a
 * nightcore speed factor, line themes (the ambient ring), several fx words on
 * one line, a plain line between timed ones, an interlude, the outro.
 */
function dynamicsDoc() {
  const line = (text, start, end, extra = {}) => ({
    start_ms: start,
    end_ms: end,
    text,
    ...(extra.plain ? {} : { words: words(text.replace(/[()]/g, '').split(' '), start, end - start) }),
    ...(extra.adlib ? { adlib: true } : {}),
    ...(extra.uncertain ? { uncertain: true } : {}),
  });
  const beats = [];
  for (let t = 500; t <= 26000; t += 480) beats.push(t);
  const energy = [];
  for (let i = 0; i < 56; i += 1) energy.push(Math.round(50 + 40 * Math.sin(i / 3)));
  return {
    schema_version: 1,
    pipeline_version: 'synthetic',
    generated_at: '2026-11-14T00:00:00Z',
    track: { source: { type: 'youtube', id: 'diffdyn0001' }, title: 'Dynamics', artist: 'Differential', duration_ms: 27000 },
    sync: 'word',
    alignment: { method: 'synthetic/differential', lyrics_source: 'synthetic', quality_score: 0.9, speed_factor: 1.25 },
    palette: { source: 'album_art', primary: '#36c98e', secondary: '#1f7a56', background: '#0d2018', text: '#ffffff', accent: '#ff5d8f' },
    beats: { bpm: 125, confidence: 0.8, times_ms: beats, downbeat_indices: beats.map((_, i) => i).filter((i) => i % 4 === 0) },
    energy: { rate_hz: 2, values: energy },
    sections: [
      { type: 'high', start_ms: 12000, end_ms: 16000 },
      { type: 'chorus', start_ms: 22000, end_ms: 25000 },
    ],
    fx: {
      lexicon: 'kashi-fx/1.2.0',
      engine: 'keywords',
      select: 'density/1.0',
      words: [
        { line: 0, word: 1, tag: 'love', intensity: 0.7 },
        { line: 4, word: 0, tag: 'explosion', intensity: 0.9 },
        { line: 4, word: 3, tag: 'money', intensity: 0.5 },
        { line: 5, word: 1, tag: 'a-newer-tag', intensity: 0.6 },
      ],
      lines: [
        { line: 0, tag: 'water' },
        { line: 4, tag: 'storm' },
      ],
    },
    lines: [
      line('neon kiru sala vento', 1000, 3600),
      line('(ooh ooh ooh)', 4000, 6400, { adlib: true }),
      line('dara misu holen', 6800, 9800, { uncertain: true }),
      line('spoken words between', 10200, 12000, { plain: true }),
      line('neon kiru sala vento', 12400, 15800),
      line('taro fiyu', 22000, 24500),
    ],
  };
}

const DOCS = {
  'a-basic': goldenDoc('a-basic'),
  'b-held': goldenDoc('b-held'),
  'c-fx': goldenDoc('c-fx'),
  'd-dynamics': dynamicsDoc(),
};

/** Every position a display rule turns on, exactly and one either side. */
function boundaryTimes(mapped) {
  const set = new Set();
  const add = (t) => {
    for (const d of [-1, 0, 1]) if (t + d >= 0) set.add(t + d);
  };
  for (const line of mapped.lines) {
    add(line.start_ms);
    add(line.end_ms);
    add(line.start_ms - R.view.LINE_LEAD_IN_MS);
    add(line.start_ms - R.view.LINE_LEAD_MIN_MS);
    add(line.end_ms + R.view.INTERLUDE_GAP_MS);
    for (const w of line.words ?? []) {
      add(w.start_ms);
      add(w.end_ms);
      set.add(Math.round((w.start_ms + w.end_ms) / 2));
    }
  }
  return [...set].sort((a, b) => a - b);
}

// ------------------------------------------------------------- scenarios

const DEFAULT_SETTINGS = (() => {
  const { box_alpha, timing_offset_rel_ms, effect_level, theme_scope, text_scale, box_scale } = R.DEFAULT_SETTINGS;
  return { box_alpha, timing_offset_rel_ms, effect_level, theme_scope, text_scale, box_scale };
})();

function keyOf(doc) {
  return `${doc.track.source.type}:${doc.track.source.id}`;
}

/** Load a document under `settings`: what main sends on a server hit. */
function loadSteps(docName, settings) {
  const doc = DOCS[docName];
  const mapped = R.mapDocument(doc);
  if (!mapped?.found) throw new Error(`${docName}: mapDocument rejected it`);
  return [
    { settings: { ...DEFAULT_SETTINGS, ...settings } },
    { connection: true },
    { track: { key: keyOf(doc), track: { title: doc.track.title, artist: doc.track.artist } } },
    { lyrics: { key: keyOf(doc), ...mapped } },
  ];
}

function sweepScenario(docName, settings, { every = 0 } = {}) {
  const mapped = R.mapDocument(DOCS[docName]);
  let times = boundaryTimes(mapped);
  if (every > 0) times = times.filter((_, i) => i % every === 0);
  const lead = R.appliedLeadMs(settings.timing_offset_rel_ms ?? 0);
  return [...loadSteps(docName, settings), ...times.map((t) => ({ seek: t - lead }))];
}

function playScenario(docName, settings, fromMs, toMs, { snapEvery = 5 } = {}) {
  const lead = R.appliedLeadMs(settings.timing_offset_rel_ms ?? 0);
  return [...loadSteps(docName, settings), { seek: fromMs - lead }, { play: { from: fromMs - lead, to: toMs - lead, snapEvery } }];
}

/** Live changes the way the tray and the scroll gestures make them. */
function lifecycleScenario() {
  const c = DOCS['c-fx'];
  const d = DOCS['d-dynamics'];
  const cKey = keyOf(c);
  const dKey = keyOf(d);
  const s = (patch) => ({ settings: { ...DEFAULT_SETTINGS, effect_level: 'hype', ...patch } });
  return [
    s({}),
    { connection: true },
    { advance: 400 },
    // The image decode runs in real time: the step waits for it on both sides.
    {
      track: { key: 'youtube:serverless01', track: { title: 'Art', artist: 'Only', artwork_url: 'https://art.kashi.test/cover.png' } },
      waitLog: 'artwork palette applied',
    },
    { lyrics: { key: 'youtube:serverless01', searching: true } },
    { lyrics: { key: 'youtube:serverless01', searching: true, retrying: true } },
    { lyrics: { key: 'youtube:serverless01', found: false, syncing: true } },
    { lyrics: { key: 'youtube:serverless01', found: false, error: true } },
    { lyrics: { key: 'youtube:serverless01', found: false } },
    { track: { key: cKey, track: { title: c.track.title, artist: c.track.artist } } },
    { lyrics: { key: cKey, ...R.mapDocument(c) } },
    { seek: 2300 - 200 },
    { advance: 300 },
    s({ theme_scope: 'none' }), // mid-line, fx word lit: the inline tint must follow
    s({ theme_scope: 'full' }),
    s({ timing_offset_rel_ms: 120 }), // live: the readout flashes
    { advance: 950 },
    s({ timing_offset_rel_ms: 120, box_scale: 'large' }), // live: reveal + readout
    { advance: 200 },
    s({ timing_offset_rel_ms: 120, box_scale: 'large', text_scale: 'small' }),
    { advance: 1000 },
    s({ timing_offset_rel_ms: 120, box_scale: 'small', text_scale: 'large', box_alpha: 0.6 }),
    { seek: 6400 - 320 },
    s({ timing_offset_rel_ms: 120, effect_level: 'full', box_scale: 'small', text_scale: 'large' }),
    s({ timing_offset_rel_ms: 120, effect_level: 'off' }),
    s({ timing_offset_rel_ms: 120, effect_level: 'simple' }),
    s({ timing_offset_rel_ms: 0, effect_level: 'hype' }),
    { ad: true },
    { advance: 500 },
    { ad: false },
    { seek: 9500 - 200 },
    { sourceGone: true },
    { advance: 100 },
    // Re-announce of the track it was showing: the idle stash puts it back.
    { track: { key: cKey, track: { title: c.track.title, artist: c.track.artist } } },
    { seek: 9500 - 200 },
    { connection: false },
    { connection: true },
    { track: { key: dKey, track: { title: d.track.title, artist: d.track.artist } } },
    { lyrics: { key: dKey, ...R.mapDocument(d) } },
    { seek: 13000 - 200 },
    { track: { key: dKey, track: { title: d.track.title, artist: d.track.artist } } }, // same-track re-announce
    { seek: 13000 - 200 },
    { lyrics: { key: 'youtube:stale', found: false } }, // stale key: ignored
    { play: { from: 12900, to: 16200, snapEvery: 4 } },
    { pause: 16200 },
    { advance: 61000 }, // watchdog leash is for PLAYING clocks: paused stays
    { play: { from: 16200, to: 16800, snapEvery: 8 } },
    { starve: 61000 }, // playing with no reports: the data-loss watchdog
  ];
}

const SCENARIOS = [];
for (const doc of Object.keys(DOCS)) {
  for (const level of ['off', 'simple', 'full', 'hype']) {
    for (const scope of ['full', 'none']) {
      const thorough = scope === 'full' && (level === 'hype' || level === 'simple');
      SCENARIOS.push({
        name: `sweep-${doc}-${level}-${scope}`,
        pictureEvery: 4,
        steps: sweepScenario(doc, { effect_level: level, theme_scope: scope }, { every: thorough ? 0 : 3 }),
      });
    }
  }
}
for (const [box, text] of [['small', 'large'], ['large', 'small']]) {
  SCENARIOS.push({
    name: `sweep-a-basic-hype-box-${box}-text-${text}`,
    pictureEvery: 2,
    steps: sweepScenario('a-basic', { effect_level: 'hype', box_scale: box, text_scale: text, box_alpha: 0.9 }, { every: 2 }),
  });
}
SCENARIOS.push({ name: 'sweep-b-held-hype-offset', pictureEvery: 2, steps: sweepScenario('b-held', { effect_level: 'hype', timing_offset_rel_ms: -250 }, { every: 2 }) });
for (const level of ['full', 'hype']) {
  SCENARIOS.push({ name: `play-d-dynamics-${level}`, steps: playScenario('d-dynamics', { effect_level: level }, 0, 26500) });
}
SCENARIOS.push({ name: 'play-c-fx-hype', steps: playScenario('c-fx', { effect_level: 'hype' }, 1000, 11500, { snapEvery: 3 }) });
SCENARIOS.push({ name: 'play-d-dynamics-hype-reduced-motion', reducedMotion: true, steps: playScenario('d-dynamics', { effect_level: 'hype' }, 0, 17000) });
SCENARIOS.push({ name: 'lifecycle', steps: lifecycleScenario() });

// ------------------------------------------------------------- the pages

// Transitions at zero duration (the settled state), animations paused at their
// first keyframe (deterministic, and no animationend arrives on its own real-
// time schedule). Canvas hidden in pictures (Pixi, own ticker).
const DIFF_CSS =
  '*,*::before,*::after{transition-duration:0s!important;transition-delay:0s!important;' +
  'animation-play-state:paused!important;animation-delay:0s!important}canvas{visibility:hidden!important}';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json' };

function serve(dir) {
  const root = resolve(dir);
  const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/__diff.css') {
      res.writeHead(200, { 'content-type': 'text/css' });
      return res.end(DIFF_CSS);
    }
    const file = join(root, url.pathname === '/' ? 'index.html' : url.pathname);
    if (!file.startsWith(root) || !existsSync(file)) {
      res.writeHead(404);
      return res.end();
    }
    res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' });
    res.end(readFileSync(file));
  });
  return new Promise((ok) => server.listen(0, '127.0.0.1', () => ok({ server, base: `http://127.0.0.1:${server.address().port}` })));
}

/** A 4x4 two-colour PNG: the "album art" behind the serverless palette. */
function artworkPng() {
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(td) >>> 0);
    return Buffer.concat([len, td, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(4, 0);
  ihdr.writeUInt32BE(4, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  const rows = [];
  for (let y = 0; y < 4; y += 1) {
    const row = [0];
    for (let x = 0; x < 4; x += 1) row.push(...(x + y < 5 ? [200, 60, 140] : [40, 120, 220]));
    rows.push(Buffer.from(row));
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.concat(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}
const ARTWORK = artworkPng();

/** In the page: the snapshot. Serialised by source, so it must stay self-contained. */
function pageSnapshot() {
  const PROPS = [
    'display', 'visibility', 'position', 'z-index', 'box-sizing', 'color', 'opacity', 'transform',
    'background-color', 'background-image', 'background-clip', '-webkit-text-fill-color', 'text-shadow',
    'box-shadow', 'font-family', 'font-size', 'font-weight', 'font-style', 'line-height', 'letter-spacing',
    'text-align', 'vertical-align', 'white-space', 'overflow-wrap', 'padding-top', 'padding-right',
    'padding-bottom', 'padding-left', 'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
    'border-top-left-radius', 'border-bottom-right-radius', 'max-width', 'width', 'height', 'overflow-x',
    'overflow-y', '-webkit-line-clamp', '-webkit-box-orient', 'cursor', 'pointer-events', 'user-select',
    'will-change', 'animation-name', 'animation-timing-function', 'animation-iteration-count',
    'animation-fill-mode', 'transition-property', 'transition-timing-function', 'gap', 'justify-content',
    'align-items', 'justify-items', 'grid-template-columns', 'content', 'top', 'right', 'bottom', 'left',
    'fill', 'transform-origin',
  ];
  const TIMING = ['transition-duration', 'transition-delay', 'animation-duration', 'animation-delay'];
  const VARS = [
    '--kashi-box-alpha', '--kashi-primary', '--kashi-secondary', '--kashi-bg-rgb', '--kashi-text',
    '--kashi-plain', '--kashi-accent', '--kashi-text-scale', '--kashi-box-max-width', '--kashi-box-pad',
    '--kashi-box-radius', '--kashi-energy', '--kashi-fill', '--kashi-pop-cap', '--kashi-adlib-fade',
    '--kashi-uncertain-fade', '--kashi-clip-room', '--kashi-italic-overhang', '--fx-color', '--fx-intensity',
    '--fx-spring', '--fx-ambient', '--fx-ambient-flash',
  ];
  const px = (v) => Math.round(v * 100) / 100;
  const computed = (el, pseudo) => {
    const cs = getComputedStyle(el, pseudo);
    const out = {};
    for (const p of PROPS) out[p] = cs.getPropertyValue(p);
    for (const v of VARS) {
      const value = cs.getPropertyValue(v);
      if (value !== '') out[v] = value;
    }
    return out;
  };
  const inline = (el, drop, sort) => {
    const entries = [];
    for (let i = 0; i < el.style.length; i += 1) {
      const p = el.style[i];
      if (drop(p)) continue;
      entries.push([p, el.style.getPropertyValue(p) + (el.style.getPropertyPriority(p) ? ' !important' : '')]);
    }
    if (sort) entries.sort(([a], [b]) => (a < b ? -1 : 1));
    return entries;
  };
  const elements = [];
  const node = (n) => {
    if (n.nodeType === 3) return n.textContent.trim() === '' ? null : { text: n.textContent };
    if (n.nodeType !== 1) return null;
    const el = n;
    const tag = el.tagName.toLowerCase();
    if (tag === 'script' || tag === 'canvas') return null;
    elements.push(el);
    const attrs = [...el.attributes]
      .filter((a) => a.name !== 'style' && a.name !== 'class')
      .map((a) => [a.name, a.value]);
    const r = el.getBoundingClientRect();
    const out = {
      tag,
      attrs,
      classes: [...el.classList],
      style: inline(el, (p) => el.id === 'stage' && p.startsWith('padding'), false),
      computed: computed(el),
      rect: [px(r.x), px(r.y), px(r.width), px(r.height)],
      children: [...el.childNodes].map(node).filter((c) => c !== null),
    };
    if (el.id === 'lyric-box') {
      out.before = computed(el, '::before');
      out.after = computed(el, '::after');
    }
    return out;
  };
  const html = document.documentElement;
  const snap = {
    html: {
      classes: [...html.classList],
      style: inline(html, (p) => p === '--kashi-stage-padding', true),
      computed: computed(html),
    },
    body: {
      classes: [...document.body.classList],
      style: inline(document.body, () => false, false),
      computed: computed(document.body),
    },
    tree: [...document.body.childNodes].map(node).filter((c) => c !== null),
  };
  // Declared timing, read with the zero-duration sheet off for a moment.
  const sheet = [...document.styleSheets].find((s) => s.href?.endsWith('/__diff.css'));
  if (sheet) sheet.disabled = true;
  snap.timing = elements.map((el) => {
    const cs = getComputedStyle(el);
    return TIMING.map((p) => cs.getPropertyValue(p)).join('|');
  });
  const box = document.getElementById('lyric-box');
  if (box) {
    snap.timing.push(...['::before', '::after'].map((p) => TIMING.map((t) => getComputedStyle(box, p).getPropertyValue(t)).join('|')));
  }
  if (sheet) sheet.disabled = false;
  const log = window.__kashiLog.splice(0);
  snap.log = log.filter((l) => !l.startsWith('fx layer'));
  window.__fxLayerLog = (window.__fxLayerLog ?? []).concat(log.filter((l) => l.startsWith('fx layer')));
  return JSON.stringify(snap);
}

class Side {
  constructor(name, browser, base) {
    this.name = name;
    this.browser = browser;
    this.base = base;
  }

  async open(scenario) {
    this.page = await this.browser.newPage(VIEWPORT);
    if (scenario.reducedMotion) {
      await this.page.send('Emulation.setEmulatedMedia', {
        features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
      });
    }
    // The serverless palette's artwork: served at an https URL the page may
    // read back (CORS), without a network.
    await this.page.send('Fetch.enable', { patterns: [{ urlPattern: 'https://art.kashi.test/*' }] });
    this.browser.listeners.add((m) => {
      if (m.sessionId !== this.page.sessionId || m.method !== 'Fetch.requestPaused') return;
      void this.page.send('Fetch.fulfillRequest', {
        requestId: m.params.requestId,
        responseCode: 200,
        responseHeaders: [
          { name: 'content-type', value: 'image/png' },
          { name: 'access-control-allow-origin', value: '*' },
        ],
        body: ARTWORK.toString('base64'),
      });
    });
    await this.page.addInitScript(PAGE_INIT_SOURCE);
    await this.page.goto(this.base + '/');
    await this.page.evaluate(
      () =>
        new Promise((ok, fail) => {
          const link = document.createElement('link');
          link.rel = 'stylesheet';
          link.href = '/__diff.css';
          link.onload = () => ok(true);
          link.onerror = () => fail(new Error('diff stylesheet refused'));
          document.head.appendChild(link);
        }),
    );
    this.v = await this.page.evaluate(() => window.__vt.now());
    this.seq = 0;
  }

  /** Runs one step; returns the snapshots it takes (most steps take one). */
  async step(step) {
    const p = this.page;
    const snaps = [];
    const snapshot = async () => snaps.push(await p.evaluate(pageSnapshot));
    const push = (name, payload) =>
      p.evaluate(({ name, payload, v }) => {
        window.__kashiPush(name, payload);
        window.__vt.frame(v);
      }, { name, payload, v: this.v });
    const playback = (msg) => push('playback', { seq: (this.seq += 1), sent_at: 0, tab_id: 1, ...msg });
    const now = () => p.evaluate(() => Date.now());
    if (step.settings) await push('settings', step.settings);
    else if (step.connection !== undefined) await push('connection', { connected: step.connection });
    else if (step.track) await push('track', step.track);
    else if (step.lyrics) await push('lyrics', step.lyrics);
    else if (step.sourceGone) await push('sourceGone', {});
    else if (step.ad !== undefined) await playback({ type: 'ad_state', is_ad: step.ad });
    else if (step.seek !== undefined) {
      this.v += 1000;
      await p.evaluate((v) => window.__vt.frame(v), this.v);
      await playback({ type: 'seek', position_ms: step.seek, is_playing: false, captured_at: await now() });
    } else if (step.pause !== undefined) {
      await playback({ type: 'playback_state', position_ms: step.pause, is_playing: false, captured_at: await now() });
    } else if (step.advance !== undefined || step.starve !== undefined) {
      const until = this.v + (step.advance ?? step.starve);
      while (this.v < until) {
        this.v = Math.min(until, this.v + 250);
        await p.evaluate((v) => window.__vt.frame(v), this.v);
      }
    } else if (step.play) {
      const { from, to, snapEvery } = step.play;
      // 60 Hz frames on the virtual clock; a position report every 250 ms,
      // captured 12 ms before it arrives (transport delay, as in the field).
      let frame = 0;
      for (let t = from; t <= to; t += 16) {
        this.v += 16;
        if (frame % 16 === 0) {
          await p.evaluate(({ v }) => window.__vt.frame(v - 12), { v: this.v });
          const capturedAt = await now();
          await playback({ type: 'position', position_ms: t - 12, playback_rate: 1, is_playing: true, captured_at: capturedAt });
        } else {
          await p.evaluate((v) => window.__vt.frame(v), this.v);
        }
        if (frame % snapEvery === 0) await snapshot();
        frame += 1;
      }
      return snaps;
    } else {
      throw new Error(`unknown step ${JSON.stringify(step)}`);
    }
    if (step.waitLog) {
      // Real time: an image decode the virtual clock does not drive.
      const deadline = Date.now() + 5000;
      let seen = false;
      while (!seen && Date.now() < deadline) {
        seen = await p.evaluate((text) => window.__kashiLog.some((l) => l.includes(text)), step.waitLog);
        if (!seen) await new Promise((ok) => setTimeout(ok, 20));
      }
      if (!seen) throw new Error(`${this.name}: no "${step.waitLog}" within 5 s`);
      this.v += 16;
      await p.evaluate((v) => window.__vt.frame(v), this.v);
    }
    await snapshot();
    return snaps;
  }

  async picture() {
    await this.page.evaluate(() => window.__vt.painted());
    return this.page.screenshot({ x: 0, y: 0, width: VIEWPORT.width, height: VIEWPORT.height });
  }
}

// ------------------------------------------------------------- run

const oldSrv = await serve(args.old);
const newSrv = await serve(args.new);
const browser = await Browser.launch({ executable: args.chromium });
const { product } = await browser.send('Browser.getVersion');
const report = args['report-dir'];
const failures = [];
let snapshots = 0;
let bytes = 0;
let pictures = 0;

try {
  for (const scenario of SCENARIOS.filter((s) => !args.only || s.name === args.only)) {
    const a = new Side('old', browser, oldSrv.base);
    const b = new Side('new', browser, newSrv.base);
    await a.open(scenario);
    await b.open(scenario);
    let diverged = false;
    for (const [i, step] of scenario.steps.entries()) {
      const sa = await a.step(step);
      const sb = await b.step(step);
      for (let k = 0; k < Math.max(sa.length, sb.length); k += 1) {
        snapshots += 1;
        bytes += (sa[k] ?? '').length;
        if (sa[k] !== sb[k]) {
          diverged = true;
          mkdirSync(report, { recursive: true });
          const tag = `${scenario.name}-step${i}-${k}`;
          writeFileSync(join(report, `${tag}-old.json`), sa[k] ?? 'null');
          writeFileSync(join(report, `${tag}-new.json`), sb[k] ?? 'null');
          failures.push(`${scenario.name} step ${i} (${JSON.stringify(step).slice(0, 80)}) snapshot ${k}: differs — ${firstDifference(sa[k], sb[k])}`);
          break;
        }
      }
      const pictureDue = i % (scenario.pictureEvery ?? 1) === 0 || i === scenario.steps.length - 1;
      if (!diverged && !args['no-pictures'] && !step.play && pictureDue) {
        const [pa, pb] = [await a.picture(), await b.picture()];
        pictures += 1;
        if (!pa.equals(pb)) {
          const cmp = comparePixels(decodePng(pa), decodePng(pb), 0);
          if (cmp.sizeMismatch || cmp.differing > 0) {
            diverged = true;
            mkdirSync(report, { recursive: true });
            writeFileSync(join(report, `${scenario.name}-step${i}-old.png`), pa);
            writeFileSync(join(report, `${scenario.name}-step${i}-new.png`), pb);
            failures.push(`${scenario.name} step ${i}: picture differs (${cmp.differing} pixels)`);
          }
        }
      }
      if (diverged) break;
    }
    const tail = async (side) => {
      const errors = side.page.logs.filter((l) => l.startsWith('pageerror'));
      // Which kinds of thing the particle layer said, numbers blanked: when its
      // asynchronous load lands relative to the bursts is not deterministic.
      const fx = await side.page.evaluate(() =>
        [...new Set((window.__fxLayerLog ?? []).map((l) => l.replace(/\d+/g, '#')))].sort(),
      );
      await side.page.close();
      return { errors, fx };
    };
    const [ta, tb] = [await tail(a), await tail(b)];
    for (const e of [...ta.errors.map((x) => `old: ${x}`), ...tb.errors.map((x) => `new: ${x}`)]) failures.push(`${scenario.name}: ${e}`);
    if (JSON.stringify(ta.fx) !== JSON.stringify(tb.fx)) {
      failures.push(`${scenario.name}: fx layer log differs: ${JSON.stringify(ta.fx)} vs ${JSON.stringify(tb.fx)}`);
    }
    console.log(`${scenario.name}: ${diverged ? 'DIFFERS' : 'identical'} (${scenario.steps.length} steps)`);
  }
} finally {
  await browser.close();
  oldSrv.server.close();
  newSrv.server.close();
}

const seconds = ((Date.now() - started) / 1000).toFixed(1);
if (failures.length > 0) {
  console.error(failures.slice(0, 20).join('\n'));
  console.error(`view differential: ${failures.length} difference(s) in ${seconds} s; snapshots in ${report}`);
  process.exit(1);
}
console.log(
  `view differential: ${snapshots} snapshots (${(bytes / 1e6).toFixed(1)} MB each side) and ${pictures} window pictures, ` +
    `old and new identical (${seconds} s, ${product})`,
);

function firstDifference(a = '', b = '') {
  let i = 0;
  while (i < a.length && a[i] === b[i]) i += 1;
  return `at char ${i}: old …${a.slice(Math.max(0, i - 80), i + 80)}… new …${b.slice(Math.max(0, i - 80), i + 80)}…`;
}
