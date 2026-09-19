/**
 * lib/renderer.mjs — Part B, look pipeline: the renderer itself.
 *
 * One job: hand back a `THREE.WebGLRenderer` configured the way a film
 * renderer is configured rather than the way a game renderer is, and tell the
 * caller what internal resolution the post chain has to match.
 *
 * The three settings that matter most, in order of how much they change the
 * image:
 *
 *   1. Supersampling. We render the scene at `width * ss` by `height * ss` and
 *      box-resolve down to the canvas at the very end of the post chain (see
 *      lib/post.mjs). At ss=2 that is 4 geometric samples *and* 4 shading
 *      samples per output pixel — it fixes edge aliasing, specular shimmer,
 *      normal-map aliasing and texture crawl all at once, which no
 *      post-process antialiasing can do. It costs 4x fill. We render offline;
 *      we have the budget.
 *   2. ACES filmic tone mapping with a correct sRGB output transform. Without
 *      it, every highlight clips to flat white and the render reads as
 *      "programmer art" no matter how good the materials are.
 *   3. Anisotropic filtering at the driver maximum, applied as the global
 *      texture default so Part A's materials inherit it for free. Ground
 *      planes seen at a grazing angle are the single most common place a
 *      render falls apart, and 16x anisotropy is the entire fix.
 *
 * Determinism: nothing here reads a clock, a frame counter or `Math.random()`.
 * The renderer is configured once and never mutates itself.
 */

import * as THREE from 'three';

/** Supersample factor used when the caller does not ask for one. */
export const DEFAULT_SUPERSAMPLE = 2;

/**
 * @typedef {Object} RendererOptions
 * @property {HTMLCanvasElement} canvas         Target canvas. Stays exactly width x height.
 * @property {number} width                     Output width in pixels.
 * @property {number} height                    Output height in pixels.
 * @property {number} [supersample=2]           Internal render scale. 1 disables supersampling.
 * @property {number} [exposure=1]              `toneMappingExposure`. Photographic stops: x2 = +1 EV.
 * @property {number} [toneMapping]             Defaults to `THREE.ACESFilmicToneMapping`.
 * @property {number} [shadowType]              Defaults to `THREE.PCFSoftShadowMap`.
 * @property {boolean} [preserveDrawingBuffer=true]  Required: capture screenshots after render() returns.
 * @property {number} [maxSupersample=4]        Guard so a silly ss cannot exceed GPU texture limits.
 */

/**
 * Build the renderer.
 *
 * Returns the `THREE.WebGLRenderer` itself (so it can be passed straight to
 * `createLighting` / `createComposer` / `renderer.render`) with the look
 * pipeline's extra metadata attached as non-enumerable properties:
 *
 *   renderer.supersample      — the effective supersample factor
 *   renderer.outputWidth      — canvas width
 *   renderer.outputHeight     — canvas height
 *   renderer.internalWidth    — width the post chain must size its buffers to
 *   renderer.internalHeight   — ditto
 *   renderer.maxAnisotropy    — driver maximum, already the texture default
 *   renderer.setOutputSize(w, h) — resize canvas + recompute internal size
 *
 * @param {RendererOptions} options
 * @returns {THREE.WebGLRenderer}
 */
