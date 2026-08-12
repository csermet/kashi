/* AUTO-GENERATED from processed-track.v1.schema.json — do not edit. Run "pnpm codegen". */

export type Ms = number;
export type Color = string;

/**
 * Processed lyrics document for one track. All timings are integer milliseconds. Schema version 1 is additive-only: consumers MUST ignore unknown fields; breaking changes require schema_version 2. When sync is "line", line objects carry no `words` array at all.
 */
export interface KashiProcessedTrackV1 {
  schema_version: 1;
  /**
   * Semver of the processing pipeline that produced this document; key input for reprocessing decisions.
   */
  pipeline_version: string;
  generated_at: string;
  track: {
    source: {
      type: "youtube" | "plex" | "upload";
      id: string;
    };
    title: string;
    artist: string;
    album?: string;
    duration_ms: Ms;
    /**
     * normalize(artist)|normalize(title)|round5(duration_s). Discovery index ONLY — never a cache key (source variants align differently).
     */
    canonical_group?: string;
  };
  /**
   * Granularity of timing data. "line": no per-word timings anywhere in `lines`. "word": at least one line carries `words`; a line whose word timings were rejected by server-side QA omits the array and renders as plain text (mixed documents).
   */
  sync: "word" | "line";
  alignment: {
    /**
     * e.g. "ctc-forced-aligner/mms-300m" or "lrclib-passthrough" for line-only documents.
     */
    method: string;
    lyrics_source?: string;
    lyrics_source_id?: number;
    vocals_separated?: boolean;
    /**
     * Normalized alignment confidence. Clients should fall back to line rendering below ~0.2 — the level this score's calibration associates with WRONG lyrics. Higher gates measure badly: against ground truth the score ranks real accuracy at Spearman +0.24, so a 0.5 gate discards documents that are in fact accurate (measured 2026-08-12: one gated document had a real PCO@0.3 of 0.979) while passing genuinely bad ones. Line-level doubt is carried per line by `uncertain` instead.
     */
    quality_score: number;
    /**
     * Which FORMULA produced quality_score (pipeline 2.5.0+; the last two added in 2.16.0). "ctc-probs": calibrated CTC probability ramp alone — no reference stamps were available. "anchors": line-anchor agreement on the windowed path. "probs+anchors": the ramp over surviving words, scaled by the fraction of referenced lines that survived QA. "line-anchors": line-anchor agreement on a document that has NO word timings at all (sync="line"). "human": human word data consumed as-is (lyricsfile), fixed 1.0. In every case except "human", word-level precision is NOT measured — a 1.0 can still drift at word granularity. Absent on documents older than 2.5.0.
     */
    quality_basis?: "ctc-probs" | "anchors" | "probs+anchors" | "line-anchors" | "human";
    /**
     * Playback-speed factor the audio was corrected by before alignment (nightcore workflow); 1.0 for unmodified audio.
     */
    speed_factor?: number;
    /**
     * Line-QA repair provenance (Faz 5): counts of lines snapped/dropped/shifted/rederived, the compensated median offset and the number of sustain-trimmed word ends. Consumers judging timing trustworthiness (e.g. a publish gate) read this; absent on documents older than pipeline 2.3.0.
     */
    qa?: {
      flagged: number;
      density_dropped: number;
      adlib_shifted: number;
      adlib_rederived: number;
      offset_ms: number;
      trimmed_ends: number;
    };
  };
  lines: {
    start_ms: Ms;
    end_ms: Ms;
    text: string;
    /**
     * Optional per-line alignment confidence (enables mixed word/line rendering).
     */
    score?: number;
    /**
     * Line is entirely nonlexical vocalization (ooh/whoa/la ad-lib); clients may style it differently (Faz 4 aesthetics). Omitted when false.
     */
    adlib?: boolean;
    /**
     * This line's word boundaries are SYNTHETIC (redistributed across the line span for sweep aesthetics), not aligner-measured. Presentation data only — never contribute them as measured timings. Omitted when false; only appears alongside `words`.
     */
    words_derived?: boolean;
    /**
     * The drift threshold flagged this line but the audio vouched for its words (Faz 8 B4): they were block-shifted onto the lrclib anchor rather than deleted. Presentation hint — a client may de-emphasise the line; the timings are still aligner-measured. Omitted when false; only appears alongside `words`.
     */
    uncertain?: boolean;
    /**
     * @minItems 1
     */
    words?: [
      {
        start_ms: Ms;
        end_ms: Ms;
        text: string;
      },
      ...{
        start_ms: Ms;
        end_ms: Ms;
        text: string;
      }[]
    ];
  }[];
  beats?: {
    bpm: number;
    confidence?: number;
    times_ms: Ms[];
    /**
     * Indices into times_ms.
     */
    downbeat_indices?: number[];
  };
  palette?: {
    source?: "album_art" | "default";
    primary?: Color;
    secondary?: Color;
    background?: Color;
    text?: Color;
    accent?: Color;
  };
  /**
   * Semantic effect tags (pipeline 2.6.0+, Faz 6). SPARSE: most words carry no tag. From 2.13.0 words[] is a SELECTED set rather than every candidate, bounded by a per-song cadence of roughly one effect per 9 s (12-24 for a normal track) — see the select field. Indices reference lines[]/words[] of THIS document. Clients without effect support ignore the whole block.
   */
  fx?: {
    /**
     * fx lexicon version, e.g. "kashi-fx/1.0.0".
     */
    lexicon: string;
    /**
     * "keywords" or "keywords+<model>@<revision12>" when the embedding layer also ran.
     */
    engine: string;
    /**
     * Selection plan that thinned words[] (pipeline 2.13.0+, e.g. "density/1.0"). ANY non-empty value means the server already chose which words fire — density, per-line quota, spacing, chorus and song cadence are all applied — so a client must render every entry as-is. ABSENT means words[] is a raw candidate list from an older pipeline, and the client must apply its own legacy rule (one winner per line). The specific value is provenance, like lexicon/engine.
     */
    select?: string;
    words?: {
      line: number;
      word: number;
      tag: string;
      intensity: number;
    }[];
    /**
     * Line-level THEME tags from the embedding layer (no trigger word identified — theme only, no word effect).
     */
    lines?: {
      line: number;
      tag: string;
    }[];
  };
  /**
   * Track-normalized loudness envelope (pipeline 2.6.0+): 0-100 ints sampled at rate_hz on the PLAYED clock (same as beats). Drives intensity ramps client-side.
   */
  energy?: {
    rate_hz: number;
    values: number[];
  };
  /**
   * Coarse song sections (pipeline 2.6.0+). type is an OPEN string: v1 emits only energy-derived "high" blocks (chorus proxy); real structure labels (verse/chorus/bridge) may join additively later.
   */
  sections?: {
    type: string;
    start_ms: Ms;
    end_ms: Ms;
  }[];
}
