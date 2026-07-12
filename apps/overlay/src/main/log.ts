/**
 * Terminal logging for the overlay main process (Faz 4 saha geri bildirimi:
 * logs were unreadable on Windows and inconsistent everywhere).
 *
 * Format: `HH:MM:SS.mmm component  message` — one wall-clock stamp per line
 * (correlates with server/Grafana timestamps), a fixed-width component tag,
 * and ASCII-safe DECORATION (data like song titles stays Unicode; the console
 * codepage fix below makes it render correctly).
 */
import { execSync } from 'node:child_process';

/**
 * Windows consoles default to a legacy codepage (CP850/857) that renders
 * UTF-8 as mojibake ("♪" → "ΓÖ¬", "ı" → "─▒") when output is piped, as it is
 * under `electron-vite dev`. Switching the attached console to UTF-8 fixes
 * every downstream log line; a no-op everywhere else, cosmetic on failure.
 */
export function enableUtf8Console(): void {
  if (process.platform !== 'win32') return;
  try {
    execSync('chcp 65001', { stdio: 'ignore' });
  } catch {
    // Purely cosmetic — never let console setup break startup.
  }
}

export function formatStamp(date: Date): string {
  const p = (n: number, w = 2) => String(n).padStart(w, '0');
  return `${p(date.getHours())}:${p(date.getMinutes())}:${p(date.getSeconds())}.${p(
    date.getMilliseconds(),
    3,
  )}`;
}

/** Longest fixed tag ("settings"/"renderer") — keeps message columns aligned. */
const COMPONENT_PAD = 8;

export function formatLogLine(component: string, line: string, date = new Date()): string {
  return `${formatStamp(date)} ${component.padEnd(COMPONENT_PAD)} ${line}`;
}

export type Logger = (line: string) => void;

export function makeLogger(component: string): Logger {
  return (line) => console.debug(formatLogLine(component, line));
}

export function makeWarnLogger(component: string): Logger {
  return (line) => console.warn(formatLogLine(component, line));
}
