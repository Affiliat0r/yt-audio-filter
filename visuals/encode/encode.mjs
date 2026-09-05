/**
 * Part C — frames to MP4.
 *
 * Takes the `frame_%06d.png` sequence written by `visuals/capture/` and hands
 * it to FFmpeg. Encoder selection deliberately mirrors
 * `src/yt_audio_filter/ffmpeg_overlay.py::_video_encoder_args` so a clip made
 * here looks like every other clip the project produces — same NVENC settings,
 * same libx264 fallback, same yuv420p pixel format.
 */

import { spawn as nodeSpawn } from "node:child_process";
import { mkdir, stat } from "node:fs/promises";
import path from "node:path";

/** Filename pattern Part B writes and FFmpeg's image2 demuxer reads. */
export const FRAME_PATTERN = "frame_%06d.png";

/** How much stderr to keep for the error message. FFmpeg puts the real cause
 * in the last handful of lines; the rest is banner and stream metadata. */
const STDERR_TAIL_LINES = 40;

/** Matches the Python probe's subprocess timeout. */
const PROBE_TIMEOUT_MS = 10_000;

/**
 * NVENC: preset p5 = balanced quality/speed (p1 fastest .. p7 slowest),
 * tune hq + rc vbr + cq 19 + b:v 0 = constant-quality VBR, ~= libx264 crf 18-19.
 */
export const NVENC_ARGS = Object.freeze([
  "-c:v", "h264_nvenc",
  "-preset", "p5",
  "-tune", "hq",
  "-rc", "vbr",
  "-cq", "19",
  "-b:v", "0",
]);

/** CPU fallback, same values as the Python overlay renderer. */
export const LIBX264_ARGS = Object.freeze([
  "-c:v", "libx264",
  "-preset", "medium",
  "-crf", "18",
]);

/**
 * @param {boolean} nvencAvailable
 * @returns {string[]} the `-c:v ...` argv fragment
 */
export function videoEncoderArgs(nvencAvailable) {
  return nvencAvailable ? [...NVENC_ARGS] : [...LIBX264_ARGS];
}

/**
 * Probe for NVENC exactly the way `ffmpeg.check_nvenc_available()` does:
 * grep `ffmpeg -encoders` for `h264_nvenc`. Any failure means "no" — a
 * missing GPU must never be fatal, we just encode on the CPU instead.
 *
 * @param {{spawnFn?:Function, ffmpegPath?:string}} [deps]
 * @returns {Promise<boolean>}
 */
export async function checkNvencAvailable({ spawnFn = nodeSpawn, ffmpegPath = "ffmpeg" } = {}) {
  try {
    const stdout = await new Promise((resolve, reject) => {
      const child = spawnFn(ffmpegPath, ["-hide_banner", "-encoders"], {
        stdio: ["ignore", "pipe", "ignore"],
      });
      let out = "";
      const timer = setTimeout(() => {
        child.kill?.();
        resolve(out);
      }, PROBE_TIMEOUT_MS);
      child.stdout?.on("data", (chunk) => {
        out += String(chunk);
      });
      child.once("error", reject);
      child.once("close", () => {
        clearTimeout(timer);
        resolve(out);
      });
    });
    return stdout.includes("h264_nvenc");
  } catch {
    return false;
  }
}

/**
 * Build the full ffmpeg argv.
 *
 * `-framerate` and `-start_number` are image2 *demuxer* options, so they have
 * to sit before `-i` — after it they are silently ignored and the sequence is
 * muxed at 25 fps, which is how you get a 10-second render that reports 12s.
 *
 * @param {{framesDir:string, outPath:string, fps:number, nvenc:boolean}} opts
 * @returns {string[]}
 */
export function buildEncodeArgs({ framesDir, outPath, fps, nvenc }) {
  return [
    "-hide_banner",
    // The frames are a scratch dir we own and the CLI re-renders constantly;
    // prompting to overwrite would just hang a non-interactive run.
    "-y",
    "-framerate", String(fps),
    "-start_number", "0",
    "-i", path.join(framesDir, FRAME_PATTERN),
    ...videoEncoderArgs(nvenc),
    // Chrome hands us RGBA PNGs; without this the output is yuv444p, which
    // Windows Media Player, QuickTime and most phones refuse to play.
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
    outPath,
  ];
}

/**
 * Run ffmpeg, streaming stderr to `onProgress` a line at a time.
 *
 * @returns {Promise<{code:number, stderrTail:string}>}
 */
