// Installed in the page before the built renderer runs: a window.kashi shim
// in place of the Electron preload (as audit-strip's real mode), a virtual
// clock, and a DOM state reader.
//
// The virtual clock replaces performance.now, Date, requestAnimationFrame and
// the timers; the caller advances it explicitly (window.__vt.frame). Nothing
// in the renderer then depends on how fast the host happens to run: the 700 ms
// fx hold, the 1 Hz self-heal and the watchdog all see song time, so a golden
// frame is the same on a loaded CI runner as on an idle desktop.

function pageInit() {
  let now = 1000;
  const EPOCH = 1_760_000_000_000;
  const RealDate = Date;
  const realRaf = window.requestAnimationFrame.bind(window);
  class VDate extends RealDate {
    constructor(...a) {
      if (a.length === 0) super(EPOCH + now);
      else super(...a);
    }
    static now() {
      return EPOCH + now;
    }
  }
  window.Date = VDate;
  performance.now = () => now;
  let rafQueue = [];
  let rafId = 0;
  window.requestAnimationFrame = (cb) => {
    rafId += 1;
    rafQueue.push([rafId, cb]);
    return rafId;
  };
  window.cancelAnimationFrame = (id) => {
    rafQueue = rafQueue.filter(([i]) => i !== id);
  };
  const timers = new Map();
  let timerId = 0;
  const addTimer = (fn, ms, argv, every) => {
    timerId += 1;
    timers.set(timerId, { at: now + Math.max(0, Number(ms) || 0), fn, argv, every });
    return timerId;
  };
  window.setTimeout = (fn, ms, ...argv) => addTimer(fn, ms, argv, 0);
  window.setInterval = (fn, ms, ...argv) => addTimer(fn, ms, argv, Math.max(1, Number(ms) || 0));
  window.clearTimeout = (id) => timers.delete(id);
  window.clearInterval = (id) => timers.delete(id);
  const runTimersTo = (t) => {
    for (let guard = 0; guard < 10000; guard += 1) {
      let best = null;
      for (const [id, timer] of timers) if (timer.at <= t && (!best || timer.at < best[1].at)) best = [id, timer];
      if (!best) break;
      const [id, timer] = best;
      now = Math.max(now, timer.at);
      if (timer.every) timer.at += timer.every;
      else timers.delete(id);
      try {
        if (typeof timer.fn === 'function') timer.fn(...timer.argv);
      } catch (e) {
        console.error('timer: ' + e);
      }
    }
    now = Math.max(now, t);
  };
  window.__vt = {
    now: () => now,
    /** Advance to `t`: due timers first, then one animation frame at exactly `t`. */
    frame(t) {
      runTimersTo(t);
      const queue = rafQueue;
      rafQueue = [];
      for (const [, cb] of queue) {
        try {
          cb(now);
        } catch (e) {
          console.error('raf: ' + e);
        }
      }
    },
    /** Resolves after the browser has really painted (screenshots wait on this). */
    painted: () => new Promise((ok) => realRaf(() => realRaf(() => ok()))),
  };
  window.__readState = () => {
    const box = document.getElementById('lyric-box');
    const line = document.getElementById('lyric-line');
    const spans = [...line.querySelectorAll('span.word')];
    const idx = (cls) => spans.map((s, i) => (s.classList.contains(cls) ? i : -1)).filter((i) => i >= 0);
    const fill = {};
    spans.forEach((s, i) => {
      const value = s.style.getPropertyValue('--kashi-fill');
      if (value !== '') fill[i] = value;
    });
    return {
      visible:
        !box.classList.contains('hidden') &&
        !line.classList.contains('interlude') &&
        spans.length + (line.textContent ?? '').trim().length > 0 &&
        !line.classList.contains('dim'),
      text: spans.length ? spans.map((s) => s.textContent).join(' ') : (line.textContent ?? '').trim(),
      active: idx('word-active')[0] ?? -1,
      sung: idx('word-sung'),
      word_fill: idx('word-fill'),
      fill,
      fx_fired: idx('fx-fired')[0] ?? null,
      line_classes: [...line.classList].filter((c) => c !== 'lyric').sort(),
      box_classes: [...box.classList].sort(),
      // The dev-only chain-latency marker (KASHI_LATENCY_MARKER=1 builds):
      // a normal build must never draw it.
      latency_marker: document.getElementById('latency-marker') !== null,
    };
  };
  const callbacks = {};
  const on = (name) => (cb) => {
    (callbacks[name] ??= []).push(cb);
    return () => {};
  };
  window.__kashiPush = (name, payload) => (callbacks[name] ?? []).forEach((cb) => cb(payload));
  window.__kashiLog = [];
  const noop = () => {};
  window.kashi = {
    version: 'golden',
    onTrack: on('track'),
    onLyrics: on('lyrics'),
    onPlayback: on('playback'),
    onSettings: on('settings'),
    onConnection: on('connection'),
    onSourceGone: on('sourceGone'),
    setInteractive: noop,
    dragStart: noop,
    dragEnd: noop,
    adjustOpacity: noop,
    adjustTimingOffset: noop,
    openMenu: noop,
    reportAnomaly: noop,
    log: (line) => window.__kashiLog.push(String(line)),
  };
}

export const PAGE_INIT_SOURCE = `(${pageInit.toString()})();`;
