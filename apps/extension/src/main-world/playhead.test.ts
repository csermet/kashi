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
    expect(sample).toEqual({ trackTimeS: 30, trackDurationS: 0, mediaTimeS: 5, videoId: null });
  });
});
