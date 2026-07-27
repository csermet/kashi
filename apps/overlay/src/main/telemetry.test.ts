import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TelemetryClient } from './telemetry.js';
import {
  FLUSH_AT_EVENTS,
  FLUSH_INTERVAL_MS,
  MAX_BATCH_EVENTS,
  MAX_BUFFERED_EVENTS,
  sessionEnvelope,
  serverHostOf,
  TelemetryBuffer,
} from './telemetry-logic.js';

describe('TelemetryBuffer', () => {
  it('asks for a flush once the burst threshold is reached', () => {
    const buffer = new TelemetryBuffer();
    for (let i = 0; i < FLUSH_AT_EVENTS - 1; i++) {
      expect(buffer.add({ ts: 'x', kind: 'watchdog', payload: {} })).toBe(false);
    }
    expect(buffer.add({ ts: 'x', kind: 'watchdog', payload: {} })).toBe(true);
  });

  it('drops the OLDEST events past the ceiling', () => {
    const buffer = new TelemetryBuffer();
    for (let i = 0; i < MAX_BUFFERED_EVENTS + 10; i++) {
      buffer.add({ ts: String(i), kind: 'watchdog', payload: {} });
    }
    expect(buffer.size).toBe(MAX_BUFFERED_EVENTS);
    // The newest events survive: they are the ones near the failure.
    const batch = buffer.take(MAX_BUFFERED_EVENTS);
    expect(batch[batch.length - 1]?.ts).toBe(String(MAX_BUFFERED_EVENTS + 9));
    expect(batch[0]?.ts).toBe('10');
  });

  it('counts overflow losses and resets the counter when read', () => {
    const buffer = new TelemetryBuffer();
    for (let i = 0; i < MAX_BUFFERED_EVENTS + 3; i++) {
      buffer.add({ ts: String(i), kind: 'watchdog', payload: {} });
    }
    expect(buffer.readDropped()).toBe(3);
    expect(buffer.readDropped()).toBe(0);
  });

  it('never hands out more than the server accepts', () => {
    const buffer = new TelemetryBuffer();
    for (let i = 0; i < MAX_BUFFERED_EVENTS; i++) {
      buffer.add({ ts: String(i), kind: 'watchdog', payload: {} });
    }
    expect(buffer.take().length).toBe(MAX_BATCH_EVENTS);
  });
});

describe('sessionEnvelope', () => {
  it('carries only the host of the server URL', () => {
    expect(serverHostOf('https://kashi.example.com/base')).toBe('kashi.example.com');
    expect(serverHostOf('not a url')).toBe('invalid');
  });

  it('names the machine without inventing fields', () => {
    const event = sessionEnvelope(
      {
        appVersion: '0.13.0',
        extensionVersion: 'kashi-extension/0.1.12',
        os: 'darwin',
        osVersion: '24.0.0',
        arch: 'arm64',
        electron: '42.6.0',
        chromium: '140',
        displayCount: 2,
        displaySize: '3456x2234',
        effectLevel: 'hype',
        themeScope: 'full',
        fillStyle: 'themed',
        timingOffsetMs: 200,
        serverHost: 'kashi.example.com',
      },
      '2026-07-26T20:00:00.000Z',
    );
    expect(event.kind).toBe('session_start');
    expect(event.payload['app_version']).toBe('0.13.0');
    expect(event.payload['extension_version']).toBe('kashi-extension/0.1.12');
    expect(event.payload['server_host']).toBe('kashi.example.com');
  });

  it('says so when no extension has connected yet', () => {
    const event = sessionEnvelope(
      {
        appVersion: '0.13.0',
        extensionVersion: null,
        os: 'win32',
        osVersion: '10',
        arch: 'x64',
        electron: '42.6.0',
        chromium: '140',
        displayCount: 1,
        displaySize: '1920x1080',
        effectLevel: 'simple',
        themeScope: 'full',
        fillStyle: 'themed',
        timingOffsetMs: 0,
        serverHost: 'h',
      },
      't',
    );
    expect(event.payload['extension_version']).toBe('none');
  });
});

describe('TelemetryClient', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const ok = () => Promise.resolve({ status: 202 } as Response);

  function makeClient(fetchFn: typeof fetch) {
    return new TelemetryClient({
      baseUrl: 'https://kashi.example.com',
      apiKey: 'ksh_test',
      sessionId: 'session-1',
      fetchFn,
      log: () => {},
    });
  }

  it('sends a bearer-authenticated batch keyed to the session', async () => {
    const fetchFn = vi.fn(ok) as unknown as typeof fetch;
    const client = makeClient(fetchFn);
    client.record('watchdog', { reason: 'stall' }, new Date('2026-07-26T20:00:00Z'));
    await client.flush();

    expect(fetchFn).toHaveBeenCalledTimes(1);
    const call = (fetchFn as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call).toBeDefined();
    const [url, init] = call as [string, RequestInit];
    expect(url).toBe('https://kashi.example.com/v1/telemetry');
    const headers = init.headers as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer ksh_test');
    const body = JSON.parse(init.body as string);
    expect(body.session_id).toBe('session-1');
    expect(body.events).toEqual([
      { ts: '2026-07-26T20:00:00.000Z', kind: 'watchdog', payload: { reason: 'stall' } },
    ]);
  });

  it('does nothing when there is nothing to send', async () => {
    const fetchFn = vi.fn(ok) as unknown as typeof fetch;
    await makeClient(fetchFn).flush();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it('flushes on the idle timer', async () => {
    const fetchFn = vi.fn(ok) as unknown as typeof fetch;
    const client = makeClient(fetchFn);
    client.start();
    client.record('watchdog', {});
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS + 1);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('flushes early when a burst fills the buffer', async () => {
    const fetchFn = vi.fn(ok) as unknown as typeof fetch;
    const client = makeClient(fetchFn);
    for (let i = 0; i < FLUSH_AT_EVENTS; i++) client.record('watchdog', {});
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('drops a failed batch instead of retrying it', async () => {
    const fetchFn = vi.fn(() => Promise.reject(new Error('offline'))) as unknown as typeof fetch;
    const client = makeClient(fetchFn);
    client.record('watchdog', {});
    await client.flush();
    await client.flush(); // nothing left — a failure is not a queue
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('survives a server that refuses the endpoint', async () => {
    const fetchFn = vi.fn(() =>
      Promise.resolve({ status: 503 } as Response),
    ) as unknown as typeof fetch;
    const client = makeClient(fetchFn);
    client.record('watchdog', {});
    await expect(client.flush()).resolves.toBeUndefined();
  });

  it('stop() flushes what is left and cancels the timer', async () => {
    const fetchFn = vi.fn(ok) as unknown as typeof fetch;
    const client = makeClient(fetchFn);
    client.start();
    client.record('watchdog', {});
    await client.stop();
    expect(fetchFn).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS * 3);
    expect(fetchFn).toHaveBeenCalledTimes(1); // timer is gone
  });
});
