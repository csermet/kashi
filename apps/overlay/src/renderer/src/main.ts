/**
 * Renderer: line-level lyrics display driven by the position clock.
 * All dynamic text goes through textContent — never innerHTML (plan R-7).
 * Screen-state rules live in view-logic.ts (pure, unit-tested); this file is
 * the wiring: IPC subscriptions, the rAF loop, hover/drag/wheel plumbing.
 */
import type {
  AdStateMessage,
  PlaybackStateMessage,
  PositionMessage,
  SeekMessage,
} from '@kashi/protocol';
import {
  parseEffectLevel,
  parseFillStyle,
  parseThemeScope,
  type EffectLevel,
  type FillStyle,
  type ThemeScope,
} from '../../shared/effect-level.js';
import {
  ambientColors,
  BEAT_IDLE,
  BeatCursor,
  beatsUsable,
  buildFxIndex,
  primaryFxHit,
  buildLineThemeIndex,
  computeFxTintVars,
  FX_BASE_COLORS,
  fillProgress,
  resolveFxProfile,
  inRampSection,
  isNightcore,
  paletteToCssVars,
  planWordFills,
  quantizedEnergy,
  type BeatFrame,
  type BeatsLike,
  type PaletteLike,
} from './effects-logic.js';
import { FxCanvas } from './fx-canvas.js';
import { FX_ICON_VARIANTS, FX_ICON_VIEWBOX } from './fx-icons.js';
import { parseTintColor, shouldMountLayer } from './fx-particles-logic.js';
import {
  BOX_ZONE,
  boxZoneFor,
  DEFAULT_BOX_SCALE,
  parseBoxScale,
  WINDOW_HEIGHT,
  type BoxScale,
} from '../../shared/box-zone.js';
import { BOX_CHROME } from '../../shared/box-zone.js';
import {
  DEFAULT_TEXT_SCALE,
  parseTextScale,
  TEXT_SCALE_FACTORS,
  type TextScale,
} from '../../shared/text-scale.js';
import { PositionClock } from './position-clock.js';
import { SnapClassifier } from './snap-classifier.js';
import {
  accumulateWheel,
  deriveView,
  findActiveWord,
  findDisplayLine,
  shouldAnimateLineChange,
  viewsEqual,
  watchdogShouldReset,
  type ViewOutput,
  type WordTiming,
} from './view-logic.js';
import type {
  AlignmentData,
  EnergyData,
  FxData,
  LyricLine,
  SectionData,
} from '../../shared/lyrics.js';
import { loadArtworkPalette } from './artwork-palette.js';

type PlaybackMessage = PositionMessage | SeekMessage | PlaybackStateMessage | AdStateMessage;

interface KashiBridge {
  version: string;
  onTrack: (cb: (payload: unknown) => void) => () => void;
  onPlayback: (cb: (payload: unknown) => void) => () => void;
  onLyrics: (cb: (payload: unknown) => void) => () => void;
  onConnection: (cb: (payload: unknown) => void) => () => void;
  onSourceGone: (cb: (payload: unknown) => void) => () => void;
  onSettings: (cb: (payload: unknown) => void) => () => void;
  setInteractive: (interactive: boolean) => void;
  dragStart: () => void;
  dragEnd: () => void;
  adjustOpacity: (deltaSteps: number) => void;
  adjustTimingOffset: (deltaSteps: number) => void;
  openMenu: () => void;
  log: (line: string) => void;
  reportAnomaly: (
    reason: string,
    deltaMs: number,
    positionMs: number,
    trackKey: string | null,
  ) => void;
}

declare global {
  interface Window {
    kashi: KashiBridge;
  }
}

const boxEl = document.getElementById('lyric-box');
const lineEl = document.getElementById('lyric-line');
const searchEl = document.getElementById('search-line');
const offsetFlashEl = document.getElementById('offset-flash');
// Outside-the-box effect anchors (Faz 6.5 P2): top icon band + left-gutter
// park spot. Both pointer-events:none — hover/click-through stays box-only.

// A jump with no seek attached to it is not yet a verdict: while the progress
// bar is being dragged, the explaining `seeked` always arrives after the jumps
// it explains. The classifier waits for the stream to go quiet before deciding.
const snapClassifier = new SnapClassifier();

const clock = new PositionClock(undefined, undefined, ({ deltaMs, positionMs }) => {
  window.kashi.log(`position snapped ${deltaMs}ms with no seek yet (pos ${positionMs}ms)`);
  snapClassifier.onJump(deltaMs, positionMs, performance.now(), currentKey);
});

/** Drains the classifier off the render path — never per frame. */
function reportClassifiedSnaps(): void {
  for (const event of snapClassifier.flush(performance.now())) {
    const collapsed = event.jumpCount > 1 ? ` (${event.jumpCount} jumps)` : '';
    window.kashi.log(`snap classified as ${event.reason}${collapsed}: ${event.deltaMs}ms`);
    window.kashi.reportAnomaly(event.reason, event.deltaMs, event.positionMs, event.trackKey);
  }
}
let currentKey: string | null = null;
let lines: LyricLine[] = [];
let adActive = false;
let searching = false;
// Idle default (Caner's call): no big "waiting" text — a small dim badge.
let statusText = 'Kashi';
let statusDim = true;
let trackLabel = '';
let lastPlaybackMono = performance.now();

/** Last applied view — repaint only on change (keeps idle frames free). */
let appliedView: ViewOutput | null = null;

// Effect engine (Faz 4). Palette/beats arrive with server lyrics; the level
// comes from settings. All per-frame beat work is class toggles on edges.
let effectLevel: EffectLevel = 'simple';
let themeScope: ThemeScope = 'full';
let fillStyle: FillStyle = 'themed';
let currentPalette: PaletteLike | undefined; // what applyPaletteVars renders
let serverPalette: PaletteLike | undefined; // from a kashi-server document
let artworkPalette: PaletteLike | undefined; // local extraction (lrclib mode)
let artworkRequest = 0; // stale-load guard (Faz 5 P5)

