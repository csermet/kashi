/**
 * Kashi extension service worker.
 *
 * Owns the WebSocket connection to the local overlay (127.0.0.1) — the socket
 * must live HERE, not in a content script (Chrome 147+ Local Network Access).
 *
 * Phase 2 will add: connection manager with exponential backoff + jitter,
 * chrome.alarms watchdog, tab dedup via chrome.storage.session, snapshot
 * replay after reconnect.
 */
import { PROTOCOL_VERSION } from '@kashi/protocol';

console.debug(`[kashi] service worker loaded (protocol v${PROTOCOL_VERSION})`);

chrome.runtime.onInstalled.addListener(() => {
  console.debug('[kashi] installed');
});
