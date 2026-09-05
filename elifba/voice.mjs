/**
 * Narration.
 *
 * edge-tts gives both voices a lesson needs, free and with no API key: a
 * Turkish one for the letter names and the hand-over, and an Arabic one for
 * the vowelled sounds themselves. Which voice speaks a line is decided in
 * timeline.mjs and travels with the line as its role, because a Turkish voice
 * cannot make the sounds the Turkish letter names stand for -- it says one
 * "ha" for three different letters.
 *
 * Clips are cached by (voice, rate, text) so a re-render costs no network and
 * an edited lesson only synthesises what changed.
 */

import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { access, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';

import { spokenId } from './timeline.mjs';

const execFileAsync = promisify(execFile);

/*
 * Slowed a little for a three-year-old, but only a little. The first pass ran
 * at -20% and the drag was most of what made it sound like a machine; the
 * clarity that buys is not worth the voice it costs. Override with --rate.
 */
export const DEFAULT_RATE = '-8%';

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
 * @param {{text: string, role: string}[]} lines
 * @param {{cacheDir: string, voices: Record<string,string>, rate?: string, python?: string,
 *          ffprobePath?: string, onProgress?: (done:number,total:number,text:string,cached:boolean)=>void}} opts
 * @returns {Promise<{files: Map<string,string>, durations: Map<string,number>}>}
 *   both keyed by spokenId(line)
 */
export async function synthesise(lines, opts) {
  const {
    cacheDir,
    voices,
    rate = DEFAULT_RATE,
    python = 'python',
    ffprobePath = 'ffprobe',
    onProgress,
  } = opts;

  if (!voices || typeof voices !== 'object') {
    throw new Error('voice: synthesise needs a `voices` map of role -> edge-tts voice');
  }
  await mkdir(cacheDir, { recursive: true });

  const files = new Map();
  const durations = new Map();
  const index = {};

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const voice = voices[line.role];
    if (!voice) {
      throw new Error(`voice: no voice configured for role ${JSON.stringify(line.role)}`);
    }
    const file = path.join(cacheDir, `${clipKey(line.text, voice, rate)}.mp3`);
    const cached = await exists(file);

    if (!cached) {
      // `--rate=-8%` must be one argv token: edge-tts parses a leading "-"
      // in a separate value as the start of another flag.
      await execFileAsync(python, [
        '-m', 'edge_tts',
        '--voice', voice,
        `--rate=${rate}`,
        '--text', line.text,
        '--write-media', file,
      ], { maxBuffer: 8 * 1024 * 1024 });
    }

    const seconds = await probeDuration(file, ffprobePath);
    // Keyed by role+text, not text: the same string handed to a different
    // voice is a different clip.
    files.set(spokenId(line), file);
    durations.set(spokenId(line), seconds);
    index[path.basename(file)] = { text: line.text, role: line.role, voice, rate, seconds };

    onProgress?.(i + 1, lines.length, line.text, cached, voice);
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
 * @param {Map<string,string>} files  spokenId(line) -> clip path
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
    const clip = seg.speak ? files.get(spokenId(seg.speak)) : null;

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

/**
 * Bring the finished track to an EBU R128 target, in two passes.
 *
 * Two passes rather than one because the single-pass filter normalises
 * *dynamically*: it would ride the gain up through the long "now you say it"
 * silences, which on this track are true digital silence and should stay that
 * way. Measuring first lets the second pass apply one flat gain
 * (`linear=true`), which cannot touch the quiet.
 *
 * -16 LUFS rather than YouTube's -14: it leaves a little headroom, and the
 * project's video renderer already targets the same figure.
 *
 * @param {string} inPath
 * @param {string} outPath
 * @param {{ffmpegPath?:string, targetI?:number, targetTP?:number, targetLRA?:number,
 *          sampleRate?:number, onWarn?:(m:string)=>void}} [opts]
 * @returns {Promise<{outPath:string, measured:object}>}
 */
export async function normaliseTrack(inPath, outPath, opts = {}) {
  const {
    ffmpegPath = 'ffmpeg',
    targetI = -16,
    targetTP = -1.5,
    targetLRA = 11,
    sampleRate = 48000,
    onWarn = (message) => console.warn(message),
  } = opts;

  const spec = `I=${targetI}:TP=${targetTP}:LRA=${targetLRA}`;

  // Pass 1: measure. ffmpeg prints the JSON block on stderr and exits 0.
  const { stderr } = await execFileAsync(ffmpegPath, [
    '-hide_banner', '-i', inPath,
    '-af', `loudnorm=${spec}:print_format=json`,
    '-f', 'null', '-',
  ], { maxBuffer: 8 * 1024 * 1024 });

  const open = stderr.lastIndexOf('{');
  const close = stderr.lastIndexOf('}');
  let measured = null;
  if (open !== -1 && close > open) {
    try {
      measured = JSON.parse(stderr.slice(open, close + 1));
    } catch {
      measured = null;
    }
  }
  if (!measured || measured.input_i === undefined) {
    // Normalisation is a polish step, not a correctness one: a track at the
    // wrong level is still a usable lesson, so warn and pass it through.
    onWarn('voice: could not measure loudness; leaving the track unnormalised');
    return { outPath: inPath, measured: null };
  }

  // Pass 2: apply it as one flat gain.
  await execFileAsync(ffmpegPath, [
    '-hide_banner', '-loglevel', 'error', '-y',
    '-i', inPath,
    '-af',
    `loudnorm=${spec}:measured_I=${measured.input_i}:measured_TP=${measured.input_tp}` +
      `:measured_LRA=${measured.input_lra}:measured_thresh=${measured.input_thresh}` +
      `:offset=${measured.target_offset}:linear=true`,
    '-ar', String(sampleRate), '-ac', '2', '-c:a', 'pcm_s16le',
    outPath,
  ], { maxBuffer: 8 * 1024 * 1024 });

  return { outPath, measured };
}

/** Read back the cache index written by {@link synthesise}. */
export async function readIndex(cacheDir) {
  try {
    return JSON.parse(await readFile(path.join(cacheDir, 'index.json'), 'utf8'));
  } catch {
    return {};
  }
}
