/**
 * The numbers a diagnostics report may carry from the renderer, coerced in
 * the preload before they cross IPC (R-7: the renderer is untrusted). Flat
 * name -> number or short word; anything else is left out. The kind decides
 * in main which names are kept (telemetry-logic.ts), and the server filters
 * again against its own contract.
 */

/** At most this many fields cross per report. */
export const MAX_DIAGNOSTIC_FIELDS = 32;
const NAME = /^[a-z][a-z0-9_]{0,39}$/;
const WORD = /^[a-z][a-z0-9_]{0,15}$/;

export function coerceDiagnosticFields(raw: unknown): Record<string, number | string> | undefined {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return undefined;
  const out: Record<string, number | string> = {};
  let kept = 0;
  for (const [name, value] of Object.entries(raw as Record<string, unknown>)) {
    if (kept >= MAX_DIAGNOSTIC_FIELDS) break;
    if (!NAME.test(name)) continue;
    if (typeof value === 'number' && Number.isFinite(value)) out[name] = value;
    else if (typeof value === 'string' && WORD.test(value)) out[name] = value;
    else continue;
    kept += 1;
  }
  return out;
}