// Server palettes always win; artwork only fills the serverless gap.
function refreshPalette(): void {
  currentPalette = serverPalette ?? artworkPalette;
  applyPaletteVars();
}
let currentBeats: BeatsLike | undefined;
let beatCursor: BeatCursor | null = null;
let appliedBeat: BeatFrame = BEAT_IDLE;

// Semantic word effects (Faz 6 P4, hype level). fx arrives with server
// lyrics; the index picks ≤1 winner per line (Caner kararı 8). Spans get
// their fx classes at BUILD time (line change) — per-frame work stays the
// existing one-class toggle; the burst fires edge-triggered on activation.
let currentFx: FxData | undefined;
let fxIndex: ReturnType<typeof buildFxIndex> = new Map();
// Line-theme ambient ring (Faz 6.5 P1): fx.lines → box halo. Applied at line
// cadence (one int compare per frame); colors come from the same tint map
// the word effects use, captured in applyPaletteVars.
let lineThemeIndex: Map<number, string> = new Map();
let currentTintVars: Record<string, string> = {};

/** Particle layer (Faz 6.7 P5); geometry comes from the shared definition. */
const reducedMotionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)');
let prefersReducedMotion = reducedMotionQuery?.matches ?? false;
// The CSS media query reacts live; the layer must too, or turning the OS
// setting on would stop every animation EXCEPT the full-window particles.
reducedMotionQuery?.addEventListener('change', (event) => {
  prefersReducedMotion = event.matches;
  syncFxLayer();
});
let fxCanvas: FxCanvas | null = null;

/** Creates the layer only where it is allowed to exist, destroys it otherwise. */
function syncFxLayer(): void {
  const wanted = shouldMountLayer(effectLevel, prefersReducedMotion);
  if (wanted && !fxCanvas) {
    fxCanvas = new FxCanvas(BOX_ZONE, (line) => window.kashi.log(line));
  } else if (!wanted && fxCanvas) {
    fxCanvas.destroy();
    fxCanvas = null;
  }
}

// Text and box size presets (Faz 7 P3). The window never changes size — the
// box scales INSIDE it around a fixed centre — so this is three CSS variables
// and nothing else. Defaults reproduce the shipped look exactly.
let textScale: TextScale = DEFAULT_TEXT_SCALE;
let boxScale: BoxScale = DEFAULT_BOX_SCALE;

/**
 * Writes the size presets as CSS variables.
 *
 * `#stage` padding is set INLINE rather than in the stylesheet: the static
 * rule states the default geometry and is pinned to BOX_ZONE by a contract
 * test, so the runtime override has to live somewhere that cannot drift from
 * it.
 */
function applySizeVars(): void {
  const root = document.documentElement.style;
  root.setProperty('--kashi-text-scale', String(TEXT_SCALE_FACTORS[textScale]));
  const zone = boxZoneFor(boxScale);
  // The box is content-sized within its zone; 60px is the gutter the shipped
  // 560px zone left around a 500px box.
  root.setProperty('--kashi-box-max-width', `${zone.width - 60}px`);
  // The visible half of the setting: wrap width only shows on LONG lines,
  // the chrome shows on every line (field report 2026-08-12).
  const chrome = BOX_CHROME[boxScale];
  root.setProperty('--kashi-box-pad', `${chrome.padY}px ${chrome.padX}px`);
  root.setProperty('--kashi-box-radius', `${chrome.radius}px`);
  const stage = document.getElementById('stage');
  if (stage) {
    stage.style.padding = `${zone.y}px ${zone.x}px ${WINDOW_HEIGHT - zone.y - zone.height}px`;
  }
}

/**
 * The category colour the PARTICLES wear.
 *
 * Reads the particle set first and the text set second. The fallback is what
 * makes this safe: a tag with no particle variable renders exactly as it did
 * before the two sets existed.
 */
function tintOf(tag: string): number {
  return parseTintColor(
    currentTintVars[`--fx-pt-${tag}`] ?? currentTintVars[`--fx-tint-${tag}`],
  );
}
let ambientLineIndex = -1;
let appliedAmbient: string | null = null;
let appliedFlash: string | null = null;


function applyAmbient(lineIndex: number, force = false): void {
  if (!force && lineIndex === ambientLineIndex) return;
  ambientLineIndex = lineIndex;
  // A new line means any pending flash is over (belt for reduced-motion,
  // where animation:none never fires animationend).
  boxEl?.classList.remove('ambient-flash');
  const { ambient, flash } = ambientColors(lineIndex, lineThemeIndex, fxIndex, currentTintVars);
  if (ambient !== appliedAmbient) {
    appliedAmbient = ambient;
    boxEl?.classList.toggle('fx-ambient', ambient !== null);
    if (ambient) boxEl?.style.setProperty('--fx-ambient', ambient);
    else boxEl?.style.removeProperty('--fx-ambient');
  }
  if (flash !== appliedFlash) {
    appliedFlash = flash;
    if (flash) boxEl?.style.setProperty('--fx-ambient-flash', flash);
    else boxEl?.style.removeProperty('--fx-ambient-flash');
  }
}

/** One-shot halo pulse on the fx word's activation (the "poison → green
 * glow around the box" field idea). Suppressed while a beat pulse is up —
 * two rings flaring together read as soup (plan feda rule). */
function triggerAmbientFlash(): void {
  if (!boxEl || appliedFlash === null || appliedBeat.active) return;
  boxEl.classList.remove('ambient-flash');
  void boxEl.offsetWidth; // re-arm the one-shot animation (burst pattern)
  boxEl.classList.add('ambient-flash');
}
// The flash class must NOT outlive its pulse: while present it also swaps
// the ring's shadow to the flash tint/geometry, so a lingering class held
// the floor in the wrong color from the first flash on (reviewer risk).
// animationend covers the normal path; the line-change removal in
// applyAmbient covers reduced-motion, where animation:none never fires it.
boxEl?.addEventListener('animationend', (event) => {
  if (event.animationName === 'kashi-ambient-flash') {
    boxEl.classList.remove('ambient-flash');
  }
});