export function createRenderer(options = {}) {
  const {
    canvas,
    width = 1280,
    height = 720,
    supersample = DEFAULT_SUPERSAMPLE,
    // 1.0 is "correctly exposed", not "unity gain": lib/lighting.mjs meters
    // its own sky and normalises the absolute level so that it is. Treat this
    // as the artistic exposure compensation — 2.0 is +1 EV, 0.5 is -1 EV.
    exposure = 1.0,
    toneMapping = THREE.ACESFilmicToneMapping,
    shadowType = THREE.PCFSoftShadowMap,
    preserveDrawingBuffer = true,
    maxSupersample = 4,
  } = options;

  if (!canvas) throw new Error('createRenderer: `canvas` is required');

  // Colour management on is what makes sRGB textures decode and linear maths
  // happen in linear space. It is the default in modern three; assert it so a
  // stray `ColorManagement.enabled = false` elsewhere cannot silently wash the
  // whole render out.
  THREE.ColorManagement.enabled = true;

  const renderer = new THREE.WebGLRenderer({
    canvas,
    // MSAA on the default framebuffer would be wasted: every scene pixel is
    // drawn into an off-screen render target. The composer asks for a
    // multisampled target instead (see post.mjs).
    antialias: false,
    // The capture step screenshots the canvas after render() resolves, which
    // is outside the compositing frame the draw belongs to.
    preserveDrawingBuffer,
    alpha: false,
    stencil: false,
    depth: true,
    powerPreference: 'high-performance',
    // Ask the driver not to silently drop to a software renderer.
    failIfMajorPerformanceCaveat: false,
  });

  // A device pixel ratio other than 1 changes the backing-store size and
  // corrupts every captured frame.
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);

  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = toneMapping;
  renderer.toneMappingExposure = exposure;

  // Physical light units. `useLegacyLights` was removed in r165 — modern three
  // is always physically correct — but three still needs to be told that a
  // DirectionalLight intensity is irradiance, which it now is by default. The
  // assignment below is a no-op on r181 and a safety net on anything older.
  if ('useLegacyLights' in renderer) renderer.useLegacyLights = false;

  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = shadowType;
  renderer.shadowMap.autoUpdate = true;

  // Grazing-angle texture quality. Setting the static default means every
  // texture Part A loads picks this up without having to know about it.
  const maxAnisotropy = renderer.capabilities.getMaxAnisotropy();
  THREE.Texture.DEFAULT_ANISOTROPY = maxAnisotropy;

  const gl = renderer.getContext();
  if (!gl || gl.isContextLost()) {
    throw new Error('createRenderer: WebGL context unavailable');
  }

  // Clamp supersampling to something the GPU can actually allocate. A 2x
  // buffer at 1280x720 is 2560x1440; the limit is normally 16384.
  const maxTexture = renderer.capabilities.maxTextureSize || 8192;

  const state = {
    supersample: 1,
    outputWidth: width,
    outputHeight: height,
    internalWidth: width,
    internalHeight: height,
  };

  function applySize(w, h, ss) {
    const requested = Math.max(1, Math.min(maxSupersample, Math.floor(ss) || 1));
    let effective = requested;
    while (effective > 1 && (w * effective > maxTexture || h * effective > maxTexture)) {
      effective -= 1;
    }
    state.supersample = effective;
    state.outputWidth = w;
    state.outputHeight = h;
    state.internalWidth = w * effective;
    state.internalHeight = h * effective;
    renderer.setSize(w, h, false);
  }

  applySize(width, height, supersample);

  Object.defineProperties(renderer, {
    supersample: { get: () => state.supersample, enumerable: false, configurable: true },
    outputWidth: { get: () => state.outputWidth, enumerable: false, configurable: true },
    outputHeight: { get: () => state.outputHeight, enumerable: false, configurable: true },
    internalWidth: { get: () => state.internalWidth, enumerable: false, configurable: true },
    internalHeight: { get: () => state.internalHeight, enumerable: false, configurable: true },
    maxAnisotropy: { value: maxAnisotropy, enumerable: false, configurable: true },
    setOutputSize: {
      value: (w, h) => {
        applySize(w, h, state.supersample);
        return getInternalResolution(renderer);
      },
      enumerable: false,
      configurable: true,
    },
  });

  return renderer;
}

/**
 * The resolution the post chain must size its render targets to.
 *
 * `createComposer` calls this itself, so callers normally do not need it; it
 * exists so the page shell can log or assert the internal size.
 *
 * @param {THREE.WebGLRenderer} renderer
 * @returns {{width:number, height:number, supersample:number, outputWidth:number, outputHeight:number}}
 */
export function getInternalResolution(renderer) {
  const ss = renderer.supersample || 1;
  const size = renderer.getSize(new THREE.Vector2());
  return {
    width: renderer.internalWidth || size.x * ss,
    height: renderer.internalHeight || size.y * ss,
    supersample: ss,
    outputWidth: renderer.outputWidth || size.x,
    outputHeight: renderer.outputHeight || size.y,
  };
}

/**
 * The GPU the context actually landed on, e.g.
 * "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 ...)".
 *
 * Worth asserting in a harness: on Windows, a wrong ANGLE backend flag drops
 * Chrome onto the Microsoft Basic Render Driver, which still produces a
 * picture — 50x slower and with different filtering.
 *
 * @param {THREE.WebGLRenderer} renderer
 * @returns {string}
 */
export function describeGPU(renderer) {
  const gl = renderer.getContext();
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  if (!ext) return gl.getParameter(gl.RENDERER) || 'unknown';
  return gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || 'unknown';
}
