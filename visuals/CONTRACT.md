# `visuals/` — contract between the three parts

Procedural three.js visuals rendered headlessly to MP4. This replaces
downloading YouTube footage: the output is ours, so there is no bot
detection, no SABR wall, and no Content ID exposure.

Three parts, three owners, one contract. **Do not edit outside your own
directory.**

| Part | Directory | Owns |
|------|-----------|------|
| A — scene | `visuals/scene/` | the three.js world and its deterministic render API |
| B — capture | `visuals/capture/` | headless Chrome, frame stepping, PNG output |
| C — encode + CLI | `visuals/encode/`, `visuals/cli.mjs`, `visuals/test/` | frames → MP4, the CLI, tests |

## The one rule that matters: determinism

Offline rendering must NOT depend on wall-clock time. A 10-second movie at
30 fps is 300 discrete frames, and frame *n* must look identical no matter
how long the machine took to get there.

So the scene never reads `Date.now()`, `performance.now()`, or
`requestAnimationFrame` deltas. It is a **pure function of `(seed, t)`**:
the same seed and the same `t` always produce the same pixels. All
randomness comes from a seeded PRNG, never `Math.random()`.

## Part A — scene API

`visuals/scene/index.html` loads as a `file://` page and must define exactly
this on `window`:

```js
window.__movie = {
  /**
   * Build the world. Called once. Must not start any animation loop.
   * @param {{seed:number, width:number, height:number, durationSeconds:number}} config
   * @returns {Promise<void>}
   */
  async init(config) {},

  /**
   * Render the single frame at time `t` seconds and leave it on the canvas.
   * Must be synchronous-complete: when it resolves, the canvas holds the
   * finished frame.
   * @param {number} t seconds from 0 to durationSeconds
   * @returns {Promise<void>}
   */
  async renderAt(t) {},

  /** True once init() has finished. Capture polls this before frame 0. */
  ready: false,
};
```

The canvas must be `#stage`, sized exactly `width x height`, with no page
margins, scrollbars, or CSS transforms — capture screenshots that element
directly, so any layout offset becomes a corrupted frame.

Load `three` from `../node_modules/three/build/three.module.js` via an
import map. No CDN: this must render with the network off.

## Part B — capture API

`visuals/capture/capture.mjs` default-exports:

```js
/**
 * @param {{
 *   sceneUrl:string, outDir:string, width:number, height:number,
 *   fps:number, durationSeconds:number, seed:number,
 *   chromePath:string, onProgress?:(done:number,total:number)=>void
 * }} opts
 * @returns {Promise<{frameCount:number, outDir:string}>}
 */
export default async function capture(opts) {}
```

Writes zero-padded `frame_%06d.png` into `outDir`, starting at
`frame_000000.png` for `t = 0`. Frame *n* is `renderAt(n / fps)`.

Launch flags: run headless with the GPU enabled (`--use-gl=angle`,
`--use-angle=gl-egl`, `--enable-gpu`, `--hide-scrollbars`) and fail loudly
if WebGL is unavailable rather than silently emitting black frames.

## Part C — encode API

`visuals/encode/encode.mjs` default-exports:

```js
/**
 * @param {{
 *   framesDir:string, outPath:string, fps:number,
 *   onProgress?:(line:string)=>void
 * }} opts
 * @returns {Promise<string>} outPath
 */
export default async function encode(opts) {}
```

Encoder selection mirrors `src/yt_audio_filter/ffmpeg_overlay.py`: probe for
NVENC and use `h264_nvenc -preset p5 -tune hq -rc vbr -cq 19 -b:v 0` when
present, else `libx264 -preset medium -crf 18`. Always `-pix_fmt yuv420p`
so the result plays everywhere.

`visuals/cli.mjs` wires the three together:

```
node visuals/cli.mjs --seconds 10 --fps 30 --width 1280 --height 720 \
                     --seed 42 --out visuals/out/test.mp4
```

It must print per-stage progress and, at the end, the output path plus
ffprobe-confirmed duration and resolution.

## Definition of done

`node visuals/cli.mjs --seconds 10` produces a playable MP4 whose ffprobe
duration is 10.0s (+/- 0.1) at the requested resolution, with no black or
duplicated frames, and re-running with the same seed produces a
byte-identical frame 150.
