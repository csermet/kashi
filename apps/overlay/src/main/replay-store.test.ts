import { describe, expect, it } from 'vitest';
import { ReplayStore } from './replay-store.js';

function replayed(store: ReplayStore): Array<[string, unknown]> {
  const out: Array<[string, unknown]> = [];
  store.replayInto((channel, payload) => out.push([channel, payload]));
  return out;
}

describe('ReplayStore', () => {
  it('replays the last payload per channel in dependency order', () => {
    const store = new ReplayStore();
    store.record('kashi:track', { key: 'a' });
    store.record('kashi:settings', { box_alpha: 0.5 });
    store.record('kashi:track', { key: 'b' }); // last write wins
    expect(replayed(store)).toEqual([
      ['kashi:settings', { box_alpha: 0.5 }],
      ['kashi:track', { key: 'b' }],
    ]);
  });

  it('latches playback anchors PAUSED, and only position/seek/playback_state', () => {
    const store = new ReplayStore();
    store.record('kashi:playback', { type: 'position', position_ms: 7, is_playing: true });
    expect(replayed(store)).toEqual([
      ['kashi:playback', { type: 'position', position_ms: 7, is_playing: false }],
    ]);

    // ad_state must NOT overwrite the anchor (it carries no position).
    store.record('kashi:playback', { type: 'ad_state', is_ad: true });
    expect(replayed(store)).toEqual([
      ['kashi:playback', { type: 'position', position_ms: 7, is_playing: false }],
    ]);
  });

  it('clearSourceChannels drops track/lyrics/playback but keeps settings/connection', () => {
    const store = new ReplayStore();
    store.record('kashi:settings', { box_alpha: 0.5 });
    store.record('kashi:connection', { connected: true });
    store.record('kashi:track', { key: 'a' });
    store.record('kashi:lyrics', { key: 'a', found: true });
    store.record('kashi:playback', { type: 'seek', position_ms: 1, is_playing: true });
    store.clearSourceChannels();
    expect(replayed(store)).toEqual([
      ['kashi:settings', { box_alpha: 0.5 }],
      ['kashi:connection', { connected: true }],
    ]);
  });
});
