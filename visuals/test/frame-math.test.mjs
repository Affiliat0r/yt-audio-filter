import assert from "node:assert/strict";
import test, { describe } from "node:test";

import { frameCount, frameTime, parseArgs } from "../cli.mjs";

describe("frame count", () => {
  test("10s at 30fps is 300 frames", () => {
    assert.equal(frameCount(10, 30), 300);
  });

  test("matches the CLI defaults", () => {
    const { seconds, fps } = parseArgs([]);
    assert.equal(frameCount(seconds, fps), 300);
  });

  test("2.5s at 30fps is 75 frames", () => {
    assert.equal(frameCount(2.5, 30), 75);
  });

  test("rounds to nearest, half up", () => {
    // 1.01 * 30 = 30.3 -> 30; 0.5 * 25 = 12.5 -> 13.
    assert.equal(frameCount(1.01, 30), 30);
    assert.equal(frameCount(0.5, 25), 13);
    assert.equal(frameCount(1 / 3, 30), 10);
  });

  test("float noise does not drop the last frame", () => {
    // 0.1 + 0.2 !== 0.3 in binary floating point; flooring here would give 8.
    assert.equal(frameCount(0.1 + 0.2, 30), 9);
  });

  test("never returns zero frames for a very short movie", () => {
    assert.equal(frameCount(0.001, 30), 1);
  });
});

describe("frame timing", () => {
  test("frame n is t = n / fps", () => {
    assert.equal(frameTime(30, 30), 1);
    assert.equal(frameTime(45, 30), 1.5);
    assert.equal(frameTime(150, 30), 5);
    assert.equal(frameTime(12, 24), 0.5);
  });

  test("the first frame is t = 0", () => {
    assert.equal(frameTime(0, 30), 0);
    assert.equal(frameTime(0, 60), 0);
  });

  test("the last frame sits one interval before the end, not on it", () => {
    const total = frameCount(10, 30);
    const last = frameTime(total - 1, 30);
    assert.ok(last < 10, `last frame t=${last} should be inside the movie`);
    assert.ok(Math.abs(last - (10 - 1 / 30)) < 1e-9);
  });

  test("frame times are exact for a whole run at 30fps", () => {
    const total = frameCount(10, 30);
    assert.equal(total, 300);
    for (const n of [0, 1, 99, 150, 299]) {
      assert.equal(frameTime(n, 30), n / 30);
    }
  });
});