function rebuildFxIndex(): void {
  fxIndex = effectLevel === 'hype' ? buildFxIndex(currentFx, lines) : new Map();
  lineThemeIndex = effectLevel === 'hype' ? buildLineThemeIndex(currentFx, lines) : new Map();
  applyAmbient(ambientLineIndex, true); // indexes changed under the same line
}

// Energy/section state (Faz 6 P5) — written only on change (edge/step).
let currentEnergy: EnergyData | undefined;
let currentSections: SectionData[] | undefined;
// Nightcore aesthetics (Faz 6.5 P5): the body class IS the state — it
// reflects the DOCUMENT only, and the CSS rules are body.fx-hype.nightcore-
// scoped, so the level needs no hook here.
function setNightcoreClass(on: boolean): void {
  document.body.classList.toggle('nightcore', on);
}
let appliedEnergy = -1;
let appliedHigh = false;

function setEnergyState(energy: number, high: boolean): void {
  if (energy !== appliedEnergy) {
    appliedEnergy = energy;
    document.documentElement.style.setProperty('--kashi-energy', String(energy));
  }
  if (high !== appliedHigh) {
    appliedHigh = high;
    boxEl?.classList.toggle('energy-high', high);
  }
}

/** Write the palette CSS vars (defaults when off / no palette — the v0.1.x look). */
function applyPaletteVars(): void {
  const vars = paletteToCssVars(effectLevel === 'off' ? undefined : currentPalette, themeScope);
  for (const [name, value] of Object.entries(vars)) {
    document.documentElement.style.setProperty(name, value);
  }
  // Fx tints key off this class: scope "none" means EVERYTHING stays stock,
  // including semantic category colors (Faz 6 — the scope contract holds).
  document.body.classList.toggle('theme-none', themeScope === 'none');
  // Per-category tint vars (field round 2): semantic hue, theme-distinct
  // tone — recomputed here (track/settings cadence, never per frame).
  const tintVars = computeFxTintVars(
    effectLevel === 'off' ? undefined : vars['--kashi-primary'],
    themeScope,
  );
  for (const tag of Object.keys(FX_BASE_COLORS)) {
    const name = `--fx-tint-${tag}`;
    const value = tintVars[name];
    if (value) document.documentElement.style.setProperty(name, value);
    else document.documentElement.style.removeProperty(name);
  }
  // Ambient ring colors ride the same tint map — re-derive for the current
  // line whenever the palette/scope changes (scope "none" empties the map
  // and the ring goes with it: the scope contract covers ambient too).
  currentTintVars = tintVars;
  applyAmbient(ambientLineIndex, true);
  if (currentPalette && effectLevel !== 'off' && themeScope !== 'none') {
    // One line per theme application — the color-iteration feedback loop
    // (field turu 2) needs to SEE what the tone mapper produced.
    window.kashi.log(
      `theme: primary ${vars['--kashi-primary']} accent ${vars['--kashi-accent']} ` +
        `bg(${vars['--kashi-bg-rgb']}) scope=${themeScope}`,
    );
  }
}

/** Rebuild the beat cursor whenever the level or the beat grid changes. */
function rebuildBeatCursor(): void {
  beatCursor = beatsUsable(effectLevel, currentBeats)
    ? new BeatCursor(
        (currentBeats?.times_ms as number[]) ?? [],
        Array.isArray(currentBeats?.downbeat_indices)
          ? (currentBeats.downbeat_indices as number[]).filter((i) => Number.isInteger(i))
          : [],
      )
    : null;
  if (!beatCursor) setBeatClasses(BEAT_IDLE);
}

function setBeatClasses(frame: BeatFrame): void {
  if (frame.active === appliedBeat.active && frame.down === appliedBeat.down) return;
  appliedBeat = frame;
  boxEl?.classList.toggle('beat', frame.active);
  boxEl?.classList.toggle('beat-down', frame.down);
}

function applyEffectLevelClass(): void {
  document.body.classList.remove('fx-off', 'fx-simple', 'fx-full', 'fx-hype');
  // Hype is a SUPERSET of full: the body carries BOTH classes so every
  // existing fx-full rule applies untouched (the "fx-full look has zero
  // CSS diff" contract holds by construction) and hype-only rules layer on.
  if (effectLevel === 'hype') document.body.classList.add('fx-full');
  document.body.classList.add(`fx-${effectLevel}`);
}

// Word mode: spans are (re)built ONLY when the active line changes; the
// per-frame work is toggling one class. Built with createElement/textContent
// exclusively — innerHTML stays banned (R-7).
let wordLineIndex = -1; // index of the line the spans belong to (-1 = none)
let wordSpans: HTMLSpanElement[] = [];
let activeWordIndex = -1;
// Which words on the current line carry an effect (hype). From pipeline
// 2.13.0 a selected document may name more than one; a legacy document still
// yields exactly one, decided by buildFxIndex.
let fxWordTags = new Map<number, { tag: string; intensity: number }>();
/**
 * Floor between two particle bursts. A belt over the server's own 700 ms
 * spacing: a plan that arrives wrong must not be able to stack bursts, since
 * the budget clips the NEWEST one — the one being looked at.
 */
const FX_BURST_COOLDOWN_MS = 450;
let lastBurstMono = Number.NEGATIVE_INFINITY;

/** Deterministic per-WORD icon pick (field round 2: the same icon repeating
 * across different words read poorly). Same word → same icon, always;
 * different words in one category spread across its 1-3 variants. */
function fxIconPath(tag: string, wordText: string): string | null {
  // hasOwn + typeof: inherited object keys ('constructor') must not leak a
  // non-string into setAttribute (defense in depth over mapFx's charset gate).
  const variants = Object.hasOwn(FX_ICON_VARIANTS, tag) ? FX_ICON_VARIANTS[tag] : undefined;
  if (!Array.isArray(variants) || variants.length === 0) return null;
  // Normalized: the same sung word must wear the same icon everywhere in a
  // song, and the transcript is not consistent about case or a trailing comma.
  const key = wordText.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) | 0;
  const path = variants[Math.abs(hash) % variants.length];
  return typeof path === 'string' ? path : null;
}

