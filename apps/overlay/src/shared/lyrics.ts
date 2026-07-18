/**
 * THE home of the lyric payload shapes crossing the main→renderer IPC
 * boundary (Faz 5 P5 — previously four hand copies drifted independently).
 *
 * The drift guards below pin these to the GENERATED schema types: after a
 * `pnpm codegen`, any server-contract change that stops fitting our tolerant
 * IPC shapes breaks typecheck HERE, not in the field. Direction matters —
 * the guards assert every valid SCHEMA value is representable in OUR types
 * (consumers stay tolerant/wider, never narrower). Type-only: nothing from
 * @kashi/schemas lands in any bundle.
 */

import type { KashiProcessedTrackV1 } from '@kashi/schemas';

export interface WordTiming {
  start_ms: number;
  end_ms: number;
  text: string;
}

export interface LyricLine {
  start_ms: number;
  end_ms: number;
  text: string;
  /** Nonlexical ad-lib line (server 2.1.0+; older docs lack it — tolerant). */
  adlib?: boolean;
  /** Present on kashi-server word-sync documents (Faz 3B). */
  words?: WordTiming[];
}

export interface PaletteData {
  source?: string;
  primary?: string;
  secondary?: string;
  background?: string;
  text?: string;
  accent?: string;
}

export interface BeatsData {
  bpm: number;
  confidence?: number;
  times_ms: number[];
  downbeat_indices?: number[];
}

/** Semantic effect tags (server 2.6.0+, Faz 6). Indices reference lines[]
 * of the SAME payload; sparse by design (server caps ~60 words/doc). */
export interface FxWordTag {
  line: number;
  word: number;
  tag: string;
  intensity: number;
}

export interface FxLineTag {
  line: number;
  tag: string;
}

export interface FxData {
  lexicon: string;
  engine: string;
  words?: FxWordTag[];
  /** Line-level THEME tags (embedding layer) — consumed from P5 on. */
  lines?: FxLineTag[];
}

/** Track-normalized loudness curve (server 2.6.0+): 0-100 ints @ rate_hz
 * on the PLAYED clock — drives the hype intensity ramp. */
export interface EnergyData {
  rate_hz: number;
  values: number[];
}

/** Coarse song sections (server 2.6.0+). type is open — v1 emits "high". */
export interface SectionData {
  type: string;
  start_ms: number;
  end_ms: number;
}

// --- compile-time drift guards (schema value ⊆ our IPC type) ---
type SchemaLine = KashiProcessedTrackV1['lines'][number];
type SchemaWord = NonNullable<SchemaLine['words']>[number];
type SchemaPalette = NonNullable<KashiProcessedTrackV1['palette']>;
type SchemaBeats = NonNullable<KashiProcessedTrackV1['beats']>;
type Satisfies<T extends Base, Base> = T;

export type _WordDriftGuard = Satisfies<SchemaWord, WordTiming>;
export type _LineDriftGuard = Satisfies<SchemaLine, LyricLine>;
export type _PaletteDriftGuard = Satisfies<SchemaPalette, PaletteData>;
export type _BeatsDriftGuard = Satisfies<SchemaBeats, BeatsData>;
type SchemaFx = NonNullable<KashiProcessedTrackV1['fx']>;
export type _FxDriftGuard = Satisfies<SchemaFx, FxData>;
type SchemaEnergy = NonNullable<KashiProcessedTrackV1['energy']>;
export type _EnergyDriftGuard = Satisfies<SchemaEnergy, EnergyData>;
type SchemaSection = NonNullable<KashiProcessedTrackV1['sections']>[number];
export type _SectionDriftGuard = Satisfies<SchemaSection, SectionData>;
