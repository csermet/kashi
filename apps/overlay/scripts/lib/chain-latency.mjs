// Pure parts of the chain-latency analysis (scripts/chain-latency.mjs): no
// I/O, no dependency, unit-tested in chain-latency.test.mjs.
//
//   1. audio alignment: where the reference song sits in the recording's
//      audio (coarse: log-energy envelopes; fine: GCC-PHAT on the waveform,
//      per segment; a robust line through the segments absorbs clock drift);
//   2. marker reading: the latency marker's cells per video frame -> code
//      changes (src/renderer/src/latency-marker.ts draws them);
//   3. matching: each code change to the word activation it shows, and the
//      lit-minus-intended time of every matched word.

// ---- numbers -------------------------------------------------------------

export function quantile(values, q) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

export const median = (values) => quantile(values, 0.5);

// ---- FFT -----------------------------------------------------------------

/** In-place iterative radix-2 FFT of (re, im); length a power of two. */
export function fft(re, im, inverse = false) {
  const n = re.length;
  if (n & (n - 1)) throw new Error(`fft length ${n} is not a power of two`);
  for (let i = 1, j = 0; i < n; i += 1) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const angle = ((inverse ? 2 : -2) * Math.PI) / len;
    const wr = Math.cos(angle);
    const wi = Math.sin(angle);
    for (let start = 0; start < n; start += len) {
      let cr = 1;
      let ci = 0;
      for (let k = 0; k < len / 2; k += 1) {
        const a = start + k;
        const b = a + len / 2;
        const tr = re[b] * cr - im[b] * ci;
        const ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr;
        im[b] = im[a] - ti;
        re[a] += tr;
        im[a] += ti;
        const next = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = next;
      }
    }
  }
  if (inverse) {
    for (let i = 0; i < n; i += 1) {
      re[i] /= n;
      im[i] /= n;
    }
  }
}

const nextPow2 = (n) => 2 ** Math.ceil(Math.log2(Math.max(2, n)));

/**
 * Cross-correlation of x against y: c[lag] = sum_t x[t + lag] * y[t], for
 * lag in [-(y.length - 1), x.length - 1]. `phat` whitens the cross spectrum
 * (GCC-PHAT), which turns "same source, other mix" into a sharp peak.
 * Returns {values, zero} with values[zero] the lag-0 term.
 */
export function crossCorrelate(x, y, { phat = false } = {}) {
  const n = nextPow2(x.length + y.length);
  const xr = new Float64Array(n);
  const xi = new Float64Array(n);
  const yr = new Float64Array(n);
  const yi = new Float64Array(n);
  xr.set(x);
  yr.set(y);
  fft(xr, xi);
  fft(yr, yi);
  for (let k = 0; k < n; k += 1) {
    // X * conj(Y)
    const r = xr[k] * yr[k] + xi[k] * yi[k];
    const i = xi[k] * yr[k] - xr[k] * yi[k];
    const mag = phat ? Math.hypot(r, i) + 1e-12 : 1;
    xr[k] = r / mag;
    xi[k] = i / mag;
  }
  fft(xr, xi, true);
  // Circular result: lags 0..x.length-1 at the front, negative lags at the back.
  const zero = y.length - 1;
  const values = new Float64Array(x.length + y.length - 1);
  for (let lag = -(y.length - 1); lag <= x.length - 1; lag += 1) {
    values[lag + zero] = xr[(lag + n) % n];
  }
  return { values, zero };
}

/** The lag with the largest value, and how far it stands above the runner-up away from it. */
export function peakLag({ values, zero }, { minLag = -Infinity, maxLag = Infinity, guard = 1 } = {}) {
  let best = -1;
  for (let i = 0; i < values.length; i += 1) {
    const lag = i - zero;
    if (lag < minLag || lag > maxLag) continue;
    if (best < 0 || values[i] > values[best]) best = i;
  }
  if (best < 0) return null;
  let second = -Infinity;
  for (let i = 0; i < values.length; i += 1) {
    const lag = i - zero;
    if (lag < minLag || lag > maxLag || Math.abs(i - best) <= guard) continue;
    second = Math.max(second, values[i]);
  }
  // Parabolic interpolation around the peak for a sub-sample lag.
  const a = values[best - 1];
  const b = values[best];
  const c = values[best + 1];
  let frac = 0;
  if (a !== undefined && c !== undefined && a - 2 * b + c !== 0) frac = (0.5 * (a - c)) / (a - 2 * b + c);
  return { lag: best - zero + frac, value: b, ratio: second > 0 ? b / second : Infinity };
}

