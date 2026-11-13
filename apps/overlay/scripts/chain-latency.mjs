// Chain latency from a screen recording (client review step E.1): how late
// the overlay lights a word, measured on the screen against the sound.
//
// Record the LIVE overlay built with KASHI_LATENCY_MARKER=1 (a black/white
// code block in its window's top-left corner changes on every word
// activation) with OBS at 60 fps, screen plus desktop audio, one song per
// file, played straight through. Then:
//
//   node scripts/chain-latency.mjs --recording song.mp4 --reference song.wav \
//        --doc doc.json [--id <video id> for a [{id, doc}] dump] --offset -200 \
//        [--region x,y,w,h] [--out report.json]
//
// --reference: the song's audio on its own clock (the audit's vocal stem
// scratch/audit/stems/<id>.wav, or the full mix). --offset: the user offset
// the overlay had during the recording (the number its tray shows; -200
// removes the hidden 200 ms base lead, so the renderer aims at the word's own
// start). --region: the marker's box in video pixels, if the automatic
// search does not find it.
//
// How: the recording's audio is aligned with the reference (log-energy
// envelopes, then GCC-PHAT per 4 s segment, a line through the segments for
// clock drift), which gives the song's media time of every video frame. The
// marker's cells are read in every frame; each code change is matched to the
// word activation it shows (the overlay's own display rules, scripts/lib/
// sim.mjs, from the same document), and lit minus intended is that word's
// chain latency. A change first visible in frame k happened between frames
// k-1 and k; the midpoint is used (+-8 ms at 60 fps, unbiased on average).
// Reported: median and IQR in ms. Needs ffmpeg/ffprobe on PATH.
//
// Checked on synthetic recordings (the marker build rendered frame-exact,
// composed with a known display delay, x264 + AAC): 0 / 70 / 133 ms came back
// as -1.6 / 68.4 / 131.3 ms in mp4. In mkv the same 70 ms read 89.2 ms because
// the container itself presented the video 21 ms late (AAC priming), which a
// player shows too: record mp4.
import { spawn, spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import {
  cellMeans,
  cellRects,
  codeChanges,
  coarseOffset,
  fineOffsets,
  fitOffsets,
  frameCodes,
  latencySummary,
  MarkerFinder,
  matchActivations,
  median,
} from './lib/chain-latency.mjs';
import { loadRendererSrc } from './lib/renderer-src.mjs';
import { wordActivations } from './lib/sim.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OVERLAY = resolve(HERE, '..');
const RATE = 16000;

const { values: args } = parseArgs({
  options: {
    recording: { type: 'string' },
    reference: { type: 'string' },
    doc: { type: 'string' },
    id: { type: 'string' },
    offset: { type: 'string', default: '0' },
    region: { type: 'string' },
    out: { type: 'string' },
  },
});
if (!args.recording || !args.reference || !args.doc) {
  console.error('need --recording, --reference and --doc (see the header of this file)');
  process.exit(2);
}

const say = (line) => console.error(line);

function probe(file) {
  const r = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'stream=index,codec_type,width,height,start_time,avg_frame_rate', '-of', 'json', file], { encoding: 'utf8' });
  if (r.status !== 0) throw new Error(`ffprobe ${file}: ${r.stderr}`);
  return JSON.parse(r.stdout).streams;
}

function decodeAudio(file) {
  const r = spawnSync('ffmpeg', ['-v', 'error', '-i', file, '-map', '0:a:0', '-ac', '1', '-ar', String(RATE), '-f', 'f32le', '-'], {
    maxBuffer: 2 ** 31,
  });
  if (r.status !== 0) throw new Error(`ffmpeg audio ${file}: ${r.stderr}`);
  return new Float32Array(r.stdout.buffer, r.stdout.byteOffset, r.stdout.byteLength / 4);
}

