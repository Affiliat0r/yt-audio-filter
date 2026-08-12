import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import test, { after, before, describe } from "node:test";

import encode, {
  FRAME_PATTERN,
  LIBX264_ARGS,
  NVENC_ARGS,
  buildEncodeArgs,
  videoEncoderArgs,
} from "../encode/encode.mjs";

/**
 * Stand-in for child_process.spawn. Each queued response drives one call;
 * the last response repeats if encode() spawns more times than expected.
 *
 * `onRun` runs after stderr drains and before 'close', which is where a real
 * ffmpeg would have finished writing its output file.
 *
 * @param {Array<{code?:number, stderr?:string, onRun?:()=>any}>} responses
 */
function makeFakeSpawn(responses) {
  const calls = [];
  const spawnFn = (command, args, options) => {
    const response = responses[Math.min(calls.length, responses.length - 1)] ?? {};
    calls.push({ command, args, options });

    const { code = 0, stderr = "", onRun } = response;
    const child = new EventEmitter();
    child.stdout = Readable.from([""]);
    child.stderr = Readable.from([stderr]);
    child.kill = () => {};
    child.stderr.once("end", async () => {
      if (onRun) await onRun();
      child.emit("close", code);
    });
    return child;
  };
  return { spawnFn, calls };
}

const encoderOf = (args) => args[args.indexOf("-c:v") + 1];

let workDir;
before(async () => {
  workDir = await mkdtemp(path.join(os.tmpdir(), "visuals-encode-test-"));
});
after(async () => {
  await rm(workDir, { recursive: true, force: true });
});

let counter = 0;
/** A fresh output path per test so a leftover file cannot fake a pass. */
const nextOut = () => path.join(workDir, `out-${counter++}.mp4`);

describe("encoder argument values", () => {
  test("NVENC args mirror ffmpeg_overlay._video_encoder_args", () => {
    assert.deepEqual(videoEncoderArgs(true), [
      "-c:v", "h264_nvenc",
      "-preset", "p5",
      "-tune", "hq",
      "-rc", "vbr",
      "-cq", "19",
      "-b:v", "0",
    ]);
  });

  test("libx264 fallback args mirror ffmpeg_overlay._video_encoder_args", () => {
    assert.deepEqual(videoEncoderArgs(false), ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]);
  });

  test("the exported constants are not mutable through videoEncoderArgs", () => {
    videoEncoderArgs(true).push("--tampered");
    assert.equal(NVENC_ARGS.length, 12);
    assert.equal(LIBX264_ARGS.length, 6);
  });
});

describe("buildEncodeArgs", () => {
  const args = buildEncodeArgs({
    framesDir: path.join("C:", "frames"),
    outPath: path.join("C:", "out", "movie.mp4"),
    fps: 30,
    nvenc: false,
  });

  test("reads the zero-padded frame sequence starting at frame 0", () => {
    const input = args[args.indexOf("-i") + 1];
    assert.equal(path.basename(input), FRAME_PATTERN);
    assert.equal(args[args.indexOf("-start_number") + 1], "0");
  });

  test("demuxer options precede -i, or ffmpeg silently muxes at 25fps", () => {
    const i = args.indexOf("-i");
    assert.ok(args.indexOf("-framerate") < i);
    assert.ok(args.indexOf("-start_number") < i);
    assert.equal(args[args.indexOf("-framerate") + 1], "30");
  });

  test("always forces yuv420p", () => {
    assert.equal(args[args.indexOf("-pix_fmt") + 1], "yuv420p");
  });

  test("the output path is the final argument", () => {
    assert.equal(args.at(-1), path.join("C:", "out", "movie.mp4"));
  });
});

