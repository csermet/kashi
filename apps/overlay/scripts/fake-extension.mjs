#!/usr/bin/env node
/**
 * Simulates the Kashi extension for overlay testing without a browser.
 * Usage:  node scripts/fake-extension.mjs [--title "..."] [--artist "..."]
 *                                         [--duration-ms 213000] [--port 17890]
 * Streams `position` messages at 4 Hz from 0 until the duration is reached.
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
  },
});

const durationMs = Number(args['duration-ms']);
const startMs = Number(args['start-ms']);
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

  const timer = setInterval(() => {
    const position = startMs + (Date.now() - startedAt);
    if (position >= durationMs) {
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
