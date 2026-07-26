#!/usr/bin/env node
/**
 * Simulates the Kashi extension for overlay testing without a browser.
 * Usage:  node scripts/fake-extension.mjs [--title "..."] [--artist "..."]
 *                                         [--duration-ms 213000] [--port 17890]
 * Streams `position` messages at 4 Hz from 0 until the duration is reached.
 *
 * `--gapless-leak-ms N` reproduces the field bug an older extension build
 * causes: the report that rides along the announce still carries the PREVIOUS
 * track's time (YTM Premium plays gapless, so the media timeline does not
 * restart at zero). Expect the overlay to log "past track end — dropped
 * before anchoring" and then anchor on the next honest report.
 */
import { WebSocket } from 'ws';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({
  options: {
    title: { type: 'string', default: 'Never Gonna Give You Up' },
    artist: { type: 'string', default: 'Rick Astley' },
    album: { type: 'string', default: 'Whenever You Need Somebody' },
    'duration-ms': { type: 'string', default: '213000' },
    port: { type: 'string', default: '17890' },
    'start-ms': { type: 'string', default: '0' },
    'gapless-leak-ms': { type: 'string', default: '0' },
    'gapless-leak-persist': { type: 'boolean', default: false },
  },
});

const durationMs = Number(args['duration-ms']);
const startMs = Number(args['start-ms']);
const gaplessLeakMs = Number(args['gapless-leak-ms']);
const gaplessLeakPersist = args['gapless-leak-persist'];
let seq = 0;

const socket = new WebSocket(`ws://127.0.0.1:${args.port}/ws`, {
  headers: { origin: 'chrome-extension://fakeextensionfortesting' },
});

const send = (msg) =>
  socket.send(JSON.stringify({ seq: seq++, sent_at: Date.now(), ...msg }));

socket.on('open', () => {
  send({ type: 'hello', protocol_version: 1, client: 'fake-extension/0.1.0' });
});

socket.on('message', (data) => {
  const msg = JSON.parse(String(data));
  if (msg.type === 'hello_ack') {
    if (!msg.accepted) {
      console.error('hello rejected');
      process.exit(1);
    }
    console.log('connected — streaming', args.title);
    start();
  } else if (msg.type === 'ping') {
    send({ type: 'pong' });
  }
});

socket.on('close', (code) => {
  console.log('closed', code);
  process.exit(0);
});

function start() {
  const startedAt = Date.now();
  send({
    type: 'track_changed',
    tab_id: 1,
    track: {
      source: { type: 'youtube', id: 'dQw4w9WgXcQ' },
      title: args.title,
      artist: args.artist,
      album: args.album,
      duration_ms: durationMs,
    },
  });

  if (gaplessLeakMs > 0) {
    const leaked = durationMs + gaplessLeakMs;
    console.log(`leaking a stale first position: ${leaked}ms (track ends at ${durationMs}ms)`);
    send({
      type: 'position',
      tab_id: 1,
      position_ms: leaked,
      playback_rate: 1,
      is_playing: true,
      captured_at: Date.now(),
    });
  }

  const timer = setInterval(() => {
    // --gapless-leak-persist: EVERY report stays past the end, the reading of
    // the root-cause hypothesis where currentTime never returns to this
    // track's range. The guard must give up on its budget and let the clock
    // anchor — a frozen overlay would be worse than a misplaced one.
    const position = gaplessLeakPersist
      ? durationMs + gaplessLeakMs + (Date.now() - startedAt)
      : startMs + (Date.now() - startedAt);
    if (!gaplessLeakPersist && position >= durationMs) {
      clearInterval(timer);
      socket.close();
      return;
    }
    send({
      type: 'position',
      tab_id: 1,
      position_ms: position,
      playback_rate: 1,
      is_playing: true,
      captured_at: Date.now(),
    });
  }, 250);
}