/** Inline SVG icon (createElementNS — R-7 keeps innerHTML banned; strict
 * CSP is untouched: presentation attributes only, no style attrs). */
function buildFxIcon(tag: string, wordText: string): SVGSVGElement | null {
  const path = fxIconPath(tag, wordText);
  if (path === null) return null; // unknown tag (newer lexicon) → no icon
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', FX_ICON_VIEWBOX);
  svg.setAttribute('fill', 'currentColor');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('fx-icon');
  const p = document.createElementNS(SVG_NS, 'path');
  p.setAttribute('d', path);
  svg.appendChild(p);
  return svg;
}


function clearWordSpans(): void {
  wordLineIndex = -1;
  wordSpans = [];
  activeWordIndex = -1;
  fillRunStart = -1; // spans are gone — nothing carries .word-fill anymore
  fillActiveIndex = -1;
  fxWordTags = new Map();
  clearFxFired(); // a mark on a detached span would keep a dead node alive
}

// Sustained-fill (Faz 4): a consecutive RUN of planned words sweeps as ONE
// gesture — completed words in the run hold full fill instead of snapping
// back (field feedback: the first word reverting mid-run looked broken).
// Per-frame cost stays one style write, on the ACTIVE span.
let fillRunStart = -1;
let fillActiveIndex = -1;
let fillPlan: boolean[] = [];

function clearRunFill(): void {
  if (fillRunStart < 0) return;
  for (let j = fillRunStart; j <= fillActiveIndex; j += 1) {
    const span = wordSpans[j];
    span?.classList.remove('word-fill');
    span?.style.removeProperty('--kashi-fill');
  }
  fillRunStart = -1;
  fillActiveIndex = -1;
}

function updateWordFill(words: readonly WordTiming[], index: number, pos: number): void {
  const word = index >= 0 ? words[index] : undefined;
  if (!word || fillPlan[index] !== true) {
    clearRunFill();
    return;
  }
  let runStart = index;
  while (runStart > 0 && fillPlan[runStart - 1] === true) runStart -= 1;
  if (fillRunStart !== runStart) {
    clearRunFill();
    fillRunStart = runStart;
    fillActiveIndex = runStart - 1; // nothing armed yet
  }
  if (index < fillActiveIndex) {
    // Seek back inside the run: words ahead of the cursor un-fill again.
    for (let j = index + 1; j <= fillActiveIndex; j += 1) {
      const span = wordSpans[j];
      span?.classList.remove('word-fill');
      span?.style.removeProperty('--kashi-fill');
    }
    fillActiveIndex = index;
  }
  while (fillActiveIndex < index) {
    // Advancing through the run: the word just completed pins at FULL fill
    // (it must not revert until the whole run ends — user rule 2026-07-12).
    if (fillActiveIndex >= fillRunStart) {
      wordSpans[fillActiveIndex]?.style.setProperty('--kashi-fill', '1');
    }
    fillActiveIndex += 1;
    wordSpans[fillActiveIndex]?.classList.add('word-fill');
  }
  wordSpans[index]?.style.setProperty('--kashi-fill', fillProgress(word, pos).toFixed(3));
}

function buildWordSpans(lineIndex: number, words: readonly WordTiming[]): void {
  if (!lineEl) return;
  lineEl.replaceChildren();
  // Line-level sweep plan (field feedback: per-word sweep/pop alternation
  // reads as random — plan once per line, not per frame). Computed BEFORE the
  // spans: planned words carry their base dialect from the first paint — the
  // base must never change mid-line (field feedback 2026-07-14: the grey ->
  // dim-theme snap at activation read as a glitch).
  fillPlan = planWordFills(words, lines[lineIndex]?.adlib === true, effectLevel);
  const fxHits = fxIndex.get(lineIndex) ?? [];
  // The halo's colour is NOT chosen here: ambientColors() takes the line's
  // primary hit itself, so a two-category line cannot repaint it mid-line.
  fxWordTags = new Map(fxHits.map((hit) => [hit.word, hit.effect]));
  const primary = primaryFxHit(fxHits);
  wordSpans = words.map((word, i) => {
    if (i > 0) lineEl.appendChild(document.createTextNode(' '));
    const span = document.createElement('span');
    span.className = 'word';
    span.textContent = word.text;
    const hit = fxHits.find((candidate) => candidate.word === i);
    if (hit) {
      const isPrimary = hit === primary;
      // Classes at build time; activation stays a class toggle (no per-frame
      // fx work). The inline icon rides INSIDE the span so it wraps with the
      // word — the box clips at the window edge, never past it.
      span.classList.add('fx-word', `fx-${hit.effect.tag}`);
      span.style.setProperty('--fx-intensity', String(hit.effect.intensity));
      // ONE icon per line, on the strongest hit. Two icons on one line read as
      // clutter — the field verdict was blunt about it — and the halo already
      // follows the same "one identity per line" rule. Secondary hits keep
      // their colour and their particles; only the glyph is withheld.
      if (isPrimary) {
        const icon = buildFxIcon(hit.effect.tag, word.text);
        if (icon) span.appendChild(icon);
      }
    }
    lineEl.appendChild(span);
    return span;
  });
  wordLineIndex = lineIndex;
  activeWordIndex = -1;
  // The old fill spans are gone with the rebuild; without this reset a
  // repeated identical ad-lib line ("Ooh" x4) never re-arms (retro finding).
  fillRunStart = -1;
  fillActiveIndex = -1;
}

/**
 * How long a word stays visibly "the one that fired", measured from the
 * burst. Comfortably longer than the shortest sung word and shorter than the
 * particle life, so the mark always outlives the span and never outlives its
 * own burst.
 */
const FX_FIRED_HOLD_MS = 700;
let firedSpan: HTMLElement | null = null;
let firedTimer: ReturnType<typeof setTimeout> | null = null;

