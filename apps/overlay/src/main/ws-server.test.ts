import { afterEach, describe, expect, it } from 'vitest';
import { WebSocket } from 'ws';
import { PROTOCOL_VERSION, type ExtensionToOverlayMessage } from '@kashi/protocol';
import { OverlayWsServer, isValidExtensionMessage, type OverlayWsServerOptions } from './ws-server.js';

// Off the production 17890-17894 range so tests never collide with a real overlay.
let nextPort = 19870;

const GOOD_ORIGIN = 'chrome-extension://abcdefghijklmnop';

function collectMessages(socket: WebSocket): unknown[] {
  const received: unknown[] = [];
  socket.on('message', (data) => received.push(JSON.parse(String(data))));
  return received;
}

function waitFor(predicate: () => boolean, timeoutMs = 2000): Promise<void> {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (predicate()) {
        clearInterval(timer);
        resolve();
      } else if (Date.now() - started > timeoutMs) {
        clearInterval(timer);
        reject(new Error('waitFor timeout'));
      }
    }, 10);
  });
}

function hello(overrides: Record<string, unknown> = {}) {
  return JSON.stringify({
    type: 'hello',
    seq: 0,
    sent_at: Date.now(),
    protocol_version: PROTOCOL_VERSION,
    client: 'test/0.0.0',
    ...overrides,
  });
}

describe('OverlayWsServer', () => {
  const servers: OverlayWsServer[] = [];
  const sockets: WebSocket[] = [];

  async function makeServer(opts: Partial<OverlayWsServerOptions> = {}) {
    const received: ExtensionToOverlayMessage[] = [];
    const server = new OverlayWsServer({
      portStart: nextPort,
      portEnd: nextPort + 4,
      onMessage: (msg) => received.push(msg),
      ...opts,
    });
    nextPort += 10;
    const port = await server.start();
    servers.push(server);
    return { server, port, received };
  }

  function connect(port: number, origin = GOOD_ORIGIN): WebSocket {
    const socket = new WebSocket(`ws://127.0.0.1:${port}/ws`, { headers: { origin } });
    sockets.push(socket);
    return socket;
  }

  afterEach(async () => {
    for (const socket of sockets.splice(0)) socket.terminate();
    for (const server of servers.splice(0)) await server.stop();
  });

  it('walks to the next port when the first is taken', async () => {
    const a = await makeServer();
    const b = new OverlayWsServer({
      portStart: a.port,
      portEnd: a.port + 4,
      onMessage: () => {},
    });
    servers.push(b);
    const portB = await b.start();
    expect(portB).toBe(a.port + 1);
  });

  it('rejects non-extension origins', async () => {
    const { port } = await makeServer();
    const socket = connect(port, 'https://music.youtube.com');
    const closed = new Promise<number>((resolve) =>
      socket.on('close', (code) => resolve(code)),
    );
    await expect(closed).resolves.toBe(4003);
  });

  it('enforces the origin allowlist when configured', async () => {
    const { port } = await makeServer({ allowedOrigins: [GOOD_ORIGIN] });
    const socket = connect(port, 'chrome-extension://otherextensionid');
    const closed = new Promise<number>((resolve) =>
      socket.on('close', (code) => resolve(code)),
    );
    await expect(closed).resolves.toBe(4003);
  });

  it('accepts a valid hello and routes subsequent messages', async () => {
    const { port, received } = await makeServer();
    const socket = connect(port);
    const messages = collectMessages(socket);

    socket.on('open', () => socket.send(hello()));
    await waitFor(() => messages.length >= 1);
    expect(messages[0]).toMatchObject({ type: 'hello_ack', accepted: true });

    socket.send(
      JSON.stringify({
        type: 'ad_state',
        seq: 1,
        sent_at: Date.now(),
        tab_id: 7,
        is_ad: true,
      }),
    );
    await waitFor(() => received.length >= 1);
    expect(received[0]).toMatchObject({ type: 'ad_state', is_ad: true });
  });

  it('rejects a protocol version mismatch', async () => {
    const { port } = await makeServer();
    const socket = connect(port);
    const messages = collectMessages(socket);
    const closed = new Promise<number>((resolve) =>
      socket.on('close', (code) => resolve(code)),
    );

    socket.on('open', () => socket.send(hello({ protocol_version: 999 })));
    await waitFor(() => messages.length >= 1);
    expect(messages[0]).toMatchObject({ type: 'hello_ack', accepted: false });
    await expect(closed).resolves.toBe(4001);
  });

  it('rejects a bad token when one is configured', async () => {
    const { port } = await makeServer({ token: 'sekrit' });
    const socket = connect(port);
    const messages = collectMessages(socket);

    socket.on('open', () => socket.send(hello({ token: 'wrong' })));
    await waitFor(() => messages.length >= 1);
    expect(messages[0]).toMatchObject({ type: 'hello_ack', accepted: false });
  });

  it('drops clients that never say hello', async () => {
    const { port } = await makeServer({ handshakeTimeoutMs: 50 });
    const socket = connect(port);
    const closed = new Promise<number>((resolve) =>
      socket.on('close', (code) => resolve(code)),
    );
    await expect(closed).resolves.toBe(4008);
  });

  it('drops malformed messages without killing the connection', async () => {
    const { port, received } = await makeServer();
    const socket = connect(port);
    const messages = collectMessages(socket);

    socket.on('open', () => socket.send(hello()));
    await waitFor(() => messages.length >= 1);

    // track without body → shape validation must reject it silently.
    socket.send(JSON.stringify({ type: 'track_changed', seq: 1, sent_at: Date.now() }));
    // ...but a valid message right after still flows.
    socket.send(
      JSON.stringify({
        type: 'ad_state',
        seq: 2,
        sent_at: Date.now(),
        tab_id: 1,
        is_ad: false,
      }),
    );
    await waitFor(() => received.length >= 1);
    expect(received).toHaveLength(1);
    expect(received[0]).toMatchObject({ type: 'ad_state' });
    expect(socket.readyState).toBe(socket.OPEN);
  });

  it('passes a stable clientId to onMessage', async () => {
    const clientIds: number[] = [];
    const { port } = await makeServer({
      onMessage: (_msg, clientId) => clientIds.push(clientId),
    });
    const socket = connect(port);
    const messages = collectMessages(socket);
    socket.on('open', () => socket.send(hello()));
    await waitFor(() => messages.length >= 1);

    for (let i = 0; i < 2; i++) {
      socket.send(
        JSON.stringify({
          type: 'ad_state',
          seq: i,
          sent_at: Date.now(),
          tab_id: 1,
          is_ad: false,
        }),
      );
    }
    await waitFor(() => clientIds.length >= 2);
    expect(clientIds[0]).toBe(clientIds[1]);
  });

  it('pings and drops after two missed pongs; pongs keep it alive', async () => {
    const { port } = await makeServer({ pingIntervalMs: 40 });
    const socket = connect(port);
    const messages = collectMessages(socket);
    let pongsSent = 0;
    let closedCode: number | null = null;
    socket.on('close', (code) => (closedCode = code));

    socket.on('message', (data) => {
      const msg = JSON.parse(String(data)) as { type: string };
      if (msg.type === 'ping' && pongsSent < 3) {
        pongsSent++;
        socket.send(
          JSON.stringify({ type: 'pong', seq: 0, sent_at: Date.now() }),
        );
      }
    });
    socket.on('open', () => socket.send(hello()));

    // Survives while ponging (3 pongs ≈ 120 ms), then dies of pong starvation.
    await waitFor(() => pongsSent >= 3);
    expect(closedCode).toBeNull();
    await waitFor(() => closedCode !== null);
    expect(closedCode).toBe(4000);
    expect(messages.filter((m) => (m as { type: string }).type === 'ping').length)
      .toBeGreaterThanOrEqual(3);
  });
});

