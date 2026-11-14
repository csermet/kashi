/**
 * Chain-latency marker (client review step E.1, "measure the 200 ms lead"):
 * a dev-only block of black/white cells in the window's top-left corner that
 * changes on every word activation, so a 60 fps screen recording with desktop
 * audio shows WHEN each word lit (apps/overlay/scripts/chain-latency.mjs reads
 * it against the song's audio).
 *
 * Build-time switch, like KASHI_FX_DEBUG: `KASHI_LATENCY_MARKER=1
 * electron-vite build`. A normal build folds LATENCY_MARKER to `false`; the
 * block is then never created, so it cannot appear in a shipped app.
 *
 * Layout: MARKER_CELLS cells in a row, CELL_PX square each. Cell 0 is a
 * parity bit that flips on every activation (so even a repeated code is a
 * visible change); cells 1..CODE_BITS carry the activated word's index in the
 * whole document, modulo 2^CODE_BITS, most significant bit first. No CSS
 * transition: the cells change in the same frame as the word's class.
 */

declare const __KASHI_LATENCY_MARKER__: boolean;

export const LATENCY_MARKER =
  typeof __KASHI_LATENCY_MARKER__ !== 'undefined' && __KASHI_LATENCY_MARKER__;

export const MARKER_CODE_BITS = 6;
export const MARKER_CELLS = 1 + MARKER_CODE_BITS;
export const MARKER_CELL_PX = 16;
export const MARKER_ID = 'latency-marker';

/** Cell colours (true = white) for one activation: [parity, code bits MSB first]. */
export function markerCells(globalWordIndex: number, parity: boolean): boolean[] {
  const code = ((globalWordIndex % 2 ** MARKER_CODE_BITS) + 2 ** MARKER_CODE_BITS) % 2 ** MARKER_CODE_BITS;
  const bits: boolean[] = [parity];
  for (let bit = MARKER_CODE_BITS - 1; bit >= 0; bit -= 1) bits.push(((code >> bit) & 1) === 1);
  return bits;
}

/** Each line's first word as an index into the whole document's words. */
export function lineWordOffsets(lines: readonly { words?: readonly unknown[] }[]): number[] {
  const offsets: number[] = [];
  let total = 0;
  for (const line of lines) {
    offsets.push(total);
    total += line.words?.length ?? 0;
  }
  return offsets;
}

/** The block itself. Only ever constructed when LATENCY_MARKER is on (main.ts). */
export class LatencyMarker {
  private readonly cells: HTMLElement[] = [];
  private parity = false;

  constructor(parent: HTMLElement) {
    const block = document.createElement('div');
    block.id = MARKER_ID;
    const s = block.style;
    s.position = 'fixed';
    s.left = '0';
    s.top = '0';
    s.display = 'flex';
    s.zIndex = '2147483647';
    s.pointerEvents = 'none';
    s.transition = 'none';
    for (let i = 0; i < MARKER_CELLS; i += 1) {
      const cell = document.createElement('div');
      cell.style.width = `${MARKER_CELL_PX}px`;
      cell.style.height = `${MARKER_CELL_PX}px`;
      cell.style.background = '#000';
      cell.style.transition = 'none';
      block.appendChild(cell);
      this.cells.push(cell);
    }
    parent.appendChild(block);
  }

  /** A word just lit: show its code and flip the parity. */
  show(globalWordIndex: number): void {
    this.parity = !this.parity;
    markerCells(globalWordIndex, this.parity).forEach((white, i) => {
      const cell = this.cells[i];
      if (cell) cell.style.background = white ? '#fff' : '#000';
    });
  }
}
