import { describe, expect, it } from 'vitest';
import type { PlayheadSample } from '../shared/messages.js';
import {
  MEDIA_DURATION_EXCESS_MS,
  offsetWorthLogging,
  parsePlayheadSample,
  playerDurationMs,
  SAME_INSTANT_TOLERANCE_MS,
  sampleFor,
  TIMELINE_OFFSET_MIN_MS,
  timelineOffsetMs,
  trackDurationMs,
  trackPositionMs,
} from './timeline.js';

const sample = (over: Partial<PlayheadSample> = {}): PlayheadSample => ({
  trackTimeS: 30,
  trackDurationS: 208,
  mediaTimeS: 30,
  videoId: 'next-track',
  ...over,
});

// The 2026-10-06 field run: Señorita (190981 ms) and Mad Love (199961 ms) had
// played gaplessly, so "Instruction" started 390.942 s into the media timeline.
const FIELD_OFFSET_S = 190.981 + 199.961;

describe('parsePlayheadSample', () => {
  it('accepts the bridge answer', () => {
    expect(parsePlayheadSample(JSON.stringify(sample()))).toEqual(sample());
  });

  it('rejects anything that is not the JSON string the bridge sends', () => {
    expect(parsePlayheadSample(sample())).toBeNull();
    expect(parsePlayheadSample(null)).toBeNull();
    expect(parsePlayheadSample('{oops')).toBeNull();
    expect(parsePlayheadSample('42')).toBeNull();
  });

  it('rejects a sample without a usable track or media time', () => {
    // JSON turns NaN into null; a page can also send garbage on purpose.
    expect(parsePlayheadSample('{"trackTimeS":null,"mediaTimeS":3}')).toBeNull();
    expect(parsePlayheadSample('{"trackTimeS":3,"mediaTimeS":-1}')).toBeNull();
    expect(parsePlayheadSample('{"trackTimeS":"3","mediaTimeS":3}')).toBeNull();
  });

  it('keeps the sample when only the duration or the id is unusable', () => {
    const parsed = parsePlayheadSample(
      '{"trackTimeS":3,"mediaTimeS":3,"trackDurationS":null,"videoId":""}',
    );
    expect(parsed).toEqual({ trackTimeS: 3, mediaTimeS: 3, trackDurationS: 0, videoId: null });
  });
});

describe('timelineOffsetMs', () => {
  it('is zero without a sample (bridge not up: old behavior)', () => {
    expect(timelineOffsetMs(null)).toBe(0);
  });

  it('is zero on a timeline that starts at zero', () => {
    expect(timelineOffsetMs(sample())).toBe(0);
  });

  it('reads the gapless offset of the field run', () => {
    expect(timelineOffsetMs(sample({ mediaTimeS: FIELD_OFFSET_S + 30 }))).toBe(390_942);
  });

  it('ignores differences below the threshold and applies one exactly on it', () => {
    const at = TIMELINE_OFFSET_MIN_MS / 1000;
    expect(timelineOffsetMs(sample({ mediaTimeS: 30 + at - 0.001 }))).toBe(0);
    expect(timelineOffsetMs(sample({ mediaTimeS: 30 + at }))).toBe(TIMELINE_OFFSET_MIN_MS);
  });

  it('never applies a negative offset', () => {
    expect(timelineOffsetMs(sample({ trackTimeS: 90, mediaTimeS: 30 }))).toBe(0);
  });
});

describe('trackPositionMs', () => {
  it('is the old formula when there is no offset', () => {
    expect(trackPositionMs(337.7, 0)).toBe(Math.round(337.7 * 1000));
  });

  it('turns the queue playhead into the track playhead', () => {
    const mediaTimeS = FIELD_OFFSET_S + 30;
    expect(trackPositionMs(mediaTimeS, timelineOffsetMs(sample({ mediaTimeS })))).toBe(30_000);
  });

  it('never goes negative', () => {
    expect(trackPositionMs(10, 12_000)).toBe(0);
  });
});

