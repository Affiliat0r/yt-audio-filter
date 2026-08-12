/**
 * Motion: coupling rods, connecting rod, crosshead and Walschaerts valve gear.
 *
 * Everything is a rigid link solved in closed form in the vehicle's ZY plane at
 * a fixed X, so no link ever changes length and nothing needs to be integrated
 * from the previous frame. Two closed-form solvers do all the work:
 *
 *   slider-crank    for the crosshead, valve spindle and piston
 *   circle-circle   for the expansion link and the combination lever
 *
 * The two sides run 90 degrees apart. That quartering is baked into the wheel
 * castings (see wheels.mjs) — here it just means the -X side is solved at
 * `angle + quarter`.
 */

import * as THREE from 'three';
import { beveledBox, beveledCylinder, mergeAll } from './deps.mjs';
import { place, annularSector } from './parts.mjs';
import { LOCO } from './spec.mjs';

const AXIS_X = new THREE.Vector3(1, 0, 0);
export const QUARTER = Math.PI / 2;

/* ------------------------------------------------------------------ solvers */

/**
 * Intersection of two circles in the ZY plane. Distances are clamped into the
 * reachable band first, so a link that is momentarily over- or under-extended
 * degrades to the closest reachable pose instead of producing NaN. `branch`
 * picks which of the two solutions to take and must stay constant for a given
 * joint or the mechanism will flip.
 */
function circleIntersect(c0z, c0y, r0, c1z, c1y, r1, branch, out) {
  const dz = c1z - c0z;
  const dy = c1y - c0y;
  const d = Math.hypot(dz, dy);
  if (d < 1e-9) {
    out.z = c0z + r0;
    out.y = c0y;
    return out;
  }
  const lo = Math.abs(r0 - r1) + 1e-5;
  const hi = r0 + r1 - 1e-5;
  const dc = Math.min(Math.max(d, lo), hi);
  const a = (dc * dc + r0 * r0 - r1 * r1) / (2 * dc);
  const h = Math.sqrt(Math.max(0, r0 * r0 - a * a));
  const uz = dz / d;
  const uy = dy / d;
  out.z = c0z + a * uz + branch * h * -uy;
  out.y = c0y + a * uy + branch * h * uz;
  return out;
}

/** Slider on a line of constant `y`, connected by a rod of length `L` to `(pz, py)`. */
function sliderZ(pz, py, y, L, sign = 1) {
  const dy = py - y;
  const r2 = L * L - dy * dy;
  return pz + sign * Math.sqrt(Math.max(1e-6, r2));
}

/* ---------------------------------------------------------------- geometry */

/**
 * A forged rod: I-section shank with a round boss at each end. Authored along
 * +Z, centred on the origin, `length` between boss centres.
 */
export function rodGeometry(length, opts = {}) {
  const {
    height = 0.17,
    thick = 0.075,
    bossR = 0.095,
    bossThick = thick * 1.9,
    taper = 0.82,
    oilCups = true,
  } = opts;
  const shank = Math.max(0.05, length - bossR * 1.4);
  const parts = [];

  // Web, tapered by stacking two sections.
  for (const [f0, f1, s] of [
    [0, 0.5, 1.0],
    [0.5, 1.0, taper],
  ]) {
    const segLen = shank * (f1 - f0);
    const segMid = -shank / 2 + shank * ((f0 + f1) / 2);
    parts.push(
      place(beveledBox(thick * 0.5, height * 0.6 * s, segLen * 1.02, { bevel: 0.01, segments: 2 }), {
        pos: [0, 0, segMid],
      })
    );
    for (const sy of [-1, 1]) {
      parts.push(
        place(beveledBox(thick, height * 0.2 * s, segLen * 1.02, { bevel: 0.012, segments: 2 }), {
          pos: [0, sy * height * 0.4 * s, segMid],
        })
      );
    }
  }

  for (const sz of [-1, 1]) {
    const r = sz > 0 ? bossR * taper : bossR;
    parts.push(
      place(beveledCylinder(r, r, bossThick, { bevel: 0.016, radialSegments: 20 }).rotateZ(Math.PI / 2), {
        pos: [0, 0, (sz * length) / 2],
      })
    );
    parts.push(
      place(beveledCylinder(r * 0.55, r * 0.55, bossThick * 1.35, { bevel: 0.01, radialSegments: 14 }).rotateZ(Math.PI / 2), {
        pos: [0, 0, (sz * length) / 2],
      })
    );
    if (oilCups) {
      parts.push(
        place(beveledCylinder(0.026, 0.032, 0.06, { bevel: 0.008, radialSegments: 12 }), {
          pos: [0, r * 0.85, (sz * length) / 2],
        })
      );
    }
  }
  return mergeAll(parts);
}

