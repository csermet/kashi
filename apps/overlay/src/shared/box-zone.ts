/**
 * Window and box geometry — one definition, three readers (Faz 6.7 P4/P5).
 *
 * The main process hit-tests and migrates saved positions against BOX_ZONE,
 * the renderer aims particles around it, and the stylesheet lays the box out
 * with the matching padding. They were three hand-kept copies; two of them
 * now import this, and style-contract.test.ts nails the third to it.
 *
 * 0.15.0 grew the window a second time (640×300 → 800×460) so effects have
 * room on EVERY side of the box: 160 above as a runway, 120 below to fade out
 * in, 120 either side for a burst radius. The zone itself is the original
 * 560×180 rect and has never changed — that is what makes saved positions
 * migratable across both growths.
 *
 * FIXED size, always: transparent windows must never resize (Electron).
 */

export interface BoxRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const WINDOW_WIDTH = 800;
export const WINDOW_HEIGHT = 460;

/** Where the lyric box lives inside the window (must match #stage padding). */
export const BOX_ZONE: BoxRect = { x: 120, y: 160, width: 560, height: 180 };
