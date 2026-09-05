/**
 * Narration.
 *
 * edge-tts gives a Turkish neural voice for free and with no API key. Turkish
 * orthography is phonetic, so the syllables in lesson.json ("be", "bi", "bu")
 * come back pronounced the way the Diyanet book teaches them -- which is also
 * this approach's limitation, and a deliberate one: a Turkish voice makes no
 * distinction between the letters Turkish collapses (ha/hi/he, se/sin/sad),
 * exactly as a Turkish elifba class does. Proper Arabic makhraj needs a
 * reciter, not a TTS.
 *
 * Clips are cached by (voice, rate, text) so a re-render costs no network and
 * an edited lesson only synthesises what changed.
 */

import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { access, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

/** Slower than default: a three-year-old needs the gap between syllables. */
export const DEFAULT_RATE = '-20%';

export function clipKey(text, voice, rate) {
  return createHash('sha1').update([voice, rate, text].join('\u0000')).digest('hex').slice(0, 16);
}

async function exists(p) {
  try {
    await access(p);
    return true;
  } catch {
    return false;
  }
}

/**
 * Length of an audio file in seconds, via ffprobe.
 *
 * @param {string} file
 * @param {string} [ffprobePath]
 * @returns {Promise<number>}
 */
export async function probeDuration(file, ffprobePath = 'ffprobe') {
  const { stdout } = await execFileAsync(ffprobePath, [
    '-v', 'error',
    '-show_entries', 'format=duration',
    '-of', 'default=noprint_wrappers=1:nokey=1',
    file,
  ]);
  const seconds = Number.parseFloat(String(stdout).trim());
  if (!Number.isFinite(seconds) || seconds <= 0) {
    throw new Error(`voice: ffprobe returned no usable duration for ${file} (got ${JSON.stringify(stdout)})`);
  }
  return seconds;
}

/**
 * Synthesise every line, reusing whatever is already cached.
 *
 * @param {string[]} lines
 * @param {{cacheDir: string, voice: string, rate?: string, python?: string,
 *          ffprobePath?: string, onProgress?: (done:number,total:number,text:string,cached:boolean)=>void}} opts
 * @returns {Promise<{files: Map<string,string>, durations: Map<string,number>}>}
 */
export async function synthesise(lines, opts) {
  const {
    cacheDir,
    voice,
    rate = DEFAULT_RATE,
    python = 'python',
    ffprobePath = 'ffprobe',
    onProgress,
  } = opts;

  await mkdir(cacheDir, { recursive: true });

  const files = new Map();
  const durations = new Map();
  const index = {};

  for (let i = 0; i < lines.length; i++) {
    const text = lines[i];
    const file = path.join(cacheDir, `${clipKey(text, voice, rate)}.mp3`);
    const cached = await exists(file);

    if (!cached) {
      // `--rate=-20%` must be one argv token: edge-tts parses a leading "-"
      // in a separate value as the start of another flag.
      await execFileAsync(python, [
        '-m', 'edge_tts',
        '--voice', voice,
        `--rate=${rate}`,
        '--text', text,
        '--write-media', file,
      ], { maxBuffer: 8 * 1024 * 1024 });
    }

    const seconds = await probeDuration(file, ffprobePath);
    files.set(text, file);
    durations.set(text, seconds);
    index[path.basename(file)] = { text, voice, rate, seconds };

    onProgress?.(i + 1, lines.length, text, cached);
  }

  // A human-readable map of the cache, so a wrong-sounding clip can be found
  // by ear and deleted by name.
  await writeFile(path.join(cacheDir, 'index.json'), JSON.stringify(index, null, 2), 'utf8');

  return { files, durations };
}

/**
 * Build one continuous narration track that matches the timeline exactly.
 *
 * Each segment contributes precisely its own duration: its clip, then silence
 * out to the segment's end. Because the segments are strictly sequential and
 * every clip fits inside its own segment, the track is a plain concatenation
 * -- no mixing, no delay filters, and no chance of accumulated drift between
 * the voice and the picture.
 *
 * @param {{segments: object[], totalSeconds: number}} timeline
 * @param {Map<string,string>} files  spoken text -> clip path
 * @param {{outPath: string, workDir: string, ffmpegPath?: string,
 *          sampleRate?: number, onProgress?: (done:number,total:number)=>void}} opts
 * @returns {Promise<string>} outPath
 */
export async function buildTrack(timeline, files, opts) {
  const { outPath, workDir, ffmpegPath = 'ffmpeg', sampleRate = 48000, onProgress } = opts;
  await mkdir(workDir, { recursive: true });
  await mkdir(path.dirname(path.resolve(outPath)), { recursive: true });

  const parts = [];

  for (let i = 0; i < timeline.segments.length; i++) {
    const seg = timeline.segments[i];
    const part = path.join(workDir, `seg_${String(i).padStart(4, '0')}.wav`);
    const clip = seg.speak ? files.get(seg.speak) : null;

    if (clip) {
      // apad then -t: pad with silence, then cut at the segment length. Doing
      // it in this order makes the output exactly `duration` long whether the
      // clip is shorter than the segment (padded) or, after an edit to the
      // timing constants, longer (truncated).
      await execFileAsync(ffmpegPath, [
        '-hide_banner', '-loglevel', 'error', '-y',
        '-i', clip,
        '-af', 'apad',
        '-t', seg.duration.toFixed(6),
        '-ar', String(sampleRate), '-ac', '2', '-c:a', 'pcm_s16le',
        part,
      ]);
    } else {
      await execFileAsync(ffmpegPath, [
        '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', `anullsrc=r=${sampleRate}:cl=stereo`,
        '-t', seg.duration.toFixed(6),
        '-c:a', 'pcm_s16le',
        part,
      ]);
    }

    parts.push(part);
    onProgress?.(i + 1, timeline.segments.length);
  }

  const listFile = path.join(workDir, 'concat.txt');
  // The concat demuxer treats a single quote as a delimiter; our paths are
  // generated and contain none, but escape anyway rather than rely on that.
  await writeFile(
    listFile,
    parts.map((p) => `file '${p.replace(/\\/g, '/').replace(/'/g, "'\\''")}'`).join('\n'),
    'utf8',
  );

  await execFileAsync(ffmpegPath, [
    '-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'concat', '-safe', '0', '-i', listFile,
    '-ar', String(sampleRate), '-ac', '2', '-c:a', 'pcm_s16le',
    outPath,
  ]);

  return outPath;
}

/** Read back the cache index written by {@link synthesise}. */
export async function readIndex(cacheDir) {
  try {
    return JSON.parse(await readFile(path.join(cacheDir, 'index.json'), 'utf8'));
  } catch {
    return {};
  }
}
