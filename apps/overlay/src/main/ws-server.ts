/**
 * Local WebSocket server the extension's service worker connects to.
 *
 * Security posture (plan R-7 / protocol §security):
 *  - binds 127.0.0.1 ONLY — never a routable interface
 *  - Origin must be chrome-extension:// (optionally pinned to specific IDs);
 *    note the Origin header is trivially forgeable by non-browser local
 *    processes, so every inbound message is also SHAPE-VALIDATED before it
 *    reaches the app (a malformed payload must never crash the overlay)
 *  - optional shared token checked in `hello` (off by default)
 * Liveness: sends `ping` every PING_INTERVAL_MS; two missed pongs → terminate
 * (also keeps the MV3 service worker awake — cadence stays under Chrome's 30 s
 * idle kill).
 */
import { setTimeout as delay } from 'node:timers/promises';
import { WebSocket, WebSocketServer } from 'ws';
import type { IncomingMessage } from 'node:http';
import {
  DEFAULT_PORT,
  PING_INTERVAL_MS,
  PORT_MAX,
  PROTOCOL_VERSION,
  type ExtensionToOverlayMessage,
  type HelloAckMessage,
  type HelloMessage,
  type PingMessage,
} from '@kashi/protocol';

export interface OverlayWsServerOptions {
  portStart?: number;
  portEnd?: number;
  pingIntervalMs?: number;
  handshakeTimeoutMs?: number;
  /**
   * Allowed extension origins (e.g. "chrome-extension://<id>"). Empty list =
   * accept any chrome-extension:// origin (dev default until the extension ID
   * is pinned via the manifest `key`, plan R-6).
   */
  allowedOrigins?: string[];
  /** Optional shared token; when set, `hello.token` must match. */
  token?: string;
  /** Warn loudly when a connecting client identifies as a different build. */
  expectedClient?: string;
  /** Called for every accepted, shape-valid post-handshake message. */
  onMessage: (msg: ExtensionToOverlayMessage, clientId: number) => void;
  /** Both callbacks receive the number of remaining handshaken clients. */
  onClientConnected?: (connectedCount: number) => void;
  onClientDisconnected?: (connectedCount: number, clientId: number) => void;
  log?: (line: string) => void;
}

interface ClientState {
  id: number;
  socket: WebSocket;
  helloDone: boolean;
  missedPongs: number;
  seq: number;
  notifiedDisconnect: boolean;
  pingTimer?: NodeJS.Timeout;
  handshakeTimer?: NodeJS.Timeout;
}

const MAX_MESSAGE_BYTES = 64 * 1024;

const isStr = (v: unknown): v is string => typeof v === 'string';
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isBool = (v: unknown): v is boolean => typeof v === 'boolean';

/**
 * Minimal per-type shape validation. Anything reaching `onMessage` is safe to
 * destructure. Kept in sync with packages/protocol (fixture-tested there when
 * the protocol grows; for now hand-maintained).
 */
export function isValidExtensionMessage(msg: unknown): msg is ExtensionToOverlayMessage {
  if (typeof msg !== 'object' || msg === null) return false;
  const m = msg as Record<string, unknown>;
  if (!isStr(m['type'])) return false;

  switch (m['type']) {
    case 'hello':
      return isNum(m['protocol_version']) && isStr(m['client']);
    case 'pong':
      return true;
    case 'track_changed': {
      if (!isNum(m['tab_id'])) return false;
      const track = m['track'] as Record<string, unknown> | undefined;
      if (typeof track !== 'object' || track === null) return false;
      const source = track['source'] as Record<string, unknown> | undefined;
      return (
        typeof source === 'object' &&
        source !== null &&
        isStr(source['type']) &&
        isStr(source['id']) &&
        isStr(track['title']) &&
        isStr(track['artist'])
      );
    }
    case 'position':
      return (
        isNum(m['tab_id']) &&
        isNum(m['position_ms']) &&
        isNum(m['playback_rate']) &&
        isBool(m['is_playing']) &&
        isNum(m['captured_at'])
      );
    case 'seek':
    case 'playback_state':
      return (
        isNum(m['tab_id']) &&
        isNum(m['position_ms']) &&
        isBool(m['is_playing']) &&
        isNum(m['captured_at'])
      );
    case 'ad_state':
      return isNum(m['tab_id']) && isBool(m['is_ad']);
    case 'log':
      return isStr(m['context']) && isStr(m['line']) && (m['line'] as string).length <= 2000;
    case 'source_gone':
      return true;
    default:
      return false;
  }
}

