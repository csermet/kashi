import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  CONTRADICTION_CONFIRMATIONS,
  DurationLedger,
  MAX_ENTRIES,
  MAX_PLAUSIBLE_DURATION_MS,
  resolveDuration,
} from './duration-ledger.js';
import { EDIT_DURATION_TOLERANCE_MS } from './edit-check.js';

/** The 2026-08-13 field case, in the numbers it actually happened with. */
const HEY_MAMA_MS = 193_000;
const PREVIOUS_TRACK_MS = 366_451;

describe('resolveDuration', () => {
  it('fills the gap the source-side fix leaves behind', () => {
    // ext 0.1.14 answers "I cannot prove this is fresh" with undefined, which
    // is right — but undefined disables the lrclib duration filter AND the
    // different-edit verdict. A remembered duration is what makes both work.
    expect(resolveDuration(undefined, HEY_MAMA_MS, PREVIOUS_TRACK_MS)).toEqual({
      ms: HEY_MAMA_MS,
      verdict: 'remembered',
    });
  });

  it('replaces a carried-over duration with the one learned for this id', () => {
    // The field case as an OLDER extension build still produces it: the new
    // id arrives wearing the previous track's duration, bit for bit.
    expect(resolveDuration(PREVIOUS_TRACK_MS, HEY_MAMA_MS, PREVIOUS_TRACK_MS)).toEqual({
      ms: HEY_MAMA_MS,
      verdict: 'remembered',
    });
  });

  it('uses a suspect duration but refuses to learn it', () => {
    // Same signature, nothing remembered yet. Dropping it would hand the
    // lookup no filter at all — which is how the wrong edit reached the screen
    // — so it is used, and only barred from being pinned.
    expect(resolveDuration(PREVIOUS_TRACK_MS, undefined, PREVIOUS_TRACK_MS)).toEqual({
      ms: PREVIOUS_TRACK_MS,
      verdict: 'observed-suspect',
    });
  });

  it('does not convict a neighbour that is merely about as long', () => {
    // Exact equality only. A tolerance here would start calling honest
    // durations carry-overs every time two tracks run to a similar length.
    expect(resolveDuration(PREVIOUS_TRACK_MS - 1, undefined, PREVIOUS_TRACK_MS)).toEqual({
      ms: PREVIOUS_TRACK_MS - 1,
      verdict: 'observed',
    });
  });

  it('keeps a ms-identical twin usable when the table agrees anyway', () => {
    // Two ids CAN share a duration to the millisecond (same master delivered
    // as album + single, clean/explicit pairs). Suspicion must not throw away
    // a number the table itself confirms.
    expect(resolveDuration(HEY_MAMA_MS, HEY_MAMA_MS, HEY_MAMA_MS)).toEqual({
      ms: HEY_MAMA_MS,
      verdict: 'observed-suspect',
    });
  });

  it('lets a fresh observation outrank the table', () => {
    // Since 0.1.14 an announced duration proved its `durationchange` landed
    // after the id change; the table may be describing audio since trimmed.
    expect(resolveDuration(180_000, HEY_MAMA_MS, undefined)).toEqual({
      ms: 180_000,
      verdict: 'observed',
    });
  });

  it('admits when nobody knows', () => {
    expect(resolveDuration(undefined, undefined, PREVIOUS_TRACK_MS)).toEqual({
      ms: undefined,
      verdict: 'unknown',
    });
  });

  it('rejects readings that cannot describe audio', () => {
    for (const bad of [0, -1, Number.NaN, Number.POSITIVE_INFINITY, MAX_PLAUSIBLE_DURATION_MS + 1]) {
      expect(resolveDuration(bad, undefined, undefined).verdict).toBe('unknown');
      expect(resolveDuration(undefined, bad, undefined).verdict).toBe('unknown');
    }
  });
});

