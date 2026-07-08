/**
 * Tiny JSON settings store (no dependency): tolerant load at startup, debounced
 * atomic writes. Validation rules live in settings-logic.ts (unit-tested).
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { DEFAULT_SETTINGS, parseSettings, type StoredSettings } from './settings-logic.js';

const SAVE_DEBOUNCE_MS = 500;

export class SettingsStore {
  private settings: StoredSettings;
  private saveTimer: NodeJS.Timeout | null = null;

  constructor(
    private readonly filePath: string,
    private readonly log: (line: string) => void = () => {},
  ) {
    let raw: string | null = null;
    try {
      raw = readFileSync(filePath, 'utf8');
    } catch {
      // First run — defaults.
    }
    this.settings = raw === null ? { ...DEFAULT_SETTINGS } : parseSettings(raw);
    this.log(
      `settings loaded: alpha=${this.settings.box_alpha}` +
        ` bounds=${this.settings.window_bounds ? JSON.stringify(this.settings.window_bounds) : 'none'}`,
    );
  }

  get(): StoredSettings {
    return this.settings;
  }

  update(patch: Partial<Omit<StoredSettings, 'schema_version'>>): void {
    this.settings = { ...this.settings, ...patch, schema_version: 1 };
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.flush(), SAVE_DEBOUNCE_MS);
  }

  /** Write NOW (also the debounce target); called from before-quit. */
  flush(): void {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    try {
      mkdirSync(dirname(this.filePath), { recursive: true });
      const tmpPath = `${this.filePath}.tmp`;
      writeFileSync(tmpPath, `${JSON.stringify(this.settings, null, 2)}\n`);
      // Atomic swap: a crash mid-write must never corrupt the settings file.
      renameSync(tmpPath, this.filePath);
    } catch (err) {
      this.log(`settings save FAILED: ${String(err)}`);
    }
  }
}