export class OverlayWsServer {
  private server: WebSocketServer | null = null;
  private clients = new Set<ClientState>();
  private nextClientId = 1;
  private _port: number | null = null;
  private stopping = false;

  constructor(private readonly opts: OverlayWsServerOptions) {}

  get port(): number | null {
    return this._port;
  }

  get connectedCount(): number {
    let count = 0;
    for (const client of this.clients) if (client.helloDone) count++;
    return count;
  }

  /** Bind the first free port in [portStart, portEnd]; returns the port. */
  async start(): Promise<number> {
    const start = this.opts.portStart ?? DEFAULT_PORT;
    const end = this.opts.portEnd ?? PORT_MAX;
    let lastError: unknown = null;

    for (let port = start; port <= end; port++) {
      try {
        this.server = await this.listen(port);
        this._port = port;
        this.server.on('connection', (socket, req) => this.onConnection(socket, req));
        this.log(`listening on 127.0.0.1:${port}`);
        return port;
      } catch (err) {
        lastError = err;
        if ((err as NodeJS.ErrnoException).code !== 'EADDRINUSE') throw err;
      }
    }
    throw new Error(`no free port in ${start}-${end}: ${String(lastError)}`);
  }

  async stop(): Promise<void> {
    this.stopping = true;
    for (const client of this.clients) {
      clearTimeout(client.handshakeTimer);
      clearInterval(client.pingTimer);
      client.socket.terminate();
    }
    this.clients.clear();
    const server = this.server;
    this.server = null;
    this._port = null;
    if (server) {
      await new Promise<void>((resolve) => server.close(() => resolve()));
      // ws closes the underlying HTTP server asynchronously; yield once.
      await delay(0);
    }
  }

  private listen(port: number): Promise<WebSocketServer> {
    return new Promise((resolve, reject) => {
      const server = new WebSocketServer({
        host: '127.0.0.1',
        port,
        maxPayload: MAX_MESSAGE_BYTES,
      });
      server.once('listening', () => {
        server.removeListener('error', reject);
        resolve(server);
      });
      server.once('error', reject);
    });
  }

  private onConnection(socket: WebSocket, req: IncomingMessage): void {
    // Attach the error handler FIRST: an unhandled 'error' emit (e.g. the peer
    // RSTs while we write the close frame) would crash the process.
    socket.on('error', (err) => this.log(`socket error: ${err.message}`));

    const origin = req.headers.origin ?? '';
    if (!this.isOriginAllowed(origin)) {
      this.log(`rejected origin: ${origin || '(none)'}`);
      socket.close(4003, 'origin not allowed');
      return;
    }

    const client: ClientState = {
      id: this.nextClientId++,
      socket,
      helloDone: false,
      missedPongs: 0,
      seq: 0,
      notifiedDisconnect: false,
    };
    this.clients.add(client);

    client.handshakeTimer = setTimeout(() => {
      if (!client.helloDone) this.dropClient(client, 4008, 'hello timeout');
    }, this.opts.handshakeTimeoutMs ?? 5000);

    socket.on('message', (data) => this.onData(client, data));
    // Single notification point for disconnects — every teardown path
    // (pong timeout, protocol violation, peer close) ends up here.
    socket.on('close', () => {
      this.forgetClient(client);
      if (client.helloDone && !client.notifiedDisconnect && !this.stopping) {
        client.notifiedDisconnect = true;
        this.opts.onClientDisconnected?.(this.connectedCount, client.id);
      }
    });
  }

