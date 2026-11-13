// What the renderer shows, computed from its own pure functions (no DOM):
// the display line, the active word, the sung words and the sweep fill at a
// clock position, and how long each word stays lit. Shared by audit-strip and
// the golden-frame harness so the two cannot drift apart.

/** main.ts FX_FIRED_HOLD_MS: at hype an fx word stays lit this long after it fires. */
export const FX_FIRED_HOLD_MS = 700;

/**
 * Per-document context the renderer builds once (line change and settings
 * cadence in main.ts): the fill plans by line identity and the fx index.
 */
export function simContext(R, lines, level, fx) {
  return {
    lines,
    level,
    plans: R.effects.planLineFillsByIdentity(lines, level),
    fxIndex: level === 'hype' ? R.effects.buildFxIndex(fx, lines) : new Map(),
  };
}

/** A line's sweep plan and the span each word sweeps over, as main.ts builds them. */
export function linePlan(R, ctx, lineIndex) {
  const { lines, level, plans } = ctx;
  const line = lines[lineIndex];
  const words = line?.words ?? [];
  const finalDwell = R.effects.lineFinalDwellMs(lines, lineIndex);
  const plan = plans[lineIndex] ?? R.effects.planLineFills(words, line?.adlib === true, level, finalDwell);
  return { ...plan, spans: R.effects.planSweepSpans(words, plan.held, finalDwell) };
}

/**
 * The renderer's state at clock position `pos` (player position + lead).
 * `fx_fired` assumes forward play through every word, as during playback.
 */
export function simulateAt(R, ctx, pos) {
  const { lines, fxIndex } = ctx;
  const lineIndex = lines.length > 0 ? R.view.findDisplayLine(lines, pos) : -1;
  const line = lineIndex >= 0 ? lines[lineIndex] : undefined;
  const words = line?.words ?? [];
  const state = { pos, line: lineIndex, text: line?.text ?? null, active: -1, sung: [], fill: {}, fx_fired: null };
  if (!line || words.length === 0) return state;
  const active = R.view.findActiveWord(words, pos);
  state.active = active;
  state.sung = words.map((_, i) => i).filter((i) => i < active);
  const { sweep, spans } = linePlan(R, ctx, lineIndex);
  if (active >= 0 && sweep[active]) {
    let runStart = active;
    while (runStart > 0 && sweep[runStart - 1]) runStart -= 1;
    for (let i = runStart; i < active; i += 1) state.fill[i] = 1;
    state.fill[active] = Number(R.effects.fillProgress(spans[active], pos).toFixed(3));
  }
  for (const hit of fxIndex.get(lineIndex) ?? []) {
    const w = words[hit.word];
    if (w && hit.word <= active && pos - w.start_ms < FX_FIRED_HOLD_MS) state.fx_fired = hit.word;
  }
  return state;
}

/**
 * How long every word is LIT on screen, in ms: the time it is the active word
 * of the displayed line. The renderer keeps a word lit until the next word
 * starts (findActiveWord), and a line-final word until the display moves on,
 * so this is the visibility a reader gets, not the word's own duration.
 * Stepped at 1 ms: every boundary the display rules use is an integer.
 *
 * @returns {number[][]} lit ms per line, per word (0 = never lit)
 */
export function wordLitTimes(R, lines) {
  const lit = lines.map((line) => (line.words ?? []).map(() => 0));
  const timed = lines.filter((line) => (line.words?.length ?? 0) > 0);
  if (timed.length === 0) return lit;
  const from = Math.min(...lines.map((l) => l.start_ms)) - R.view.LINE_LEAD_IN_MS - 1;
  const to = Math.max(...lines.map((l) => l.end_ms)) + R.view.INTERLUDE_GAP_MS + 1;
  for (let pos = from; pos <= to; pos += 1) {
    const lineIndex = R.view.findDisplayLine(lines, pos);
    if (lineIndex < 0) continue;
    const words = lines[lineIndex].words;
    if (!words || words.length === 0) continue;
    const active = R.view.findActiveWord(words, pos);
    if (active >= 0) lit[lineIndex][active] += 1;
  }
  return lit;
}

/**
 * Every word activation the renderer makes during straight playback, in
 * order: [{line, word, g, pos}], `g` the word's index in the whole document
 * (what the latency marker shows), `pos` the clock position it lights at.
 * Mirrors main.ts: a new displayed line rebuilds its spans and lights its
 * active word, if any; within a line, each change of the active word lights.
 */
export function wordActivations(R, lines) {
  const offsets = [];
  let total = 0;
  for (const line of lines) {
    offsets.push(total);
    total += line.words?.length ?? 0;
  }
  const out = [];
  if (total === 0) return out;
  const from = Math.min(...lines.map((l) => l.start_ms)) - R.view.LINE_LEAD_IN_MS - 1;
  const to = Math.max(...lines.map((l) => l.end_ms)) + R.view.INTERLUDE_GAP_MS + 1;
  let previousLine = -2;
  let previousWord = -2;
  for (let pos = from; pos <= to; pos += 1) {
    const line = R.view.findDisplayLine(lines, pos);
    const words = line >= 0 ? lines[line].words : undefined;
    const word = words && words.length > 0 ? R.view.findActiveWord(words, pos) : -1;
    if (line === previousLine && word === previousWord) continue;
    if (word >= 0 && (line !== previousLine || word !== previousWord)) {
      out.push({ line, word, g: offsets[line] + word, pos });
    }
    previousLine = line;
    previousWord = word;
  }
  return out;
}

/** Counts behind the dwell report: words lit for under 120 and 180 ms. */
export function dwellSummary(litTimes) {
  const all = litTimes.flat();
  const sorted = [...all].sort((a, b) => a - b);
  const median = sorted.length === 0 ? null : sorted[Math.floor((sorted.length - 1) / 2)];
  return {
    words: all.length,
    never_lit: all.filter((ms) => ms === 0).length,
    lit_lt_120: all.filter((ms) => ms < 120).length,
    lit_lt_180: all.filter((ms) => ms < 180).length,
    median_lit_ms: median,
  };
}