// ---- audio alignment -----------------------------------------------------

/** Log-energy envelope, one value per `hop` samples, mean removed. */
export function envelope(samples, hop) {
  const out = new Float64Array(Math.floor(samples.length / hop));
  for (let i = 0; i < out.length; i += 1) {
    let energy = 0;
    for (let j = i * hop; j < (i + 1) * hop; j += 1) energy += samples[j] * samples[j];
    out[i] = Math.log10(energy / hop + 1e-10);
  }
  const mean = out.reduce((s, v) => s + v, 0) / Math.max(1, out.length);
  for (let i = 0; i < out.length; i += 1) out[i] -= mean;
  return out;
}

/**
 * Where reference sample 0 falls in the recording, in seconds (the recording
 * time at which the song's media time 0 plays), from log-energy envelopes at
 * 100 Hz: robust to a different mix, ~10 ms coarse.
 */
export function coarseOffset(recording, reference, rate) {
  const hop = Math.round(rate / 100);
  const corr = crossCorrelate(envelope(recording, hop), envelope(reference, hop));
  const peak = peakLag(corr, { guard: 50 });
  return { offsetS: (peak.lag * hop) / rate, ratio: peak.ratio };
}

/**
 * Fine offsets with GCC-PHAT on the waveform: for segments of the recording
 * (segmentS long, every stepS), the lag against the reference within
 * +-searchS of the coarse offset. Returns [{atS, offsetS, ratio}]: atS is the
 * segment's centre in recording time.
 */
export function fineOffsets(recording, reference, rate, coarseS, { segmentS = 4, stepS = 10, searchS = 0.1 } = {}) {
  const seg = Math.round(segmentS * rate);
  const search = Math.round(searchS * rate);
  const out = [];
  for (let start = 0; start + seg <= recording.length; start += Math.round(stepS * rate)) {
    const refStart = start - Math.round(coarseS * rate) - search;
    if (refStart < 0 || refStart + seg + 2 * search > reference.length) continue;
    const x = recording.subarray(start, start + seg);
    let energy = 0;
    for (const v of x) energy += v * v;
    if (energy / seg < 1e-6) continue; // silence: nothing to align
    const y = reference.subarray(refStart, refStart + seg + 2 * search);
    const peak = peakLag(crossCorrelate(x, y, { phat: true }), { minLag: -2 * search, maxLag: 0, guard: 2 });
    if (!peak) continue;
    // x[t] ~ y[t - lag]  =>  recording sample `start` ~ reference sample refStart - lag
    const refSample = refStart - peak.lag;
    out.push({ atS: (start + seg / 2) / rate, offsetS: (start - refSample) / rate, ratio: peak.ratio });
  }
  return out;
}

/**
 * offset(t) = a + b * t through the fine offsets, refitted without points more
 * than `toleranceS` from the first fit. Media time of recording time t is
 * t - offset(t).
 */
export function fitOffsets(points, { toleranceS = 0.004, minRatio = 1.5 } = {}) {
  const usable = points.filter((p) => p.ratio >= minRatio);
  const fit = (pts) => {
    if (pts.length === 0) return null;
    if (pts.length === 1) return { a: pts[0].offsetS, b: 0 };
    const mt = pts.reduce((s, p) => s + p.atS, 0) / pts.length;
    const mo = pts.reduce((s, p) => s + p.offsetS, 0) / pts.length;
    let num = 0;
    let den = 0;
    for (const p of pts) {
      num += (p.atS - mt) * (p.offsetS - mo);
      den += (p.atS - mt) ** 2;
    }
    const b = den > 0 ? num / den : 0;
    return { a: mo - b * mt, b };
  };
  // Start from the median (robust to a stray segment), then least squares on the inliers.
  const med = median(usable.map((p) => p.offsetS));
  let inliers = usable.filter((p) => Math.abs(p.offsetS - med) <= 0.02);
  let line = fit(inliers);
  if (!line) return null;
  inliers = usable.filter((p) => Math.abs(p.offsetS - (line.a + line.b * p.atS)) <= toleranceS);
  line = fit(inliers) ?? line;
  const residuals = inliers.map((p) => p.offsetS - (line.a + line.b * p.atS));
  return {
    ...line,
    used: inliers.length,
    of: points.length,
    residualMaxS: residuals.length ? Math.max(...residuals.map(Math.abs)) : null,
  };
}

// ---- the marker ----------------------------------------------------------

