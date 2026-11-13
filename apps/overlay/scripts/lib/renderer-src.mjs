// Loads the overlay's own TypeScript modules into a Node script, so the audit
// and golden tools compute what the renderer computes instead of keeping a
// copy of its rules (the twin trap). Node >= 22.18 strips the types.
import { register } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// src/ imports siblings as './x.js' while only x.ts exists; Node's type
// stripping does not remap that, so fall back to .ts on a miss.
register(
  'data:text/javascript,' +
    encodeURIComponent(`
export async function resolve(specifier, context, next) {
  try { return await next(specifier, context); }
  catch (err) {
    if (err?.code === 'ERR_MODULE_NOT_FOUND' && specifier.endsWith('.js'))
      return next(specifier.slice(0, -3) + '.ts', context);
    throw err;
  }
}`),
);

/** The pure modules of the overlay at `overlayRoot` (apps/overlay). */
export async function loadRendererSrc(overlayRoot) {
  const src = (p) => pathToFileURL(join(overlayRoot, 'src', p)).href;
  const [main, settings, timing, view, effects, marker] = await Promise.all([
    import(src('main/kashi-server-logic.ts')),
    import(src('main/settings-logic.ts')),
    import(src('shared/timing-offset.ts')),
    import(src('renderer/src/view-logic.ts')),
    import(src('renderer/src/effects-logic.ts')),
    import(src('renderer/src/latency-marker.ts')),
  ]);
  return {
    mapDocument: main.mapDocument,
    DEFAULT_SETTINGS: settings.DEFAULT_SETTINGS,
    appliedLeadMs: timing.appliedLeadMs,
    view,
    effects,
    marker,
  };
}
