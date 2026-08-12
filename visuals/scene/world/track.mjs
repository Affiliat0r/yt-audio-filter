/**
 * The endless loop of track, and the permanent way that sits on it.
 *
 * A closed Catmull-Rom spline through jittered points on a ring, so the train
 * can run forever without reaching an end. Everything downstream — the terrain
 * corridor, prop placement, Part D's train, the camera — is expressed as a
 * distance along this curve. The spline maths is carried over from v1; the
 * geometry on top of it is new.
 *
 * Above the formation, in order: a trapezoidal ballast bed of crushed stone,
 * creosoted sleepers at 680 mm centres, cast baseplates, and flat-bottom
 * Vignoles rail swept from an 18-point section. The running surface is a
 * separate polished-steel cap so it catches one continuous specular streak
 * down the length of the frame — that highlight is what reads as steel.
 */

import * as THREE from 'three';

export const GAUGE = 1.6;
/** Heights relative to the spline, which runs at rail level minus RAIL_TOP. */
export const BALLAST_BASE = -0.30;
export const BALLAST_TOP = 0.36;
/** Underside of the sleeper: the crib is packed to within 100 mm of its top. */
export const SLEEPER_BOTTOM = 0.24;
export const SLEEPER_TOP = 0.46;
/** Height of the railhead above the spline. Part D's train sits on this. */
export const RAIL_TOP = 0.64;

const CONTROL_POINTS = 15;
const RING_RADIUS = 128;
const RADIUS_JITTER = 26;

/** Grid cell size for the nearest-point lookup. Must exceed the widest query. */
const CELL = 20;
const LOOKUP_SPACING = 1.5;

const SLEEPER_SPACING = 0.68;
const SLEEPER_LENGTH = 2.72;
const SLEEPER_WIDTH = 0.28;

/** Vignoles flat-bottom rail section, in metres from the rail's own centreline. */
const RAIL_SECTION = [
  [-0.076, 0.0],
  [0.076, 0.0],
  [0.076, 0.014],
  [0.026, 0.042],
  [0.0195, 0.058],
  [0.0195, 0.112],
  [0.026, 0.128],
  [0.0425, 0.141],
  [0.0425, 0.164],
  [0.0335, 0.176],
  [-0.0335, 0.176],
  [-0.0425, 0.164],
  [-0.0425, 0.141],
  [-0.026, 0.128],
  [-0.0195, 0.112],
  [-0.0195, 0.058],
  [-0.026, 0.042],
  [-0.076, 0.014],
];

/** The polished cap that sits on the head, swept separately. */
const RAIL_CAP = [
  [-0.0335, 0.1735],
  [0.0335, 0.1735],
  [0.0335, 0.1805],
  [-0.0335, 0.1805],
];

/** Ballast: wide base, 1:2 shoulders, broken crest. */
const BALLAST_SECTION = [
  [-2.42, BALLAST_BASE],
  [2.42, BALLAST_BASE],
  [2.34, BALLAST_BASE + 0.05],
  [1.92, BALLAST_TOP - 0.06],
  [1.76, BALLAST_TOP],
  [-1.76, BALLAST_TOP],
  [-1.92, BALLAST_TOP - 0.06],
  [-2.34, BALLAST_BASE + 0.05],
];

/**
 * @param {object} deps `{ rng, materials, geom }`
 * @param {(x:number,z:number)=>number} macroHeight the landform, sampled before
 *   the terrain exists — the track picks its own heights from it, and the
 *   terrain then levels a corridor to match.
 */
