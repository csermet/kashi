// The shared view's plain-browser demo (packages/view/demo), built with the
// overlay's own vite and checked in headless Chromium: proof that
// @kashi/view runs on a host that is not Electron (client review E.4).
//
// The demo mounts createLyricsView on <body> with a fake host and one
// synthetic document under the desktop's strict CSP. The check seeks it and
// reads the DOM: the line on screen, the active and sung words, the sweep on
// an ad-lib line, the fx word's inline tint; then destroy() must take the
// view's DOM away and leave later pushes harmless. No page error and no CSP
// violation is allowed.
//
//   node scripts/view-demo.mjs [--out <dir>] [--build-only] [--chromium <path>]
//        [--screenshot <png>]   (the page at 5.4 s, hype, the fx word lit)
// Runs inside the pinned Playwright image like the golden harness
// (view-demo.sh). The build lands in packages/view/dist/demo (git-ignored,
// never packaged: electron-builder ships apps/overlay/out only).
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { build } from 'vite';
import { Browser, IMAGE_CHROMIUM } from './lib/cdp.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const VIEW = resolve(HERE, '../../../packages/view');

const { values: args } = parseArgs({
  options: {
    out: { type: 'string', default: join(VIEW, 'dist/demo') },
    'build-only': { type: 'boolean', default: false },
    chromium: { type: 'string', default: IMAGE_CHROMIUM },
    screenshot: { type: 'string' },
  },
});

const started = Date.now();
await build({
  root: join(VIEW, 'demo'),
  base: './',
  logLevel: 'warn',
  configFile: false,
  build: { outDir: args.out, emptyOutDir: true, chunkSizeWarningLimit: 4000 },
});
console.log(`demo built in ${args.out}`);
if (args['build-only']) process.exit(0);

const root = resolve(args.out);
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };
const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
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

const failures = [];
const browser = await Browser.launch({ executable: args.chromium });
try {
  const page = await browser.newPage({ width: 900, height: 500 });
  await page.addInitScript(`(() => {
    window.__violations = [];
    document.addEventListener('securitypolicyviolation', (e) =>
      window.__violations.push(e.violatedDirective + ' ' + e.blockedURI));
  })();`);
  await page.goto(base + '/');
  const frames = () => page.evaluate(() => new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok))));
  /** Seek to a DOCUMENT position (the view adds its 200 ms lead) and read the line. */
  const at = async (docMs) => {
    await page.evaluate((ms) => window.__demo.seek(ms), docMs - 200);
    await frames();
    return page.evaluate(() => {
      const line = document.getElementById('lyric-line');
      const spans = [...line.querySelectorAll('span.word')];
      const idx = (cls) => spans.map((s, i) => (s.classList.contains(cls) ? i : -1)).filter((i) => i >= 0);
      return {
        text: spans.length ? spans.map((s) => s.textContent).join(' ') : line.textContent.trim(),
        lineClasses: [...line.classList].sort().join(' '),
        active: idx('word-active')[0] ?? -1,
        sung: idx('word-sung'),
        fill: idx('word-fill'),
        fx: spans.map((s, i) => (s.classList.contains('fx-word') ? `${i}:${s.style.getPropertyValue('--fx-color')}` : null)).filter(Boolean),
        bodyClasses: document.body.className,
        boxHidden: document.getElementById('lyric-box').classList.contains('hidden'),
      };
    });
  };
  const expect = (label, actual, want) => {
    const a = JSON.stringify(actual);
    const w = JSON.stringify(want);
    if (a !== w) failures.push(`${label}: got ${a}, want ${w}`);
  };

  const s1 = await at(2000);
  expect('2.0 s line', s1.text, 'velo mira tando suvi');
  expect('2.0 s active / sung', [s1.active, s1.sung], [1, [0]]);
  expect('hype classes on body', s1.bodyClasses.split(' ').filter((c) => c.startsWith('fx-')).sort(), ['fx-full', 'fx-hype']);

  const s2 = await at(5400);
  expect('5.4 s line', s2.text, 'kerra lomo amor pesta');
  expect('5.4 s active', s2.active, 2);
  if (s2.fx.length !== 1 || !/^2:#[0-9a-f]{6}$/.test(s2.fx[0])) failures.push(`5.4 s fx word tint: ${JSON.stringify(s2.fx)}`);
  if (args.screenshot) writeFileSync(args.screenshot, await page.screenshot({ x: 0, y: 0, width: 900, height: 500 }));

  const s3 = await at(8000);
  expect('8.0 s ad-lib line', [s3.text, s3.lineClasses.includes('adlib')], ['ooh aah ooh', true]);
  expect('8.0 s ad-lib sweep', [s3.active, s3.fill], [1, [0, 1]]);

  const s4 = await at(30000);
  expect('30 s interlude mark', [s4.text, s4.lineClasses.includes('interlude')], ['♪', true]);

  await page.evaluate(() => window.__demo.setLevel('off'));
  const s5 = await at(2000);
  expect('level off: no fx class on body', s5.bodyClasses.split(' ').filter((c) => c.startsWith('fx-')), ['fx-off']);
  expect('level off: words still lit', s5.active, 1);

  await page.evaluate(() => window.__demo.destroy());
  await frames();
  const gone = await page.evaluate(() => {
    window.__demo.seek(5000);
    return { stage: document.getElementById('stage') !== null, box: document.getElementById('lyric-box') !== null };
  });
  await frames();
  expect('destroy removes the view', gone, { stage: false, box: false });

  const violations = await page.evaluate(() => window.__violations);
  for (const v of violations) failures.push(`CSP violation: ${v}`);
  for (const line of page.logs.filter((l) => l.startsWith('pageerror') || l.startsWith('error'))) failures.push(line);
  const viewLog = await page.evaluate(() => window.__demo.log.slice(0, 3));
  console.log(`view log (first lines): ${viewLog.join(' | ')}`);
  await page.close();
} finally {
  await browser.close();
  server.close();
}

const seconds = ((Date.now() - started) / 1000).toFixed(1);
if (failures.length > 0) {
  console.error(failures.join('\n'));
  console.error(`view demo: ${failures.length} failure(s) in ${seconds} s`);
  process.exit(1);
}
console.log(`view demo: the view runs in a plain browser behind a fake host, all checks passed (${seconds} s)`);
