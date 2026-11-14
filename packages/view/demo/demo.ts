/**
 * Dev-only demo (client review E.4): the shared view mounted in a plain
 * browser page by a fake host — no Electron, no extension, no server. One
 * synthetic document (invented words), a play/pause button, a scrubber and
 * the effect level. `window.__demo` lets a test drive it
 * (apps/overlay/scripts/view-demo.mjs).
 */
import { createLyricsView, mapDocument, type HostBridge, type Listener } from '../src/index.js';

/** A host in a few lines: listeners per channel, and a way to push to them. */
function fakeHost(): { host: HostBridge; push: (channel: string, payload: unknown) => void; log: string[] } {
  const listeners = new Map<string, Listener[]>();
  const log: string[] = [];
  const on = (channel: string) => (cb: Listener) => {
    const list = listeners.get(channel) ?? [];
    list.push(cb);
    listeners.set(channel, list);
    return () => listeners.set(channel, (listeners.get(channel) ?? []).filter((x) => x !== cb));
  };
  return {
    host: {
      onTrack: on('track'),
      onPlayback: on('playback'),
      onLyrics: on('lyrics'),
      onConnection: on('connection'),
      onSourceGone: on('sourceGone'),
      onSettings: on('settings'),
      log: (line) => log.push(line),
      reportAnomaly: (reason, deltaMs) => log.push(`anomaly ${reason} ${deltaMs}`),
    },
    push: (channel, payload) => {
      for (const cb of listeners.get(channel) ?? []) cb(payload);
    },
    log,
  };
}

/** Words spread over their line, a small gap after each. */
function line(text: string, start: number, end: number, extra: Record<string, unknown> = {}) {
  const texts = text.replace(/[()]/g, '').split(' ');
  const step = Math.floor((end - start) / texts.length);
  return {
    start_ms: start,
    end_ms: end,
    text,
    words: texts.map((t, i) => ({
      text: t,
      start_ms: start + i * step,
      end_ms: start + i * step + Math.floor(step * 0.85),
    })),
    ...extra,
  };
}

const DOC = {
  schema_version: 1,
  pipeline_version: 'demo',
  generated_at: '2026-11-14T00:00:00Z',
  track: { source: { type: 'demo', id: 'demo0001' }, title: 'Demo', artist: 'Kashi view', duration_ms: 20000 },
  sync: 'word',
  alignment: { method: 'synthetic/demo', lyrics_source: 'synthetic', quality_score: 0.9 },
  palette: { source: 'album_art', primary: '#5aa9ff', secondary: '#2d5f99', background: '#0e1a2b', text: '#ffffff', accent: '#ffb86b' },
  fx: {
    lexicon: 'kashi-fx/1.2.0',
    engine: 'keywords',
    select: 'demo',
    words: [{ line: 1, word: 2, tag: 'love', intensity: 0.8 }],
  },
  lines: [
    line('velo mira tando suvi', 1000, 3600),
    line('kerra lomo amor pesta', 4000, 6600),
    line('(ooh aah ooh)', 7000, 9200, { adlib: true }),
    line('sela vintu doranna miko', 9600, 13000),
  ],
};
const KEY = 'demo:demo0001';

const { host, push, log } = fakeHost();
const view = createLyricsView(document.body, host);

let level = 'hype';
const settings = () => ({ box_alpha: 0.6, timing_offset_rel_ms: 0, effect_level: level, theme_scope: 'full', text_scale: 'medium', box_scale: 'medium' });
push('settings', settings());
push('connection', { connected: true });
push('track', { key: KEY, track: { title: DOC.track.title, artist: DOC.track.artist } });
const mapped = mapDocument(DOC);
if (!mapped) throw new Error('demo document rejected');
push('lyrics', { key: KEY, ...mapped });

// Playback: positions reported at 4 Hz like the extension, from a local clock.
let playing = false;
let positionMs = 0;
let startedAt = 0;
let seq = 0;
const nowPosition = () => (playing ? positionMs + (performance.now() - startedAt) : positionMs);
const report = (type: 'position' | 'seek' | 'playback_state', pos: number) =>
  push('playback', {
    type,
    seq: (seq += 1),
    sent_at: Date.now(),
    tab_id: 1,
    position_ms: Math.round(pos),
    playback_rate: 1,
    is_playing: playing,
    captured_at: Date.now(),
  });
setInterval(() => {
  if (playing) report('position', nowPosition());
}, 250);

function seek(ms: number): void {
  positionMs = ms;
  startedAt = performance.now();
  report('seek', ms);
}

// Controls (outside the view's DOM, appended after it).
const controls = document.createElement('div');
controls.id = 'demo-controls';
const play = document.createElement('button');
play.textContent = 'Play';
const scrub = document.createElement('input');
scrub.type = 'range';
scrub.min = '0';
scrub.max = '14000';
scrub.value = '0';
const levelSelect = document.createElement('select');
for (const value of ['off', 'simple', 'full', 'hype']) {
  const option = document.createElement('option');
  option.value = value;
  option.textContent = value;
  option.selected = value === level;
  levelSelect.appendChild(option);
}
controls.append(play, scrub, levelSelect);
document.body.appendChild(controls);
play.addEventListener('click', () => {
  positionMs = nowPosition();
  startedAt = performance.now();
  playing = !playing;
  play.textContent = playing ? 'Pause' : 'Play';
  report('playback_state', positionMs);
});
scrub.addEventListener('input', () => seek(Number(scrub.value)));
levelSelect.addEventListener('change', () => {
  level = levelSelect.value;
  push('settings', settings());
});

declare global {
  interface Window {
    __demo: {
      seek: (ms: number) => void;
      setLevel: (value: string) => void;
      log: string[];
      destroy: () => void;
    };
  }
}
window.__demo = {
  seek,
  setLevel: (value) => {
    level = value;
    push('settings', settings());
  },
  log,
  destroy: () => view.destroy(),
};