  private onData(client: ClientState, data: unknown): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(String(data));
    } catch {
      this.dropClient(client, 4002, 'invalid json');
      return;
    }
    if (!isValidExtensionMessage(parsed)) {
      // Tolerate (log + drop message, keep connection): a buggy-but-honest
      // extension version must not get insta-disconnected in a reconnect loop.
      const type =
        typeof parsed === 'object' && parsed !== null
          ? String((parsed as Record<string, unknown>)['type'])
          : typeof parsed;
      this.log(`dropped malformed message (type: ${type}) from client ${client.id}`);
      return;
    }
    const msg = parsed;

    if (!client.helloDone) {
      if (msg.type !== 'hello') {
        this.dropClient(client, 4002, 'expected hello');
        return;
      }
      this.finishHandshake(client, msg);
      return;
    }

    if (msg.type === 'pong') {
      client.missedPongs = 0;
      return;
    }
    try {
      this.opts.onMessage(msg, client.id);
    } catch (err) {
      this.log(`onMessage handler threw: ${String(err)}`);
    }
  }

  private finishHandshake(client: ClientState, hello: HelloMessage): void {
    const versionOk = hello.protocol_version === PROTOCOL_VERSION;
    const tokenOk = !this.opts.token || hello.token === this.opts.token;
    const accepted = versionOk && tokenOk;

    const ack: HelloAckMessage = {
      type: 'hello_ack',
      seq: client.seq++,
      sent_at: Date.now(),
      protocol_version: PROTOCOL_VERSION,
      server: 'kashi-overlay/0.1.0',
      accepted,
    };
    client.socket.send(JSON.stringify(ack));

    if (!accepted) {
      this.log(`hello rejected (version ok: ${versionOk}, token ok: ${tokenOk})`);
      this.dropClient(client, 4001, 'hello rejected');
      return;
    }

    client.helloDone = true;
    clearTimeout(client.handshakeTimer);
    this.startPinging(client);
    this.opts.onClientConnected?.(this.connectedCount);
    this.log(`client ${client.id} connected: ${hello.client}`);
    if (this.opts.expectedClient && hello.client !== this.opts.expectedClient) {
      this.log(
        `*** UYARI: eklenti surumu eski/farkli (${hello.client}, beklenen ` +
          `${this.opts.expectedClient}) — 'pnpm --filter kashi-extension build' + ` +
          `chrome://extensions'ta yenile + YTM sekmesini F5'le ***`,
      );
    }
  }

  private startPinging(client: ClientState): void {
    const interval = this.opts.pingIntervalMs ?? PING_INTERVAL_MS;
    client.pingTimer = setInterval(() => {
      if (client.missedPongs >= 2) {
        this.dropClient(client, 4000, 'pong timeout'); // close event notifies
        return;
      }
      client.missedPongs++;
      const ping: PingMessage = { type: 'ping', seq: client.seq++, sent_at: Date.now() };
      client.socket.send(JSON.stringify(ping));
    }, interval);
  }

  private isOriginAllowed(origin: string): boolean {
    if (!origin.startsWith('chrome-extension://')) return false;
    const allowed = this.opts.allowedOrigins ?? [];
    return allowed.length === 0 || allowed.includes(origin);
  }

  private dropClient(client: ClientState, code: number, reason?: string): void {
    this.forgetClient(client);
    if (
      client.socket.readyState === WebSocket.OPEN ||
      client.socket.readyState === WebSocket.CONNECTING
    ) {
      client.socket.close(code, reason);
    }
  }

  private forgetClient(client: ClientState): void {
    clearTimeout(client.handshakeTimer);
    clearInterval(client.pingTimer);
    this.clients.delete(client);
  }

  private log(line: string): void {
    this.opts.log?.(`[ws-server] ${line}`);
  }
}
