// A minimal Chrome DevTools Protocol driver over --remote-debugging-pipe.
//
// Why not Playwright: playwright-core is not a dependency of this repository
// (audit-strip.mjs borrows a scratch install), and the dependency policy keeps
// new packages out without a human. Chromium itself ships in the pinned
// Playwright image (mcr.microsoft.com/playwright:v1.63.0-noble), and the pipe
// transport needs nothing but child_process: NUL-terminated JSON on fd 3 (to
// the browser) and fd 4 (from it). Only the handful of commands the golden
// harness and the clip tools use are wrapped here.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/** The headless shell inside mcr.microsoft.com/playwright:v1.63.0-noble. */
export const IMAGE_CHROMIUM =
  '/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell';

// Software GL (as audit-strip), sRGB output and greyscale anti-aliasing: the
// flags that make two runs of the same image paint the same pixels.
const DEFAULT_ARGS = [
  '--remote-debugging-pipe',
  '--no-first-run',
  '--no-default-browser-check',
  '--no-sandbox',
  '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader',
  '--force-color-profile=srgb',
  '--disable-lcd-text',
  '--hide-scrollbars',
  '--mute-audio',
  '--disable-field-trial-config',
  '--disable-background-timer-throttling',
  '--disable-backgrounding-occluded-windows',
  '--disable-renderer-backgrounding',
  '--disable-dev-shm-usage',
];

export class Browser {
  /** @param {{executable?: string, args?: string[]}} [opts] */
  static async launch(opts = {}) {
    const profile = mkdtempSync(join(tmpdir(), 'kashi-cdp-'));
    const child = spawn(
      opts.executable ?? IMAGE_CHROMIUM,
      [...DEFAULT_ARGS, ...(opts.args ?? []), `--user-data-dir=${profile}`, 'about:blank'],
      { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] },
    );
    const browser = new Browser(child, profile);
    await browser.send('Browser.getVersion');
    return browser;
  }

  constructor(child, profile) {
    this.child = child;
    this.profile = profile;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Set();
    this.stderr = '';
    this.exited = false;
    child.stderr.on('data', (chunk) => {
      this.stderr = (this.stderr + chunk).slice(-4000);
    });
    let buffer = Buffer.alloc(0);
    child.stdio[4].on('data', (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      let end;
      while ((end = buffer.indexOf(0)) >= 0) {
        const message = JSON.parse(buffer.subarray(0, end).toString('utf8'));
        buffer = buffer.subarray(end + 1);
        this.dispatch(message);
      }
    });
    child.on('exit', (code) => {
      this.exited = true;
      for (const { reject } of this.pending.values()) {
        reject(new Error(`chromium exited (${code}): ${this.stderr.slice(-800)}`));
      }
      this.pending.clear();
    });
  }

  dispatch(message) {
    if (message.id !== undefined) {
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error) waiter.reject(new Error(`${waiter.method}: ${message.error.message}`));
      else waiter.resolve(message.result);
      return;
    }
    for (const listener of this.listeners) listener(message);
  }

  /** One CDP command; `sessionId` targets a page session. */
  send(method, params = {}, sessionId = undefined) {
    if (this.exited) return Promise.reject(new Error('chromium is gone'));
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) });
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method });
      this.child.stdio[3].write(payload + '\0');
    });
  }

  /** A fresh tab with its own session. */
  async newPage({ width, height, deviceScaleFactor = 1 }) {
    const { targetId } = await this.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await this.send('Target.attachToTarget', { targetId, flatten: true });
    const page = new Page(this, sessionId, targetId);
    await page.send('Page.enable');
    await page.send('Runtime.enable');
    await page.send('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor,
      mobile: false,
    });
    return page;
  }

  async close() {
    if (!this.exited) {
      const gone = new Promise((ok) => this.child.once('exit', ok));
      this.send('Browser.close').catch(() => {});
      await Promise.race([gone, new Promise((ok) => setTimeout(ok, 3000))]);
      if (!this.exited) this.child.kill('SIGKILL');
    }
    rmSync(this.profile, { recursive: true, force: true });
  }
}

export class Page {
  constructor(browser, sessionId, targetId) {
    this.browser = browser;
    this.sessionId = sessionId;
    this.targetId = targetId;
    /** Console lines and uncaught exceptions, in arrival order. */
    this.logs = [];
    browser.listeners.add((message) => {
      if (message.sessionId !== this.sessionId) return;
      if (message.method === 'Runtime.consoleAPICalled') {
        const text = message.params.args.map((a) => a.value ?? a.description ?? '').join(' ');
        this.logs.push(`${message.params.type}: ${text}`);
      } else if (message.method === 'Runtime.exceptionThrown') {
        const details = message.params.exceptionDetails;
        this.logs.push(`pageerror: ${details.exception?.description ?? details.text}`);
      }
    });
  }

  send(method, params = {}) {
    return this.browser.send(method, params, this.sessionId);
  }

  /** Runs `source` in every new document of this page, before its own scripts. */
  addInitScript(source) {
    return this.send('Page.addScriptToEvaluateOnNewDocument', { source });
  }

  async goto(url) {
    const loaded = new Promise((ok) => {
      const listener = (message) => {
        if (message.sessionId === this.sessionId && message.method === 'Page.loadEventFired') {
          this.browser.listeners.delete(listener);
          ok();
        }
      };
      this.browser.listeners.add(listener);
    });
    const result = await this.send('Page.navigate', { url });
    if (result.errorText) throw new Error(`navigate ${url}: ${result.errorText}`);
    await loaded;
  }

  /**
   * Evaluates `fn` (a function, serialised by source) with one JSON argument
   * in the page and returns its JSON result; a returned promise is awaited.
   */
  async evaluate(fn, arg = undefined) {
    const expression = `(${fn.toString()})(${JSON.stringify(arg) ?? 'undefined'})`;
    const { result, exceptionDetails } = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (exceptionDetails) {
      throw new Error(`evaluate: ${exceptionDetails.exception?.description ?? exceptionDetails.text}`);
    }
    return result.value;
  }

  /** PNG bytes of a CSS-pixel rectangle of the page. */
  async screenshot(clip) {
    const { data } = await this.send('Page.captureScreenshot', {
      format: 'png',
      clip: { ...clip, scale: 1 },
      captureBeyondViewport: false,
    });
    return Buffer.from(data, 'base64');
  }

  async close() {
    await this.browser.send('Target.closeTarget', { targetId: this.targetId });
  }
}