/** A plain flat link (expansion link arm, combination lever). */
function barGeometry(length, { width = 0.05, height = 0.1, bossR = 0.055 } = {}) {
  const parts = [
    place(beveledBox(width, height, Math.max(0.04, length - bossR), { bevel: 0.012, segments: 2 }), { pos: [0, 0, 0] }),
  ];
  for (const sz of [-1, 1]) {
    parts.push(
      place(beveledCylinder(bossR, bossR, width * 1.7, { bevel: 0.01, radialSegments: 14 }).rotateZ(Math.PI / 2), {
        pos: [0, 0, (sz * length) / 2],
      })
    );
  }
  return mergeAll(parts);
}

/**
 * A moving link: one mesh whose long axis is +Z, repositioned each frame from
 * its two end points. Because every link is rigid, the only per-frame work is a
 * midpoint and an `atan2`.
 */
class Link {
  constructor(parent, geometry, material, name) {
    this.mesh = new THREE.Mesh(geometry, material);
    this.mesh.name = name;
    this.mesh.castShadow = true;
    this.mesh.receiveShadow = true;
    this.mesh.matrixAutoUpdate = false;
    this.mesh.frustumCulled = false;
    parent.add(this.mesh);
    this._q = new THREE.Quaternion();
    this._p = new THREE.Vector3();
    this._s = new THREE.Vector3(1, 1, 1);
    this.triangles = (geometry.index ? geometry.index.count : geometry.attributes.position.count) / 3;
  }

  /** Aim the link from (az, ay) to (bz, by) in the plane x = `x`. */
  span(x, az, ay, bz, by) {
    // +Z rotated about +X by θ is (0, -sin θ, cos θ), so θ = atan2(-Δy, Δz).
    const angle = Math.atan2(-(by - ay), bz - az);
    this._q.setFromAxisAngle(AXIS_X, angle);
    this._p.set(x, (ay + by) / 2, (az + bz) / 2);
    this.mesh.matrix.compose(this._p, this._q, this._s);
    this.mesh.matrixWorldNeedsUpdate = true;
  }

  /** Place the link without rotating it (crossheads, pistons). */
  at(x, y, z, angle = 0) {
    this._q.setFromAxisAngle(AXIS_X, angle);
    this._p.set(x, y, z);
    this.mesh.matrix.compose(this._p, this._q, this._s);
    this.mesh.matrixWorldNeedsUpdate = true;
  }
}

/* ------------------------------------------------------------------ engine */

/**
 * Build the motion for one side.
 *
 * @param {THREE.Object3D} parent the sprung body group
 * @param {number} sideX +1 for the +X side, -1 for the -X side
 */
