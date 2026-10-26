import { describe, expect, it } from 'vitest';
import { TRUST_SHUFFLE, parseLength, readNextFromQueue } from './queue.js';

const opts = { trustShuffle: false };

const renderer = (id: string, over: Record<string, unknown> = {}) => ({
  videoId: id,
  title: { runs: [{ text: `Song ${id}` }] },
  longBylineText: { runs: [{ text: 'Artist' }, { text: ' • ' }, { text: 'Album' }] },
  lengthText: { runs: [{ text: '3:20' }] },
  ...over,
});
const item = (id: string, over: Record<string, unknown> = {}) => ({
  playlistPanelVideoRenderer: renderer(id, over),
});
const wrapped = (id: string, twin: string) => ({
  playlistPanelVideoWrapperRenderer: {
    primaryRenderer: { playlistPanelVideoRenderer: renderer(id) },
    counterpart: [{ counterpartRenderer: { playlistPanelVideoRenderer: renderer(twin) } }],
  },
});
const id = (n: number) => `vid${String(n).padStart(8, '0')}`; // 11 chars

const state = (over: Record<string, unknown> = {}) => ({
  items: [item(id(0)), item(id(1)), item(id(2)), item(id(3))],
  automixItems: [],
  selectedItemIndex: 1,
  autoplay: true,
  shuffleEnabled: false,
  repeatMode: 'NONE',
  ...over,
});

const ids = (s: unknown, o = opts) => readNextFromQueue(s, o).map((i) => i.videoId);

describe('readNextFromQueue', () => {
  it('returns the next two items after the selected one, with their metadata', () => {
    expect(readNextFromQueue(state(), opts)).toEqual([
      { videoId: id(2), title: `Song ${id(2)}`, artist: 'Artist', durationMs: 200_000 },
      { videoId: id(3), title: `Song ${id(3)}`, artist: 'Artist', durationMs: 200_000 },
    ]);
  });

  it('never looks further than two ahead', () => {
    expect(ids(state({ selectedItemIndex: 0 }))).toEqual([id(1), id(2)]);
  });

  it('continues into automix after the last queue item while autoplay is on', () => {
    const s = state({ selectedItemIndex: 3, automixItems: [item(id(8)), item(id(9))] });
    expect(ids(s)).toEqual([id(8), id(9)]);
    expect(ids({ ...s, selectedItemIndex: 2 })).toEqual([id(3), id(8)]);
  });

  it('predicts nothing past the end with autoplay off', () => {
    const s = state({ selectedItemIndex: 3, autoplay: false, automixItems: [item(id(8))] });
    expect(ids(s)).toEqual([]);
  });

  it('wraps around under repeat-all and ignores automix there', () => {
    const s = state({ selectedItemIndex: 3, repeatMode: 'ALL', automixItems: [item(id(8))] });
    expect(ids(s)).toEqual([id(0), id(1)]);
  });

  it('predicts nothing under repeat-one', () => {
    expect(ids(state({ repeatMode: 'ONE' }))).toEqual([]);
  });

  it('predicts nothing under shuffle unless shuffle is trusted', () => {
    expect(ids(state({ shuffleEnabled: true }))).toEqual([]);
    expect(ids(state({ shuffleEnabled: true }), { trustShuffle: true })).toEqual([id(2), id(3)]);
  });

  it('takes the primary of a wrapped item, never its song/video twin', () => {
    const s = state({ items: [item(id(0)), wrapped(id(5), id(6)), item(id(7))], selectedItemIndex: 0 });
    expect(ids(s)).toEqual([id(5), id(7)]);
  });

  it('finds the current item by its selected flag when the index is missing', () => {
    const items = [item(id(0)), item(id(1), { selected: true }), item(id(2))];
    expect(ids(state({ items, selectedItemIndex: undefined }))).toEqual([id(2)]);
    expect(ids(state({ items, selectedItemIndex: 99 }))).toEqual([id(2)]);
  });

  it('predicts nothing without a current item', () => {
    const items = [item(id(0)), item(id(1))];
    expect(ids(state({ items, selectedItemIndex: undefined }))).toEqual([]);
  });

  it('skips the current track appearing again, duplicates and malformed items', () => {
    const items = [
      item(id(0)),
      item(id(0)),
      { somethingElse: {} },
      item('bad id'),
      item(id(1), { title: { runs: [] } }),
      item(id(2)),
      item(id(2)),
      item(id(3)),
    ];
    expect(ids(state({ items, selectedItemIndex: 0 }))).toEqual([id(2), id(3)]);
  });

  it('reads the videoId from the watch endpoint when the renderer lacks it', () => {
    const items = [
      item(id(0)),
      item(id(1), { videoId: undefined, navigationEndpoint: { watchEndpoint: { videoId: id(4) } } }),
    ];
    expect(ids(state({ items, selectedItemIndex: 0 }))).toEqual([id(4)]);
  });

  it('prefers the short byline and caps page text', () => {
    const long = 'x'.repeat(1000);
    const items = [
      item(id(0)),
      item(id(1), { shortBylineText: { simpleText: 'Short Artist' }, title: { simpleText: long } }),
    ];
    const [next] = readNextFromQueue(state({ items, selectedItemIndex: 0 }), opts);
    expect(next?.artist).toBe('Short Artist');
    expect(next?.title.length).toBe(300);
  });

  it('leaves the duration out when the length text does not parse', () => {
    const items = [item(id(0)), item(id(1), { lengthText: { runs: [{ text: 'LIVE' }] } })];
    const [next] = readNextFromQueue(state({ items, selectedItemIndex: 0 }), opts);
    expect(next).toEqual({ videoId: id(1), title: `Song ${id(1)}`, artist: 'Artist' });
  });

  it('survives garbage', () => {
    for (const s of [null, undefined, 3, 'x', {}, { items: 'x' }, { items: [] }]) {
      expect(readNextFromQueue(s, opts)).toEqual([]);
    }
  });
});

describe('TRUST_SHUFFLE', () => {
  it('is on: the live check showed the shuffled order lives in the queue', () => {
    // Pinned, not derived: flipping it changes what gets prefetched.
    expect(TRUST_SHUFFLE).toBe(true);
  });
});

describe('parseLength', () => {
  it('parses m:ss and h:mm:ss', () => {
    expect(parseLength('3:20')).toBe(200_000);
    expect(parseLength('1:02:03')).toBe(3_723_000);
  });

  it('rejects anything else', () => {
    for (const text of ['', 'LIVE', '3', '3:2', '0:00', 'a:bc']) expect(parseLength(text)).toBeUndefined();
  });
});
