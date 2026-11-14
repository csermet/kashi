/**
 * @kashi/view — the lyrics view, written once for every host (client review
 * E.4): the display rules, the effects, the styles and the DOM/canvas
 * renderer. It imports nothing from Electron or Node (a test walks its import
 * graph); a host feeds it through a small bridge.
 */
export * from './artwork-palette.js';
export * from './box-scale.js';
export * from './clock-stats.js';
export * from './color-tone.js';
export * from './document.js';
export * from './effect-level.js';
export * from './effects-logic.js';
export * from './fx-canvas.js';
export * from './fx-icons.js';
export * from './fx-particles-logic.js';
export * from './fx-textures.js';
export * from './host.js';
export * from './idle-stash.js';
export * from './latency-marker.js';
export * from './lyrics.js';
export * from './lyrics-view.js';
export * from './position-clock.js';
export * from './snap-classifier.js';
export * from './text-scale.js';
export * from './timing-offset.js';
export * from './view-logic.js';