/**
 * Cell means (0-255, one array per frame, cells in marker order) -> the code
 * each frame shows, or null for a frame caught mid-change (a cell between
 * the clear levels). Code = parity bit then CODE_BITS bits, as an integer
 * with the parity as its top bit.
 */
export function frameCodes(cellMeans, { low = 70, high = 185 } = {}) {
  return cellMeans.map((cells) => {
    let code = 0;
    for (const v of cells) {
      if (v > low && v < high) return null;
      code = code * 2 + (v >= high ? 1 : 0);
    }
    return code;
  });
}

/**
 * Code changes: [{frame, code}] at the first clean frame showing a new code.
 * The first clean frame is the starting state, never a change: the recording
 * cannot tell when a code already on screen appeared.
 */
export function codeChanges(codes) {
  const changes = [];
  let previous;
  codes.forEach((code, frame) => {
    if (code === null || code === previous) return;
    if (previous !== undefined) changes.push({ frame, code });
    previous = code;
  });
  return changes;
}

/** The word index part of a code (the parity bit dropped). */
export const wordCode = (code, codeBits = 6) => code & (2 ** codeBits - 1);

/**
 * Pairs each marker change with the activation it shows: same index mod
 * 2^codeBits, nearest intended time within `windowS`. `changes` carry media
 * times (mediaS, the change's estimated time); `activations` are
 * [{g, mediaS}] from the overlay's own display rules. Returns matched pairs
 * with lit-minus-intended in ms, and what could not be matched.
 */
export function matchActivations(changes, activations, { codeBits = 6, windowS = 1 } = {}) {
  const byCode = new Map();
  for (const a of activations) {
    const key = a.g % 2 ** codeBits;
    if (!byCode.has(key)) byCode.set(key, []);
    byCode.get(key).push(a);
  }
  const used = new Set();
  const matched = [];
  const unmatched = [];
  for (const change of changes) {
    const candidates = byCode.get(wordCode(change.code, codeBits)) ?? [];
    let best = null;
    for (const a of candidates) {
      if (used.has(a)) continue;
      const d = Math.abs(change.mediaS - a.mediaS);
      if (d <= windowS && (!best || d < Math.abs(change.mediaS - best.mediaS))) best = a;
    }
    if (!best) {
      unmatched.push(change);
      continue;
    }
    used.add(best);
    matched.push({ g: best.g, intendedS: best.mediaS, litS: change.mediaS, latencyMs: (change.mediaS - best.mediaS) * 1000 });
  }
  return { matched, unmatched, missed: activations.length - used.size };
}

/** Median, IQR and spread of the matched latencies (ms). */
export function latencySummary(matched) {
  const values = matched.map((m) => m.latencyMs);
  const r = (v) => (v === null ? null : Math.round(v * 10) / 10);
  const p25 = quantile(values, 0.25);
  const p75 = quantile(values, 0.75);
  return {
    n: values.length,
    median_ms: r(median(values)),
    p25_ms: r(p25),
    p75_ms: r(p75),
    iqr_ms: p25 === null ? null : r(p75 - p25),
    p05_ms: r(quantile(values, 0.05)),
    p95_ms: r(quantile(values, 0.95)),
  };
}

// ---- finding the marker --------------------------------------------------

/**
 * Per-pixel statistics over sampled grey frames, updated frame by frame (no
 * frames kept). The marker's cells are the pixels that are almost always
 * pure black or pure white AND switch between the two several times; text,
 * the box and the desktop either sit still or pass through middle greys.
 */
export class MarkerFinder {
  constructor(width, height, { dark = 50, bright = 205 } = {}) {
    this.width = width;
    this.height = height;
    this.dark = dark;
    this.bright = bright;
    this.frames = 0;
    this.extreme = new Uint32Array(width * height);
    this.whites = new Uint32Array(width * height);
    this.flips = new Uint32Array(width * height);
    this.state = new Int8Array(width * height); // -1 dark, 1 bright, 0 unknown
  }

  add(gray) {
    this.frames += 1;
    for (let i = 0; i < gray.length; i += 1) {
      const v = gray[i];
      const s = v <= this.dark ? -1 : v >= this.bright ? 1 : 0;
      if (s === 0) continue;
      this.extreme[i] += 1;
      if (s === 1) this.whites[i] += 1;
      if (this.state[i] !== 0 && this.state[i] !== s) this.flips[i] += 1;
      this.state[i] = s;
    }
  }

