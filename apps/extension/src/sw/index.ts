/**
 * Service worker glue: receives ContentEvents from tabs, maintains per-tab
 * state in chrome.storage.session (survives SW death — R-10), picks the
 * active tab (playing > most recent), and forwards ONLY the active tab's
 * stream to the overlay. On (re)connect the active tab's last track+position
 * snapshot is replayed so the overlay never starts blind.
 */
import type { ExtensionToOverlayMessage, TrackInfo } from '@kashi/protocol';
import type { ContentEvent } from '../shared/messages.js';
import { OverlayConnection, type Sendable } from './connection.js';
import { selectActiveTab, type TabState } from './logic.js';

const STATE_KEY = 'kashi-state';
const WATCHDOG_ALARM = 'kashi-watchdog';

type Snapshot = {
  track?: Extract<ExtensionToOverlayMessage, { type: 'track_changed' }>;
  position?: Extract<ExtensionToOverlayMessage, { type: 'position' }>;
  savedAt?: number;
};

/** Snapshots older than this are not replayed — a fresh reannounce wins. */
const SNAPSHOT_MAX_AGE_MS = 5 * 60_000;

interface SessionState {
  tabs: Record<number, TabState>;
  snapshots: Record<number, Snapshot>;
  activeTabId: number | null;
}

const EMPTY_STATE: SessionState = { tabs: {}, snapshots: {}, activeTabId: null };

const connection = new OverlayConnection(
  () => {
    // Instant UX from the stored snapshot, then ask the live page to
    // re-announce — fresh state corrects anything stale.
    void replayActiveSnapshot().then(requestReannounce);
  },
  (line) => console.debug(`[kashi-sw] ${line}`),
);

async function requestReannounce(): Promise<void> {
  try {
    const tabs = await chrome.tabs.query({ url: 'https://music.youtube.com/*' });
    for (const tab of tabs) {
      if (tab.id !== undefined) {
        void chrome.tabs.sendMessage(tab.id, { kind: 'reannounce' }).catch(() => {});
      }
    }
  } catch {
    /* no tabs / no permission — nothing to refresh */
  }
}

async function readState(): Promise<SessionState> {
  const stored = await chrome.storage.session.get(STATE_KEY);
  return (stored[STATE_KEY] as SessionState | undefined) ?? structuredClone(EMPTY_STATE);
}

async function writeState(state: SessionState): Promise<void> {
  await chrome.storage.session.set({ [STATE_KEY]: state });
}

function toProtocolMessage(event: ContentEvent, tabId: number): Sendable | null {
  switch (event.kind) {
    case 'track_changed': {
      const track: TrackInfo = {
        source: { type: 'youtube', id: event.videoId },
        title: event.title,
        artist: event.artist,
        album: event.album,
        duration_ms: event.duration_ms,
        artwork_url: event.artwork_url,
      };
      return { type: 'track_changed', sent_at: event.sent_at, tab_id: tabId, track };
    }
    case 'position':
      return {
        type: 'position',
        sent_at: event.sent_at,
        tab_id: tabId,
        position_ms: event.position_ms,
        playback_rate: event.playback_rate,
        is_playing: event.is_playing,
        captured_at: event.captured_at,
      };
    case 'seek':
    case 'playback_state':
      return {
        type: event.kind,
        sent_at: event.sent_at,
        tab_id: tabId,
        position_ms: event.position_ms,
        is_playing: event.is_playing,
        captured_at: event.captured_at,
      };
    case 'ad_state':
      return { type: 'ad_state', sent_at: event.sent_at, tab_id: tabId, is_ad: event.is_ad };
    default:
      return null;
  }
}

