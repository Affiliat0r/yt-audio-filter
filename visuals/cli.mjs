#!/usr/bin/env node
/**
 * Part C — the CLI that wires capture (Part B) to encode (Part C).
 *
 *   node visuals/cli.mjs --seconds 10 --fps 30 --width 1280 --height 720 \
 *                        --seed 42 --out visuals/out/test.mp4
 *
 * Frames land in a temp directory and are deleted afterwards; `--keep-frames`
 * keeps them so a bad render can be inspected PNG by PNG.
 */

import { execFile } from "node:child_process";
import { access, mkdtemp, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath, pathToFileURL } from "node:url";

const execFileAsync = promisify(execFile);

/** Directory holding this file, i.e. `<repo>/visuals`. */
export const VISUALS_DIR = path.dirname(fileURLToPath(import.meta.url));

export const DEFAULTS = Object.freeze({
  seconds: 10,
  fps: 30,
  width: 1280,
  height: 720,
  seed: 42,
  // Resolved against this file, not the cwd, so `node visuals/cli.mjs` and
  // `npm --prefix visuals run movie` write to the same place.
  out: path.join(VISUALS_DIR, "out", "test.mp4"),
  keepFrames: false,
  chrome: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
});

const USAGE = `Render a procedural three.js movie to MP4.

Usage: node visuals/cli.mjs [options]

  --seconds N      movie length in seconds        (default ${DEFAULTS.seconds})
  --fps N          frames per second              (default ${DEFAULTS.fps})
  --width N        frame width in pixels          (default ${DEFAULTS.width})
  --height N       frame height in pixels         (default ${DEFAULTS.height})
  --seed N         PRNG seed; same seed = same movie (default ${DEFAULTS.seed})
  --out PATH       output MP4                     (default visuals/out/test.mp4)
  --keep-frames    keep the PNG scratch directory for debugging
  --chrome PATH    Chrome executable
                   (default ${DEFAULTS.chrome})
  -h, --help       show this message
`;

/**
 * Number of frames in the movie.
 *
 * 10s at 30fps is 300 frames. Rounding (rather than floor or ceil) keeps
 * fractional lengths honest — 2.5s at 30fps is exactly 75 frames — and stops
 * float noise from silently dropping the last frame.
 *
 * @param {number} seconds
 * @param {number} fps
 * @returns {number}
 */
export function frameCount(seconds, fps) {
  return Math.max(1, Math.round(seconds * fps));
}

/**
 * The scene time of frame `n`. Frame 0 is t=0, so the movie starts on the
 * scene's initial state rather than one frame into it.
 *
 * @param {number} n
 * @param {number} fps
 * @returns {number} seconds
 */
export function frameTime(n, fps) {
  return n / fps;
}

function parseNumber(flag, raw) {
  if (raw === undefined) throw new Error(`${flag} requires a value`);
  const value = Number(raw);
  if (raw.trim() === "" || !Number.isFinite(value)) {
    throw new Error(`${flag} must be a number, got "${raw}"`);
  }
  return value;
}

function positiveNumber(flag, raw) {
  const value = parseNumber(flag, raw);
  if (value <= 0) throw new Error(`${flag} must be greater than 0, got ${value}`);
  return value;
}

function positiveInteger(flag, raw) {
  const value = positiveNumber(flag, raw);
  // Fractional pixel dimensions produce a canvas Chrome rounds on its own,
  // and the mismatch shows up as a scaled/offset frame rather than an error.
  if (!Number.isInteger(value)) {
    throw new Error(`${flag} must be a whole number of pixels, got ${value}`);
  }
  return value;
}

function integer(flag, raw) {
  const value = parseNumber(flag, raw);
  if (!Number.isInteger(value)) {
    throw new Error(`${flag} must be an integer, got ${value}`);
  }
  return value;
}

function requireValue(flag, raw) {
  if (raw === undefined || raw === "") throw new Error(`${flag} requires a value`);
  return raw;
}

/**
 * Parse `process.argv.slice(2)`. Accepts both `--flag value` and `--flag=value`.
 * Throws on unknown flags, missing values and out-of-range numbers — a typo
 * here would otherwise silently render the default movie.
 *
 * @param {string[]} argv
 * @returns {{seconds:number, fps:number, width:number, height:number,
 *            seed:number, out:string, keepFrames:boolean, chrome:string,
 *            help:boolean}}
 */
export function parseArgs(argv = []) {
  const opts = { ...DEFAULTS, help: false };

  for (let i = 0; i < argv.length; i++) {
    const token = argv[i];
    const eq = token.indexOf("=");
    const flag = eq === -1 ? token : token.slice(0, eq);
    const inline = eq === -1 ? undefined : token.slice(eq + 1);
    // Only consume the next token when the flag needs a value and none was
    // glued on with `=`.
    const next = () => (inline !== undefined ? inline : argv[++i]);

    switch (flag) {
      case "--seconds":
        opts.seconds = positiveNumber(flag, next());
        break;
      case "--fps":
        opts.fps = positiveNumber(flag, next());
        break;
      case "--width":
        opts.width = positiveInteger(flag, next());
        break;
      case "--height":
        opts.height = positiveInteger(flag, next());
        break;
      case "--seed":
        opts.seed = integer(flag, next());
        break;
      case "--out":
        opts.out = path.resolve(requireValue(flag, next()));
        break;
      case "--chrome":
        opts.chrome = requireValue(flag, next());
        break;
      case "--keep-frames":
        opts.keepFrames = true;
        break;
      case "-h":
      case "--help":
        opts.help = true;
        break;
      default:
        throw new Error(
          flag.startsWith("-")
            ? `Unknown flag: ${flag}`
            : `Unexpected argument: ${token}`
        );
    }
  }

  return opts;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "?";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m${String(s).padStart(2, "0")}s` : `${s}s`;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "?";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(2)} MB` : `${(bytes / 1024).toFixed(0)} KB`;
}

