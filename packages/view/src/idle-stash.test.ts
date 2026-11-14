import { describe, expect, it } from 'vitest';
import { IdleStash } from './idle-stash.js';

describe('IdleStash', () => {
  it('gives the lyrics back when the SAME track is re-announced after idling', () => {
    // The 2026-10 review case: the watchdog idles the renderer mid-song, then a
    // duplicate announce re-sends the track only. Without this the song plays
    // on with no lyrics.
    const stash = new IdleStash<string>();
    stash.remember('yt:A', 'lyrics of A');
    stash.idle('yt:A');
    expect(stash.take('yt:A')).toBe('lyrics of A');
  });

  it('gives them back at most once', () => {
    const stash = new IdleStash<string>();
    stash.remember('yt:A', 'lyrics of A');
    stash.idle('yt:A');
    stash.take('yt:A');
    expect(stash.take('yt:A')).toBeNull();
  });

  it('a different track invalidates the stash', () => {
    const stash = new IdleStash<string>();
    stash.remember('yt:A', 'lyrics of A');
    stash.idle('yt:A');
    expect(stash.take('yt:B')).toBeNull();
    expect(stash.take('yt:A')).toBeNull(); // B came in between: A's stash is gone
  });

  it('never stashes lyrics that belong to another key', () => {
    // Lyrics for A were the last applied, but the renderer idled while showing
    // B (whose lookup had not answered yet): restoring A's lines on B is wrong.
    const stash = new IdleStash<string>();
    stash.remember('yt:A', 'lyrics of A');
    stash.idle('yt:B');
    expect(stash.take('yt:B')).toBeNull();
  });

  it('survives a second idle edge with nothing on screen (watchdog, then source gone)', () => {
    const stash = new IdleStash<string>();
    stash.remember('yt:A', 'lyrics of A');
    stash.idle('yt:A'); // watchdog
    stash.idle(null); // source gone: the key was already dropped
    expect(stash.take('yt:A')).toBe('lyrics of A');
  });
});
