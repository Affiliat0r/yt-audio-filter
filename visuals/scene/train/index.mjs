/**
 * Part D — the train.
 *
 *   const train = createTrain(scene, { rng, materials, track, ... });
 *   const state = train.update(t);   // { position, direction, speed, headPosition, ... }
 *
 * `update(t)` is a pure function of `(seed, t)`. It calls no clock and no
 * `Math.random()`, and holds no state that carries between frames: rendering
 * frame 150 in isolation gives the same pixels as rendering frames 0..150 in
 * order. The steam is the part where that is hard, and steam.mjs explains how
 * it is done.
 *
 * Vehicles are posed from their two axle points on the spline — the body sits
 * on the chord between them — so a carriage genuinely tracks a curve instead of
 * sliding round it rigidly. The body then rides on springs above that.
 */

import * as THREE from 'three';
import { provenance } from './deps.mjs';
import { createMaterialSet, disposeMaterialSet } from './materials.mjs';
import { disposeShadedVariants } from './parts.mjs';
import { buildLocomotive } from './locomotive.mjs';
import { buildCarriage } from './carriages.mjs';
import { createSuspension } from './suspension.mjs';
import { createSteam } from './steam.mjs';
import { adaptTrack } from './spline.mjs';
import { LOCO, CARRIAGE } from './spec.mjs';

const WORLD_UP = new THREE.Vector3(0, 1, 0);

/** Metres per second. Slow enough to sit under a recitation without hurrying. */
export const DEFAULT_SPEED = 6.0;

const _right = new THREE.Vector3();
const _up = new THREE.Vector3();
const _fwd = new THREE.Vector3();

/** Basis from a forward direction: local +Z along `forward`, +Y world up. */
function composeFromForward(matrix, position, forward) {
  _fwd.copy(forward).normalize();
  _right.crossVectors(WORLD_UP, _fwd);
  if (_right.lengthSq() < 1e-8) _right.set(1, 0, 0);
  _right.normalize();
  _up.crossVectors(_fwd, _right).normalize();
  matrix.makeBasis(_right, _up, _fwd);
  matrix.setPosition(position);
  return matrix;
}

/** Default consist. Ordered the way a real mixed train is marshalled. */
const DEFAULT_CONSIST = [
  { kind: 'coach', livery: 'crimson' },
  { kind: 'coach', livery: 'teak' },
  { kind: 'wagon' },
  { kind: 'brake' },
];

const LIVERIES = {
  crimson: (M) => ({ lower: M.crimson, upper: M.coachCream }),
  teak: (M) => ({ lower: M.teak, upper: M.teak }),
  green: (M) => ({ lower: M.crimsonDeep, upper: M.coachCream }),
};

/**
 * @param {THREE.Scene} scene
 * @param {object} opts
 * @param {Rng}    opts.rng        seeded stream (`next/range/int/pick/fork`)
 * @param {object} [opts.materials] Part A's material module, if it is loaded
 * @param {object} [opts.track]     Part C's track; a fallback loop is built if absent
 * @param {number} [opts.speed]     metres per second
 * @param {number} [opts.railTop]   rail-head height above the spline
 * @param {number} [opts.startDistance] where the locomotive sits at t = 0
 */
