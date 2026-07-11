import { describe, expect, it } from 'vitest';
import { backoffDelayMs, selectActiveTab } from './logic.js';

describe('backoffDelayMs', () => {
  const noJitter = () => 0.5; // random()=0.5 → jitter factor 1.0

  it('follows the 1→2→5→10→30 s ladder and caps at 30 s', () => {
    expect(backoffDelayMs(0, noJitter)).toBe(1000);
    expect(backoffDelayMs(1, noJitter)).toBe(2000);
    expect(backoffDelayMs(2, noJitter)).toBe(5000);
    expect(backoffDelayMs(3, noJitter)).toBe(10_000);
    expect(backoffDelayMs(4, noJitter)).toBe(30_000);
    expect(backoffDelayMs(99, noJitter)).toBe(30_000);
  });

  it('applies ±20% jitter', () => {
    expect(backoffDelayMs(0, () => 1)).toBe(1200);
    expect(backoffDelayMs(0, () => 0)).toBe(800);
  });

  it('clamps negative attempts', () => {
    expect(backoffDelayMs(-5, noJitter)).toBe(1000);
  });
});

describe('selectActiveTab', () => {
  it('returns null for no tabs', () => {
    expect(selectActiveTab({})).toBeNull();
  });

  it('prefers a playing tab over a more recent paused one', () => {
    expect(
      selectActiveTab({
        1: { isPlaying: true, lastEventAt: 100 },
        2: { isPlaying: false, lastEventAt: 999 },
      }),
    ).toBe(1);
  });

  it('audible beats everything (ground truth over self-reports)', () => {
    expect(
      selectActiveTab({
        1: { isPlaying: true, lastEventAt: 999 },
        2: { isPlaying: false, lastEventAt: 100, audible: true },
      }),
    ).toBe(2);
    expect(
      selectActiveTab({
        1: { isPlaying: true, lastEventAt: 100, audible: true },
        2: { isPlaying: true, lastEventAt: 999, audible: false },
      }),
    ).toBe(1);
  });

  it('breaks ties by most recent event (no incumbent)', () => {
    expect(
      selectActiveTab({
        1: { isPlaying: true, lastEventAt: 100 },
        2: { isPlaying: true, lastEventAt: 200 },
      }),
    ).toBe(2);
    expect(
      selectActiveTab({
        3: { isPlaying: false, lastEventAt: 300 },
        4: { isPlaying: false, lastEventAt: 250 },
      }),
    ).toBe(3);
  });

  it('is sticky: an equal-score newcomer cannot steal the seat', () => {
    const tabs = {
      1: { isPlaying: true, lastEventAt: 100, audible: true },
      2: { isPlaying: true, lastEventAt: 999, audible: true }, // newer, same score
    };
    expect(selectActiveTab(tabs, 1)).toBe(1);
    // ...and no flip-flop as events alternate:
    tabs[1].lastEventAt = 1000;
    expect(selectActiveTab(tabs, 1)).toBe(1);
    tabs[2].lastEventAt = 1001;
    expect(selectActiveTab(tabs, 1)).toBe(1);
  });

  it('switches when the incumbent actually stops', () => {
    expect(
      selectActiveTab(
        {
          1: { isPlaying: false, lastEventAt: 999, audible: false }, // stopped
          2: { isPlaying: true, lastEventAt: 100, audible: true },
        },
        1,
      ),
    ).toBe(2);
  });

  it('falls back to normal selection when the incumbent is gone', () => {
    expect(
      selectActiveTab({ 2: { isPlaying: true, lastEventAt: 100, audible: true } }, 1),
    ).toBe(2);
  });
});

// --- seat state machine ------------------------------------------------------

import {
  applyContentEvent,
  handleTabRemoved,
  snapshotReplayMessages,
  SNAPSHOT_MAX_AGE_MS,
  type SessionState,
} from './logic.js';
import type { ContentEvent } from '../shared/messages.js';

const NOW = 1_000_000;

function freshState(): SessionState {
  return { tabs: {}, snapshots: {}, activeTabId: null };
}

function announce(videoId: string): Extract<ContentEvent, { kind: 'track_changed' }> {
  return { kind: 'track_changed', videoId, title: 'T', artist: 'A', sent_at: NOW };
}

type SeatEvent = Exclude<ContentEvent, { kind: 'log' }>;

function position(isPlaying: boolean): SeatEvent {
  return {
    kind: 'position',
    position_ms: 1234,
    playback_rate: 1,
    is_playing: isPlaying,
    captured_at: NOW,
    sent_at: NOW,
  };
}

function pause(): SeatEvent {
  return {
    kind: 'playback_state',
    position_ms: 2000,
    playback_rate: 1,
    is_playing: false,
    captured_at: NOW,
    sent_at: NOW,
  };
}

