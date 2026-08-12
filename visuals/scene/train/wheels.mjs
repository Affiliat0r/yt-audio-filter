/**
 * Wheels.
 *
 * A railway wheel is a steel tyre shrunk onto a spoked iron centre, with a
 * flange on the inside face. Modelling those as separate parts — rather than as
 * one disc — is what makes a wheel read as a wheel: the tyre carries a bright
 * rolled highlight, the web sits back in shadow, and the flange draws a thin
 * dark line against the rail.
 *
 * Rotation is always `-distance / radius`, derived here:
 *   rolling forward along +Z with the contact point at (0, -R, 0),
 *   v = ω × r  ⇒  (0, 0, V) = (ωx, 0, 0) × (0, -R, 0) = (0, 0, -ωx·R)
 *   ⇒ ωx = -V/R, so the angle about +X is -distance/R.
 * Get this wrong and the wheels visibly slip, which the eye picks up instantly
 * even when it cannot say why.
 */

import * as THREE from 'three';
import { beveledBox, beveledCylinder, latheProfile, rivetHead, mergeAll } from './deps.mjs';
import { place, roundedLoop, annularSector } from './parts.mjs';

const AXIS_X = new THREE.Vector3(1, 0, 0);

/** Mirror a geometry through the YZ plane, fixing normals and winding order. */
export function mirrorX(geometry) {
  const g = geometry.clone();
  g.scale(-1, 1, 1); // applyMatrix4 also runs the normal matrix, so normals flip
  if (g.index) {
    const idx = g.index.array;
    for (let i = 0; i < idx.length; i += 3) {
      const t = idx[i];
      idx[i] = idx[i + 2];
      idx[i + 2] = t;
    }
    g.index.needsUpdate = true;
  }
  g.computeBoundingSphere();
  return g;
}

/**
 * One wheel for the **+X side of the vehicle**: flange on -X (inboard), crank
 * pin on +X (outboard). The crank pin sits at local angle 0, straight up at
 * +Y, so a wheel rotation of `a` about +X puts the pin at `(·, R·cos a, R·sin a)`.
 *
 * @returns {Map<THREE.Material, THREE.BufferGeometry>}
 */