function markFxFired(index: number): void {
  const span = wordSpans[index];
  if (!span) return;
  firedSpan?.classList.remove('fx-fired');
  span.classList.add('fx-fired');
  firedSpan = span;
  if (firedTimer !== null) clearTimeout(firedTimer);
  firedTimer = setTimeout(() => {
    firedSpan?.classList.remove('fx-fired');
    firedSpan = null;
  }, FX_FIRED_HOLD_MS);
}

/** Line rebuilds discard the spans; a mark on a detached node would leak. */
function clearFxFired(): void {
  if (firedTimer !== null) clearTimeout(firedTimer);
  firedTimer = null;
  firedSpan?.classList.remove('fx-fired');
  firedSpan = null;
}

function highlightWord(index: number): void {
  if (index === activeWordIndex) return;
  // Burst on the fx word's ACTIVATION edge only (never on rebuild/seek-back
  // repaints of an already-passed word).
  const fxHit = fxWordTags.get(index);
  const fxTag = fxHit?.tag;
  if (fxTag && index > activeWordIndex && effectLevel === 'hype') {
    // Particles OUTSIDE the box, radiating from the whole box edge in the
    // word's category colour. Deliberately not aimed at the word: a burst
    // from one point read as detached from the line it belonged to.
    // Which WAY they go is the category's own (Faz 7): poison spills and
    // sinks, love rises, money pours down the sides.
    //
    // The cooldown is a belt, not the rule: the server already spaces its
    // choices (700 ms), so this only catches a plan that arrives wrong — two
    // bursts closer than the eye can separate cost the particle budget twice
    // and the second one gets clipped, which is the one being watched.
    const now = performance.now();
    if (fxCanvas && now - lastBurstMono >= FX_BURST_COOLDOWN_MS) {
      lastBurstMono = now;
      const box = boxEl?.getBoundingClientRect();
      void fxCanvas.burst(
        tintOf(fxTag),
        box ? { x: box.left, y: box.top, width: box.width, height: box.height } : undefined,
        resolveFxProfile(fxTag, fxHit?.intensity),
      );
    }
    // Hold the TRIGGERING word lit for as long as the burst is in the air.
    // Field report 2026-08-12: "the effect fires but the word is gone before
    // you can see why". A misplaced word's span gets clipped to its
    // neighbour's start, so the cause can flash past in 150 ms while its
    // burst lives a full second — the eye sees an effect with no reason.
    // This does NOT touch timing: the word is highlighted on its own clock
    // as before; only the fired mark outlives the span.
    markFxFired(index);
    // Same edge lights the box halo — in the LINE's primary category, so a
    // two-category line does not change the halo colour halfway through.
    triggerAmbientFlash();
  }
  wordSpans[activeWordIndex]?.classList.remove('word-active');
  wordSpans[index]?.classList.add('word-active');
  // Sung pinning: everything BEFORE the active word stays bright; a
  // seek-back un-pins (field feedback 2026-07-14 round 2 — passed words
  // must never fade back to the resting tone while the line is up).
  for (let i = 0; i < wordSpans.length; i += 1) {
    wordSpans[i]?.classList.toggle('word-sung', i < index);
  }
  activeWordIndex = index;
}

function applyView(view: ViewOutput): void {
  if (viewsEqual(appliedView, view)) return;
  const prev = appliedView;
  appliedView = view;
  boxEl?.classList.toggle('hidden', !view.boxVisible);
  if (lineEl) {
    // Plain-text mode always wins here; word mode repopulates right after.
    if (lineEl.textContent !== view.lineText) lineEl.textContent = view.lineText;
    lineEl.classList.toggle('dim', view.lineDim);
    lineEl.classList.toggle('interlude', view.interlude);
    lineEl.classList.toggle('adlib', view.lineAdlib);
    // A line with no word timings renders as one plain string. It used to sit
    // at stock white while every timed line around it wore the album's
    // colours, so a song that opens with an unalignable intro looked broken
    // for its first few seconds — the field report was "white text for eight
    // seconds, then it goes normal". It is not broken; it just had nothing to
    // say. A soft tint says the same thing quietly.
    //
    // The answer comes from the VIEW, not from `wordSpans`. Reading the span
    // array here asked "did the line I painted LAST time have words?" — the
    // spans are cleared further down and rebuilt later in the frame — so a
    // plain line arriving after a word-synced one never got the class at all,
    // and a word-synced line arriving after a plain one kept it.
    lineEl.classList.toggle('plain', view.linePlain);
    // The server's `uncertain` flag finally reaches a pixel (Faz 8.1). It used
    // to be written on every rescued line and read by nobody, so the arbiter's
    // whole "the anchor proposes, the audio disposes" decision was invisible
    // in the field. The line still runs its word clock; it just stops
    // competing for attention with the lines we are sure about.
    lineEl.classList.toggle('uncertain', view.lineUncertain);
    // One-shot entrance: unconditional removal first — a stale class must
    // never linger into interlude/status views (its ID selector would
    // out-specificity the ♪ animation).
    lineEl.classList.remove('line-in');
    if (shouldAnimateLineChange(prev, view, effectLevel)) {
      void lineEl.offsetWidth; // forced reflow re-arms the one-shot animation
      lineEl.classList.add('line-in');
    }
  }
  clearWordSpans(); // any full repaint invalidates the span cache
  if (searchEl) searchEl.hidden = !view.searchVisible;
  // The box can hide under a MOTIONLESS cursor (ad start, watchdog reset) —
  // no mousemove follows, so without this the invisible window keeps
  // swallowing clicks until the user happens to move the mouse.
  if (!view.boxVisible && interactive && !dragging) {
    interactive = false;
    window.kashi.setInteractive(false);
  }
}

function clearEnrichment(): void {
  currentPalette = undefined;
  serverPalette = undefined;
  artworkPalette = undefined;
  artworkRequest += 1; // in-flight artwork loads for the OLD track go stale
  currentBeats = undefined;
  currentFx = undefined;
  currentEnergy = undefined;
  currentSections = undefined;
  setNightcoreClass(false);
  setEnergyState(0, false);
  rebuildFxIndex();
  rebuildBeatCursor();
  applyPaletteVars();
}

