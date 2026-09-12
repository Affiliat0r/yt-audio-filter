/**
 * Measuring what a clip actually contains.
 *
 * File duration is useless on its own here: edge-tts pads every clip with
 * leading and trailing silence, so a 1.3-second file can hold 0.2 seconds of
 * speech. Everything that reasons about how long a sound is has to trim first.
 */

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

/** Below this is treated as silence. A neural TTS floor is not digital zero. */
const SILENCE_DB = -45;

/** Shorter quiet stretches than this are part of the speech, not a gap. */
const MIN_SILENCE_SECONDS = 0.06;

/**
 * Seconds of actual speech in an audio file.
 *
 * @param {string} file
 * @param {{ffmpegPath?: string, ffprobePath?: string}} [opts]
 * @returns {Promise<number>}
 */
export async function speechSeconds(file, opts = {}) {
  const { ffmpegPath = 'ffmpeg', ffprobePath = 'ffprobe' } = opts;

  const { stderr } = await execFileAsync(ffmpegPath, [
    '-hide_banner', '-nostats',
    '-i', file,
    '-af', `silencedetect=noise=${SILENCE_DB}dB:d=${MIN_SILENCE_SECONDS}`,
    '-f', 'null', '-',
  ]);
  const { stdout } = await execFileAsync(ffprobePath, [
    '-v', 'error', '-show_entries', 'format=duration',
    '-of', 'default=nw=1:nk=1', file,
  ]);

  const total = Number(String(stdout).trim());
  if (!Number.isFinite(total)) {
    throw new Error(`audio-metrics: no duration for ${file}`);
  }

  let silent = 0;
  for (const m of String(stderr).matchAll(/silence_duration: ([\d.]+)/g)) {
    silent += Number(m[1]);
  }
  return Math.max(0, total - silent);
}

export function mean(xs) {
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

/** (longest - shortest) / mean. Zero means every value is identical. */
export function spread(xs) {
  const avg = mean(xs);
  return avg === 0 ? 0 : (Math.max(...xs) - Math.min(...xs)) / avg;
}