export function buildWheel(materials, opts = {}) {
  const {
    radius = 0.62,
    width = 0.2,
    spokes = 12,
    web = 0.072,
    hubRadius = 0.16,
    hubWidth = 0.3,
    flange = 0.055,
    crankRadius = 0,
    pinStart = 0.1,
    pinLength = 0.16,
    counterweight = false,
    boltCircle = true,
    tyreMaterial = materials.tyre,
    centreMaterial = materials.wheelIron || materials.iron,
    pinMaterial = materials.steel,
  } = opts;

  const buckets = new Map();
  const add = (geometry, material) => {
    let list = buckets.get(material);
    if (!list) buckets.set(material, (list = []));
    list.push(geometry);
  };

  const half = width / 2;
  const tyreInner = radius - Math.min(0.085, radius * 0.16);

  // --- tyre ---------------------------------------------------------------
  // Lathed about +Y then rolled onto the X axis; `rotateZ(π/2)` sends +Y to -X,
  // so the flange (authored at the profile's +axis end) lands inboard.
  const tyreProfile = roundedLoop(
    [
      [tyreInner, -half, 0.01],
      [radius - 0.004, -half, 0.012],
      [radius, half - width * 0.38, 0.01],
      [radius + flange * 0.35, half - width * 0.22, 0.012],
      [radius + flange, half - width * 0.08, 0.018],
      [radius + flange * 0.45, half, 0.014],
      [tyreInner, half, 0.01],
    ],
    { bevel: 0.012, segments: 2 }
  );
  add(latheProfile(tyreProfile, 40).rotateZ(Math.PI / 2), tyreMaterial);

  // --- centre: rim ring, hub, spokes --------------------------------------
  const rimProfile = roundedLoop(
    [
      [tyreInner - 0.08, -web / 2, 0.012],
      [tyreInner + 0.005, -half + 0.014, 0.012],
      [tyreInner + 0.005, half - 0.014, 0.012],
      [tyreInner - 0.08, web / 2, 0.012],
    ],
    { bevel: 0.01, segments: 2 }
  );
  add(latheProfile(rimProfile, 36).rotateZ(Math.PI / 2), centreMaterial);

  const hubProfile = roundedLoop(
    [
      [0.05, -hubWidth / 2, 0.008],
      [hubRadius, -hubWidth / 2 + 0.02, 0.018],
      [hubRadius * 0.8, -web * 0.9, 0.014],
      [hubRadius * 0.8, web * 0.9, 0.014],
      [hubRadius, hubWidth / 2 - 0.02, 0.018],
      [0.05, hubWidth / 2, 0.008],
    ],
    { bevel: 0.01, segments: 2 }
  );
  add(latheProfile(hubProfile, 28).rotateZ(Math.PI / 2), centreMaterial);

  const spokeInner = hubRadius * 0.88;
  const spokeOuter = tyreInner - 0.02;
  const spokeLen = spokeOuter - spokeInner;
  for (let i = 0; i < spokes; i++) {
    const a = (i / spokes) * Math.PI * 2 + Math.PI / spokes;
    // Tapered: fat at the hub, slim at the rim, built as two stacked sections.
    for (const [frac0, frac1, thick] of [
      [0.0, 0.45, 1.0],
      [0.45, 1.0, 0.8],
    ]) {
      const segLen = spokeLen * (frac1 - frac0);
      const segMid = spokeInner + spokeLen * ((frac0 + frac1) / 2);
      const g = beveledBox(web * 1.4 * thick, segLen * 1.06, radius * 0.12 * thick, {
        bevel: Math.min(0.016, web * 0.4 * thick),
        segments: 2,
      });
      place(g, { rot: [a, 0, 0], pos: [0, Math.cos(a) * segMid, Math.sin(a) * segMid] });
      add(g, centreMaterial);
    }
    // Boss where the spoke meets the rim.
    const fillet = beveledCylinder(radius * 0.09, radius * 0.12, web * 1.6, {
      bevel: 0.018,
      radialSegments: 12,
    }).rotateZ(Math.PI / 2);
    add(
      place(fillet, { pos: [0, Math.cos(a) * (spokeOuter - 0.02), Math.sin(a) * (spokeOuter - 0.02)] }),
      centreMaterial
    );
  }

  if (boltCircle) {
    for (let i = 0; i < spokes; i++) {
      const a = (i / spokes) * Math.PI * 2;
      const r = tyreInner + 0.025;
      add(
        place(rivetHead(radius * 0.03), {
          pos: [half + 0.004, Math.cos(a) * r, Math.sin(a) * r],
          dir: new THREE.Vector3(1, 0, 0),
        }),
        centreMaterial
      );
    }
  }

  // --- counterweight: the crescent opposite the crank pin ------------------
  if (counterweight) {
    const sector = annularSector(hubRadius + 0.16, tyreInner - 0.02, Math.PI * 1.27, Math.PI * 1.73, web * 2.2, {
      bevel: 0.016,
      divisions: 26,
    });
    // Authored in the XY plane extruded along +Z; rotate the plane into YZ.
    sector.rotateY(Math.PI / 2);
    add(sector, centreMaterial);
  }

  // --- crank pin (outboard, +X) -------------------------------------------
  // `pinStart`/`pinLength` are offsets from the wheel centre. The axle the
  // connecting rod drives needs a long pin (coupling rod *and* main rod *and*
  // return crank); the merely coupled axles get a short one, which is what
  // keeps the main rod from fouling them as it sweeps past.
  if (crankRadius > 0) {
    const boss = beveledCylinder(0.085, 0.1, 0.08, { bevel: 0.016, radialSegments: 18 }).rotateZ(Math.PI / 2);
    add(place(boss, { pos: [half + 0.035, crankRadius, 0] }), centreMaterial);
    const pin = beveledCylinder(0.05, 0.05, pinLength, { bevel: 0.012, radialSegments: 16 }).rotateZ(Math.PI / 2);
    add(place(pin, { pos: [pinStart + pinLength / 2, crankRadius, 0] }), pinMaterial);
    const collar = beveledCylinder(0.063, 0.063, 0.028, { bevel: 0.01, radialSegments: 16 }).rotateZ(Math.PI / 2);
    add(place(collar, { pos: [pinStart + pinLength + 0.015, crankRadius, 0] }), pinMaterial);
  }

  const out = new Map();
  for (const [material, list] of buckets) out.set(material, mergeAll(list));
  return out;
}