function resetToIdle(): void {
  currentKey = null;
  lines = [];
  adActive = false;
  searching = false;
  clock.reset();
  statusText = 'Kashi';
  statusDim = true;
  clearEnrichment();
}

window.kashi.onTrack((payload) => {
  const { key, track } = payload as {
    key: string;
    track: { title: string; artist: string; artwork_url?: string };
  };
  // Same song RE-announced (MV3 service-worker flap / WS reconnect re-fires
  // the current track — Caner field bug): keep the lyrics, palette, fx and
  // clock exactly as they are. Wiping here is what blinked effects and
  // flashed the search dots while a song already played fine. Keyed on the
  // id alone (not lines) so instrumental / no-lyrics tracks keep their
  // artwork palette too (reviewer). A same-key replay-from-0 self-corrects:
  // the retained clock extrapolates ahead, the incoming position 0 is a
  // >1.5 s delta, and the clock snaps.
  const sameTrack = key === currentKey;
  currentKey = key;
  adActive = false; // a track announce proves no ad is playing (audit: a lost
  // ad_state=false otherwise blanks every following song forever)
  if (!sameTrack) {
    lines = [];
    searching = false;
    clock.reset();
    clearEnrichment(); // last track's palette/beats must not theme this one
    if (typeof track.artwork_url === 'string' && track.artwork_url) {
      // Serverless palette (Faz 5 P5): theme from the artwork while (or in
      // place of) a server document. A later server palette overrides.
      const request = artworkRequest;
      void loadArtworkPalette(track.artwork_url).then((palette) => {
        if (palette === null || request !== artworkRequest || key !== currentKey) return;
        artworkPalette = palette;
        refreshPalette();
        window.kashi.log('artwork palette applied (serverless theme)');
      });
    }
  }
  trackLabel = `♪ ${track.artist} — ${track.title}`;
  statusText = trackLabel;
  statusDim = false;
  // Log line stays ASCII-decorated; the label keeps its glyphs for DISPLAY.
  window.kashi.log(`track ${sameTrack ? 're-announced' : 'set'}: ${key} "${track.artist} - ${track.title}"`);
  ensureLoop();
});

window.kashi.onLyrics((payload) => {
  const data = payload as {
    key: string;
    found?: boolean;
    searching?: boolean;
    error?: boolean;
    lines?: LyricLine[];
    palette?: PaletteLike;
    beats?: BeatsLike;
    fx?: FxData;
    energy?: EnergyData;
    sections?: SectionData[];
    alignment?: AlignmentData;
  };
  if (data.key !== currentKey) return; // stale (R-9)
  if (data.searching) {
    // Keep an already-shown lyric visible during a BACKGROUND re-lookup (SW
    // flap / reconnect re-runs the lookup for the current song): only surface
    // the searching dots when there is nothing on screen yet. Otherwise a
    // re-lookup would blink a playing lyric to dots (Caner field bug).
    if (lines.length === 0) {
      searching = true;
      statusText = trackLabel;
      statusDim = false;
    }
    ensureLoop();
    return;
  }
  searching = false;
  if (data.found && data.lines) {
    lines = data.lines;
    // Server enrichment (Faz 4): palette themes the box, beats drive the
    // pulse; fx tags feed the hype level (Faz 6). lrclib results carry
    // none of them — defaults keep the plain look.
    serverPalette = data.palette;
    currentBeats = data.beats;
    currentFx = data.fx;
    currentEnergy = data.energy;
    currentSections = data.sections;
    setNightcoreClass(isNightcore(data.alignment));
    rebuildFxIndex();
    rebuildBeatCursor();
    refreshPalette();
    window.kashi.log(
      `lyrics applied: ${lines.length} lines` +
        (data.beats ? ' +beats' : '') +
        (data.palette ? ' +palette' : '') +
        (data.fx?.words?.length ? ` +fx(${data.fx.words.length})` : ''),
    );
  } else {
    lines = [];
    // Keep the artwork theme: palette is about the PLAYING track, not about
    // whether lyrics exist. Only server enrichment resets here.
    serverPalette = undefined;
    currentBeats = undefined;
    currentFx = undefined;
    currentEnergy = undefined;
    currentSections = undefined;
    setNightcoreClass(false);
    rebuildFxIndex();
    rebuildBeatCursor();
    refreshPalette();
    statusText = data.error ? 'Lyrics unavailable (network)' : 'No synced lyrics found';
    statusDim = true;
    window.kashi.log(`lyrics ${data.error ? 'ERROR' : 'not found'}`);
  }
  ensureLoop();
});

window.kashi.onPlayback((payload) => {
  const msg = payload as PlaybackMessage;
  if (msg.type === 'ad_state') {
    adActive = msg.is_ad;
    // Both ad edges reset the starvation timer: positions are suppressed on
    // purpose during ads, and after one the clock may extrapolate for a beat
    // before the first fresh report — neither is data loss (closure review).
    lastPlaybackMono = performance.now();
    ensureLoop(); // repaint — ad end must not leave a blank stopped screen
    return;
  }
  clock.update(
    {
      position_ms: msg.position_ms,
      // seek/playback_state carry no rate — undefined keeps the current rate.
      playback_rate: 'playback_rate' in msg ? msg.playback_rate : undefined,
      is_playing: msg.is_playing,
      captured_at: msg.captured_at,
    },
    msg.type === 'seek',
  );
  if (msg.type === 'seek') snapClassifier.onSeek(performance.now());
  lastPlaybackMono = performance.now();
  ensureLoop();
});

window.kashi.onSourceGone(() => {
  window.kashi.log('source gone -> idle');
  resetToIdle();
  ensureLoop();
});

window.kashi.onConnection((payload) => {
  const { connected } = payload as { connected: boolean };
  window.kashi.log(`connection: ${connected}`);
  if (!connected) {
    // Source gone → back to the small idle badge (no stale lyrics on screen).
    resetToIdle();
  }
  ensureLoop();
});

