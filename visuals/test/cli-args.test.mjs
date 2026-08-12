import assert from "node:assert/strict";
import path from "node:path";
import test, { describe } from "node:test";

import { DEFAULTS, VISUALS_DIR, parseArgs } from "../cli.mjs";

describe("parseArgs — defaults", () => {
  test("no arguments gives the documented defaults", () => {
    const opts = parseArgs([]);
    assert.equal(opts.seconds, 10);
    assert.equal(opts.fps, 30);
    assert.equal(opts.width, 1280);
    assert.equal(opts.height, 720);
    assert.equal(opts.seed, 42);
    assert.equal(opts.keepFrames, false);
    assert.equal(opts.help, false);
    assert.equal(opts.chrome, "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe");
  });

  test("default --out is visuals/out/test.mp4, absolute and cwd-independent", () => {
    const opts = parseArgs([]);
    assert.equal(path.isAbsolute(opts.out), true);
    assert.equal(opts.out, path.join(VISUALS_DIR, "out", "test.mp4"));
  });

  test("undefined argv behaves like no arguments", () => {
    assert.deepEqual(parseArgs(), parseArgs([]));
  });

  test("DEFAULTS is frozen so a caller cannot mutate it out from under us", () => {
    assert.equal(Object.isFrozen(DEFAULTS), true);
  });
});

describe("parseArgs — overrides", () => {
  test("every flag can be set with a separate value token", () => {
    const opts = parseArgs([
      "--seconds", "5",
      "--fps", "60",
      "--width", "1920",
      "--height", "1080",
      "--seed", "7",
      "--out", "custom.mp4",
      "--chrome", "D:\\chrome.exe",
      "--keep-frames",
    ]);
    assert.equal(opts.seconds, 5);
    assert.equal(opts.fps, 60);
    assert.equal(opts.width, 1920);
    assert.equal(opts.height, 1080);
    assert.equal(opts.seed, 7);
    assert.equal(opts.out, path.resolve("custom.mp4"));
    assert.equal(opts.chrome, "D:\\chrome.exe");
    assert.equal(opts.keepFrames, true);
  });

  test("--flag=value form is equivalent", () => {
    const opts = parseArgs(["--seconds=2.5", "--fps=24", "--out=x.mp4"]);
    assert.equal(opts.seconds, 2.5);
    assert.equal(opts.fps, 24);
    assert.equal(opts.out, path.resolve("x.mp4"));
  });

  test("fractional seconds are allowed", () => {
    assert.equal(parseArgs(["--seconds", "0.5"]).seconds, 0.5);
  });

  test("negative and zero seeds are valid PRNG seeds", () => {
    assert.equal(parseArgs(["--seed", "0"]).seed, 0);
    assert.equal(parseArgs(["--seed", "-3"]).seed, -3);
  });

  test("-h and --help set help", () => {
    assert.equal(parseArgs(["-h"]).help, true);
    assert.equal(parseArgs(["--help"]).help, true);
  });
});

describe("parseArgs — rejects invalid values", () => {
  const cases = [
    [["--seconds", "0"], /--seconds must be greater than 0/],
    [["--seconds", "-5"], /--seconds must be greater than 0/],
    [["--seconds", "abc"], /--seconds must be a number/],
    [["--fps", "0"], /--fps must be greater than 0/],
    [["--fps", "-30"], /--fps must be greater than 0/],
    [["--width", "12.5"], /--width must be a whole number of pixels/],
    [["--height", "0"], /--height must be greater than 0/],
    [["--height", "719.5"], /--height must be a whole number of pixels/],
    [["--seed", "1.5"], /--seed must be an integer/],
    [["--seconds", "Infinity"], /--seconds must be a number/],
    [["--fps", ""], /--fps must be a number/],
  ];

  for (const [argv, pattern] of cases) {
    test(`rejects ${argv.join(" ")}`, () => {
      assert.throws(() => parseArgs(argv), pattern);
    });
  }

  test("rejects a flag with no value", () => {
    assert.throws(() => parseArgs(["--seconds"]), /--seconds requires a value/);
    assert.throws(() => parseArgs(["--out"]), /--out requires a value/);
  });

  test("rejects unknown flags rather than ignoring them", () => {
    assert.throws(() => parseArgs(["--frames", "300"]), /Unknown flag: --frames/);
  });

  test("rejects stray positional arguments", () => {
    assert.throws(() => parseArgs(["movie.mp4"]), /Unexpected argument: movie\.mp4/);
  });
});