export function createTrain(scene, opts = {}) {
  const {
    rng,
    materials: materialLib = null,
    track: rawTrack = null,
    speed = DEFAULT_SPEED,
    railTop = null,
    startDistance = 0,
    consist = DEFAULT_CONSIST,
    sleeperSpacing = 0.65,
    quality = 1,
    couplingGap = 0.55,
  } = opts;

  if (!rng || typeof rng.next !== 'function') {
    throw new Error('createTrain: an rng with next()/range()/fork() is required');
  }

  const group = new THREE.Group();
  group.name = 'train';

  const track = adaptTrack(rawTrack, rng.fork(), { railTop });
  const materials = createMaterialSet((rng.next() * 1e9) | 0, materialLib);

  /* --- vehicles ---------------------------------------------------------- */

  const units = [];

  const loco = buildLocomotive(rng.fork(), materials);
  units.push({
    kind: 'locomotive',
    node: loco.node,
    sprung: loco.sprung,
    halfWheelbase: loco.halfWheelbase,
    length: loco.length,
    wheelRadius: LOCO.driverRadius,
    offset: 0,
    update: loco.update,
    suspension: createSuspension(rng.fork(), { sleeperSpacing, softness: 0.75, rollGain: 0.042 }),
  });
  group.add(loco.node);

  let offset = loco.length / 2;
  for (const entry of consist) {
    const kind = typeof entry === 'string' ? entry : entry.kind;
    const livery = (LIVERIES[entry.livery] || LIVERIES.crimson)(materials);
    const vehicle = buildCarriage(rng.fork(), materials, kind, { livery });
    offset += vehicle.length / 2 + couplingGap;
    units.push({
      kind,
      node: vehicle.node,
      sprung: vehicle.sprung,
      halfWheelbase: vehicle.halfWheelbase,
      length: vehicle.length,
      wheelRadius: CARRIAGE.wheelRadius,
      offset,
      update: vehicle.update,
      triangles: vehicle.triangles,
      suspension: createSuspension(rng.fork(), { sleeperSpacing, softness: 1.15, rollGain: 0.062 }),
    });
    group.add(vehicle.node);
    offset += vehicle.length / 2;
  }

  const consistLength = offset;

  /* --- steam ------------------------------------------------------------- */

  const steam = createSteam(group, {
    rng: rng.fork(),
    track,
    speed,
    driverRadius: LOCO.driverRadius,
    railTop: track.railTop,
    chimney: loco.chimney,
    cylinderVents: loco.cylinderVents,
    quality,
    distanceAt: (time) => startDistance + speed * time,
  });

  /* --- state ------------------------------------------------------------- */

  const front = new THREE.Vector3();
  const back = new THREE.Vector3();
  const centre = new THREE.Vector3();
  const forward = new THREE.Vector3();

  const trainState = {
    position: new THREE.Vector3(),
    direction: new THREE.Vector3(0, 0, 1),
    up: new THREE.Vector3(0, 1, 0),
    right: new THREE.Vector3(1, 0, 0),
    speed,
    headPosition: new THREE.Vector3(),
    tailPosition: new THREE.Vector3(),
    chimneyPosition: new THREE.Vector3(),
    distance: startDistance,
    curvature: 0,
    length: consistLength,
    trackLength: track.length,
    units: units.map((u) => ({ kind: u.kind, position: new THREE.Vector3(), direction: new THREE.Vector3() })),
  };

  let triangles = loco.triangles + steam.triangles;
  for (const u of units) if (u.triangles) triangles += u.triangles;

  function update(t) {
    const distance = startDistance + speed * t;

    for (let i = 0; i < units.length; i++) {
      const unit = units[i];
      const s = distance - unit.offset;
      const hw = unit.halfWheelbase;

      track.pointAt(s + hw, front);
      track.pointAt(s - hw, back);
      centre.addVectors(front, back).multiplyScalar(0.5);
      forward.subVectors(front, back).normalize();
      centre.addScaledVector(WORLD_UP, track.railTop);
      composeFromForward(unit.node.matrix, centre, forward);
      unit.node.matrixWorldNeedsUpdate = true;

      const curvature = track.curvatureAt(s);
      const ride = unit.suspension.evaluate(s, speed, curvature, hw * 2);
      unit.sprung.position.set(ride.lateral, ride.bounce, 0);
      unit.sprung.rotation.set(ride.pitch, 0, ride.roll);

      // The wheels stay on the rail; the frame rose by `ride.bounce`, so the
      // motion gear is solved with the crank pins that much lower in its frame.
      unit.update(s, ride.bounce);

      const view = trainState.units[i];
      view.position.copy(centre);
      view.direction.copy(forward);

      if (i === 0) {
        trainState.position.copy(centre);
        trainState.direction.copy(forward);
        trainState.right.crossVectors(WORLD_UP, forward).normalize();
        trainState.up.crossVectors(forward, trainState.right).normalize();
        trainState.curvature = curvature;
        trainState.chimneyPosition
          .copy(centre)
          .addScaledVector(forward, loco.chimney.z)
          .addScaledVector(WORLD_UP, loco.chimney.y + ride.bounce);
      }
    }

    track.pointAt(distance + LOCO.length * 0.5 + 1.5, trainState.headPosition);
    trainState.headPosition.y += track.railTop;
    track.pointAt(distance - consistLength, trainState.tailPosition);
    trainState.tailPosition.y += track.railTop;
    trainState.distance = distance;

    steam.update(t);
    return trainState;
  }

  // Frame 0 so the caller can measure and the renderer can compile.
  update(0);

  if (scene) scene.add(group);

  return {
    group,
    update,
    /** Diagnostics — not part of the contract, but useful in a harness. */
    info: {
      triangles: Math.round(triangles),
      units: units.length,
      trackSource: track.source,
      trackLength: track.length,
      deps: provenance,
      speed,
      consistLength,
    },
    dispose() {
      steam.dispose();
      disposeShadedVariants(materials);
      disposeMaterialSet(materials);
      group.traverse((o) => {
        if (o.isMesh || o.isInstancedMesh) o.geometry?.dispose();
      });
      group.parent?.remove(group);
    },
  };
}

export default createTrain;