describe('playerDurationMs', () => {
  it("is the player's duration for the video it names", () => {
    expect(playerDurationMs(sample(), 'next-track')).toBe(208_000);
  });

  it('refuses a sample that still describes another video (mid-switch)', () => {
    expect(playerDurationMs(sample({ videoId: 'prev-track' }), 'next-track')).toBeUndefined();
    expect(playerDurationMs(sample({ videoId: null }), 'next-track')).toBeUndefined();
    expect(playerDurationMs(sample(), null)).toBeUndefined();
  });

  it('has nothing to say without a sample or a duration', () => {
    expect(playerDurationMs(null, 'next-track')).toBeUndefined();
    expect(playerDurationMs(sample({ trackDurationS: 0 }), 'next-track')).toBeUndefined();
  });
});

describe('sampleFor', () => {
  it('keeps a sample read in the same instant as the media time', () => {
    expect(sampleFor(sample({ mediaTimeS: 420.942 }), 420.942)).toEqual(
      sample({ mediaTimeS: 420.942 }),
    );
  });

  it('drops a sample about another element, at the tolerance edge exactly', () => {
    const edge = SAME_INSTANT_TOLERANCE_MS / 1000;
    expect(sampleFor(sample({ mediaTimeS: 30 + edge }), 30)).not.toBeNull();
    expect(sampleFor(sample({ mediaTimeS: 30 + edge + 0.001 }), 30)).toBeNull();
    expect(sampleFor(sample({ mediaTimeS: 30 - edge - 0.001 }), 30)).toBeNull();
  });

  it('has nothing without a reply or an element', () => {
    expect(sampleFor(null, 30)).toBeNull();
    expect(sampleFor(sample(), undefined)).toBeNull();
  });
});

describe('offsetWorthLogging', () => {
  it('logs entering and leaving a gapless run', () => {
    expect(offsetWorthLogging(0, 390_942)).toBe(true);
    expect(offsetWorthLogging(390_942, 0)).toBe(true);
  });

  it('logs a step of a whole threshold, not millisecond jitter', () => {
    expect(offsetWorthLogging(390_942, 390_942 + TIMELINE_OFFSET_MIN_MS - 1)).toBe(false);
    expect(offsetWorthLogging(390_942, 390_942 + TIMELINE_OFFSET_MIN_MS)).toBe(true);
    expect(offsetWorthLogging(390_942, 390_942 - TIMELINE_OFFSET_MIN_MS)).toBe(true);
  });
});

describe('trackDurationMs', () => {
  it("takes the player's number on an offset timeline, whatever the element says", () => {
    const s = sample({ mediaTimeS: FIELD_OFFSET_S + 30 });
    expect(trackDurationMs(630_142, s, 'next-track')).toBe(208_000);
    expect(trackDurationMs(undefined, s, 'next-track')).toBe(208_000);
    // ...but only for the video the player names (mid-switch: nothing).
    expect(trackDurationMs(630_142, s, 'other-track')).toBeUndefined();
  });

  it('overrules the first track of a run once the next one is appended', () => {
    // Offset 0, element duration = this track + the next one.
    expect(trackDurationMs(208_000 + 199_961, sample(), 'next-track')).toBe(208_000);
  });

  it("keeps the element's number when it is not clearly the longer one", () => {
    const edge = 208_000 + MEDIA_DURATION_EXCESS_MS;
    expect(trackDurationMs(edge, sample(), 'next-track')).toBe(edge);
    expect(trackDurationMs(edge + 1, sample(), 'next-track')).toBe(208_000);
    expect(trackDurationMs(207_000, sample(), 'next-track')).toBe(207_000);
  });

  it('is the old behavior without a sample, and never invents a fresh duration', () => {
    expect(trackDurationMs(630_142, null, 'next-track')).toBe(630_142);
    expect(trackDurationMs(undefined, null, 'next-track')).toBeUndefined();
    // Offset 0 and the element's number withheld as stale: still nothing.
    expect(trackDurationMs(undefined, sample(), 'next-track')).toBeUndefined();
  });
});