function buildSide(parent, materials, sideX, S) {
  const g = {};
  const steel = materials.steel;
  const dull = materials.steelDull;

  const axleZ = S.axleZ;
  const mainZ = axleZ[S.mainAxle];

  // Coupling rods: one per gap between coupled axles, each sharing the pin at
  // the axle they meet on. They translate on a circle without rotating.
  const couplingRods = [];
  for (let i = 0; i < axleZ.length - 1; i++) {
    const span = Math.abs(axleZ[i] - axleZ[i + 1]);
    couplingRods.push({
      link: new Link(parent, rodGeometry(span, { height: 0.16, thick: 0.07, bossR: 0.09, taper: 1 }), steel, `coupling-rod-${i}`),
      z0: axleZ[i],
      z1: axleZ[i + 1],
    });
  }
  g.couplingRods = couplingRods;

  g.mainRod = new Link(
    parent,
    rodGeometry(S.mainRodLength, { height: 0.2, thick: 0.082, bossR: 0.105, taper: 0.72 }),
    steel,
    'connecting-rod'
  );

  // Crosshead: a block with slippers riding the slide bars, plus the piston rod
  // heading forward into the cylinder.
  {
    const parts = [
      place(beveledBox(0.13, 0.26, 0.24, { bevel: 0.022, segments: 2 }), { pos: [0, 0, 0] }),
      place(beveledBox(0.17, 0.055, 0.34, { bevel: 0.014, segments: 2 }), { pos: [0, 0.16, 0] }),
      place(beveledBox(0.17, 0.055, 0.34, { bevel: 0.014, segments: 2 }), { pos: [0, -0.16, 0] }),
      place(beveledCylinder(0.055, 0.055, 0.14, { bevel: 0.012, radialSegments: 16 }).rotateZ(Math.PI / 2), {
        pos: [0.08, 0, 0],
      }),
    ];
    g.crosshead = new Link(parent, mergeAll(parts), steel, 'crosshead');
  }
  g.pistonRod = new Link(
    parent,
    beveledCylinder(0.05, 0.05, S.pistonRodLength, { bevel: 0.012, radialSegments: 18 }).rotateX(Math.PI / 2),
    steel,
    'piston-rod'
  );

  // --- Walschaerts ---------------------------------------------------------
  // Link lengths are derived from the same constants the solver uses, so a
  // link can never be drawn at a length its joints do not actually hold.
  const returnCrankLength = Math.hypot(
    S.returnCrankRadius * Math.sin(S.returnCrankPhase) - 0,
    S.returnCrankRadius * Math.cos(S.returnCrankPhase) - S.crankRadius
  );
  const unionLinkLength = Math.hypot(S.unionPinDZ - S.crossLugDZ, S.unionPinDY - S.crossLugDY);

  g.returnCrank = new Link(parent, barGeometry(returnCrankLength, { width: 0.052, height: 0.12, bossR: 0.06 }), dull, 'return-crank');
  g.eccRod = new Link(parent, rodGeometry(S.eccRodLength, { height: 0.1, thick: 0.05, bossR: 0.062, taper: 0.9, oilCups: false }), steel, 'eccentric-rod');

  // Expansion link: a *slotted* curved arm rocking about its trunnion. Two thin
  // concentric arcs with the die-block slot between them. A solid blade here
  // reads as a shark fin and gives the whole mechanism away.
  {
    const parts = [];
    const r = S.linkArmLower;
    const span = 0.55;
    const half = Math.PI * 1.5;
    // Two smooth annular sectors with the die slot between them. Built as flat
    // extruded arcs rather than stacked boxes so the edge is a clean curve.
    for (const [rIn, rOut] of [
      [r * 0.86, r * 1.06],
      [r * 0.5, r * 0.7],
    ]) {
      const arc = annularSector(rIn, rOut, half - span, half + span, 0.045, { bevel: 0.012, divisions: 22 });
      arc.rotateY(Math.PI / 2);
      parts.push(arc);
    }
    // Close the slot at both ends.
    for (const s of [-1, 1]) {
      const a = s * span;
      parts.push(
        place(beveledBox(0.045, r * 0.38, 0.05, { bevel: 0.011 }), {
          rot: [-a, 0, 0],
          pos: [0, -Math.cos(a) * r * 0.78, Math.sin(a) * r * 0.78],
        })
      );
    }
    parts.push(place(beveledCylinder(0.058, 0.058, 0.082, { bevel: 0.014, radialSegments: 16 }).rotateZ(Math.PI / 2), { pos: [0, 0, 0] }));
    parts.push(place(beveledCylinder(0.048, 0.048, 0.07, { bevel: 0.012, radialSegments: 14 }).rotateZ(Math.PI / 2), { pos: [0, -r, 0] }));
    g.expansionLink = new Link(parent, mergeAll(parts), dull, 'expansion-link');
  }

  g.radiusRod = new Link(parent, rodGeometry(S.radiusRodLength, { height: 0.09, thick: 0.045, bossR: 0.056, taper: 0.95, oilCups: false }), steel, 'radius-rod');
  g.combLever = new Link(parent, barGeometry(S.combLeverLength, { width: 0.05, height: 0.1, bossR: 0.052 }), steel, 'combination-lever');
  g.unionLink = new Link(parent, barGeometry(unionLinkLength, { width: 0.042, height: 0.075, bossR: 0.045 }), steel, 'union-link');
  g.valveRod = new Link(parent, rodGeometry(S.valveRodLength, { height: 0.07, thick: 0.04, bossR: 0.048, taper: 1, oilCups: false }), steel, 'valve-rod');

  g.x = sideX;
  g.mainZ = mainZ;
  return g;
}

/**
 * The complete motion for both sides.
 *
 * `update(angle, lift)` takes the wheel rotation about +X and the vertical
 * offset of the sprung frame relative to the axles, so that when the body
 * bounces on its springs the rods stay exactly on their crank pins.
 */
