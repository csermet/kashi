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
     * Normalized alignment confidence. Clients should fall back to line rendering below ~0.5.
     */
    quality_score: number;
    /**
     * Playback-speed factor the audio was corrected by before alignment (nightcore workflow); 1.0 for unmodified audio.
     */
    speed_factor?: number;
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
}