  /**
   * The marker's bounding box in this finder's pixels: the largest 4-connected
   * group of candidate pixels (joined across rarely flipping cells) whose box
   * is a cells:1 strip.
   */
  locate({ cells = 7, minFlips = 4, minExtreme = 0.95, aspectTolerance = 0.3 } = {}) {
    const { width, height } = this;
    const candidate = new Uint8Array(width * height);
    for (let i = 0; i < candidate.length; i += 1) {
      const white = this.whites[i] / Math.max(1, this.extreme[i]);
      candidate[i] =
        this.extreme[i] >= minExtreme * this.frames && this.flips[i] >= minFlips && white > 0.02 && white < 0.98 ? 1 : 0;
    }
    const seen = new Uint8Array(width * height);
    const boxes = [];
    for (let start = 0; start < candidate.length; start += 1) {
      if (!candidate[start] || seen[start]) continue;
      let x0 = width;
      let y0 = height;
      let x1 = -1;
      let y1 = -1;
      let count = 0;
      const stack = [start];
      seen[start] = 1;
      while (stack.length) {
        const i = stack.pop();
        const x = i % width;
        const y = (i - x) / width;
        count += 1;
        x0 = Math.min(x0, x);
        x1 = Math.max(x1, x);
        y0 = Math.min(y0, y);
        y1 = Math.max(y1, y);
        for (const j of [i - 1, i + 1, i - width, i + width]) {
          if (j < 0 || j >= candidate.length || seen[j] || !candidate[j]) continue;
          if ((j === i - 1 || j === i + 1) && Math.floor(j / width) !== y) continue;
          seen[j] = 1;
          stack.push(j);
        }
      }
      boxes.push({ x: x0, y: y0, width: x1 - x0 + 1, height: y1 - y0 + 1, count });
    }
    // A high code bit can flip too rarely to count as a candidate (bit 5
    // changes once per 32 words), which splits the strip. Join boxes on the
    // same row whose gap is at most two cells of pixels that are always pure
    // black or white.
    const extremeEnough = (i) => this.extreme[i] >= minExtreme * this.frames;
    const gapIsMarker = (a, b) => {
      for (let y = a.y; y < a.y + a.height; y += 1) {
        for (let x = a.x + a.width; x < b.x; x += 1) if (!extremeEnough(y * width + x)) return false;
      }
      return true;
    };
    let merged = true;
    while (merged) {
      merged = false;
      boxes.sort((a, b) => a.y - b.y || a.x - b.x);
      for (let i = 0; i < boxes.length && !merged; i += 1) {
        for (let j = 0; j < boxes.length && !merged; j += 1) {
          const a = boxes[i];
          const b = boxes[j];
          if (i === j || Math.abs(a.y - b.y) > 1 || Math.abs(a.height - b.height) > 1) continue;
          const gap = b.x - (a.x + a.width);
          if (gap < 0 || gap > 2 * a.height || !gapIsMarker(a, b)) continue;
          const y = Math.min(a.y, b.y);
          const joined = {
            x: a.x,
            y,
            width: b.x + b.width - a.x,
            height: Math.max(a.y + a.height, b.y + b.height) - y,
            count: a.count + b.count + gap * a.height,
          };
          boxes.splice(Math.max(i, j), 1);
          boxes.splice(Math.min(i, j), 1, joined);
          merged = true;
        }
      }
    }
    for (const b of boxes) {
      b.aspect = b.width / b.height;
      b.fill = b.count / (b.width * b.height);
    }
    const strips = boxes.filter((b) => b.aspect > cells * (1 - aspectTolerance) && b.aspect < cells * (1 + aspectTolerance) && b.fill > 0.6 && b.count >= cells);
    strips.sort((a, b) => b.count - a.count);
    return { box: strips[0] ?? null, candidates: boxes.sort((a, b) => b.count - a.count).slice(0, 5) };
  }
}

/** The centre half of each of `cells` equal cells in a box: [{x, y, width, height}]. */
export function cellRects(box, cells = 7) {
  const cw = box.width / cells;
  return Array.from({ length: cells }, (_, i) => ({
    x: Math.round(box.x + i * cw + cw / 4),
    y: Math.round(box.y + box.height / 4),
    width: Math.max(1, Math.round(cw / 2)),
    height: Math.max(1, Math.round(box.height / 2)),
  }));
}

/** Mean grey of each rect in one frame (`gray` is `width` pixels wide). */
export function cellMeans(gray, width, rects) {
  return rects.map((r) => {
    let sum = 0;
    for (let y = r.y; y < r.y + r.height; y += 1) {
      for (let x = r.x; x < r.x + r.width; x += 1) sum += gray[y * width + x];
    }
    return sum / (r.width * r.height);
  });
}
