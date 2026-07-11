/**
 * Renderer replay latch. `record` remembers the last payload per channel so a
 * (re)loaded renderer never starts blind; `replayInto` plays them back in
 * dependency order. Pure (no Electron) — the broadcast itself stays in main.
 */

/** Replay order matters: settings -> connection -> track -> lyrics -> anchor. */
const REPLAY_ORDER = [
  'kashi:settings',
  'kashi:connection',
  'kashi:track',
  'kashi:lyrics',
  'kashi:playback',
];

export class ReplayStore {
  private readonly payloads = new Map<string, unknown>();

  record(channel: string, payload: unknown): void {
    if (channel !== 'kashi:playback') {
      this.payloads.set(channel, payload);
      return;
    }
    const msg = payload as { type?: string; is_playing?: boolean };
    if (msg.type === 'position' || msg.type === 'seek' || msg.type === 'playback_state') {
      // Keep a PAUSED copy for renderer-reload replay: without an anchor the
      // reloaded renderer shows nothing until the next live report — and a
      // stale is_playing=true report would extrapolate across the whole gap.
      this.payloads.set('kashi:playback', { ...msg, is_playing: false });
    }
  }

  /** Drop the channels that describe the (now gone) source. */
  clearSourceChannels(): void {
    this.payloads.delete('kashi:track');
    this.payloads.delete('kashi:lyrics');
    this.payloads.delete('kashi:playback');
  }

  replayInto(sendFn: (channel: string, payload: unknown) => void): void {
    for (const channel of REPLAY_ORDER) {
      const payload = this.payloads.get(channel);
      if (payload !== undefined) sendFn(channel, payload);
    }
  }
}
