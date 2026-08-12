/**
 * The endless loop of track.
 *
 * A closed Catmull-Rom spline through jittered points on a ring, so the train
 * can run forever without ever reaching an end. Everything downstream — the
 * terrain corridor, prop placement, the train, the camera — is expressed as a
 * distance along this curve.
 */

import * as THREE from 'three';
import { TRACK } from './palette.mjs';
import { extrudeProfile, composeFromForward, toyMaterial, WORLD_UP } from './geom.mjs';

export const GAUGE = 1.6;
export const BALLAST_TOP = 0.3;
export const SLEEPER_TOP = 0.46;
/** Height of the rail head above the spline. The train sits on this. */
export const RAIL_TOP = 0.64;

const CONTROL_POINTS = 14;
const RING_RADIUS = 104;
const RADIUS_JITTER = 22;

/** Grid cell size for the nearest-point lookup. Must exceed the widest query. */
const CELL = 20;
const LOOKUP_SPACING = 2;

export function buildTrack(rng, hills) {
  const controls = [];
  for (let i = 0; i < CONTROL_POINTS; i++) {
    const angle = (i / CONTROL_POINTS) * Math.PI * 2 + rng.range(-0.1, 0.1);
    const radius = RING_RADIUS + rng.range(-RADIUS_JITTER, RADIUS_JITTER);
    const x = Math.cos(angle) * radius;
    const z = Math.sin(angle) * radius;
    // Follow the macro landform, damped, so the track never climbs a hill it
    // would look silly on and never dips below the water line.
    const y = Math.max(-4, hills.macro(x, z) * 0.85 + 3.2);
    controls.push(new THREE.Vector3(x, y, z));
  }

  const curve = new THREE.CatmullRomCurve3(controls, true, 'catmullrom', 0.5);
  curve.arcLengthDivisions = 8000;
  const length = curve.getLength();

  // --- frames -------------------------------------------------------------

  const _p = new THREE.Vector3();
  const _ahead = new THREE.Vector3();

  /** Wrap a distance into [0, 1) of the closed curve. */
  function u(distance) {
    const v = (distance / length) % 1;
    return v < 0 ? v + 1 : v;
  }

  /** Point on the spline centreline at `distance` metres round the loop. */
  function pointAt(distance, target = new THREE.Vector3()) {
    return curve.getPointAt(u(distance), target);
  }

  /**
   * Oriented frame at `distance`. `forward` is a chord over `span` metres,
   * which is both cheaper and smoother than a true tangent.
   */
  function frameAt(distance, span = 1.5, out = null) {
    const frame = out || {
      pos: new THREE.Vector3(),
      forward: new THREE.Vector3(),
      right: new THREE.Vector3(),
      up: new THREE.Vector3(),
    };
    pointAt(distance, frame.pos);
    pointAt(distance + span, _ahead);
    frame.forward.subVectors(_ahead, frame.pos).normalize();
    frame.right.crossVectors(WORLD_UP, frame.forward).normalize();
    frame.up.crossVectors(frame.forward, frame.right).normalize();
    return frame;
  }

  function frameSeries(spacing) {
    const count = Math.max(8, Math.round(length / spacing));
    const step = length / count;
    const frames = [];
    for (let i = 0; i < count; i++) frames.push(frameAt(i * step, step));
    return frames;
  }

  // --- nearest-point lookup ----------------------------------------------

  const lookupCount = Math.round(length / LOOKUP_SPACING);
  const lookupXYZ = new Float32Array(lookupCount * 3);
  for (let i = 0; i < lookupCount; i++) {
    pointAt((i * length) / lookupCount, _p);
    lookupXYZ[i * 3] = _p.x;
    lookupXYZ[i * 3 + 1] = _p.y;
    lookupXYZ[i * 3 + 2] = _p.z;
  }

  const grid = new Map();
  const key = (cx, cz) => cx * 100003 + cz;
  for (let i = 0; i < lookupCount; i++) {
    const cx = Math.floor(lookupXYZ[i * 3] / CELL);
    const cz = Math.floor(lookupXYZ[i * 3 + 2] / CELL);
    const k = key(cx, cz);
    let bucket = grid.get(k);
    if (!bucket) grid.set(k, (bucket = []));
    bucket.push(i);
  }

  const _near = { dist: 1e6, y: 0, index: -1 };

  /**
   * Distance from (x, z) to the track centreline and the track height there.
   * Exact for anything within CELL metres; reports 1e6 beyond that, which is
   * all the terrain corridor needs.
   */
  function nearest(x, z) {
    const cx = Math.floor(x / CELL);
    const cz = Math.floor(z / CELL);
    let best = Infinity;
    let bestIndex = -1;
    for (let dx = -1; dx <= 1; dx++) {
      for (let dz = -1; dz <= 1; dz++) {
        const bucket = grid.get(key(cx + dx, cz + dz));
        if (!bucket) continue;
        for (let n = 0; n < bucket.length; n++) {
          const i = bucket[n];
          const ex = lookupXYZ[i * 3] - x;
          const ez = lookupXYZ[i * 3 + 2] - z;
          const d2 = ex * ex + ez * ez;
          if (d2 < best) {
            best = d2;
            bestIndex = i;
          }
        }
      }
    }
    if (bestIndex < 0) {
      _near.dist = 1e6;
      _near.y = 0;
      _near.index = -1;
    } else {
      _near.dist = Math.sqrt(best);
      _near.y = lookupXYZ[bestIndex * 3 + 1];
      _near.index = bestIndex;
    }
    return _near;
  }

  /** Distance along the loop of lookup sample `index`. */
  function distanceOfIndex(index) {
    return (index * length) / lookupCount;
  }

  // --- meshes -------------------------------------------------------------

  const group = new THREE.Group();
  group.name = 'track';

  const ballastFrames = frameSeries(2.0);
  const ballast = new THREE.Mesh(
    extrudeProfile(ballastFrames, [
      [-2.4, -0.55],
      [2.4, -0.55],
      [1.75, BALLAST_TOP],
      [-1.75, BALLAST_TOP],
    ]),
    toyMaterial(TRACK.ballast, { shininess: 2 })
  );
  ballast.castShadow = false;
  ballast.receiveShadow = true;
  group.add(ballast);

  const railFrames = frameSeries(0.7);
  const railProfile = (offset) => [
    [offset - 0.075, SLEEPER_TOP],
    [offset + 0.075, SLEEPER_TOP],
    [offset + 0.075, RAIL_TOP],
    [offset - 0.075, RAIL_TOP],
  ];
  const railMaterial = toyMaterial(TRACK.rail, { shininess: 70, specular: 0x777777 });
  for (const side of [-1, 1]) {
    const rail = new THREE.Mesh(extrudeProfile(railFrames, railProfile(side * (GAUGE / 2))), railMaterial);
    rail.castShadow = false;
    rail.receiveShadow = true;
    group.add(rail);
  }

  // Sleepers.
  const sleeperSpacing = 1.35;
  const sleeperCount = Math.round(length / sleeperSpacing);
  const sleepers = new THREE.InstancedMesh(
    new THREE.BoxGeometry(2.9, SLEEPER_TOP - BALLAST_TOP, 0.52),
    toyMaterial(TRACK.sleeper, { shininess: 4 }),
    sleeperCount
  );
  sleepers.castShadow = false;
  sleepers.receiveShadow = true;
  const _m = new THREE.Matrix4();
  const _pos = new THREE.Vector3();
  for (let i = 0; i < sleeperCount; i++) {
    const d = (i * length) / sleeperCount;
    const f = frameAt(d, sleeperSpacing);
    _pos.copy(f.pos).addScaledVector(f.up, (BALLAST_TOP + SLEEPER_TOP) / 2);
    composeFromForward(_m, _pos, f.forward);
    sleepers.setMatrixAt(i, _m);
  }
  sleepers.instanceMatrix.needsUpdate = true;
  group.add(sleepers);

  // Lineside marker posts — cheap, and they sell the sense of speed.
  const postSpacing = 12;
  const postCount = Math.round(length / postSpacing);
  const postBody = new THREE.InstancedMesh(
    new THREE.CylinderGeometry(0.09, 0.11, 1.3, 6).translate(0, 0.65, 0),
    toyMaterial(TRACK.postBody, { shininess: 10 }),
    postCount
  );
  const postCap = new THREE.InstancedMesh(
    new THREE.CylinderGeometry(0.13, 0.13, 0.22, 6).translate(0, 1.38, 0),
    toyMaterial(TRACK.postCap, { shininess: 24 }),
    postCount
  );
  postBody.castShadow = true;
  postBody.receiveShadow = true;
  postCap.castShadow = true;
  postCap.receiveShadow = true;
  for (let i = 0; i < postCount; i++) {
    const d = (i * length) / postCount;
    const f = frameAt(d, 2);
    const side = i % 2 === 0 ? 1 : -1;
    _pos.copy(f.pos).addScaledVector(f.right, side * 3.6).addScaledVector(f.up, -0.35);
    composeFromForward(_m, _pos, f.forward);
    postBody.setMatrixAt(i, _m);
    postCap.setMatrixAt(i, _m);
  }
  postBody.instanceMatrix.needsUpdate = true;
  postCap.instanceMatrix.needsUpdate = true;
  group.add(postBody, postCap);

  return {
    curve,
    length,
    group,
    pointAt,
    frameAt,
    nearest,
    distanceOfIndex,
    RAIL_TOP,
  };
}
