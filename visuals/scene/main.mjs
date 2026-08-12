/**
 * Part A entry point: the `window.__movie` render API from visuals/CONTRACT.md.
 *
 *   await window.__movie.init({ seed, width, height, durationSeconds })
 *   await window.__movie.renderAt(t)
 *
 * `renderAt` is a pure function of (seed, t). It renders exactly one frame and
 * returns; there is no animation loop and no clock anywhere in this directory.
 */

import * as THREE from 'three';
import { buildWorld } from './world.mjs';

const DEFAULTS = { seed: 1, width: 1280, height: 720, durationSeconds: 10 };

let renderer = null;
let world = null;
let gl = null;

function setupCanvas(width, height) {
  const canvas = document.getElementById('stage');
  if (!canvas) throw new Error('scene: no <canvas id="stage"> in the document');
  canvas.width = width;
  canvas.height = height;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  canvas.style.display = 'block';
  return canvas;
}

const movie = {
  ready: false,

  async init(config = {}) {
    const settings = { ...DEFAULTS, ...config };
    const canvas = setupCanvas(settings.width, settings.height);

    renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      // The capture step screenshots the canvas after render() returns, which
      // happens outside the compositing frame the draw belongs to.
      preserveDrawingBuffer: true,
      alpha: false,
      powerPreference: 'high-performance',
    });
    // A device pixel ratio other than 1 would change the backing-store size and
    // corrupt every captured frame.
    renderer.setPixelRatio(1);
    renderer.setSize(settings.width, settings.height, false);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    gl = renderer.getContext();
    if (!gl || gl.isContextLost()) {
      throw new Error('scene: WebGL context unavailable');
    }

    world = buildWorld(settings);

    // Compile up front so frame 0 is not missing materials or shadow shaders.
    world.update(0);
    if (typeof renderer.compileAsync === 'function') {
      await renderer.compileAsync(world.scene, world.camera);
    } else {
      renderer.compile(world.scene, world.camera);
    }
    renderer.render(world.scene, world.camera);
    gl.finish();

    movie.ready = true;
  },

  async renderAt(t) {
    if (!renderer || !world) throw new Error('scene: renderAt called before init');
    world.update(t);
    renderer.render(world.scene, world.camera);
    // Force the GPU to drain so the canvas holds the finished frame by the time
    // this resolves, rather than whenever the driver gets round to it.
    gl.finish();
  },
};

window.__movie = movie;
