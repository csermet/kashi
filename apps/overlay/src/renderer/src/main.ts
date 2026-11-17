/**
 * The desktop host (Electron renderer): mounts the shared view
 * (@kashi/view, client review E.4) on the window's <body> and keeps what only
 * a desktop window has —
 *   - the window geometry, handed to the view as CSS variables (where the box
 *     zone sits in the 800x460 window, how wide its text may run);
 *   - the preload bridge's desktop members: hover click-through, manual drag,
 *     the right-click menu, Ctrl+scroll opacity and Ctrl+Shift+scroll offset.
 * Everything the view needs from the bridge is the HostBridge subset.
 */
import {
  createLyricsView,
  DEFAULT_BOX_SCALE,
  parseBoxScale,
  type BoxScale,
  type HostBridge,
} from '@kashi/view';
import { geometryVars } from '../../shared/box-zone.js';
import { accumulateWheel } from './wheel.js';

/** The Electron preload's bridge (src/preload/index.ts). */
interface KashiBridge extends HostBridge {
  version: string;
  setInteractive: (interactive: boolean) => void;
  dragStart: () => void;
  dragEnd: () => void;
  adjustOpacity: (deltaSteps: number) => void;
  adjustTimingOffset: (deltaSteps: number) => void;
  openMenu: () => void;
}

declare global {
  interface Window {
    kashi: KashiBridge;
  }
}

const bridge = window.kashi;

/**
 * The window's geometry for a box size (shared/box-zone.ts geometryVars). The
 * window never changes size; the box scales inside it around a fixed centre.
 */
function applyGeometry(scale: BoxScale): void {
  for (const [name, value] of Object.entries(geometryVars(scale))) {
    document.documentElement.style.setProperty(name, value);
  }
}

// Registered BEFORE the view's own settings listener (listeners run in
// registration order), so the view measures the new zone when it re-aims the
// particle layer.
let geometryScale: BoxScale = DEFAULT_BOX_SCALE;
applyGeometry(geometryScale);
bridge.onSettings((payload) => {
  const { box_scale } = payload as { box_scale?: unknown };
  if (box_scale !== undefined && parseBoxScale(box_scale) !== geometryScale) {
    geometryScale = parseBoxScale(box_scale);
    applyGeometry(geometryScale);
  }
});

// Hover ↔ click-through: the window ignores mouse events by default but
// forwards mousemove; hovering the lyric BOX flips it interactive so it can
// be dragged (and Ctrl+scrolled), leaving flips it back (R-4). Dragging is
// manual (mousedown → IPC cursor-follow) because -webkit-app-region would
// swallow mouse events.
let interactive = false;
let dragging = false;

const view = createLyricsView(document.body, bridge, {
  // The box can hide under a MOTIONLESS cursor (ad start, watchdog reset) —
  // no mousemove follows, so without this the invisible window keeps
  // swallowing clicks until the user happens to move the mouse.
  onBoxHidden: () => {
    if (interactive && !dragging) {
      interactive = false;
      bridge.setInteractive(false);
    }
  },
});
const boxEl = view.box;

document.addEventListener('mousemove', (event) => {
  if (dragging) return;
  const over = boxEl.contains(event.target as Node);
  if (over !== interactive) {
    interactive = over;
    bridge.setInteractive(over);
  }
});
document.documentElement.addEventListener('mouseleave', () => {
  if (interactive && !dragging) {
    interactive = false;
    bridge.setInteractive(false);
  }
});
boxEl.addEventListener('mousedown', (event) => {
  if (event.button !== 0) return;
  dragging = true;
  bridge.dragStart();
});
// Right-click opens the Kashi menu (opacity presets / reset / quit) — same
// template the tray uses; the box is easier to find than the tray icon.
boxEl.addEventListener('contextmenu', (event) => {
  event.preventDefault();
  bridge.openMenu();
});
window.addEventListener('mouseup', () => {
  if (dragging) {
    dragging = false;
    bridge.dragEnd();
  }
});
window.addEventListener('blur', () => {
  if (dragging) {
    dragging = false;
    bridge.dragEnd();
  }
});
// Ctrl+scroll over the box tunes its background opacity; Ctrl+Shift+scroll
// tunes the lyric timing offset live (Faz 4.5 — the setting is inherently
// iterative, the menu path was bureaucratic). Deltas go through an
// accumulator (wheel.ts) so touchpads and classic wheels both land on
// ~1 step per comfortable gesture unit. Separate accumulators: releasing
// Shift mid-gesture must not spill leftover pixels into the other control.
let wheelAccPx = 0;
let offsetWheelAccPx = 0;
boxEl.addEventListener(
  'wheel',
  (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    if (event.shiftKey) {
      // Chromium reroutes the wheel to deltaX while Shift is held — this
      // gesture would read an eternal deltaY=0 without the fallback. The
      // opacity branch keeps ignoring horizontal deltas on purpose.
      const delta = event.deltaY !== 0 ? event.deltaY : event.deltaX;
      const { accumulatedPx, steps } = accumulateWheel(offsetWheelAccPx, delta, event.deltaMode);
      offsetWheelAccPx = accumulatedPx;
      // Scroll up = lyrics earlier (positive offset), mirroring "up = more".
      if (steps !== 0) bridge.adjustTimingOffset(-steps);
      return;
    }
    const { accumulatedPx, steps } = accumulateWheel(wheelAccPx, event.deltaY, event.deltaMode);
    wheelAccPx = accumulatedPx;
    // Scroll up (negative deltaY) increases opacity.
    if (steps !== 0) bridge.adjustOpacity(-steps);
  },
  { passive: false },
);
