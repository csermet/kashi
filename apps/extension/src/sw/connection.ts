/**
 * Overlay connection manager (service worker).
 *
 * The WebSocket lives HERE — extension SW contexts with proper host
 * permissions are exempt from Chrome's Local Network Access prompt, while
 * content-script sockets are attributed to the page origin and gated (R-6,
 * ytm-scout 2026-07). Active WS traffic (overlay pings every 20 s) keeps the
 * SW alive on Chrome 116+; a chrome.alarms watchdog covers the gaps.
 *
 * Port walk: tries 17890-17894 and settles on the first port whose
 * `hello_ack` comes back accepted. Reconnects forever with jittered backoff.
 */
import {
  DEFAULT_PORT,
  PORT_MAX,
  PROTOCOL_VERSION,
  type ExtensionToOverlayMessage,
  type OverlayToExtensionMessage,
} from '@kashi/protocol';
import { backoffDelayMs } from './logic.js';

const CLIENT_ID = 'kashi-extension/0.1.2';
const ACK_TIMEOUT_MS = 3000;

/** Omit must distribute over the message union (plain Omit collapses it). */
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
export type Sendable = DistributiveOmit<ExtensionToOverlayMessage, 'seq'>;

export class OverlayConnection {
  private socket: WebSocket | null = null;
  private acked = false;
  private seq = 0;
  private attempt = 0;
  private connecting = false;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly onReconnected: () => void,
    private readonly log: (line: string) => void = () => {},
  ) {}

  get isConnected(): boolean {
    return this.acked && this.socket?.readyState === WebSocket.OPEN;
  }

  /** Idempotent: starts a connection attempt unless one is live/in-flight. */
  ensureConnected(): void {
    if (this.isConnected || this.connecting) return;
    clearTimeout(this.retryTimer);
    void this.connect();
  }

  /** Send a message (seq is stamped here). Silently dropped while offline. */
  send(msg: Sendable): void {
    if (!this.isConnected || !this.socket) return;
    this.socket.send(JSON.stringify({ ...msg, seq: this.seq++ }));
  }

  private async connect(): Promise<void> {
    this.connecting = true;
    try {
      for (let port = DEFAULT_PORT; port <= PORT_MAX; port++) {
        const socket = await this.tryPort(port);
        if (socket) {
          this.adopt(socket, port);
          return;
        }
      }
      this.scheduleRetry();
    } finally {
      this.connecting = false;
    }
  }

  /** Resolves with an OPEN socket whose hello was accepted, else null. */
  private tryPort(port: number): Promise<WebSocket | null> {
    return new Promise((resolve) => {
      let settled = false;
      const socket = new WebSocket(`ws://127.0.0.1:${port}/ws`);
      const finish = (ok: boolean) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        if (!ok) {
          socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
          try {
            socket.close();
          } catch {
            /* already closed */
          }
        }
        resolve(ok ? socket : null);
      };
      const timeout = setTimeout(() => finish(false), ACK_TIMEOUT_MS);

      socket.onopen = () => {
        socket.send(
          JSON.stringify({
            type: 'hello',
            seq: 0,
            sent_at: Date.now(),
            protocol_version: PROTOCOL_VERSION,
            client: CLIENT_ID,
          }),
        );
      };
      socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(String(event.data)) as OverlayToExtensionMessage;
          if (msg.type === 'hello_ack') finish(msg.accepted === true);
        } catch {
          finish(false);
        }
      };
      socket.onerror = () => finish(false);
      socket.onclose = () => finish(false);
    });
  }

  private adopt(socket: WebSocket, port: number): void {
    this.socket = socket;
    this.acked = true;
    this.seq = 1; // hello used 0
    this.attempt = 0;
    this.log(`connected to overlay on port ${port}`);

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(String(event.data)) as OverlayToExtensionMessage;
        if (msg.type === 'ping') {
          this.send({ type: 'pong', sent_at: Date.now() });
        }
      } catch {
        /* ignore malformed server frames */
      }
    };
    socket.onclose = () => {
      this.acked = false;
      this.socket = null;
      this.log('overlay connection lost');
      this.scheduleRetry();
    };
    socket.onerror = () => {
      /* onclose follows and handles retry */
    };

    this.onReconnected();
  }

  private scheduleRetry(): void {
    const delay = backoffDelayMs(this.attempt++);
    this.log(`retrying in ${delay} ms (attempt ${this.attempt})`);
    // SW may be killed during long waits; the 1-min alarms watchdog and any
    // content event both call ensureConnected(), so a lost timer only delays.
    clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => this.ensureConnected(), delay);
  }
}