/** Overwrite one status line on a TTY; on a redirected stream, plain lines. */
function makeStatusWriter() {
  const tty = Boolean(process.stdout.isTTY);
  let lastWidth = 0;
  return {
    update(text) {
      if (tty) {
        const padded = text.padEnd(lastWidth, " ");
        lastWidth = text.length;
        process.stdout.write(`\r${padded}`);
      } else {
        process.stdout.write(`${text}\n`);
      }
    },
    done() {
      if (tty && lastWidth > 0) process.stdout.write("\n");
      lastWidth = 0;
    },
  };
}

async function probeOutput(outPath) {
  const { stdout } = await execFileAsync("ffprobe", [
    "-v", "error",
    "-select_streams", "v:0",
    "-show_entries",
    "format=duration,size:stream=codec_name,width,height,nb_frames,avg_frame_rate",
    "-of", "json",
    outPath,
  ]);
  const data = JSON.parse(stdout);
  const stream = data.streams?.[0] ?? {};
  const format = data.format ?? {};
  return {
    duration: Number(format.duration),
    size: Number(format.size),
    codec: stream.codec_name ?? "?",
    width: stream.width,
    height: stream.height,
    frames: Number(stream.nb_frames),
  };
}

async function main(argv) {
  const opts = parseArgs(argv);
  if (opts.help) {
    process.stdout.write(USAGE);
    return;
  }

  const total = frameCount(opts.seconds, opts.fps);
  const sceneEntry = path.join(VISUALS_DIR, "scene", "index.html");
  try {
    await access(sceneEntry);
  } catch {
    throw new Error(`Scene not found at ${sceneEntry} (Part A must be present)`);
  }

  // Imported lazily so `--help`, argument errors and the unit tests never
  // depend on the sibling parts being on disk, and so a missing part reports
  // itself by name instead of blowing up at module load.
  let capture;
  let encode;
  try {
    capture = (await import("./capture/capture.mjs")).default;
  } catch (err) {
    throw new Error(`Could not load visuals/capture/capture.mjs: ${err.message}`);
  }
  try {
    encode = (await import("./encode/encode.mjs")).default;
  } catch (err) {
    throw new Error(`Could not load visuals/encode/encode.mjs: ${err.message}`);
  }

  const framesDir = await mkdtemp(path.join(os.tmpdir(), "visuals-frames-"));
  const status = makeStatusWriter();

  process.stdout.write(
    `Movie: ${opts.seconds}s @ ${opts.fps}fps = ${total} frames, ` +
      `${opts.width}x${opts.height}, seed ${opts.seed}\n`
  );
  process.stdout.write(`Frames: ${framesDir}\n`);

  try {
    const captureStarted = Date.now();
    let lastReport = 0;
    const result = await capture({
      sceneUrl: pathToFileURL(sceneEntry).href,
      outDir: framesDir,
      width: opts.width,
      height: opts.height,
      fps: opts.fps,
      durationSeconds: opts.seconds,
      seed: opts.seed,
      chromePath: opts.chrome,
      onProgress: (done, count) => {
        // On a redirected stream every frame would be its own log line, so
        // throttle to roughly 5% steps there.
        if (!process.stdout.isTTY && done !== count && done - lastReport < Math.ceil(count / 20)) {
          return;
        }
        lastReport = done;
        const elapsed = (Date.now() - captureStarted) / 1000;
        const rate = elapsed > 0 ? done / elapsed : 0;
        const eta = rate > 0 ? (count - done) / rate : Infinity;
        status.update(
          `  capture ${done}/${count} frames  ${rate.toFixed(1)} fps  eta ${formatDuration(eta)}`
        );
      },
    });
    status.done();

    const captured = result?.frameCount ?? 0;
    if (captured === 0) throw new Error("Capture produced no frames");
    if (captured !== total) {
      process.stdout.write(
        `  warning: captured ${captured} frames, expected ${total}; ` +
          `output duration will be ${(captured / opts.fps).toFixed(2)}s\n`
      );
    }
    process.stdout.write(
      `  capture done in ${formatDuration((Date.now() - captureStarted) / 1000)}\n`
    );

    const encodeStarted = Date.now();
    await encode({
      framesDir: result?.outDir ?? framesDir,
      outPath: opts.out,
      fps: opts.fps,
      onProgress: (line) => {
        // Everything else ffmpeg prints (banner, stream mapping) is only
        // interesting when the encode fails, and encode() keeps that tail.
        if (line.startsWith("frame=")) status.update(`  encode ${line}`);
      },
    });
    status.done();
    process.stdout.write(
      `  encode done in ${formatDuration((Date.now() - encodeStarted) / 1000)}\n`
    );

    const info = await probeOutput(opts.out);
    const size = Number.isFinite(info.size) ? info.size : (await stat(opts.out)).size;
    process.stdout.write(
      `\n${opts.out}\n` +
        `  duration ${info.duration.toFixed(3)}s  ` +
        `${info.width}x${info.height}  ${info.codec}  ` +
        `${formatBytes(size)}` +
        `${Number.isFinite(info.frames) ? `  ${info.frames} frames` : ""}\n`
    );
  } finally {
    if (opts.keepFrames) {
      process.stdout.write(`Frames kept at ${framesDir}\n`);
    } else {
      await rm(framesDir, { recursive: true, force: true });
    }
  }
}

const invokedDirectly =
  process.argv[1] !== undefined &&
  pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url;

if (invokedDirectly) {
  main(process.argv.slice(2)).catch((err) => {
    process.stderr.write(`\nError: ${err.message}\n`);
    process.exitCode = 1;
  });
}
