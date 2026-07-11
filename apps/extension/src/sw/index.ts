/**
 * Service worker glue: receives ContentEvents from tabs, maintains per-tab
 * state in chrome.storage.session (survives SW death — R-10). The seat state
 * machine itself is PURE and lives in logic.ts (applyContentEvent /
 * handleTabRemoved / snapshotReplayMessages); this file owns only the chrome
 * APIs, the overlay connection, and the storage lock.
 *
 * ALL state mutations run through the lock: handlers interleave at awaits
 * (4 Hz position events vs tabs.onRemoved etc.), and an unserialized
 * read-modify-write can resurrect a closed tab as the active source with a
 * stale copy — silently dropping the surviving tab's stream.
 */
import type { ContentEvent } from '../shared/messages.js';
import { OverlayConnection } from './connection.js';
import {
  applyContentEvent,
  handleTabRemoved,
  snapshotReplayMessages,
  type SeatOutcome,
  type SessionState,
} from './logic.js';

const STATE_KEY = 'kashi-state';
const WATCHDOG_ALARM = 'kashi-watchdog';

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

function emit(outcome: SeatOutcome): void {
  for (const log of outcome.logs) slog(log.context, log.line);
  for (const msg of outcome.sends) connection.send(msg);
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
    emit(applyContentEvent(state, event, tabId, freshAudible, Date.now()));
  });
}

async function replayActiveSnapshot(): Promise<void> {
  await withState((state) => {
    for (const msg of snapshotReplayMessages(state, Date.now())) connection.send(msg);
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
    const outcome = handleTabRemoved(state, tabId, Date.now());
    emit(outcome);
    if (outcome.reannounceTabId !== null) {
      // Refresh from the live page — its snapshot may be minutes old.
      void chrome.tabs.sendMessage(outcome.reannounceTabId, { kind: 'reannounce' }).catch(() => {});
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