/**
 * A set of identical wheels sharing one spin angle, drawn as one instanced mesh
 * per material. `offsets` are `[x, z]` pairs in the vehicle frame; each wheel
 * centre sits `radius` above the rail head.
 */
export class WheelSet {
  constructor(parent, geometryByMaterial, offsets, { radius, name = 'wheels' } = {}) {
    this.radius = radius;
    this.offsets = offsets;
    this.layers = [];
    this.triangles = 0;
    this._matrix = new THREE.Matrix4();
    this._quat = new THREE.Quaternion();
    this._pos = new THREE.Vector3();
    this._scale = new THREE.Vector3(1, 1, 1);

    for (const [material, geometry] of geometryByMaterial) {
      const mesh = new THREE.InstancedMesh(geometry, material, offsets.length);
      mesh.name = name;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      // Wheels sit well outside the merged bounding sphere of the vehicle.
      mesh.frustumCulled = false;
      parent.add(mesh);
      this.layers.push(mesh);
      const tris = (geometry.index ? geometry.index.count : geometry.attributes.position.count) / 3;
      this.triangles += tris * offsets.length;
    }
    this.spin(0);
  }

  /** `angle` radians about +X. `lift` raises the set (unsprung travel). */
  spin(angle, lift = 0) {
    this._quat.setFromAxisAngle(AXIS_X, angle);
    for (let i = 0; i < this.offsets.length; i++) {
      this._pos.set(this.offsets[i][0], this.radius + lift, this.offsets[i][1]);
      this._matrix.compose(this._pos, this._quat, this._scale);
      for (const layer of this.layers) layer.setMatrixAt(i, this._matrix);
    }
    for (const layer of this.layers) layer.instanceMatrix.needsUpdate = true;
  }
}

/**
 * A left/right pair of wheel sets built from one master wheel.
 *
 * Real locomotives **quarter** the drive: the crank pins on the two sides are
 * pressed into the wheel centres 90 degrees apart, so the engine can always
 * start no matter where it stopped. The wheels themselves share an axle and so
 * share a rotation — the quartering lives in the geometry, not in the spin. So
 * the -X side gets the master wheel rotated by `quarter` before mirroring, and
 * the motion solver is handed `angle` for +X and `angle + quarter` for -X.
 */
export function makeWheelPair(parent, materials, opts) {
  const { offsetsPlusX = [], offsetsMinusX = [], quarter = 0, name = 'wheels', ...wheelOpts } = opts;
  const master = buildWheel(materials, wheelOpts);

  const plus = new Map();
  const minus = new Map();
  for (const [material, geometry] of master) {
    plus.set(material, geometry);
    const rotated = quarter ? geometry.clone().rotateX(quarter) : geometry;
    minus.set(material, mirrorX(rotated));
  }

  return {
    plusX: new WheelSet(parent, plus, offsetsPlusX, { radius: wheelOpts.radius, name: `${name}+x` }),
    minusX: new WheelSet(parent, minus, offsetsMinusX, { radius: wheelOpts.radius, name: `${name}-x` }),
    quarter,
  };
}
