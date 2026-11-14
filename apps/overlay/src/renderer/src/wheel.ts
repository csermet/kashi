/**
 * Desktop input: the scroll-gesture accumulator behind Ctrl+scroll (box
 * opacity) and Ctrl+Shift+scroll (timing offset). Pure, unit-tested; moved
 * out of the shared view's view-logic.ts (client review E.4) because only a
 * desktop host has a wheel.
 */

/**
 * Wheel deltas are device-dependent: pixel touchpads fire dozens of tiny
 * events per swipe while classic wheels send one ±100-px notch. Accumulate
 * into ~100-px steps so one notch ≈ one opacity step and a touchpad swipe
 * stays fine-grained instead of slamming the value to a bound.
 */
export const WHEEL_PX_PER_STEP = 100;
/** deltaMode 1 = lines, 2 = pages — normalize to approximate pixels. */
const WHEEL_LINE_PX = 20;
const WHEEL_PAGE_PX = 100;

export function accumulateWheel(
  accumulatedPx: number,
  deltaY: number,
  deltaMode: number,
): { accumulatedPx: number; steps: number } {
  if (deltaY === 0 || !Number.isFinite(deltaY)) {
    // Pure horizontal scroll (tilt wheel / two-finger sideways) is not an
    // opacity gesture — treating it as one dims the box by surprise.
    return { accumulatedPx, steps: 0 };
  }
  let unitPx = 1; // deltaMode 0: already pixels
  if (deltaMode === 1) unitPx = WHEEL_LINE_PX;
  else if (deltaMode === 2) unitPx = WHEEL_PAGE_PX;
  const px = deltaY * unitPx;
  const total = accumulatedPx + px;
  // `|| 0` normalizes Math.trunc's -0 — don't leak it into IPC/comparisons.
  const steps = Math.trunc(total / WHEEL_PX_PER_STEP) || 0;
  return { accumulatedPx: total - steps * WHEEL_PX_PER_STEP, steps };
}
