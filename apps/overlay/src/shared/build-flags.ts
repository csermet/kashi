/**
 * Build-time switches for dev-only renderer features (electron.vite.config.ts
 * folds them into the bundle as constants; the renderer is sandboxed and has
 * no process.env). Only the exact string "1" turns one on, so a stray
 * `KASHI_LATENCY_MARKER=true` or `=0` in a shell can never ship a debug
 * drawing in a normal build.
 */
export function buildFlag(value: string | undefined): boolean {
  return value === '1';
}