// Brief "+120 ms" readout in the box corner: the live scroll gesture needs
// feedback the tray can't give (the menu is closed while scrolling).
let settingsSeen = false;
let offsetFlashTimer: ReturnType<typeof setTimeout> | null = null;
function flashTimingOffset(offsetMs: number): void {
  if (!offsetFlashEl) return;
  offsetFlashEl.textContent = `${offsetMs > 0 ? '+' : ''}${offsetMs} ms`;
  offsetFlashEl.classList.add('show');
  if (offsetFlashTimer !== null) clearTimeout(offsetFlashTimer);
  offsetFlashTimer = setTimeout(() => offsetFlashEl.classList.remove('show'), 900);
}

/** Show the box's extent for a moment so a size change is SEEN, not taken
 * on faith — the tray menu closes the instant the user clicks the preset. */
let boxRevealTimer: ReturnType<typeof setTimeout> | null = null;
function flashBoxScale(scale: string): void {
  if (offsetFlashEl) {
    offsetFlashEl.textContent = `box: ${scale}`;
    offsetFlashEl.classList.add('show');
  }
  boxEl?.classList.add('reveal');
  if (boxRevealTimer !== null) clearTimeout(boxRevealTimer);
  boxRevealTimer = setTimeout(() => {
    offsetFlashEl?.classList.remove('show');
    boxEl?.classList.remove('reveal');
  }, 900);
}

window.kashi.onSettings((payload) => {
  // Captured BEFORE the timing-offset branch flips it: branches below the
  // flip would otherwise treat the startup replay as a live change.
  const live = settingsSeen;
  const { box_alpha, timing_offset_ms, effect_level, theme_scope, fill_style, text_scale, box_scale } =
    payload as {
      box_alpha?: unknown;
      timing_offset_ms?: unknown;
      effect_level?: unknown;
      theme_scope?: unknown;
      fill_style?: unknown;
      text_scale?: unknown;
      box_scale?: unknown;
    };
  if (typeof box_alpha === 'number' && Number.isFinite(box_alpha)) {
    document.documentElement.style.setProperty('--kashi-box-alpha', String(box_alpha));
  }
  if (typeof timing_offset_ms === 'number' && Number.isFinite(timing_offset_ms)) {
    // positive = lyrics fire earlier (clamped main-side; belt here)
    const next = Math.max(-500, Math.min(500, Math.round(timing_offset_ms)));
    // Flash only on live CHANGES — the startup replay must stay silent.
    if (settingsSeen && next !== timingOffsetMs) flashTimingOffset(next);
    timingOffsetMs = next;
  }
  settingsSeen = true;
  if (effect_level !== undefined && parseEffectLevel(effect_level) !== effectLevel) {
    // Instant switch: a body class + variable/cursor reset — nothing rebuilt
    // except the word spans (their fill plan depends on the level).
    effectLevel = parseEffectLevel(effect_level);
    applyEffectLevelClass();
    applyPaletteVars();
    rebuildBeatCursor();
    rebuildFxIndex(); // hype gates the index; other levels empty it
    syncFxLayer(); // the particle layer exists only at hype (pixel identity)
    setEnergyState(0, false); // paused level switches must not strand the ramp
    clearRunFill();
    clearWordSpans(); // next frame rebuilds spans + fill plan for the new level
  }
  if (theme_scope !== undefined && parseThemeScope(theme_scope) !== themeScope) {
    themeScope = parseThemeScope(theme_scope);
    applyPaletteVars();
  }
  if (fill_style !== undefined && parseFillStyle(fill_style) !== fillStyle) {
    fillStyle = parseFillStyle(fill_style);
    // Pure CSS dialect switch — the gradient tail and the pre-base both key
    // off this one class; spans and plans stay as they are.
    document.body.classList.toggle('fill-neutral', fillStyle === 'neutral');
  }
  if (text_scale !== undefined && parseTextScale(text_scale) !== textScale) {
    textScale = parseTextScale(text_scale);
    applySizeVars();
  }
  if (box_scale !== undefined && parseBoxScale(box_scale) !== boxScale) {
    boxScale = parseBoxScale(box_scale);
    applySizeVars();
    if (live) flashBoxScale(boxScale); // startup replay stays silent
    // The particle layer aims at the box's REAL rect per burst, so it follows
    // on its own; only the fallback (before anything has been measured) needs
    // telling that the zone moved.
    fxCanvas?.setFallbackBox(boxZoneFor(boxScale));
  }
  // Paused screens must repaint NOW, not on the 1 Hz self-heal — the user is
  // looking at the box exactly when they change a setting (retro finding).
  ensureLoop();
});

// Hover ↔ click-through: the window ignores mouse events by default but
// forwards mousemove; hovering the lyric BOX flips it interactive so it can
// be dragged (and Ctrl+scrolled), leaving flips it back (R-4). Dragging is
// manual (mousedown → IPC cursor-follow) because -webkit-app-region would
// swallow mouse events.
let interactive = false;
let dragging = false;

