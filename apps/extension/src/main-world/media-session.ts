/**
 * MAIN-world script: reads `navigator.mediaSession.metadata` (invisible to
 * ISOLATED content scripts) and forwards it via window.postMessage.
 * Pattern proven by web-scrobbler's YTM connector.
 *
 * Phase 2 will add: MutationObserver on playback state, metadata debounce
 * (metadata settles milliseconds after track changes).
 */
console.debug('[kashi] main-world bridge loaded');
