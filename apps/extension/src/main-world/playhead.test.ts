import { describe, expect, it } from 'vitest';
import { readPlayhead, type PlayerApi } from './playhead.js';

const player = (over: Partial<PlayerApi> = {}): PlayerApi => ({
  getCurrentTime: () => 30,
  getDuration: () => 208,
  getVideoData: () => ({ video_id: 'next-track' }),
  ...over,
});

describe('readPlayhead', () => {
  it('pairs the track time with the media time of the same instant', () => {
    expect(readPlayhead(player(), { currentTime: 420.942 })).toEqual({
      trackTimeS: 30,
      trackDurationS: 208,
      durationFrom: 'player',
      mediaTimeS: 420.942,
      videoId: 'next-track',
    });
  });

  it('has no sample without a player, a video or a track time', () => {
    expect(readPlayhead(null, { currentTime: 1 })).toBeNull();
    expect(readPlayhead(player(), null)).toBeNull();
    expect(readPlayhead(player({ getCurrentTime: undefined }), { currentTime: 1 })).toBeNull();
    const nan = player({ getCurrentTime: () => Number.NaN });
    expect(readPlayhead(nan, { currentTime: 1 })).toBeNull();
  });

  it('survives a player that throws mid-rebuild', () => {
    const broken = player({
      getCurrentTime: () => {
        throw new Error('player not ready');
      },
    });
    expect(readPlayhead(broken, { currentTime: 1 })).toBeNull();
  });

  it('reports an unknown duration and id as such, keeping the times', () => {
    const sample = readPlayhead(
      player({ getDuration: () => undefined, getVideoData: () => undefined }),
      { currentTime: 5 },
    );
    expect(sample).toEqual({
      trackTimeS: 30,
      trackDurationS: 0,
      durationFrom: 'player',
      mediaTimeS: 5,
      videoId: null,
    });
  });

  it("prefers the metadata's length when it names the same video", () => {
    // getDuration() reports the ~30 s preloaded segment (field: 29917 ms).
    const details = (videoId: string, lengthSeconds: unknown) =>
      player({
        getDuration: () => 29.917,
        getPlayerResponse: () => ({ videoDetails: { videoId, lengthSeconds } }),
      });
    const listed = readPlayhead(details('next-track', '155'), { currentTime: 1 });
    expect(listed).toMatchObject({ trackDurationS: 155, durationFrom: 'details' });
    // Another video's metadata, or no usable length: back to getDuration().
    for (const p of [details('prev-track', '155'), details('next-track', '0')]) {
      expect(readPlayhead(p, { currentTime: 1 })).toMatchObject({
        trackDurationS: 29.917,
        durationFrom: 'player',
      });
    }
  });

  it('keeps the sample when only the metadata read throws', () => {
    const throwing = player({
      getPlayerResponse: () => {
        throw new Error('response not ready');
      },
    });
    expect(readPlayhead(throwing, { currentTime: 1 })).toMatchObject({
      trackTimeS: 30,
      durationFrom: 'player',
    });
  });
});