/** Streams grey frames of `filter` output to onFrame(gray, ptsS); resolves when done. */
function streamFrames(file, filter, width, height, onFrame) {
  return new Promise((ok, fail) => {
    const size = width * height;
    const child = spawn('ffmpeg', ['-v', 'info', '-nostats', '-i', file, '-map', '0:v:0', '-vf', `${filter},format=gray,showinfo`, '-fps_mode', 'passthrough', '-f', 'rawvideo', '-']);
    const pts = [];
    let stderr = '';
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
      let nl;
      while ((nl = stderr.indexOf('\n')) >= 0) {
        const line = stderr.slice(0, nl);
        stderr = stderr.slice(nl + 1);
        const m = /\bn:\s*(\d+)\b.*\bpts_time:\s*(-?[\d.]+)/.exec(line);
        if (m) pts[Number(m[1])] = Number(m[2]);
      }
    });
    let pending = Buffer.alloc(0);
    let index = 0;
    const frames = [];
    child.stdout.on('data', (chunk) => {
      pending = pending.length ? Buffer.concat([pending, chunk]) : chunk;
      while (pending.length >= size) {
        frames.push(new Uint8Array(pending.subarray(0, size)));
        pending = pending.subarray(size);
      }
      // showinfo's line for a frame can arrive after the frame: hand frames on
      // once their timestamp is known.
      while (frames.length && pts[index] !== undefined) {
        onFrame(frames.shift(), pts[index]);
        index += 1;
      }
    });
    child.on('close', (code) => {
      while (frames.length && pts[index] !== undefined) {
        onFrame(frames.shift(), pts[index]);
        index += 1;
      }
      if (code !== 0) return fail(new Error(`ffmpeg video exited ${code}`));
      if (frames.length) return fail(new Error(`${frames.length} frame(s) without a timestamp`));
      ok(index);
    });
  });
}

/** The marker's box in video pixels: coarse at 1/4 scale over 5 fps, then exact on a crop. */
async function findMarker(file, video) {
  const W = Math.floor(video.width / 4);
  const H = Math.floor(video.height / 4);
  const coarse = new MarkerFinder(W, H);
  await streamFrames(file, `fps=5,scale=${W}:${H}:flags=area`, W, H, (gray) => coarse.add(gray));
  // At quarter scale a cell is a few pixels and its edges blur into the
  // neighbours: accept a rough strip here, the full-resolution pass is exact.
  const found = coarse.locate({ cells: CELLS, aspectTolerance: 0.5 });
  if (!found.box) return { box: null, candidates: found.candidates };
  const margin = 12;
  const x = Math.max(0, found.box.x * 4 - margin);
  const y = Math.max(0, found.box.y * 4 - margin);
  const w = Math.min(video.width - x, found.box.width * 4 + 2 * margin);
  const h = Math.min(video.height - y, found.box.height * 4 + 2 * margin);
  const exact = new MarkerFinder(w, h);
  await streamFrames(file, `fps=5,crop=${w}:${h}:${x}:${y}`, w, h, (gray) => exact.add(gray));
  const refined = exact.locate({ cells: CELLS });
  if (!refined.box) return { box: null, candidates: refined.candidates };
  const b = refined.box;
  return { box: { x: x + b.x, y: y + b.y, width: b.width, height: b.height } };
}

// ---- 1. the document's activations ------------------------------------------
const R = await loadRendererSrc(OVERLAY);
// The marker's layout from the renderer's own module (latency-marker.ts).
const CELLS = R.marker.MARKER_CELLS;
const CODE_BITS = R.marker.MARKER_CODE_BITS;
const raw = JSON.parse(readFileSync(args.doc, 'utf8'));
const doc = Array.isArray(raw) ? raw.find((e) => e.id === args.id)?.doc : raw;
if (!doc) throw new Error(`no document${args.id ? ` for ${args.id}` : ''} in ${args.doc}`);
const mapped = R.mapDocument(doc);
if (!mapped?.found || mapped.sync !== 'word') throw new Error('the document has no word timings');
const userOffset = Number(args.offset);
const leadMs = R.appliedLeadMs(userOffset);
const activations = wordActivations(R, mapped.lines).map((a) => ({ ...a, mediaS: (a.pos - leadMs) / 1000 }));
say(`document: ${activations.length} word activations, lead ${leadMs} ms (user offset ${userOffset})`);