describe('DurationLedger', () => {
  let cacheDir: string;
  const key = 'youtube:hey-mama';

  beforeEach(async () => {
    cacheDir = await mkdtemp(join(tmpdir(), 'kashi-ledger-'));
  });

  afterEach(async () => {
    await rm(cacheDir, { recursive: true, force: true });
  });

  /**
   * Long debounce on purpose: the tests that care about the file call `flush()`
   * themselves, and a timer that fires on its own would race the temp-directory
   * cleanup (it recreates the directory as it writes).
   */
  const ledger = (now = 1000) =>
    new DurationLedger({ cacheDir, nowFn: () => now, flushDelayMs: 60_000 });

  it('learns a first sighting and answers with it', () => {
    const l = ledger();
    expect(l.learn(key, HEY_MAMA_MS, 'announce')).toBe('learned');
    expect(l.durationFor(key)).toBe(HEY_MAMA_MS);
  });

  it('ignores a number that cannot be a duration', () => {
    const l = ledger();
    expect(l.learn(key, 0, 'announce')).toBe('ignored');
    expect(l.learn(key, undefined, 'server')).toBe('ignored');
    expect(l.learn(key, MAX_PLAUSIBLE_DURATION_MS + 1, 'server')).toBe('ignored');
    expect(l.durationFor(key)).toBeUndefined();
  });

  it('holds its ground against a single contradicting announce', () => {
    // This is the bug's own shape: one announce carrying somebody else's
    // number. One sighting must never be enough to overwrite what we know.
    const l = ledger();
    l.learn(key, HEY_MAMA_MS, 'announce');
    expect(l.learn(key, PREVIOUS_TRACK_MS, 'announce')).toBe('pending');
    expect(l.durationFor(key)).toBe(HEY_MAMA_MS);
  });

  it('changes its mind when the contradiction repeats', () => {
    // The trim case: YouTube Studio can shorten a PUBLISHED video without
    // changing its id. A table that could never update would stay wrong.
    const l = ledger();
    l.learn(key, HEY_MAMA_MS, 'announce');
    for (let i = 1; i < CONTRADICTION_CONFIRMATIONS; i++) {
      expect(l.learn(key, 150_000, 'announce')).toBe('pending');
    }
    expect(l.learn(key, 150_000, 'announce')).toBe('learned');
    expect(l.durationFor(key)).toBe(150_000);
  });

  it('forgets a contradiction that does not hold up', () => {
    const l = ledger();
    l.learn(key, HEY_MAMA_MS, 'announce');
    l.learn(key, 150_000, 'announce'); // pending
    expect(l.learn(key, HEY_MAMA_MS, 'announce')).toBe('confirmed');
    l.learn(key, 150_000, 'announce'); // starts over, not a second sighting
    expect(l.durationFor(key)).toBe(HEY_MAMA_MS);
  });

  it('lets one measurement outrank any number of page reports', () => {
    // ffprobe read the audio itself; no confirmation ritual applies.
    const l = ledger();
    l.learn(key, PREVIOUS_TRACK_MS, 'announce');
    expect(l.learn(key, HEY_MAMA_MS, 'server')).toBe('learned');
    expect(l.durationFor(key)).toBe(HEY_MAMA_MS);
  });

  it('upgrades an agreeing announce to the measured number', () => {
    const l = ledger();
    l.learn(key, HEY_MAMA_MS + 400, 'announce');
    expect(l.learn(key, HEY_MAMA_MS, 'server')).toBe('confirmed');
    expect(l.durationFor(key)).toBe(HEY_MAMA_MS);
    // ...and having been measured, it no longer yields to a lone announce.
    expect(l.learn(key, 150_000, 'announce')).toBe('pending');
    expect(l.durationFor(key)).toBe(HEY_MAMA_MS);
  });

  it('treats agreement within the edit tolerance as the same duration', () => {
    const l = ledger();
    l.learn(key, HEY_MAMA_MS, 'announce');
    expect(l.learn(key, HEY_MAMA_MS + EDIT_DURATION_TOLERANCE_MS, 'announce')).toBe('confirmed');
    expect(l.learn(key, HEY_MAMA_MS + EDIT_DURATION_TOLERANCE_MS + 1, 'announce')).toBe('pending');
  });

  it('survives a restart', async () => {
    const l = ledger();
    l.learn(key, HEY_MAMA_MS, 'server');
    await l.flush();

    const reloaded = ledger();
    await reloaded.load();
    expect(reloaded.durationFor(key)).toBe(HEY_MAMA_MS);
  });

  it('never persists an unconfirmed contradiction', async () => {
    // Otherwise one stale announce today plus one tomorrow would add up to a
    // confirmation, which is precisely the evidence we refuse to accept.
    const l = ledger();
    l.learn(key, HEY_MAMA_MS, 'announce');
    l.learn(key, PREVIOUS_TRACK_MS, 'announce');
    await l.flush();

    const raw = await readFile(join(cacheDir, 'duration-ledger.json'), 'utf8');
    expect(raw).not.toContain(String(PREVIOUS_TRACK_MS));

    const reloaded = ledger();
    await reloaded.load();
    expect(reloaded.learn(key, PREVIOUS_TRACK_MS, 'announce')).toBe('pending');
  });

  it('starts empty rather than trusting a damaged file', async () => {
    await writeFile(join(cacheDir, 'duration-ledger.json'), '{ not json', 'utf8');
    const l = ledger();
    await l.load();
    expect(l.durationFor(key)).toBeUndefined();
  });

  it('drops entries that cannot be durations on the way in', async () => {
    await writeFile(
      join(cacheDir, 'duration-ledger.json'),
      JSON.stringify({
        v: 1,
        entries: {
          bad: { ms: -5, source: 'server', at: 1 },
          worse: { ms: 193_000, source: 'made-up', at: 1 },
          good: { ms: 193_000, source: 'server', at: 1 },
        },
      }),
      'utf8',
    );
    const l = ledger();
    await l.load();
    expect(l.durationFor('bad')).toBeUndefined();
    expect(l.durationFor('worse')).toBeUndefined();
    expect(l.durationFor('good')).toBe(193_000);
  });

  it('stays a listening history, not an archive', () => {
    const l = ledger();
    for (let i = 0; i < MAX_ENTRIES + 10; i++) l.learn(`youtube:${i}`, 100_000 + i, 'announce');
    expect(l.durationFor('youtube:0')).toBeUndefined(); // oldest fell off
    expect(l.durationFor(`youtube:${MAX_ENTRIES + 9}`)).toBe(100_000 + MAX_ENTRIES + 9);
  });

  it('resolves against what it remembers', () => {
    const l = ledger();
    l.learn(key, HEY_MAMA_MS, 'server');
    expect(l.resolve(key, undefined, PREVIOUS_TRACK_MS)).toEqual({
      ms: HEY_MAMA_MS,
      verdict: 'remembered',
    });
  });

  it('walks the 2026-08-13 field case end to end', () => {
    const l = ledger();
    const previous = 'youtube:previous';

    // 1. The track that would later lend its duration away plays normally.
    expect(l.learn(previous, PREVIOUS_TRACK_MS, 'announce')).toBe('learned');

    // 2. Auto-advance. An older build announces Hey Mama wearing the previous
    //    video's duration. Nothing is known about this id yet, so the number is
    //    used — but pinning it is what would have made the bug permanent.
    const first = l.resolve(key, PREVIOUS_TRACK_MS, PREVIOUS_TRACK_MS);
    expect(first.verdict).toBe('observed-suspect');
    expect(l.durationFor(key)).toBeUndefined();

    // 3. The real duration arrives late (11.5 s in the field log) and IS learned.
    const correction = l.resolve(key, HEY_MAMA_MS, PREVIOUS_TRACK_MS);
    expect(correction.verdict).toBe('observed');
    l.learn(key, correction.ms, 'announce');
    expect(l.durationFor(key)).toBe(HEY_MAMA_MS);

    // 4. The next auto-advance into this track: ext 0.1.14 refuses to guess and
    //    sends no duration at all. That used to mean an unscoped lrclib search
    //    and an 'unverifiable' edit check — the two doors the wrong stamps came
    //    through. The ledger closes both without the page's help.
    expect(l.resolve(key, undefined, PREVIOUS_TRACK_MS)).toEqual({
      ms: HEY_MAMA_MS,
      verdict: 'remembered',
    });
  });
});