function runFfmpeg(ffmpegPath, args, { onProgress, spawnFn }) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawnFn(ffmpegPath, args, { stdio: ["ignore", "ignore", "pipe"] });
    } catch (err) {
      reject(err);
      return;
    }

    const tail = [];
    let pending = "";

    const pushLine = (raw) => {
      const line = raw.trim();
      if (!line) return;
      tail.push(line);
      if (tail.length > STDERR_TAIL_LINES) tail.shift();
      onProgress?.(line);
    };

    child.stderr?.on("data", (chunk) => {
      pending += String(chunk);
      // FFmpeg ends its periodic `frame= ... fps= ...` status with a carriage
      // return, not a newline. Splitting on \n alone buffers the whole encode
      // and the caller sees no progress until the process exits.
      const parts = pending.split(/\r\n|\n|\r/);
      pending = parts.pop() ?? "";
      for (const part of parts) pushLine(part);
    });

    // A spawn error (ffmpeg not on PATH) is not an encoder problem, so it
    // rejects straight past the NVENC retry instead of failing twice.
    child.once("error", reject);
    child.once("close", (code) => {
      if (pending) pushLine(pending);
      resolve({ code: code ?? -1, stderrTail: tail.join("\n") });
    });
  });
}

/**
 * FFmpeg can exit 0 having written nothing (bad glob, full disk). Catch that
 * here rather than letting the CLI report success on a zero-byte file.
 *
 * @returns {Promise<number>} file size in bytes
 */
async function verifyOutput(outPath) {
  let info;
  try {
    info = await stat(outPath);
  } catch {
    throw new Error(`ffmpeg reported success but no file was created at ${outPath}`);
  }
  if (!info.isFile() || info.size === 0) {
    throw new Error(`ffmpeg produced an empty file at ${outPath}`);
  }
  return info.size;
}

/**
 * Encode `framesDir/frame_%06d.png` into an MP4 at `outPath`.
 *
 * `spawnFn`, `probeNvenc`, `onWarn` and `ffmpegPath` are injection seams for
 * the tests; production callers pass only the four documented options.
 *
 * @param {{
 *   framesDir:string, outPath:string, fps:number,
 *   onProgress?:(line:string)=>void,
 *   onWarn?:(message:string)=>void,
 *   spawnFn?:Function, probeNvenc?:Function, ffmpegPath?:string
 * }} opts
 * @returns {Promise<string>} outPath
 */
export default async function encode({
  framesDir,
  outPath,
  fps,
  onProgress,
  onWarn = (message) => console.warn(message),
  spawnFn = nodeSpawn,
  probeNvenc = checkNvencAvailable,
  ffmpegPath = "ffmpeg",
}) {
  if (!framesDir) throw new Error("encode: framesDir is required");
  if (!outPath) throw new Error("encode: outPath is required");
  if (!Number.isFinite(fps) || fps <= 0) {
    throw new Error(`encode: fps must be a positive number, got ${fps}`);
  }

  await mkdir(path.dirname(path.resolve(outPath)), { recursive: true });

  const nvenc = await probeNvenc({ spawnFn, ffmpegPath });
  // NVENC on laptop GPUs intermittently refuses a session when something else
  // (a game, a browser, a previous render that has not released the encoder)
  // holds one. Falling back to the CPU costs minutes; failing loses the frames.
  const attempts = nvenc ? ["h264_nvenc", "libx264"] : ["libx264"];

  for (let i = 0; i < attempts.length; i++) {
    const encoder = attempts[i];
    const args = buildEncodeArgs({
      framesDir,
      outPath,
      fps,
      nvenc: encoder === "h264_nvenc",
    });
    onProgress?.(`ffmpeg ${args.join(" ")}`);

    const { code, stderrTail } = await runFfmpeg(ffmpegPath, args, { onProgress, spawnFn });

    let failure = null;
    if (code !== 0) {
      failure = `ffmpeg exited with code ${code}`;
    } else {
      try {
        await verifyOutput(outPath);
      } catch (err) {
        failure = err.message;
      }
    }
    if (!failure) return outPath;

    const isLastAttempt = i === attempts.length - 1;
    if (isLastAttempt) {
      throw new Error(
        `Encode failed (${encoder}): ${failure}\n` +
          `Command: ffmpeg ${args.join(" ")}\n` +
          `--- ffmpeg stderr (last ${STDERR_TAIL_LINES} lines) ---\n${stderrTail}`
      );
    }
    onWarn(
      `NVENC encode failed (${failure}); retrying once with libx264 (CPU). ` +
        `Last ffmpeg output: ${stderrTail.split("\n").slice(-3).join(" | ")}`
    );
  }

  // Unreachable: the loop either returns or throws on the last attempt.
  throw new Error("Encode failed: no encoder attempts were made");
}
