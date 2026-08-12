/**
 * Minimal, dependency-free PNG reader.
 *
 * Only what the capture harness needs to validate the frames Chrome hands
 * back: the IHDR dimensions, and a full pixel decode so frame 0 can be
 * checked for the "entirely one flat colour" failure mode (dead GPU, scene
 * never drew, wrong canvas). Chrome's screenshot encoder emits 8-bit
 * non-interlaced RGB/RGBA, which is exactly the subset handled here.
 *
 * No PNG writing: Chrome already gives us encoded PNG bytes and we write
 * them through untouched, so a captured frame is byte-for-byte what the
 * browser produced.
 */

import { inflateSync } from 'node:zlib';

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** Channel count per PNG colour type. `null` = unsupported (palette). */
const CHANNELS_BY_COLOR_TYPE = { 0: 1, 2: 3, 3: null, 4: 2, 6: 4 };

function asBuffer(bytes) {
  if (Buffer.isBuffer(bytes)) return bytes;
  if (bytes instanceof Uint8Array) return Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return Buffer.from(bytes);
}

/**
 * Parse just the IHDR chunk. Cheap enough to run on every frame.
 *
 * @param {Uint8Array|Buffer} bytes
 * @returns {{width:number, height:number, bitDepth:number, colorType:number, interlace:number}}
 */
export function readPngHeader(bytes) {
  const buf = asBuffer(bytes);
  if (buf.length < 33 || !buf.subarray(0, 8).equals(SIGNATURE)) {
    throw new Error('not a PNG: bad signature');
  }
  if (buf.toString('latin1', 12, 16) !== 'IHDR') {
    throw new Error('malformed PNG: first chunk is not IHDR');
  }
  return {
    width: buf.readUInt32BE(16),
    height: buf.readUInt32BE(20),
    bitDepth: buf[24],
    colorType: buf[25],
    interlace: buf[28],
  };
}

/**
 * Decode an 8-bit, non-interlaced PNG to raw samples.
 *
 * @param {Uint8Array|Buffer} bytes
 * @returns {{width:number, height:number, channels:number, data:Buffer}}
 */
export function decodePng(bytes) {
  const buf = asBuffer(bytes);
  const header = readPngHeader(buf);
  const { width, height, bitDepth, colorType, interlace } = header;

  if (bitDepth !== 8) throw new Error(`unsupported PNG bit depth ${bitDepth} (expected 8)`);
  if (interlace !== 0) throw new Error('unsupported interlaced PNG');
  const channels = CHANNELS_BY_COLOR_TYPE[colorType];
  if (!channels) throw new Error(`unsupported PNG colour type ${colorType}`);

  // Walk the chunk list and concatenate IDAT payloads.
  const idat = [];
  let offset = 8;
  while (offset + 8 <= buf.length) {
    const length = buf.readUInt32BE(offset);
    const type = buf.toString('latin1', offset + 4, offset + 8);
    const dataStart = offset + 8;
    if (dataStart + length > buf.length) throw new Error(`truncated PNG chunk ${type}`);
    if (type === 'IDAT') idat.push(buf.subarray(dataStart, dataStart + length));
    if (type === 'IEND') break;
    offset = dataStart + length + 4; // + CRC
  }
  if (idat.length === 0) throw new Error('malformed PNG: no IDAT data');

  const raw = inflateSync(Buffer.concat(idat));
  const bpp = channels; // bitDepth 8 => one byte per sample
  const rowBytes = width * bpp;
  const expected = (rowBytes + 1) * height;
  if (raw.length < expected) {
    throw new Error(`truncated PNG pixel data: got ${raw.length} bytes, expected ${expected}`);
  }

  const out = Buffer.allocUnsafe(rowBytes * height);
  let src = 0;
  for (let y = 0; y < height; y++) {
    const filter = raw[src++];
    const row = y * rowBytes;
    const prev = row - rowBytes;
    for (let i = 0; i < rowBytes; i++) {
      const x = raw[src + i];
      const a = i >= bpp ? out[row + i - bpp] : 0;
      const b = y > 0 ? out[prev + i] : 0;
      const c = i >= bpp && y > 0 ? out[prev + i - bpp] : 0;
      let value;
      switch (filter) {
        case 0: value = x; break;
        case 1: value = x + a; break;
        case 2: value = x + b; break;
        case 3: value = x + ((a + b) >> 1); break;
        case 4: {
          const p = a + b - c;
          const pa = Math.abs(p - a);
          const pb = Math.abs(p - b);
          const pc = Math.abs(p - c);
          value = x + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c);
          break;
        }
        default:
          throw new Error(`unsupported PNG row filter ${filter} on row ${y}`);
      }
      out[row + i] = value & 0xff;
    }
    src += rowBytes;
  }

  return { width, height, channels, data: out };
}

/**
 * Is every pixel in the image the same colour? That is the signature of a
 * dead GPU or a scene that never drew anything.
 *
 * @param {{width:number, height:number, channels:number, data:Buffer}} image
 * @returns {{flat:boolean, color:number[]}}
 */
export function isUniform(image) {
  const { channels, data } = image;
  const color = [];
  for (let c = 0; c < channels; c++) color.push(data[c]);
  for (let i = channels; i < data.length; i += channels) {
    for (let c = 0; c < channels; c++) {
      if (data[i + c] !== color[c]) return { flat: false, color };
    }
  }
  return { flat: true, color };
}