document.addEventListener('mousemove', (event) => {
  if (dragging) return;
  const over = boxEl?.contains(event.target as Node) ?? false;
  if (over !== interactive) {
    interactive = over;
    window.kashi.setInteractive(over);
  }
});
document.documentElement.addEventListener('mouseleave', () => {
  if (interactive && !dragging) {
    interactive = false;
    window.kashi.setInteractive(false);
  }
});
boxEl?.addEventListener('mousedown', (event) => {
  if (event.button !== 0) return;
  dragging = true;
  window.kashi.dragStart();
});
// Right-click opens the Kashi menu (opacity presets / reset / quit) — same
// template the tray uses; the box is easier to find than the tray icon.
boxEl?.addEventListener('contextmenu', (event) => {
  event.preventDefault();
  window.kashi.openMenu();
});
window.addEventListener('mouseup', () => {
  if (dragging) {
    dragging = false;
    window.kashi.dragEnd();
  }
});
window.addEventListener('blur', () => {
  if (dragging) {
    dragging = false;
    window.kashi.dragEnd();
  }
});
// Ctrl+scroll over the box tunes its background opacity; Ctrl+Shift+scroll
// tunes the lyric timing offset live (Faz 4.5 — the setting is inherently
// iterative, the menu path was bureaucratic). Deltas go through an
// accumulator (view-logic) so touchpads and classic wheels both land on
// ~1 step per comfortable gesture unit. Separate accumulators: releasing
// Shift mid-gesture must not spill leftover pixels into the other control.
let wheelAccPx = 0;
let offsetWheelAccPx = 0;
boxEl?.addEventListener(
  'wheel',
  (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    if (event.shiftKey) {
      // Chromium reroutes the wheel to deltaX while Shift is held — this
      // gesture would read an eternal deltaY=0 without the fallback. The
      // opacity branch keeps ignoring horizontal deltas on purpose.
      const delta = event.deltaY !== 0 ? event.deltaY : event.deltaX;
      const { accumulatedPx, steps } = accumulateWheel(offsetWheelAccPx, delta, event.deltaMode);
      offsetWheelAccPx = accumulatedPx;
      // Scroll up = lyrics earlier (positive offset), mirroring "up = more".
      if (steps !== 0) window.kashi.adjustTimingOffset(-steps);
      return;
    }
    const { accumulatedPx, steps } = accumulateWheel(wheelAccPx, event.deltaY, event.deltaMode);
    wheelAccPx = accumulatedPx;
    // Scroll up (negative deltaY) increases opacity.
    if (steps !== 0) window.kashi.adjustOpacity(-steps);
  },
  { passive: false },
);

// The rAF loop only runs while there is motion to render (playing clock);
// otherwise we render the static state once and stop — keeps the compositor
// asleep when idle (battery).
let loopActive = false;
let timingOffsetMs = 0;

function frame(): void {
  // Data-loss watchdog: a "playing" clock with no position reports for 60 s
  // means the source vanished mid-play (tab closed, browser gone) — don't
  // keep scrolling ghost lyrics forever, drop to the idle badge. Ads get a
  // 3-minute leash instead (see watchdogShouldReset).
  if (watchdogShouldReset(clock.isPlaying, adActive, performance.now() - lastPlaybackMono)) {
    window.kashi.log('data-loss watchdog: position stream starved -> idle');
    resetToIdle();
  }
  // Lyrics render on the user-offset clock; the beat pulse must stay on the
  // RAW clock — a +150 ms offset would detach every pulse from the heard
  // beat (the window is only [-30,+60] ms).
  const rawPos = clock.positionAt();
  const pos = rawPos + timingOffsetMs;
  let activeText: string | null = null;
  let lineIndex = -1;
  if (!adActive && lines.length > 0) {
    // Short gaps HOLD the previous line; only long breaks yield the interlude
    // mark (Caner's feedback — the ♪ was flashing between every section).
    lineIndex = findDisplayLine(lines, pos);
    activeText = lineIndex >= 0 ? (lines[lineIndex]?.text ?? null) : null;
  }
  const activeAdlib = lineIndex >= 0 && lines[lineIndex]?.adlib === true;
  // Read BEFORE applyView: whether this line has a word clock decides how it
  // is painted, and asking after the fact is what made the plain tint miss.
  const words = lineIndex >= 0 ? lines[lineIndex]?.words : undefined;
  // Ambient ring follows the DISPLAY line (line cadence; the call is an int
  // compare on quiet frames). Ads/idle pass -1 and clear it.
  applyAmbient(lineIndex);
  applyView(
    deriveView({
      adActive,
      hasLines: lines.length > 0,
      activeText,
      statusText,
      statusDim,
      searching,
      activeAdlib,
      activeHasWords: (words?.length ?? 0) > 0,
      activeUncertain: lineIndex >= 0 && lines[lineIndex]?.uncertain === true,
    }),
  );

  // Beat pulse BEFORE word karaoke: the ambient flash suppresses itself
  // while a beat pulse is up, and reading appliedBeat one block later left
  // that check a frame stale exactly on the beat's opening edge (reviewer).
  // Per-frame cost is a couple of integer comparisons; DOM classes change
  // only on window edges. No work while paused/hidden/ad — and never a
  // stuck .beat class after a stop.
  if (beatCursor && clock.isPlaying && !adActive && lines.length > 0) {
    setBeatClasses(beatCursor.frame(rawPos));
  } else {
    setBeatClasses(BEAT_IDLE);
  }

  // Word karaoke (kashi-server word-sync documents): applyView's change
  // detection leaves the spans alone on quiet frames; a line change repaints
  // the text and clears the span cache, and they are rebuilt here once.
  if (words && words.length > 0 && activeText !== null && !adActive) {
    if (wordLineIndex !== lineIndex) buildWordSpans(lineIndex, words);
    const wordIndex = findActiveWord(words, pos);
    highlightWord(wordIndex);
    updateWordFill(words, wordIndex, pos);
  }

  // Energy ramp + section dynamics (hype, Faz 6 P5): the played clock like
  // beats. Style writes happen only on QUANTIZED step changes / section
  // edges — a few times per second, never per frame.
  if (effectLevel === 'hype' && clock.isPlaying && !adActive && lines.length > 0) {
    setEnergyState(quantizedEnergy(currentEnergy, rawPos), inRampSection(currentSections, rawPos));
  } else {
    setEnergyState(0, false);
  }

  if (clock.isPlaying && !adActive) {
    requestAnimationFrame(frame);
  } else {
    loopActive = false;
  }
}

function ensureLoop(): void {
  if (loopActive) return;
  loopActive = true;
  requestAnimationFrame(frame);
}
// First paint uses the default level; the settings replay (first channel in
// replay order) corrects it before any lyrics arrive.
applyEffectLevelClass();
syncFxLayer(); // no-op at the default level; keeps bootstrap honest if it changes
applyPaletteVars();
applySizeVars();
ensureLoop();

// Self-healing repaint: several halt states (stopped loop + stale screen) had
// no exit path when the position stream dies mid-transition. One cheap frame
// per second guarantees the display always converges to current state.
setInterval(() => {
  ensureLoop();
  reportClassifiedSnaps();
}, 1000);

export {};
