/**
 * YouTube Music's play queue, read in the MAIN world (LyricFix round 3): which
 * tracks come next, so the overlay can warm their lyrics ahead of time.
 *
 * Source: YTM's own queue store, `#queue`'s `queue.store.store.getState()
 * .queue` — the signal pear-desktop and ytmdesktop read (ytm-scout,
 * 2026-10-26). Items are `playlistPanelVideoRenderer`s, or a wrapper whose
 * `primaryRenderer` is the one that plays (its `counterpart` is the song/video
 * twin). After the last queue item, radio/autoplay continues with
 * `automixItems` while autoplay is on.
 *
 * Pure: takes the state object, returns plain data. Page data is untrusted —
 * every field is type-checked and length-capped here, and the result is only
 * ever a prefetch HINT (a wrong guess wastes one lookup, nothing more).
 */

export const QUEUE_LOOKAHEAD = 2;
const MAX_TEXT = 300;

export interface QueueItem {
  videoId: string;
  title: string;
  artist: string;
  durationMs?: number;
}

export interface QueueReadOptions {
  /**
   * Under shuffle, is the shuffled order written into `items`? Unverified
   * until a live check (plan P1 probe); until then shuffle predicts nothing.
   */
  trustShuffle: boolean;
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null;

/** Text of a YTM `{runs:[{text}]}` or `{simpleText}` field. */
function textOf(field: unknown): string {
  if (!isObj(field)) return '';
  if (typeof field['simpleText'] === 'string') return field['simpleText'];
  const runs = field['runs'];
  if (!Array.isArray(runs)) return '';
  return runs.map((run) => (isObj(run) && typeof run['text'] === 'string' ? run['text'] : '')).join('');
}

/** The byline's artist part: "Artist • Album • 2024" → "Artist". */
function artistOf(renderer: Obj): string {
  const short = textOf(renderer['shortBylineText']);
  const byline = short || textOf(renderer['longBylineText']);
  return byline.split(' • ')[0]?.trim() ?? '';
}

/** "3:45" / "1:02:03" → ms; undefined when it does not parse. */
export function parseLength(text: string): number | undefined {
  if (!/^\d{1,2}(:\d{2}){1,2}$/.test(text.trim())) return undefined;
  const ms = text
    .trim()
    .split(':')
    .reduce((total, part) => total * 60 + Number(part), 0) * 1000;
  return ms > 0 ? ms : undefined;
}

function rendererOf(item: unknown): Obj | null {
  if (!isObj(item)) return null;
  const direct = item['playlistPanelVideoRenderer'];
  if (isObj(direct)) return direct;
  const wrapper = item['playlistPanelVideoWrapperRenderer'];
  if (!isObj(wrapper)) return null;
  const primary = wrapper['primaryRenderer'];
  if (!isObj(primary)) return null;
  const inner = primary['playlistPanelVideoRenderer'];
  return isObj(inner) ? inner : null;
}

const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

function toItem(item: unknown): QueueItem | null {
  const renderer = rendererOf(item);
  if (!renderer) return null;
  const endpoint = isObj(renderer['navigationEndpoint']) ? renderer['navigationEndpoint'] : {};
  const watch = isObj(endpoint['watchEndpoint']) ? endpoint['watchEndpoint'] : {};
  const videoId = renderer['videoId'] ?? watch['videoId'];
  if (typeof videoId !== 'string' || !VIDEO_ID.test(videoId)) return null;
  const title = textOf(renderer['title']).trim().slice(0, MAX_TEXT);
  const artist = artistOf(renderer).slice(0, MAX_TEXT);
  if (!title || !artist) return null;
  const durationMs = parseLength(textOf(renderer['lengthText']));
  return durationMs === undefined ? { videoId, title, artist } : { videoId, title, artist, durationMs };
}

function selectedIndex(state: Obj, items: unknown[]): number {
  const index = state['selectedItemIndex'];
  if (typeof index === 'number' && Number.isInteger(index) && index >= 0 && index < items.length) {
    return index;
  }
  return items.findIndex((item) => rendererOf(item)?.['selected'] === true);
}

/**
 * The next QUEUE_LOOKAHEAD tracks in play order, or [] when the next one cannot
 * be predicted: repeat-one (the same track again), shuffle (unless trusted),
 * no current item, or the end of the queue with autoplay off.
 */
export function readNextFromQueue(state: unknown, options: QueueReadOptions): QueueItem[] {
  if (!isObj(state)) return [];
  const items = state['items'];
  if (!Array.isArray(items) || items.length === 0) return [];
  if (state['repeatMode'] === 'ONE') return [];
  if (state['shuffleEnabled'] === true && !options.trustShuffle) return [];
  const current = selectedIndex(state, items);
  if (current < 0) return [];

  const following: unknown[] = items.slice(current + 1);
  if (state['repeatMode'] === 'ALL') {
    following.push(...items.slice(0, current));
  } else if (state['autoplay'] !== false && Array.isArray(state['automixItems'])) {
    following.push(...state['automixItems']);
  }

  const currentId = toItem(items[current])?.videoId;
  const next: QueueItem[] = [];
  for (const candidate of following) {
    const item = toItem(candidate);
    if (!item || item.videoId === currentId || next.some((n) => n.videoId === item.videoId)) {
      continue;
    }
    next.push(item);
    if (next.length === QUEUE_LOOKAHEAD) break;
  }
  return next;
}

/**
 * Shuffle stays untrusted until the live probe shows that YTM writes the
 * shuffled order into `items` (plan P1 prerequisite). Flip after the check.
 */
export const TRUST_SHUFFLE = false;

const QUEUE_DEBOUNCE_MS = 500;
const QUEUE_SAFETY_NET_MS = 5000;

interface QueueStore {
  getState?: () => unknown;
  /** Redux-style: returns the unsubscribe function. */
  subscribe?: (listener: () => void) => unknown;
}

/**
 * Watch YTM's queue store and call `onChange` with the predicted next tracks
 * whenever the prediction changes (debounced). The store can be replaced on a
 * page rebuild, so it is looked up again on a slow safety-net interval, which
 * also covers a missed subscription. Never throws into the page.
 */
export function observeQueue(onChange: (next: QueueItem[]) => void): void {
  let store: QueueStore | null = null;
  let unsubscribe: (() => void) | null = null;
  let last = '';
  let timer: number | undefined;

  const emit = (): void => {
    let state: unknown = null;
    try {
      const root = store?.getState?.();
      state = isObj(root) ? root['queue'] : null;
    } catch {
      state = null;
    }
    const next = readNextFromQueue(state, { trustShuffle: TRUST_SHUFFLE });
    const serialized = JSON.stringify(next);
    if (serialized === last) return;
    last = serialized;
    onChange(next);
  };
  // Trailing throttle, not a debounce: a store that dispatches more often
  // than the delay must still get read (a debounce would wait for a quiet
  // moment that never comes).
  const schedule = (): void => {
    if (timer !== undefined) return;
    timer = window.setTimeout(() => {
      timer = undefined;
      emit();
    }, QUEUE_DEBOUNCE_MS);
  };
  const attach = (): void => {
    const element = document.querySelector('#queue') as (Element & { queue?: unknown }) | null;
    const queue = isObj(element?.queue) ? element.queue : null;
    const holder = queue && isObj(queue['store']) ? queue['store'] : null;
    const candidate = holder && isObj(holder['store']) ? (holder['store'] as QueueStore) : null;
    if (!candidate || candidate === store) return;
    store = candidate;
    try {
      unsubscribe?.();
    } catch {
      // the replaced store may already be gone
    }
    unsubscribe = null;
    try {
      const off = candidate.subscribe?.(schedule);
      if (typeof off === 'function') unsubscribe = off as () => void;
    } catch {
      // a store without subscribe still gets the safety-net reads
    }
    schedule();
  };

  attach();
  window.setInterval(() => {
    attach();
    schedule();
  }, QUEUE_SAFETY_NET_MS);
}