async function handleContentEvent(event: ContentEvent, tabId: number): Promise<void> {
  connection.ensureConnected();
  const state = await readState();

  // A tab earns isPlaying only through playback events — announcing a track
  // proves nothing (phantom/prerender pages announce without ever playing).
  const isPlaying =
    event.kind === 'track_changed' || event.kind === 'ad_state'
      ? (state.tabs[tabId]?.isPlaying ?? false)
      : event.is_playing;

  // Ground truth check on announcements: the sender must be a REAL tab.
  // chrome.tabs.get throws for phantom contexts — drop those entirely.
  let audible = state.tabs[tabId]?.audible;
  if (event.kind === 'track_changed') {
    try {
      audible = (await chrome.tabs.get(tabId)).audible ?? false;
    } catch {
      console.debug(`[kashi-sw] dropped announce from non-existent tab ${tabId}`);
      return;
    }
  }
  state.tabs[tabId] = { isPlaying, lastEventAt: Date.now(), audible };

  const msg = toProtocolMessage(event, tabId);
  if (!msg) return;

  const snapshot = state.snapshots[tabId] ?? {};
  if (msg.type === 'track_changed') {
    snapshot.track = { ...msg, seq: 0 } as Snapshot['track'];
    snapshot.position = undefined;
    snapshot.savedAt = Date.now();
  } else if (msg.type === 'position') {
    snapshot.position = { ...msg, seq: 0 } as Snapshot['position'];
    snapshot.savedAt = Date.now();
  }
  state.snapshots[tabId] = snapshot;

  const previousActive = state.activeTabId;
  state.activeTabId = selectActiveTab(state.tabs, state.activeTabId);
  await writeState(state);

  if (state.activeTabId !== tabId) return; // inactive tab — recorded, not forwarded

  // The active tab changed → re-announce its track before streaming (protocol
  // §multi-tab: the overlay must never apply positions to the wrong track).
  if (previousActive !== state.activeTabId && msg.type !== 'track_changed') {
    const track = state.snapshots[tabId]?.track;
    if (track) connection.send(track);
  }
  connection.send(msg);
}

async function replayActiveSnapshot(): Promise<void> {
  const state = await readState();
  if (state.activeTabId === null) return;
  const snapshot = state.snapshots[state.activeTabId];
  // Stale snapshots (e.g. overlay restarted hours later) must not resurrect
  // an old track — the reannounce that follows will bring live state.
  if (!snapshot?.savedAt || Date.now() - snapshot.savedAt > SNAPSHOT_MAX_AGE_MS) return;
  if (snapshot.track) connection.send(snapshot.track);
  if (snapshot.position) connection.send(snapshot.position);
}

// --- listeners (top-level, so every SW wake re-registers them) -------------

chrome.runtime.onMessage.addListener((message, sender) => {
  const tabId = sender.tab?.id;
  if (typeof tabId !== 'number') return;
  // Prerendered/cached documents are phantom senders with their own tab ids —
  // their announcements must never reach the overlay (defense in depth on top
  // of the content-script prerender gate).
  const lifecycle = (sender as { documentLifecycle?: string }).documentLifecycle;
  if (lifecycle && lifecycle !== 'active') return;
  void handleContentEvent(message as ContentEvent, tabId);
});

// Chrome tells us when sound starts/stops in a tab — the strongest signal for
// picking the active source. Reselect on every audibility flip.
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.audible === undefined) return;
  void (async () => {
    const state = await readState();
    const tab = state.tabs[tabId];
    if (!tab) return;
    tab.audible = changeInfo.audible;
    const previous = state.activeTabId;
    state.activeTabId = selectActiveTab(state.tabs, state.activeTabId);
    await writeState(state);
    if (state.activeTabId !== previous && state.activeTabId !== null) {
      const snapshot = state.snapshots[state.activeTabId];
      if (snapshot?.track) connection.send(snapshot.track);
      if (snapshot?.position) connection.send(snapshot.position);
    }
  })();
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void (async () => {
    const state = await readState();
    delete state.tabs[tabId];
    delete state.snapshots[tabId];
    if (state.activeTabId === tabId) {
      state.activeTabId = selectActiveTab(state.tabs, null);
      const next = state.activeTabId !== null ? state.snapshots[state.activeTabId] : null;
      if (next?.track) connection.send(next.track);
      if (next?.position) connection.send(next.position);
    }
    await writeState(state);
  })();
});

chrome.runtime.onInstalled.addListener(() => {
  void chrome.alarms.create(WATCHDOG_ALARM, { periodInMinutes: 1 });
});
chrome.runtime.onStartup.addListener(() => {
  void chrome.alarms.create(WATCHDOG_ALARM, { periodInMinutes: 1 });
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === WATCHDOG_ALARM) connection.ensureConnected();
});

connection.ensureConnected();
console.debug('[kashi-sw] service worker ready v0.1.1');