// ---- 2. media time of the recording -------------------------------------------
const streams = probe(args.recording);
const video = streams.find((s) => s.codec_type === 'video');
const audioStream = streams.find((s) => s.codec_type === 'audio');
if (!video || !audioStream) throw new Error('the recording needs a video and an audio stream');
const audioStart = Number(audioStream.start_time ?? 0);
const recording = decodeAudio(args.recording);
const reference = decodeAudio(args.reference);
const coarse = coarseOffset(recording, reference, RATE);
const points = fineOffsets(recording, reference, RATE, coarse.offsetS);
const line = fitOffsets(points);
if (!line || line.used < 3) {
  throw new Error(`the song was not found in the recording's audio (coarse ${coarse.offsetS.toFixed(3)} s, ${line?.used ?? 0} of ${points.length} segments agree)`);
}
say(
  `audio: song starts at ${(audioStart + line.a).toFixed(4)} s of the recording, drift ${(line.b * 1e6).toFixed(0)} ppm, ` +
    `${line.used} of ${line.of} segments within 4 ms (max residual ${(line.residualMaxS * 1000).toFixed(2)} ms)`,
);
/** Song media time (s) of a recording timestamp (s). */
const mediaOf = (ptsS) => {
  const u = ptsS - audioStart;
  return u - (line.a + line.b * u);
};

// ---- 3. the marker -----------------------------------------------------------
let box;
if (args.region) {
  const [x, y, width, height] = args.region.split(',').map(Number);
  box = { x, y, width, height };
} else {
  const found = await findMarker(args.recording, video);
  if (!found.box) {
    console.error('marker not found automatically; pass --region x,y,w,h. Largest flipping areas:', JSON.stringify(found.candidates));
    process.exit(3);
  }
  box = found.box;
}
say(`marker: ${box.width}x${box.height} px at ${box.x},${box.y}`);
const rects = cellRects({ x: 0, y: 0, width: box.width, height: box.height }, CELLS);
const means = [];
const stamps = [];
await streamFrames(args.recording, `crop=${box.width}:${box.height}:${box.x}:${box.y}`, box.width, box.height, (gray, pts) => {
  means.push(cellMeans(gray, box.width, rects));
  stamps.push(pts);
});
const codes = frameCodes(means);
const changes = codeChanges(codes).map(({ frame, code }) => {
  // First visible in `frame`: it happened after the previous frame was taken.
  const at = frame > 0 ? (stamps[frame - 1] + stamps[frame]) / 2 : stamps[frame];
  return { frame, code, mediaS: mediaOf(at) };
});
const frameIntervals = stamps.slice(1).map((t, i) => t - stamps[i]);
say(`video: ${stamps.length} frames (median interval ${(median(frameIntervals) * 1000).toFixed(2)} ms), ${changes.length} marker changes, ${codes.filter((c) => c === null).length} frames mid-change`);

// ---- 4. lit minus intended ------------------------------------------------------
const { matched, unmatched, missed } = matchActivations(changes, activations, { codeBits: CODE_BITS });
const summary = latencySummary(matched);
const half = Math.floor(matched.length / 2);
const report = {
  recording: args.recording,
  reference: args.reference,
  user_offset_ms: userOffset,
  lead_ms: leadMs,
  audio: { song_start_s: audioStart + line.a, drift_ppm: line.b * 1e6, segments_used: line.used, segments: line.of, residual_max_ms: line.residualMaxS * 1000 },
  marker_box: box,
  frames: stamps.length,
  marker_changes: changes.length,
  matched: matched.length,
  unmatched_changes: unmatched.length,
  activations_not_seen: missed,
  latency: summary,
  latency_first_half: latencySummary(matched.slice(0, half)),
  latency_second_half: latencySummary(matched.slice(half)),
  words: matched,
};
if (args.out) writeFileSync(args.out, JSON.stringify(report, null, 1) + '\n');
console.log(
  `chain latency: median ${summary.median_ms} ms, IQR ${summary.p25_ms}..${summary.p75_ms} ms (${summary.iqr_ms} ms), ` +
    `p5..p95 ${summary.p05_ms}..${summary.p95_ms} ms over ${summary.n} words ` +
    `(${unmatched.length} marker changes unmatched, ${missed} activations not seen)`,
);