describe('queue_changed validation', () => {
  const item = (id: string) => ({
    source: { type: 'youtube', id },
    title: `Song ${id}`,
    artist: 'Artist',
    duration_ms: 200_000,
  });
  const msg = (next: unknown, extra: Record<string, unknown> = {}) => ({
    type: 'queue_changed',
    seq: 1,
    sent_at: 0,
    tab_id: 7,
    next,
    ...extra,
  });

  it('accepts up to two well-formed items, and an empty prediction', () => {
    expect(isValidExtensionMessage(msg([item('a'), item('b')]))).toBe(true);
    expect(isValidExtensionMessage(msg([]))).toBe(true);
  });

  it('rejects more items than the lookahead allows', () => {
    expect(isValidExtensionMessage(msg([item('a'), item('b'), item('c')]))).toBe(false);
  });

  it('rejects a malformed item, a missing list or a missing tab', () => {
    expect(isValidExtensionMessage(msg([{ ...item('a'), title: 5 }]))).toBe(false);
    expect(isValidExtensionMessage(msg([{ title: 'x', artist: 'y' }]))).toBe(false);
    expect(isValidExtensionMessage(msg('a'))).toBe(false);
    expect(isValidExtensionMessage(msg([item('a')], { tab_id: 'x' }))).toBe(false);
  });

  it('keeps validating track_changed through the shared track check', () => {
    const good = { type: 'track_changed', seq: 1, sent_at: 0, tab_id: 1, track: item('a') };
    expect(isValidExtensionMessage(good)).toBe(true);
    expect(isValidExtensionMessage({ ...good, track: { ...item('a'), artist: null } })).toBe(false);
  });
});