describe('applyContentEvent (TAB-PINNED seat)', () => {
  it('an empty seat is claimed by the first announcing tab and its message goes out', () => {
    const state = freshState();
    const out = applyContentEvent(state, announce('vid1'), 7, false, NOW);
    expect(state.activeTabId).toBe(7);
    expect(out.sends.map((m) => m.type)).toEqual(['track_changed']);
    expect(out.logs.some((l) => l.context === 'seat')).toBe(true);
  });

  it('a fresh claim via a position event re-keys the overlay with the stored track first', () => {
    const state = freshState();
    applyContentEvent(state, announce('vid1'), 7, false, NOW); // seat claimed + snapshot
    state.activeTabId = null; // seat later cleared (e.g. succession found none)
    const out = applyContentEvent(state, position(true), 7, undefined, NOW + 1);
    expect(out.sends.map((m) => m.type)).toEqual(['track_changed', 'position']);
  });

  it('announcing does NOT earn isPlaying (phantom pages announce without playing)', () => {
    const state = freshState();
    applyContentEvent(state, announce('vid1'), 7, false, NOW);
    expect(state.tabs[7]?.isPlaying).toBe(false);
  });

  it('a non-seat tab is recorded but its stream never reaches the overlay', () => {
    const state = freshState();
    applyContentEvent(state, announce('vid1'), 7, false, NOW);
    const out = applyContentEvent(state, position(true), 8, undefined, NOW + 1);
    expect(out.sends).toEqual([]);
    expect(state.tabs[8]?.isPlaying).toBe(true); // recorded for succession
    expect(state.activeTabId).toBe(7); // pinned
  });

  it('pause/track-change on another tab never moves the seat', () => {
    const state = freshState();
    applyContentEvent(state, announce('vid1'), 7, false, NOW);
    applyContentEvent(state, pause(), 7, undefined, NOW + 1); // seat tab pauses
    const out = applyContentEvent(state, announce('vid2'), 8, true, NOW + 2);
    expect(state.activeTabId).toBe(7);
    expect(out.sends).toEqual([]);
  });

  it('a pause lands in the snapshot as a paused position (replay must not extrapolate)', () => {
    const state = freshState();
    applyContentEvent(state, announce('vid1'), 7, false, NOW);
    applyContentEvent(state, position(true), 7, undefined, NOW + 1);
    applyContentEvent(state, pause(), 7, undefined, NOW + 2);
    expect(state.snapshots[7]?.position?.is_playing).toBe(false);
    expect(state.snapshots[7]?.position?.position_ms).toBe(2000);
  });
});

describe('handleTabRemoved (succession)', () => {
  function seatWithTwoTabs(): SessionState {
    const state = freshState();
    applyContentEvent(state, announce('vid1'), 7, true, NOW);
    applyContentEvent(state, position(true), 7, undefined, NOW);
    applyContentEvent(state, announce('vid2'), 8, false, NOW);
    return state;
  }

  it('closing a non-seat tab changes nothing', () => {
    const state = seatWithTwoTabs();
    const out = handleTabRemoved(state, 8, NOW);
    expect(state.activeTabId).toBe(7);
    expect(out.sends).toEqual([]);
    expect(out.reannounceTabId).toBeNull();
  });

  it('closing the seat with no PLAYING survivor clears immediately (source_gone)', () => {
    const state = seatWithTwoTabs(); // tab 8 announced but never played
    const out = handleTabRemoved(state, 7, NOW);
    expect(state.activeTabId).toBeNull();
    expect(out.sends.map((m) => m.type)).toEqual(['source_gone']);
  });

  it('closing the seat hands over ONLY to a playing tab, replaying its snapshot paused', () => {
    const state = seatWithTwoTabs();
    applyContentEvent(state, position(true), 8, undefined, NOW + 1); // 8 now playing
    const out = handleTabRemoved(state, 7, NOW + 2);
    expect(state.activeTabId).toBe(8);
    expect(out.sends.map((m) => m.type)).toEqual(['track_changed', 'position']);
    const pos = out.sends.find((m) => m.type === 'position');
    expect(pos && 'is_playing' in pos && pos.is_playing).toBe(false);
    expect(out.reannounceTabId).toBe(8);
  });
});

describe('snapshotReplayMessages', () => {
  it('replays the active snapshot paused, and nothing when stale', () => {
    const state = freshState();
    applyContentEvent(state, announce('vid1'), 7, false, NOW);
    applyContentEvent(state, position(true), 7, undefined, NOW);
    const fresh = snapshotReplayMessages(state, NOW + 1000);
    expect(fresh.map((m) => m.type)).toEqual(['track_changed', 'position']);
    const pos = fresh.find((m) => m.type === 'position');
    expect(pos && 'is_playing' in pos && pos.is_playing).toBe(false);

    const stale = snapshotReplayMessages(state, NOW + SNAPSHOT_MAX_AGE_MS + 1);
    expect(stale).toEqual([]);
  });

  it('replays nothing without an active seat', () => {
    expect(snapshotReplayMessages(freshState(), NOW)).toEqual([]);
  });
});
