import { describe, expect, it } from 'vitest';
import type { ExtensionToOverlayMessage } from '@kashi/protocol';
import {
  applyExtensionMessage,
  clearReasonOnDisconnect,
  emptyLatch,
} from './source-latch-logic.js';

const TRACK = {
  source: { type: 'youtube', id: 'vid1' },
  title: 'T',
  artist: 'A',
} as const;

function announce(tabId: number, sentAt = 1000, id = 'vid1'): ExtensionToOverlayMessage {
  return {
    type: 'track_changed',
    seq: 0,
    sent_at: sentAt,
    tab_id: tabId,
    track: { ...TRACK, source: { type: 'youtube', id } },
  };
}

function position(tabId: number, capturedAt: number): ExtensionToOverlayMessage {
  return {
    type: 'position',
    seq: 0,
    sent_at: capturedAt,
    tab_id: tabId,
    position_ms: 500,
    playback_rate: 1,
    is_playing: true,
    captured_at: capturedAt,
  };
}

describe('applyExtensionMessage', () => {
  it('latches the announcing (client, tab) and reports a new track', () => {
    const latch = emptyLatch();
    const decision = applyExtensionMessage(latch, announce(5, 1000), 1);
    expect(decision).toMatchObject({ action: 'new-track', key: 'youtube:vid1' });
    expect(latch.activeSource).toEqual({ clientId: 1, tabId: 5 });
    expect(latch.lastTrackSentAt).toBe(1000);
  });

  it('same-key re-announce is a duplicate (metadata refresh), latch owner may move tabs', () => {
    const latch = emptyLatch();
    applyExtensionMessage(latch, announce(5), 1);
    const decision = applyExtensionMessage(latch, announce(6, 2000), 1);
    expect(decision.action).toBe('duplicate-track');
    expect(latch.activeSource?.tabId).toBe(6); // announce always re-latches
    expect(latch.lastTrackSentAt).toBe(1000); // but the track epoch is unchanged
  });

  it('playback from another tab or client is ignored', () => {
    const latch = emptyLatch();
    applyExtensionMessage(latch, announce(5), 1);
    expect(applyExtensionMessage(latch, position(6, 2000), 1).action).toBe('ignore');
    expect(applyExtensionMessage(latch, position(5, 2000), 2).action).toBe('ignore');
    expect(applyExtensionMessage(latch, position(5, 2000), 1).action).toBe('playback');
  });

  it('a report captured before the current announce is stale and ignored', () => {
    const latch = emptyLatch();
    applyExtensionMessage(latch, announce(5, 1000), 1);
    expect(applyExtensionMessage(latch, position(5, 999), 1).action).toBe('ignore');
  });

  it('ad_state has no captured_at and passes the staleness guard', () => {
    const latch = emptyLatch();
    applyExtensionMessage(latch, announce(5, 1000), 1);
    const ad: ExtensionToOverlayMessage = {
      type: 'ad_state',
      seq: 0,
      sent_at: 900,
      tab_id: 5,
      is_ad: true,
    };
    const decision = applyExtensionMessage(latch, ad, 1);
    expect(decision).toMatchObject({ action: 'playback', isPlaying: null });
  });

  it('source_gone asks for a clear', () => {
    expect(applyExtensionMessage(emptyLatch(), { type: 'source_gone', seq: 0, sent_at: 1 }, 1)).toEqual(
      { action: 'clear' },
    );
  });
});

describe('clearReasonOnDisconnect', () => {
  it('clears when the last client leaves, or when the latch owner leaves', () => {
    const latch = emptyLatch();
    applyExtensionMessage(latch, announce(5), 1);
    expect(clearReasonOnDisconnect(latch, 0, 9)).toBe('last client disconnected');
    expect(clearReasonOnDisconnect(latch, 1, 1)).toBe('latch owner disconnected');
    expect(clearReasonOnDisconnect(latch, 1, 2)).toBeNull();
  });
});
