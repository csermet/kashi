/**
 * pixi.js ships `lib/unsafe-eval/init.d.ts`, but its package export map omits
 * a `types` entry for the `./unsafe-eval` subpath, so TypeScript cannot follow
 * it. Nothing is imported by name — the module patches prototypes on import —
 * so an opaque declaration is the whole contract.
 */
declare module 'pixi.js/unsafe-eval';
