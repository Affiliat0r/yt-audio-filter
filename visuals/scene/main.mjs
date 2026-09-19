/**
 * `window.__movie` — the capture harness's entry point.
 *
 * Assembles the four independently-built parts into one deterministic movie:
 *
 *   lib/renderer.mjs   ACES tone mapping, supersampling, shadow config
 *   lib/lighting.mjs   HDRI image-based lighting, physical sky, cascaded shadows
 *   lib/post.mjs       GTAO -> bloom -> depth of field -> SMAA -> output
 *   scene/world/       terrain, track, vegetation, props, characters
 *   scene/train/       locomotive, carriages, valve gear, suspension, steam
 *
 * The whole thing is a pure function of (seed, t). See CONTRACT.md — nothing
 * here may read the clock, and `renderAt` must produce identical pixels for a
 * given `t` no matter what order frames are requested in.
 */

import * as THREE from 'three';
import { createRenderer } from '../lib/renderer.mjs';
import { createLighting } from '../lib/lighting.mjs';
import { createComposer } from '../lib/post.mjs';
import * as materials from '../lib/materials.mjs';
import * as geom from '../lib/geom.mjs';
import { createWorld } from './world/index.mjs';
import { createTrain } from './train/index.mjs';
import { Rng } from './world/rng.mjs';

/** Chase camera placement, in metres relative to the locomotive. */
const CAMERA = {
  back: 17.5, // behind the smokebox; closer than this crops the loco
  up: 4.8, // above rail level
  side: 4.2, // offset for a three-quarter view; dead-astern reads flat
  lookAhead: 15.0, // aim point down the track, so curves open up
  lookUp: 1.6,
  // A slow lateral drift keeps a 20-minute journey from feeling locked-off.
  driftAmplitude: 2.2,
  driftPeriod: 47.0, // seconds; deliberately not a round number
};

const state = {
  renderer: null,
  scene: null,
  camera: null,
  lighting: null,
  post: null,
  world: null,
  train: null,
  config: null,
};

const tmp = {
  camPos: new THREE.Vector3(),
  lookAt: new THREE.Vector3(),
  side: new THREE.Vector3(),
};

window.__movie = {
  ready: false,

  async init(config = {}) {
    const seed = (config.seed ?? 1) >>> 0;
    const width = config.width ?? 1280;
    const height = config.height ?? 720;
    state.config = { seed, width, height };

    const canvas = document.getElementById('stage');
    canvas.width = width;
    canvas.height = height;

    const renderer = createRenderer({ canvas, width, height, supersample: 2 });
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, width / height, 0.35, 4000);

    // One root RNG, forked per subsystem, so adding a system later does not
    // reshuffle the worlds that earlier seeds produced.
    const rng = new Rng(seed);

    const world = await createWorld(scene, {
      seed,
      rng: rng.fork(),
      geom,
      materials,
    });

    const train = createTrain(scene, {
      rng: rng.fork(),
      materials,
      geom,
      track: world.track,
    });

    const lighting = await createLighting(scene, renderer, {
      hdri: 'kloofendal_48d_partly_cloudy_puresky',
      sunElevation: 27,
      sunAzimuth: 132,
      camera,
      shadowMaxFar: 220,
      fog: true,
      // The world runs out to a 2.6 km horizon ring. The derived default put
      // 50% haze at ~330 m, which bleached everything past the middle distance
      // into a flat wall and hid the hills entirely. Pushing the half-distance
      // out keeps aerial perspective as depth cue rather than fog.
      fogVisibility: 1100,
      fogStrength: 0.5,
    });

    const post = createComposer(renderer, scene, camera, {
      width,
      height,
      quality: 'high',
    });

    Object.assign(state, { renderer, scene, camera, lighting, post, world, train });

    // Diagnostic handle. Read-only from the harness's point of view; it exists
    // so a bad frame can be bisected by toggling subsystems from the outside
    // rather than by editing and re-running the whole pipeline.
    window.__debug = state;

    // Frame 0 must be as correct as frame 300: place the camera before the
    // first render so nothing is captured mid-warmup.
    this.renderAtSync(0);
    window.__movie.ready = true;
  },

  /** Positions everything for time `t`. Split out so init can prime frame 0. */
  renderAtSync(t) {
    const { camera, lighting, post, world, train } = state;

    const trainState = train.update(t);
    world.update(t, trainState);

    const { headPosition, direction, up } = trainState;

    // Right-hand vector from the track frame rather than world up, so the
    // camera banks with the train through curves instead of sliding.
    tmp.side.copy(direction).cross(up).normalize();

    const drift =
      Math.sin((t / CAMERA.driftPeriod) * Math.PI * 2) * CAMERA.driftAmplitude;

    tmp.camPos
      .copy(headPosition)
      .addScaledVector(direction, -CAMERA.back)
      .addScaledVector(up, CAMERA.up)
      .addScaledVector(tmp.side, CAMERA.side + drift);

    tmp.lookAt
      .copy(headPosition)
      .addScaledVector(direction, CAMERA.lookAhead)
      .addScaledVector(up, CAMERA.lookUp);

    camera.position.copy(tmp.camPos);
    camera.up.copy(up);
    camera.lookAt(tmp.lookAt);
    camera.updateMatrixWorld(true);

    lighting.update(camera, t);

    // Keep the locomotive sharp and let the far field fall away.
    post.setFocus(camera.position.distanceTo(headPosition));

    post.render(t);
  },

  async renderAt(t) {
    this.renderAtSync(t);
    // The capture harness screenshots straight after this resolves, so make
    // sure the GPU has actually finished rather than just accepted the work.
    const gl = state.renderer.getContext();
    gl.finish();
  },
};
