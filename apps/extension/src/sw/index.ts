/**
 * Service worker glue: receives ContentEvents from tabs, maintains per-tab
 * state in chrome.storage.session (survives SW death — R-10). The seat is
 * TAB-PINNED: it belongs to one tab until that tab closes (pause/resume/
 * track-changes never move it); succession happens only in tabs.onRemoved,
 * and ONLY that tab's stream reaches the overlay. On (re)connect the active tab's last
 * track+position snapshot is replayed so the overlay never starts blind.
 *
 * ALL state mutations run through a lock: handlers interleave at awaits
 * (4 Hz position events vs tabs.onRemoved etc.), and an unserialized
 * read-modify-write can resurrect a closed tab as the active source with a
 * stale copy — silently dropping the surviving tab's stream.
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
  (line) => slog('conn', line),
);

/** Diagnostic: SW console + mirrored to the overlay terminal when connected. */
function slog(context: string, line: string): void {
  console.debug(`[kashi-sw:${context}] ${line}`);
  connection.send({ type: 'log', context, line, sent_at: Date.now() });
}

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

let stateLock: Promise<unknown> = Promise.resolve();

/** Serialized read-modify-write; the mutator may send while holding the lock. */
function withState<T>(mutate: (state: SessionState) => T | Promise<T>): Promise<T> {
  const run = stateLock.then(async () => {
    const state = await readState();
    const result = await mutate(state);
    await writeState(state);
    return result;
  });
  stateLock = run.catch(() => {});
  return run;
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

  if (event.kind === 'log') {
    slog(`tab${tabId}`, event.line);
    return;
  }

  // Ground truth check on announcements: the sender must be a REAL tab.
  // chrome.tabs.get throws for phantom contexts — drop those entirely.
  // (Queried OUTSIDE the lock; applied inside.)
  let freshAudible: boolean | undefined;
  if (event.kind === 'track_changed') {
    try {
      freshAudible = (await chrome.tabs.get(tabId)).audible ?? false;
    } catch {
      console.debug(`[kashi-sw] dropped announce from non-existent tab ${tabId}`);
      return;
    }
  }

  await withState((state) => {
    // A tab earns isPlaying only through playback events — announcing a track
    // proves nothing (phantom/prerender pages announce without ever playing).
    const wasPlaying = state.tabs[tabId]?.isPlaying ?? false;
    const isPlaying =
      event.kind === 'track_changed' || event.kind === 'ad_state'
        ? wasPlaying
        : event.is_playing;
    const audible = freshAudible ?? state.tabs[tabId]?.audible;
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
    } else if (msg.type === 'seek' || msg.type === 'playback_state') {
      // A pause MUST land in the snapshot: replaying a stale is_playing=true
      // report catapults the overlay clock forward by the whole gap.
      snapshot.position = {
        type: 'position',
        seq: 0,
        sent_at: msg.sent_at,
        tab_id: tabId,
        position_ms: msg.position_ms,
        playback_rate: snapshot.position?.playback_rate ?? 1,
        is_playing: msg.is_playing,
        captured_at: msg.captured_at,
      } as Snapshot['position'];
      snapshot.savedAt = Date.now();
    }
    state.snapshots[tabId] = snapshot;

    const before = state.activeTabId;
    // TAB-PINNED seat (user verdict 2026-07-08 final): lyrics belong to a TAB
    // until that tab CLOSES. Pause/resume/track-changes/transient silence
    // never move the seat — YTM goes briefly silent on every track switch,
    // which made any state-based handover jump to another tab mid-listening.
    // An empty seat is claimed by the first tab that plays (or announces);
    // succession on close lives in tabs.onRemoved.
    if (state.activeTabId === null && (isPlaying || msg.type === 'track_changed')) {
      state.activeTabId = tabId;
      slog('seat', `tab - -> ${tabId} (claim, via ${event.kind})`);
    }

    if (state.activeTabId !== tabId) return; // not the seat tab — recorded only

    // Fresh claim via a non-track event: re-key the overlay first so the
    // position stream never applies to a previous source's lyrics.
    if (before === null && msg.type !== 'track_changed') {
      const track = state.snapshots[tabId]?.track;
      if (track) connection.send(track);
    }
    connection.send(msg);
  });
}

async function replayActiveSnapshot(): Promise<void> {
  await withState((state) => {
    if (state.activeTabId === null) return;
    const snapshot = state.snapshots[state.activeTabId];
    // Stale snapshots (e.g. overlay restarted hours later) must not resurrect
    // an old track — the reannounce that follows will bring live state.
    if (!snapshot?.savedAt || Date.now() - snapshot.savedAt > SNAPSHOT_MAX_AGE_MS) return;
    if (snapshot.track) connection.send(snapshot.track);
    // Replayed positions go out PAUSED: the clock anchors at the last known
    // spot instead of extrapolating a stale is_playing=true report (the
    // latency clamp caps compensation at 5s, so minutes-old gaps time-shift).
    if (snapshot.position) connection.send({ ...snapshot.position, is_playing: false });
  });
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

// Audibility is RECORDED only (it feeds succession choice on tab close) —
// it never moves the seat: YTM flips audible around every track switch and a
// state-based handover made lyrics jump between tabs (user verdict).
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.audible === undefined) return;
  void withState((state) => {
    const tab = state.tabs[tabId];
    if (!tab) return;
    tab.audible = changeInfo.audible;
    slog('tabs', `tab ${tabId} audible -> ${changeInfo.audible}`);
  });
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void withState((state) => {
    delete state.tabs[tabId];
    delete state.snapshots[tabId];
    slog('tabs', `tab ${tabId} closed`);
    if (state.activeTabId === tabId) {
      // Only a tab that is actually PLAYING may take over; a paused leftover
      // must not resurrect stale lyrics. No successor -> clear immediately.
      const playing = Object.fromEntries(
        Object.entries(state.tabs).filter(([, t]) => t.isPlaying),
      );
      state.activeTabId = selectActiveTab(playing, null);
      slog('seat', `tab ${tabId} -> ${state.activeTabId ?? '-'} (active tab closed)`);
      if (state.activeTabId === null) {
        connection.send({ type: 'source_gone', sent_at: Date.now() });
        return;
      }
      const next = state.snapshots[state.activeTabId];
      if (next?.track) connection.send(next.track);
      if (next?.position) connection.send({ ...next.position, is_playing: false });
      // Refresh from the live page — its snapshot may be minutes old.
      const successor = state.activeTabId;
      void chrome.tabs.sendMessage(successor, { kind: 'reannounce' }).catch(() => {});
    }
  });
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
console.debug(`[kashi-sw] service worker ready v${chrome.runtime.getManifest().version}`);