export function createTrack({ rng, materials, geom, macroHeight }) {
  const controls = [];
  for (let i = 0; i < CONTROL_POINTS; i++) {
    const angle = (i / CONTROL_POINTS) * Math.PI * 2 + rng.range(-0.09, 0.09);
    const radius = RING_RADIUS + rng.range(-RADIUS_JITTER, RADIUS_JITTER);
    const x = Math.cos(angle) * radius;
    const z = Math.sin(angle) * radius;
    // Follow the macro landform, damped, so the line never climbs a gradient a
    // steam engine would refuse and never dips below the water table.
    const y = Math.max(-3.0, macroHeight(x, z) * 0.72 + 3.4);
    controls.push(new THREE.Vector3(x, y, z));
  }

  const curve = new THREE.CatmullRomCurve3(controls, true, 'catmullrom', 0.5);
  curve.arcLengthDivisions = 12000;
  const length = curve.getLength();

  // --- frames -------------------------------------------------------------

  const WORLD_UP = new THREE.Vector3(0, 1, 0);
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
    const frame =
      out ||
      {
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
   * Exact within CELL metres; reports 1e6 beyond, which is all the terrain
   * corridor and the placement rejection tests need.
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
  const _m = new THREE.Matrix4();
  const _pos = new THREE.Vector3();
  const _q = new THREE.Quaternion();
  const _s = new THREE.Vector3(1, 1, 1);
  const _e = new THREE.Euler();

  // Ballast bed.
  const ballast = new THREE.Mesh(
    geom.extrudeProfile(frameSeries(1.4), BALLAST_SECTION, { uvScale: 1 }),
    materials.ballast
  );
  ballast.castShadow = true;
  ballast.receiveShadow = true;
  ballast.name = 'ballast';
  group.add(ballast);

  // Rails: both sides merged per material, so the whole permanent way is four
  // draw calls however long the loop is.
  const railFrames = frameSeries(0.55);
  const bodies = [];
  const caps = [];
  for (const side of [-1, 1]) {
    const off = side * (GAUGE / 2);
    bodies.push(
      geom.extrudeProfile(
        railFrames,
        RAIL_SECTION.map(([r, up]) => [r + off, up + SLEEPER_TOP]),
        { uvScale: 1 }
      )
    );
    caps.push(
      geom.extrudeProfile(
        railFrames,
        RAIL_CAP.map(([r, up]) => [r + off, up + SLEEPER_TOP]),
        { uvScale: 1 }
      )
    );
  }
  const railBody = new THREE.Mesh(geom.mergeAll(bodies), materials.railWeb);
  railBody.castShadow = true;
  railBody.receiveShadow = true;
  railBody.name = 'rails';
  const railCap = new THREE.Mesh(geom.mergeAll(caps), materials.railHead);
  railCap.castShadow = false;
  railCap.receiveShadow = true;
  railCap.name = 'railheads';
  group.add(railBody, railCap);

  // Sleepers.
  const sleeperCount = Math.round(length / SLEEPER_SPACING);
  const sleeperGeom = geom.beveledBox(SLEEPER_LENGTH, SLEEPER_TOP - SLEEPER_BOTTOM, SLEEPER_WIDTH, {
    bevel: 0.018,
    segments: 2,
    uvScale: 1,
  });
  const sleepers = new THREE.InstancedMesh(sleeperGeom, materials.sleeper, sleeperCount);
  sleepers.castShadow = true;
  sleepers.receiveShadow = true;
  sleepers.name = 'sleepers';

  // Cast baseplates under each rail, plus the spike heads that hold them.
  const chairGeom = geom.mergeAll([
    geom.beveledBox(0.30, 0.035, SLEEPER_WIDTH + 0.02, { bevel: 0.008, uvScale: 1 }),
    geom.beveledBox(0.055, 0.05, 0.055, { bevel: 0.012, uvScale: 1 }).translate(0.105, 0.03, 0.07),
    geom.beveledBox(0.055, 0.05, 0.055, { bevel: 0.012, uvScale: 1 }).translate(-0.105, 0.03, -0.07),
  ]);
  const chairs = new THREE.InstancedMesh(chairGeom, materials.railWeb, sleeperCount * 2);
  chairs.castShadow = false;
  chairs.receiveShadow = true;
  chairs.name = 'baseplates';

  const chairRng = rng.fork();
  const frame = {
    pos: new THREE.Vector3(),
    forward: new THREE.Vector3(),
    right: new THREE.Vector3(),
    up: new THREE.Vector3(),
  };
  for (let i = 0; i < sleeperCount; i++) {
    const d = (i * length) / sleeperCount;
    frameAt(d, SLEEPER_SPACING, frame);
    // A little settlement and skew: a bed of hand-packed stone is never true.
    const sink = chairRng.range(-0.022, 0.012);
    const skew = chairRng.range(-0.02, 0.02);
    const roll = chairRng.range(-0.012, 0.012);

    _pos
      .copy(frame.pos)
      .addScaledVector(frame.up, (SLEEPER_BOTTOM + SLEEPER_TOP) / 2 + sink)
      .addScaledVector(frame.right, chairRng.range(-0.02, 0.02));
    geom.composeFromForward(_m, _pos, frame.forward);
    _e.set(roll, skew, 0);
    _q.setFromEuler(_e);
    _m.multiply(new THREE.Matrix4().makeRotationFromQuaternion(_q));
    sleepers.setMatrixAt(i, _m);

    for (let s = 0; s < 2; s++) {
      const side = s === 0 ? -1 : 1;
      _pos
        .copy(frame.pos)
        .addScaledVector(frame.up, SLEEPER_TOP + sink)
        .addScaledVector(frame.right, side * (GAUGE / 2));
      geom.composeFromForward(_m, _pos, frame.forward);
      chairs.setMatrixAt(i * 2 + s, _m);
    }
  }
  sleepers.instanceMatrix.needsUpdate = true;
  chairs.instanceMatrix.needsUpdate = true;
  sleepers.computeBoundingSphere();
  chairs.computeBoundingSphere();
  group.add(sleepers, chairs);

  // Shoulder stones: a few thousand loose cobbles breaking the ballast crest,
  // so the bed has a broken silhouette instead of an extruded edge.
  const stoneRng = rng.fork();
  const stoneCount = Math.round(length * 4.0);
  const stoneGeom = geom.beveledBox(0.11, 0.075, 0.095, { bevel: 0.03, segments: 2, uvScale: 0.35 });
  const stones = new THREE.InstancedMesh(stoneGeom, materials.ballast, stoneCount);
  stones.castShadow = false;
  stones.receiveShadow = true;
  stones.name = 'ballast-shoulder';
  for (let i = 0; i < stoneCount; i++) {
    const d = stoneRng.next() * length;
    frameAt(d, 1.2, frame);
    const lateral = stoneRng.sign() * stoneRng.range(1.35, 2.62);
    const shoulder = Math.abs(lateral) > 1.86;
    const up = shoulder
      ? BALLAST_TOP - (Math.abs(lateral) - 1.86) * 1.32 + stoneRng.range(-0.03, 0.03)
      : BALLAST_TOP + stoneRng.range(-0.015, 0.025);
    _pos.copy(frame.pos).addScaledVector(frame.right, lateral).addScaledVector(frame.up, up);
    _e.set(stoneRng.range(0, 6.28), stoneRng.range(0, 6.28), stoneRng.range(0, 6.28));
    _q.setFromEuler(_e);
    const sc = stoneRng.range(0.65, 1.5);
    _s.set(sc, sc * stoneRng.range(0.6, 1.0), sc * stoneRng.range(0.75, 1.25));
    _m.compose(_pos, _q, _s);
    stones.setMatrixAt(i, _m);
  }
  stones.instanceMatrix.needsUpdate = true;
  stones.computeBoundingSphere();
  group.add(stones);

  // Fishplated joints every 18 m or so, both rails.
  const jointCount = Math.round(length / 18);
  const plateGeom = geom.beveledBox(0.46, 0.10, 0.016, { bevel: 0.006, uvScale: 1 });
  const plates = new THREE.InstancedMesh(plateGeom, materials.railWeb, jointCount * 4);
  plates.castShadow = false;
  plates.receiveShadow = true;
  plates.name = 'fishplates';
  for (let i = 0; i < jointCount; i++) {
    const d = (i * length) / jointCount;
    frameAt(d, 1.0, frame);
    for (let s = 0; s < 2; s++) {
      const railSide = s === 0 ? -1 : 1;
      for (let f = 0; f < 2; f++) {
        const face = f === 0 ? -1 : 1;
        _pos
          .copy(frame.pos)
          .addScaledVector(frame.right, railSide * (GAUGE / 2) + face * 0.028)
          .addScaledVector(frame.up, SLEEPER_TOP + 0.085);
        geom.composeFromForward(_m, _pos, frame.forward);
        // Local +X runs across the sleepers; rotate the plate to face outward.
        _m.multiply(new THREE.Matrix4().makeRotationY(Math.PI / 2));
        plates.setMatrixAt(i * 4 + s * 2 + f, _m);
      }
    }
  }
  plates.instanceMatrix.needsUpdate = true;
  plates.computeBoundingSphere();
  group.add(plates);

  // --- public API ---------------------------------------------------------

  const _tanA = new THREE.Vector3();
  const _tanB = new THREE.Vector3();

  return {
    group,
    curve,
    length,
    GAUGE,
    RAIL_TOP,
    BALLAST_TOP,
    SLEEPER_TOP,

    // Distance-parameterised (metres round the loop).
    pointAt,
    frameAt,
    nearest,
    distanceOfIndex,
    frameSeries,

    // Normalised-parameter API, per the contract: position and tangent at a
    // normalised distance along the closed loop. `u` wraps, so callers can
    // integrate forever without bookkeeping.
    /** @param {number} uu normalised distance in [0,1); wraps */
    getPointAt(uu, target = new THREE.Vector3()) {
      return curve.getPointAt(u(uu * length), target);
    },
    getTangentAt(uu, target = new THREE.Vector3()) {
      const d = uu * length;
      pointAt(d - 0.75, _tanA);
      pointAt(d + 0.75, _tanB);
      return target.subVectors(_tanB, _tanA).normalize();
    },
    /** Position + tangent at normalised distance `uu`, as one object. */
    at(uu) {
      const position = new THREE.Vector3();
      const tangent = new THREE.Vector3();
      this.getPointAt(uu, position);
      this.getTangentAt(uu, tangent);
      return { position, tangent };
    },
    /** Normalised parameter for a distance in metres. */
    toU: u,
  };
}
