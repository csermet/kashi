/**
 * ISOLATED-world content script.
 *
 * Phase 2 will add: video element tracking via `timeupdate` (never polling),
 * `yt-navigate-finish` navigation handling, ad filtering
 * (`.ytmusic-player-bar.advertisement`), and capture-time stamping of positions.
 * videoId always comes from the URL, never from mediaSession metadata.
 */
console.debug('[kashi] content script loaded');