export function createMotion(parent, materials, spec = LOCO) {
  const S = spec;
  const plus = buildSide(parent, materials, 1, S);
  const minus = buildSide(parent, materials, -1, S);
  const sides = [plus, minus];

  const p = { z: 0, y: 0 };
  const die = { z: 0, y: 0 };
  const combTop = { z: 0, y: 0 };

  let triangles = 0;
  for (const side of sides) {
    for (const value of Object.values(side)) {
      if (value instanceof Link) triangles += value.triangles;
      else if (Array.isArray(value)) for (const entry of value) triangles += entry.link.triangles;
    }
  }

  function solveSide(side, angle, lift) {
    const x = side.x;
    const R = S.driverRadius - lift; // axle height seen from the sprung frame
    const sin = Math.sin(angle);
    const cos = Math.cos(angle);
    const rc = S.crankRadius;

    // Coupling rods: every pin shares the crank angle, so the rod translates.
    const pinY = R + cos * rc;
    for (const rod of side.couplingRods) {
      rod.link.span(x * S.couplingRodX, rod.z0 + sin * rc, pinY, rod.z1 + sin * rc, pinY);
    }

    // Connecting rod and crosshead.
    const mainPinZ = side.mainZ + sin * rc;
    const crossZ = sliderZ(mainPinZ, pinY, S.cylY, S.mainRodLength, 1);
    side.mainRod.span(x * S.mainRodX, mainPinZ, pinY, crossZ, S.cylY);
    side.crosshead.at(x * S.mainRodX, S.cylY, crossZ);
    side.pistonRod.at(x * S.mainRodX, S.cylY, crossZ + S.pistonRodLength / 2 + 0.09);

    // Return crank on the main pin, then the eccentric rod down to the
    // expansion link's lower end.
    const ecAngle = angle + S.returnCrankPhase;
    const ecZ = side.mainZ + Math.sin(ecAngle) * S.returnCrankRadius;
    const ecY = R + Math.cos(ecAngle) * S.returnCrankRadius;
    side.returnCrank.span(x * S.valveX, mainPinZ, pinY, ecZ, ecY);

    circleIntersect(S.linkPivotZ, S.linkPivotY - lift, S.linkArmLower, ecZ, ecY, S.eccRodLength, -1, p);
    side.eccRod.span(x * S.valveX, ecZ, ecY, p.z, p.y);

    // The link rocks about its trunnion; the die block rides the opposite arm.
    const pivotY = S.linkPivotY - lift;
    const lambda = Math.atan2(p.z - S.linkPivotZ, -(p.y - pivotY));
    side.expansionLink.at(x * S.valveX, pivotY, S.linkPivotZ, -lambda);
    die.z = S.linkPivotZ - Math.sin(lambda) * S.linkArmDie;
    die.y = pivotY + Math.cos(lambda) * S.linkArmDie;

    // Combination lever: bottom pin driven by the crosshead through the union
    // link, top pin held by the radius rod.
    const unionZ = crossZ + S.unionPinDZ;
    const unionY = S.cylY + S.unionPinDY;
    circleIntersect(unionZ, unionY, S.combLeverLength, die.z, die.y, S.radiusRodLength, -1, combTop);
    side.combLever.span(x * S.valveX, unionZ, unionY, combTop.z, combTop.y);
    side.radiusRod.span(x * S.valveX, die.z, die.y, combTop.z, combTop.y);
    side.unionLink.span(x * S.valveX, crossZ + S.crossLugDZ, S.cylY + S.crossLugDY, unionZ, unionY);

    // Valve rod: picked up part way along the lever, sliding into the chest.
    const f = S.combLeverValveFrac;
    const fz = unionZ + (combTop.z - unionZ) * f;
    const fy = unionY + (combTop.y - unionY) * f;
    const spindleZ = sliderZ(fz, fy, S.valveY - lift, S.valveRodLength, 1);
    side.valveRod.span(x * S.valveX, fz, fy, spindleZ, S.valveY - lift);
  }

  return {
    triangles,
    /**
     * @param {number} angle wheel rotation about +X (= -distance / radius)
     * @param {number} lift  sprung-frame rise above its static height
     */
    update(angle, lift = 0) {
      solveSide(plus, angle, lift);
      solveSide(minus, angle + QUARTER, lift);
    },
  };
}