describe("encode — encoder selection", () => {
  test("uses NVENC when the probe reports it available", async () => {
    const outPath = nextOut();
    const { spawnFn, calls } = makeFakeSpawn([
      { code: 0, stderr: "frame=  300 fps=120", onRun: () => writeFile(outPath, "mp4") },
    ]);

    const returned = await encode({
      framesDir: workDir,
      outPath,
      fps: 30,
      spawnFn,
      probeNvenc: async () => true,
    });

    assert.equal(returned, outPath);
    assert.equal(calls.length, 1);
    assert.equal(encoderOf(calls[0].args), "h264_nvenc");
  });

  test("uses libx264 when the probe reports NVENC unavailable", async () => {
    const outPath = nextOut();
    const { spawnFn, calls } = makeFakeSpawn([
      { code: 0, onRun: () => writeFile(outPath, "mp4") },
    ]);

    await encode({
      framesDir: workDir,
      outPath,
      fps: 30,
      spawnFn,
      probeNvenc: async () => false,
    });

    assert.equal(calls.length, 1);
    assert.equal(encoderOf(calls[0].args), "libx264");
  });

  test("retries once with libx264 when NVENC fails, and warns", async () => {
    const outPath = nextOut();
    const { spawnFn, calls } = makeFakeSpawn([
      { code: 1, stderr: "[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: out of memory" },
      { code: 0, onRun: () => writeFile(outPath, "mp4") },
    ]);
    const warnings = [];

    const returned = await encode({
      framesDir: workDir,
      outPath,
      fps: 30,
      spawnFn,
      probeNvenc: async () => true,
      onWarn: (message) => warnings.push(message),
    });

    assert.equal(returned, outPath);
    assert.equal(calls.length, 2);
    assert.equal(encoderOf(calls[0].args), "h264_nvenc");
    assert.equal(encoderOf(calls[1].args), "libx264");
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /libx264/);
    assert.match(warnings[0], /OpenEncodeSessionEx/);
  });

  test("does not retry when libx264 was the only attempt", async () => {
    const { spawnFn, calls } = makeFakeSpawn([{ code: 1, stderr: "boom" }]);

    await assert.rejects(
      encode({
        framesDir: workDir,
        outPath: nextOut(),
        fps: 30,
        spawnFn,
        probeNvenc: async () => false,
      })
    );
    assert.equal(calls.length, 1);
  });
});

describe("encode — failure reporting", () => {
  test("throws on a non-zero exit, surfacing the ffmpeg stderr tail", async () => {
    const stderr = [
      "Input #0, image2, from 'frame_%06d.png':",
      "[image2 @ 0x55] Could find no file with path 'frame_%06d.png'",
      "Error opening input files: No such file or directory",
    ].join("\n");
    const { spawnFn } = makeFakeSpawn([{ code: 1, stderr }]);

    await assert.rejects(
      encode({
        framesDir: workDir,
        outPath: nextOut(),
        fps: 30,
        spawnFn,
        probeNvenc: async () => false,
      }),
      (err) => {
        assert.match(err.message, /exited with code 1/);
        assert.match(err.message, /Could find no file with path/);
        assert.match(err.message, /Error opening input files/);
        return true;
      }
    );
  });

  test("throws when ffmpeg exits 0 but writes nothing", async () => {
    const { spawnFn } = makeFakeSpawn([{ code: 0, stderr: "" }]);

    await assert.rejects(
      encode({
        framesDir: workDir,
        outPath: nextOut(),
        fps: 30,
        spawnFn,
        probeNvenc: async () => false,
      }),
      /no file was created/
    );
  });

  test("throws when ffmpeg exits 0 but the file is empty", async () => {
    const outPath = nextOut();
    const { spawnFn } = makeFakeSpawn([{ code: 0, onRun: () => writeFile(outPath, "") }]);

    await assert.rejects(
      encode({ framesDir: workDir, outPath, fps: 30, spawnFn, probeNvenc: async () => false }),
      /empty file/
    );
  });

  test("rejects an invalid fps before spawning anything", async () => {
    const { spawnFn, calls } = makeFakeSpawn([{ code: 0 }]);
    await assert.rejects(
      encode({ framesDir: workDir, outPath: nextOut(), fps: 0, spawnFn, probeNvenc: async () => true }),
      /fps must be a positive number/
    );
    assert.equal(calls.length, 0);
  });
});

describe("encode — progress streaming", () => {
  test("stderr reaches onProgress line by line, including \\r-terminated status", async () => {
    const outPath = nextOut();
    const { spawnFn } = makeFakeSpawn([
      {
        code: 0,
        // ffmpeg separates periodic status updates with \r, not \n.
        stderr: "Input #0, image2\rframe=  100 fps=99\rframe=  200 fps=98\nmuxing overhead",
        onRun: () => writeFile(outPath, "mp4"),
      },
    ]);
    const lines = [];

    await encode({
      framesDir: workDir,
      outPath,
      fps: 30,
      spawnFn,
      probeNvenc: async () => false,
      onProgress: (line) => lines.push(line),
    });

    const progress = lines.filter((line) => line.startsWith("frame="));
    assert.deepEqual(progress, ["frame=  100 fps=99", "frame=  200 fps=98"]);
    assert.equal(lines.at(-1), "muxing overhead");
  });
});
