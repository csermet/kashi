// Just enough PNG for golden-frame comparison: decode the 8-bit, non-interlaced
// RGB/RGBA images Chromium's screenshots produce (zlib from node:), and count
// the pixels two images disagree on. No dependency.
import { inflateSync } from 'node:zlib';

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** @returns {{width: number, height: number, rgba: Uint8Array}} */
export function decodePng(bytes) {
  if (!bytes.subarray(0, 8).equals(SIGNATURE)) throw new Error('not a PNG');
  let offset = 8;
  let header = null;
  const idat = [];
  while (offset < bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const type = bytes.toString('ascii', offset + 4, offset + 8);
    const data = bytes.subarray(offset + 8, offset + 8 + length);
    offset += 12 + length;
    if (type === 'IHDR') {
      header = {
        width: data.readUInt32BE(0),
        height: data.readUInt32BE(4),
        depth: data[8],
        colorType: data[9],
        interlace: data[12],
      };
    } else if (type === 'IDAT') {
      idat.push(data);
    } else if (type === 'IEND') {
      break;
    }
  }
  if (!header) throw new Error('PNG without IHDR');
  const { width, height, depth, colorType, interlace } = header;
  const channels = { 2: 3, 6: 4 }[colorType];
  if (depth !== 8 || !channels || interlace !== 0) {
    throw new Error(`unsupported PNG (depth ${depth}, colour type ${colorType}, interlace ${interlace})`);
  }
  const raw = inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const pixels = new Uint8Array(height * stride);
  for (let y = 0; y < height; y += 1) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const out = pixels.subarray(y * stride, (y + 1) * stride);
    const prev = y > 0 ? pixels.subarray((y - 1) * stride, y * stride) : null;
    for (let x = 0; x < stride; x += 1) {
      const a = x >= channels ? out[x - channels] : 0;
      const b = prev ? prev[x] : 0;
      const c = prev && x >= channels ? prev[x - channels] : 0;
      let value = line[x];
      if (filter === 1) value += a;
      else if (filter === 2) value += b;
      else if (filter === 3) value += (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a);
        const pb = Math.abs(p - b);
        const pc = Math.abs(p - c);
        value += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      } else if (filter !== 0) {
        throw new Error(`bad PNG filter ${filter}`);
      }
      out[x] = value & 0xff;
    }
  }
  if (channels === 4) return { width, height, rgba: pixels };
  const rgba = new Uint8Array(width * height * 4);
  for (let i = 0, j = 0; i < pixels.length; i += 3, j += 4) {
    rgba[j] = pixels[i];
    rgba[j + 1] = pixels[i + 1];
    rgba[j + 2] = pixels[i + 2];
    rgba[j + 3] = 255;
  }
  return { width, height, rgba };
}

/**
 * How far two decoded images are apart: the share of pixels where any
 * channel differs by more than `channelTolerance`, and the largest channel
 * difference seen. A size mismatch is reported, not compared.
 */
export function comparePixels(a, b, channelTolerance) {
  if (a.width !== b.width || a.height !== b.height) {
    return { sizeMismatch: true, differing: Infinity, share: 1, maxDelta: 255 };
  }
  let differing = 0;
  let maxDelta = 0;
  for (let i = 0; i < a.rgba.length; i += 4) {
    let worst = 0;
    for (let c = 0; c < 4; c += 1) worst = Math.max(worst, Math.abs(a.rgba[i + c] - b.rgba[i + c]));
    if (worst > maxDelta) maxDelta = worst;
    if (worst > channelTolerance) differing += 1;
  }
  return { sizeMismatch: false, differing, share: differing / (a.width * a.height), maxDelta };
}
