/**
 * The locomotive: an 0-6-0 saddle tank.
 *
 * Two nodes per vehicle:
 *   `node`   — placed on the spline from its two outer axle points.
 *   `sprung` — everything the springs carry: body, cab, cylinders, motion.
 * The wheels hang off `node` so they stay welded to the rail while the body
 * bounces, and the motion solver is told how far the frame has risen so the
 * rods stay exactly on their crank pins through the bounce.
 */

import * as THREE from 'three';
import { beveledBox, beveledCylinder, latheProfile, extrudeShape, rivetHead, tubeAlong, mergeAll } from './deps.mjs';
import {
  Parts,
  place,
  cylZ,
  roundedLoop,
  rivetRun,
  rivetRing,
  handrail,
  buffer,
  couplingHook,
  lamp,
  leafSpring,
} from './parts.mjs';
import { makeWheelPair } from './wheels.mjs';
import { createMotion, QUARTER } from './motion.mjs';
import { LOCO } from './spec.mjs';
import { makeWeathering } from './weathering.mjs';

const S = LOCO;

/* ------------------------------------------------------------ shape makers */

/** Rounded rectangle as a list of Vector2, for Shape outlines and holes. */
function roundedRect(x0, y0, x1, y1, r, segments = 4) {
  const pts = [];
  const corners = [
    [x1 - r, y0 + r, -Math.PI / 2, 0],
    [x1 - r, y1 - r, 0, Math.PI / 2],
    [x0 + r, y1 - r, Math.PI / 2, Math.PI],
    [x0 + r, y0 + r, Math.PI, Math.PI * 1.5],
  ];
  for (const [cx, cy, a0, a1] of corners) {
    for (let i = 0; i <= segments; i++) {
      const a = a0 + ((a1 - a0) * i) / segments;
      pts.push(new THREE.Vector2(cx + Math.cos(a) * r, cy + Math.sin(a) * r));
    }
  }
  return pts;
}

/** Saddle-tank cross-section: a shoulder-topped box with the boiler cut out. */
function saddleTankShape() {
  const hw = S.tankHalfWidth;
  const shoulder = S.tankTopY - 0.46;
  const pts = [new THREE.Vector2(-hw, S.tankSideY), new THREE.Vector2(-hw, shoulder)];
  // Circular crown across the top.
  const crownR = 1.28;
  const crownY = S.tankTopY - crownR;
  const edgeA = Math.atan2(shoulder - crownY, -hw);
  const edgeB = Math.atan2(shoulder - crownY, hw);
  for (let i = 0; i <= 26; i++) {
    const a = edgeA + ((edgeB - edgeA) * i) / 26;
    pts.push(new THREE.Vector2(Math.cos(a) * crownR, crownY + Math.sin(a) * crownR));
  }
  pts.push(new THREE.Vector2(hw, shoulder), new THREE.Vector2(hw, S.tankSideY));

  // Underside: the boiler tunnel.
  const clear = S.boilerR + 0.03;
  const dy = S.tankSideY - S.boilerY;
  const bx = Math.sqrt(Math.max(0.0004, clear * clear - dy * dy));
  pts.push(new THREE.Vector2(bx, S.tankSideY));
  const t1 = Math.atan2(dy, bx);
  const t2 = Math.atan2(dy, -bx) + Math.PI * 2;
  for (let i = 0; i <= 40; i++) {
    const a = t1 + ((t2 - t1) * i) / 40;
    pts.push(new THREE.Vector2(Math.cos(a) * clear, S.boilerY + Math.sin(a) * clear));
  }
  pts.push(new THREE.Vector2(-bx, S.tankSideY));
  return new THREE.Shape(roundedLoop(pts, { bevel: 0.05, segments: 2 }));
}

const CAB_SIDE_TOP = S.cabRoofY - 0.22;

/** Cab side sheet, in (z, y), with a window opening and the crew cutaway. */
function cabSideShape() {
  const outline = roundedLoop(
    [
      [S.cabFrontZ, S.cabFloorY, 0.03],
      [S.cabFrontZ, CAB_SIDE_TOP - 0.16, 0.2],
      [S.cabFrontZ - 0.2, CAB_SIDE_TOP, 0.06],
      [S.cabBackZ, CAB_SIDE_TOP, 0.08],
      [S.cabBackZ, 1.98, 0.12],
      [S.cabBackZ + 0.24, 1.72, 0.2],
      [S.cabBackZ + 0.5, S.cabFloorY, 0.07],
    ],
    { bevel: 0.05, segments: 2 }
  );
  const shape = new THREE.Shape(outline);
  shape.holes.push(new THREE.Path(roundedRect(-2.56, 2.04, -1.68, 2.66, 0.12, 5)));
  return shape;
}

/** Cab front sheet, in (x, y): notched over the firebox, two spectacle lights. */
function cabFrontShape() {
  const hw = S.cabHalfWidth;
  const fw = S.fireboxHalfWidth + 0.03;
  const notchTop = 2.5;
  const shape = new THREE.Shape(
    roundedLoop(
      [
        [-hw, S.cabFloorY, 0.02],
        [-fw, S.cabFloorY, 0.02],
        [-fw, notchTop, 0.06],
        [fw, notchTop, 0.06],
        [fw, S.cabFloorY, 0.02],
        [hw, S.cabFloorY, 0.02],
        [hw, S.cabRoofY - 0.06, 0.12],
        [-hw, S.cabRoofY - 0.06, 0.12],
      ],
      { bevel: 0.04, segments: 2 }
    )
  );
  for (const sx of [-1, 1]) {
    const hole = [];
    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * Math.PI * 2;
      hole.push(new THREE.Vector2(sx * 0.6 + Math.cos(a) * 0.19, 2.74 + Math.sin(a) * 0.19));
    }
    shape.holes.push(new THREE.Path(hole));
  }
  return shape;
}

/** Locomotive frame plate, in (z, y), with a horn gap under each axle. */
function framePlateShape() {
  const pts = [[S.frameZ0, S.frameY1], [S.frameZ1, S.frameY1], [S.frameZ1, S.frameY0]];
  const sorted = [...S.axleZ].sort((a, b) => b - a);
  for (const z of sorted) {
    pts.push([z + 0.24, S.frameY0], [z + 0.2, 0.58], [z - 0.2, 0.58], [z - 0.24, S.frameY0]);
  }
  pts.push([S.frameZ0, S.frameY0]);
  return new THREE.Shape(roundedLoop(pts.map((p) => [p[0], p[1], 0.03]), { bevel: 0.03, segments: 1 }));
}

/**
 * A lined panel on a flat flank: a broad straw band with a fine black line
 * inside it. Lining is the cheapest possible way to stop a large painted panel
 * reading as a slab, and every railway in the world used it for exactly that
 * reason.
 */
function linedPanel(p, M, { x, z0, z1, y0, y1, band = 0.055, gap = 0.085 }) {
  for (const [inset, mat, wide] of [
    [0, M.lining, band],
    [gap, M.black, band * 0.47],
  ]) {
    const a0 = z0 + inset;
    const a1 = z1 - inset;
    const b0 = y0 + inset;
    const b1 = y1 - inset;
    if (a1 <= a0 || b1 <= b0) continue;
    for (const [cz, cy, len, hgt] of [
      [(a0 + a1) / 2, b0, a1 - a0, wide],
      [(a0 + a1) / 2, b1, a1 - a0, wide],
      [a0, (b0 + b1) / 2, wide, b1 - b0],
      [a1, (b0 + b1) / 2, wide, b1 - b0],
    ]) {
      p.add(place(beveledBox(0.012, hgt, len, { bevel: 0.004, segments: 1 }), { pos: [x, cy, cz] }), mat);
    }
  }
}

/* --------------------------------------------------------------- the model */

export function buildLocomotive(rng, materials) {
  const node = new THREE.Group();
  node.name = 'locomotive';
  node.matrixAutoUpdate = false;

  const sprung = new THREE.Group();
  sprung.name = 'locomotive-body';
  node.add(sprung);

  const p = new Parts();
  const M = materials;

  /* --- frames, buffer beams, running plate ------------------------------ */

  for (const sx of [-1, 1]) {
    const plate = extrudeShape(framePlateShape(), 0.07, { bevel: 0.014, curveSegments: 6 });
    plate.rotateY(-Math.PI / 2);
    p.add(place(plate, { pos: [sx * S.frameX, 0, 0] }), M.iron);
  }
  // Frame stretchers.
  for (const z of [-2.6, -0.68, 0.68, 2.4, 3.3]) {
    p.add(place(beveledBox(S.frameX * 2, 0.34, 0.09, { bevel: 0.018 }), { pos: [0, 0.52, z] }), M.iron);
  }
  // Axles.
  for (const z of S.axleZ) {
    p.add(
      place(beveledCylinder(0.1, 0.1, S.halfGauge * 2 - 0.06, { bevel: 0.012, radialSegments: 16 }).rotateZ(Math.PI / 2), {
        pos: [0, S.driverRadius, z],
      }),
      M.steelDull
    );
  }

  // Running plate with a valance below the edge.
  p.add(place(beveledBox(S.plateX * 2, 0.055, 6.85, { bevel: 0.02, segments: 2 }), { pos: [0, S.plateY, 0.2] }), M.black);
  for (const sx of [-1, 1]) {
    p.add(
      place(beveledBox(0.038, 0.1, 6.85, { bevel: 0.014, segments: 2 }), { pos: [sx * (S.plateX - 0.02), S.plateY - 0.068, 0.2] }),
      M.black
    );
    // Angle brackets under the plate edge — they read as structure rather than
    // as a floating slab, and catch a rim light along the whole side.
    for (let i = 0; i < 9; i++) {
      const z = -2.9 + i * 0.72;
      p.add(place(beveledBox(0.5, 0.075, 0.06, { bevel: 0.012 }), { pos: [sx * 0.9, S.plateY - 0.06, z] }), M.iron);
    }
    // Rivets down the valance. The running plate is the longest single line on
    // the model; unbroken it reads as a strip of black card.
    rivetRun(p, M.black, [sx * (S.plateX - 0.004), S.plateY - 0.062, -3.15], [sx * (S.plateX - 0.004), S.plateY - 0.062, 3.5], 34, {
      radius: 0.013,
      normal: [sx, 0, 0],
    });
  }

  // Buffer beams, buffers, hooks.
  for (const [z, facing] of [
    [S.bufferBeamZ, 1],
    [-S.bufferBeamZ + 0.08, -1],
  ]) {
    p.add(place(beveledBox(2.26, 0.66, 0.12, { bevel: 0.024, segments: 2 }), { pos: [0, S.bufferY, z] }), M.crimsonDeep);
    p.add(place(beveledBox(2.3, 0.07, 0.16, { bevel: 0.02 }), { pos: [0, S.bufferY + 0.3, z] }), M.black);
    for (const sx of [-1, 1]) {
      buffer(p, { bodyMaterial: M.black, headMaterial: M.steel }, {
        x: sx * 0.78,
        y: S.bufferY,
        z: z + facing * 0.06,
        length: 0.34,
        facing,
        radius: 0.14,
      });
    }
    couplingHook(p, M.steelDull, { y: S.bufferY - 0.06, z: z + facing * 0.06, facing });
    rivetRun(p, M.black, [-1.02, S.bufferY + 0.22, z - facing * 0.062], [1.02, S.bufferY + 0.22, z - facing * 0.062], 21, {
      radius: 0.013,
      normal: [0, 0, -facing],
    });
  }

  /* --- boiler, smokebox, chimney ---------------------------------------- */

  p.add(
    place(cylZ(S.boilerR, S.boilerR * 0.985, S.boilerZ1 - S.boilerZ0, { bevel: 0.03, radialSegments: 48, openEnded: true }), {
      pos: [0, S.boilerY, (S.boilerZ0 + S.boilerZ1) / 2],
    }),
    M.crimson
  );
  // Boiler bands.
  for (const z of [-0.55, 0.5, 1.6, 2.3]) {
    p.add(place(cylZ(S.boilerR + 0.016, S.boilerR + 0.016, 0.1, { bevel: 0.016, radialSegments: 48 }), { pos: [0, S.boilerY, z] }), M.brass);
  }

  // Smokebox: matt graphite, wrapper rivets at each ring.
  p.add(
    place(cylZ(S.smokeboxR, S.smokeboxR, S.smokeboxZ1 - S.smokeboxZ0, { bevel: 0.03, radialSegments: 48, openEnded: true }), {
      pos: [0, S.boilerY, (S.smokeboxZ0 + S.smokeboxZ1) / 2],
    }),
    M.graphite
  );
  for (const z of [S.smokeboxZ0 + 0.07, S.smokeboxZ1 - 0.09]) {
    rivetRing(p, M.graphite, { z, radius: S.smokeboxR + 0.004, centreY: S.boilerY, count: 54, rivet: 0.017 });
  }
  // Smokebox door: a dished disc with a beading ring, hinge straps and dart.
  {
    // Dished door: a shallow dome, not a cone. The subtle double curvature is
    // what makes the front end read as pressed steel.
    const R = S.smokeboxR;
    const door = latheProfile(
      roundedLoop(
        [
          [0, -0.06, 0],
          [R - 0.02, -0.06, 0.05],
          [R - 0.03, 0.005, 0.03],
          [R * 0.88, 0.055, 0.06],
          [R * 0.62, 0.115, 0.1],
          [R * 0.3, 0.155, 0.1],
          [0, 0.168, 0],
        ],
        { bevel: 0.03, segments: 3 }
      ),
      48
    ).rotateX(Math.PI / 2);
    p.add(place(door, { pos: [0, S.boilerY, S.smokeboxZ1 - 0.02] }), M.graphite);
    p.add(
      place(cylZ(S.smokeboxR + 0.01, S.smokeboxR + 0.01, 0.055, { bevel: 0.018, radialSegments: 48 }), {
        pos: [0, S.boilerY, S.smokeboxZ1 - 0.01],
      }),
      M.steelDull
    );
    // Dart: central boss with two handles.
    p.add(place(cylZ(0.075, 0.075, 0.1, { bevel: 0.018, radialSegments: 18 }), { pos: [0, S.boilerY, S.smokeboxZ1 + 0.13] }), M.brass);
    for (const rot of [0.5, 0.5 + Math.PI / 2]) {
      p.add(
        place(beveledBox(0.34, 0.05, 0.045, { bevel: 0.014 }), { rot: [0, 0, rot], pos: [0, S.boilerY, S.smokeboxZ1 + 0.18] }),
        M.brass
      );
    }
    // Hinge straps.
    for (const sy of [-1, 1]) {
      p.add(
        place(beveledBox(0.09, 0.07, 0.34, { bevel: 0.016 }), {
          rot: [0, 0, 0],
          pos: [-S.smokeboxR * 0.86, S.boilerY + sy * 0.34, S.smokeboxZ1 - 0.05],
        }),
        M.steelDull
      );
    }
  }
  // Smokebox saddle down to the frames.
  p.add(place(beveledBox(1.62, 0.6, 0.94, { bevel: 0.04, segments: 2 }), { pos: [0, 1.14, 2.98] }), M.graphite);

  // Chimney: a hollow lathe with a flared cap, plus a copper cap band.
  {
    const base = S.chimneyBaseY;
    const top = S.chimneyMouthY;
    const outer = [
      [0.4, base],
      [0.335, base + 0.1],
      [0.245, base + 0.22],
      [0.223, top - 0.24],
      [0.262, top - 0.12],
      [0.318, top - 0.015],
      [0.318, top],
      [0.252, top],
      [0.24, top - 0.12],
      [0.2, top - 0.3],
      [0.2, base],
    ];
    p.add(place(latheProfile(roundedLoop(outer, { bevel: 0.022, segments: 2 }), 40), { pos: [0, 0, S.chimneyZ] }), M.graphite);
    p.add(
      place(beveledCylinder(0.328, 0.318, 0.075, { bevel: 0.022, radialSegments: 40 }), { pos: [0, top - 0.055, S.chimneyZ] }),
      M.copper
    );
  }

  /* --- saddle tank ------------------------------------------------------- */

  {
    const tank = extrudeShape(saddleTankShape(), S.tankZ1 - S.tankZ0, { bevel: 0.028, curveSegments: 6 });
    p.add(place(tank, { pos: [0, 0, (S.tankZ0 + S.tankZ1) / 2] }), M.crimson);

    // Lining and plating. The flank of a saddle tank is the largest flat
    // surface on the whole engine; left plain it reads as a slab, so it gets
    // the full treatment: a lined panel, riveted seam straps where the plates
    // butt, and a brass nameplate.
    for (const sx of [-1, 1]) {
      const x = sx * (S.tankHalfWidth + 0.006);
      const z0 = S.tankZ0 + 0.3;
      const z1 = S.tankZ1 - 0.3;
      const y0 = S.tankSideY + 0.15;
      const y1 = S.tankTopY - 0.5;
      linedPanel(p, M, { x, z0, z1, y0, y1 });
      // Brass nameplate on the panel centre, with a raised bead round it.
      p.add(place(beveledBox(0.022, 0.24, 1.3, { bevel: 0.012, segments: 2 }), { pos: [x + sx * 0.01, (y0 + y1) / 2, 0.72] }), M.brass);
      p.add(place(beveledBox(0.016, 0.15, 1.2, { bevel: 0.01, segments: 2 }), { pos: [x + sx * 0.024, (y0 + y1) / 2, 0.72] }), M.crimsonDeep);

      // Riveted seam straps where the tank plates butt.
      for (const z of [S.tankZ0 + 0.24, 0.5, S.tankZ1 - 0.24]) {
        p.add(place(beveledBox(0.014, S.tankTopY - S.tankSideY - 0.42, 0.1, { bevel: 0.005, segments: 1 }), { pos: [x, (S.tankSideY + S.tankTopY) / 2 - 0.19, z] }), M.crimson);
        for (const dz of [-0.03, 0.03]) {
          rivetRun(p, M.crimson, [x + sx * 0.01, S.tankSideY + 0.12, z + dz], [x + sx * 0.01, S.tankTopY - 0.46, z + dz], 10, {
            radius: 0.011,
            normal: [sx, 0, 0],
          });
        }
      }
      // Bottom flange rivets along the whole flank.
      rivetRun(p, M.crimson, [x, S.tankSideY + 0.062, S.tankZ0 + 0.1], [x, S.tankSideY + 0.062, S.tankZ1 - 0.1], 42, {
        radius: 0.011,
        normal: [sx, 0, 0],
      });
      // Lifting eyes on the tank top edge.
      for (const z of [S.tankZ0 + 0.3, S.tankZ1 - 0.3]) {
        p.add(
          place(new THREE.TorusGeometry(0.055, 0.018, 6, 14), { rot: [0, Math.PI / 2, 0], pos: [sx * 0.72, S.tankTopY - 0.06, z] }),
          M.steelDull
        );
      }
    }
    // Cross seams over the crown.
    for (const z of [S.tankZ0 + 0.06, (S.tankZ0 + S.tankZ1) / 2, S.tankZ1 - 0.06]) {
      for (let i = 0; i <= 22; i++) {
        const f = i / 22;
        const x = -S.tankHalfWidth + f * S.tankHalfWidth * 2;
        const crownR = 1.28;
        const crownY = S.tankTopY - crownR;
        const yy = crownY + Math.sqrt(Math.max(0, crownR * crownR - x * x));
        if (yy < S.tankTopY - 0.47) continue;
        p.add(
          place(rivetHead(0.012), { pos: [x, yy + 0.003, z], dir: new THREE.Vector3(x / crownR, (yy - crownY) / crownR, 0) }),
          M.crimson
        );
      }
    }
    // Filler cap.
    p.add(place(beveledCylinder(0.15, 0.17, 0.07, { bevel: 0.02, radialSegments: 20 }), { pos: [0, S.tankTopY + 0.01, 1.5] }), M.brass);
    p.add(place(beveledCylinder(0.055, 0.055, 0.05, { bevel: 0.014, radialSegments: 14 }), { pos: [0.07, S.tankTopY + 0.06, 1.5] }), M.brass);
  }

  /* --- firebox, dome, safety valves, whistle ---------------------------- */

  {
    // Belpaire-ish firebox: square shouldered, with generous top radii.
    const fb = extrudeShape(
      new THREE.Shape(
        roundedLoop(
          [
            [-S.fireboxHalfWidth, 1.32, 0.06],
            [S.fireboxHalfWidth, 1.32, 0.06],
            [S.fireboxHalfWidth, S.fireboxTopY, 0.26],
            [-S.fireboxHalfWidth, S.fireboxTopY, 0.26],
          ],
          { bevel: 0.05, segments: 3 }
        )
      ),
      S.fireboxZ1 - S.fireboxZ0,
      { bevel: 0.03, curveSegments: 6 }
    );
    p.add(place(fb, { pos: [0, 0, (S.fireboxZ0 + S.fireboxZ1) / 2] }), M.crimson);
    for (const sx of [-1, 1]) {
      rivetRun(p, M.crimson, [sx * (S.fireboxHalfWidth + 0.004), 1.45, S.fireboxZ0 + 0.1], [sx * (S.fireboxHalfWidth + 0.004), 1.45, S.fireboxZ1 - 0.1], 16, {
        radius: 0.012,
        normal: [sx, 0, 0],
      });
    }

    // Safety-valve cover and whistle on the firebox top.
    const dome = latheProfile(
      roundedLoop(
        [
          [0.3, 0, 0.03],
          [0.27, 0.12, 0.05],
          [0.19, 0.3, 0.07],
          [0, 0.36, 0],
          [0, 0, 0],
        ],
        { bevel: 0.03, segments: 3 }
      ),
      30
    );
    p.add(place(dome, { pos: [0, S.fireboxTopY - 0.03, S.domeZ] }), M.brass);
    for (const sx of [-1, 1]) {
      p.add(
        place(beveledCylinder(0.045, 0.05, 0.16, { bevel: 0.014, radialSegments: 14 }), { pos: [sx * 0.1, S.fireboxTopY + 0.3, S.domeZ] }),
        M.brass
      );
    }
    // Whistle.
    p.add(place(beveledCylinder(0.055, 0.06, 0.3, { bevel: 0.016, radialSegments: 16 }), { pos: [0.3, S.fireboxTopY + 0.16, -1.62] }), M.brass);
    p.add(place(beveledCylinder(0.08, 0.07, 0.05, { bevel: 0.016, radialSegments: 16 }), { pos: [0.3, S.fireboxTopY + 0.33, -1.62] }), M.brass);
  }

  /* --- cab --------------------------------------------------------------- */

  {
    for (const sx of [-1, 1]) {
      const sheet = extrudeShape(cabSideShape(), 0.055, { bevel: 0.014, curveSegments: 6 });
      sheet.rotateY(-Math.PI / 2);
      p.add(place(sheet, { pos: [sx * S.cabHalfWidth, 0, 0] }), M.crimson);
      // Beading round the window.
      for (const [z, y, w, h] of [
        [-2.12, 2.68, 0.9, 0.035],
        [-2.12, 2.02, 0.9, 0.035],
        [-2.58, 2.35, 0.035, 0.68],
        [-1.66, 2.35, 0.035, 0.68],
      ]) {
        p.add(place(beveledBox(0.014, h, w, { bevel: 0.006 }), { pos: [sx * (S.cabHalfWidth + 0.034), y, z] }), M.gold);
      }
      // Lined panel below the cab window, matching the tank.
      linedPanel(p, M, {
        x: sx * (S.cabHalfWidth + 0.034),
        z0: S.cabBackZ + 0.58,
        z1: S.cabFrontZ - 0.12,
        y0: 1.44,
        y1: 1.92,
        band: 0.045,
        gap: 0.07,
      });
      // Grab handle by the cab entrance.
      p.add(
        tubeAlong(
          [
            new THREE.Vector3(sx * (S.cabHalfWidth + 0.075), 1.55, S.cabBackZ + 0.16),
            new THREE.Vector3(sx * (S.cabHalfWidth + 0.075), 2.05, S.cabBackZ + 0.1),
          ],
          0.022,
          { radialSegments: 8 }
        ),
        M.brass
      );
    }

    const front = extrudeShape(cabFrontShape(), 0.06, { bevel: 0.014, curveSegments: 6 });
    p.add(place(front, { pos: [0, 0, S.cabFrontZ] }), M.crimson);
    for (const sx of [-1, 1]) {
      p.add(
        place(cylZ(0.21, 0.21, 0.035, { bevel: 0.012, radialSegments: 26 }), { pos: [sx * 0.6, 2.74, S.cabFrontZ - 0.02] }),
        M.brass
      );
      p.add(place(cylZ(0.185, 0.185, 0.02, { bevel: 0.008, radialSegments: 26 }), { pos: [sx * 0.6, 2.74, S.cabFrontZ - 0.02] }), M.glass);
    }

    // Roof: a shallow arc plate that overhangs the sheets on all four sides.
    {
      const arc = [];
      const rr = 3.6;
      const cy = S.cabRoofY - rr;
      const halfSpan = S.cabHalfWidth + 0.13;
      const outer = [];
      const inner = [];
      for (let i = 0; i <= 24; i++) {
        const x = -halfSpan + (2 * halfSpan * i) / 24;
        const y = cy + Math.sqrt(rr * rr - x * x);
        outer.push(new THREE.Vector2(x, y));
        inner.push(new THREE.Vector2(x * 0.99, y - 0.09));
      }
      arc.push(...outer, ...inner.reverse());
      const roofLen = Math.abs(S.cabBackZ - S.cabFrontZ) + 0.2;
      const roof = extrudeShape(new THREE.Shape(arc), roofLen, { bevel: 0.022, curveSegments: 4 });
      p.add(place(roof, { pos: [0, 0, (S.cabFrontZ + S.cabBackZ) / 2] }), M.black);
      // Rain strips along the eaves.
      for (const sx of [-1, 1]) {
        p.add(
          place(beveledBox(0.05, 0.05, roofLen - 0.06, { bevel: 0.014 }), {
            pos: [sx * (S.cabHalfWidth + 0.09), cy + Math.sqrt(rr * rr - (S.cabHalfWidth + 0.09) ** 2) + 0.02, (S.cabFrontZ + S.cabBackZ) / 2],
          }),
          M.black
        );
      }
      // Roof vent.
      p.add(place(beveledBox(0.36, 0.08, 0.52, { bevel: 0.022 }), { pos: [0, S.cabRoofY + 0.05, -2.3] }), M.black);
    }

    // Rear spectacle plate above the bunker, so the cab is a room rather than a
    // hole you can see the next vehicle through.
    {
      const rear = new THREE.Shape(
        roundedLoop(
          [
            [-S.cabHalfWidth, 1.98, 0.05],
            [S.cabHalfWidth, 1.98, 0.05],
            [S.cabHalfWidth, CAB_SIDE_TOP, 0.1],
            [-S.cabHalfWidth, CAB_SIDE_TOP, 0.1],
          ],
          { bevel: 0.045, segments: 2 }
        )
      );
      for (const sx of [-1, 1]) {
        const hole = [];
        for (let i = 0; i < 22; i++) {
          const a = (i / 22) * Math.PI * 2;
          hole.push(new THREE.Vector2(sx * 0.56 + Math.cos(a) * 0.17, 2.4 + Math.sin(a) * 0.17));
        }
        rear.holes.push(new THREE.Path(hole));
      }
      p.add(place(extrudeShape(rear, 0.06, { bevel: 0.014, curveSegments: 5 }), { pos: [0, 0, S.cabBackZ + 0.03] }), M.crimson);
      for (const sx of [-1, 1]) {
        p.add(place(cylZ(0.19, 0.19, 0.03, { bevel: 0.01, radialSegments: 24 }), { pos: [sx * 0.56, 2.4, S.cabBackZ + 0.06] }), M.brass);
        p.add(place(cylZ(0.165, 0.165, 0.018, { bevel: 0.006, radialSegments: 24 }), { pos: [sx * 0.56, 2.4, S.cabBackZ + 0.06] }), M.glass);
      }
    }

    // Cab floor and interior suggestion: backhead, firehole glow, gauges, seats.
    p.add(place(beveledBox(1.94, 0.06, 1.6, { bevel: 0.016 }), { pos: [0, S.cabFloorY, -2.32] }), M.wood);
    p.add(place(beveledBox(1.5, 1.28, 0.14, { bevel: 0.04, segments: 2 }), { pos: [0, 1.98, -2.24] }), M.graphite);
    p.add(place(cylZ(0.27, 0.27, 0.1, { bevel: 0.03, radialSegments: 22 }), { pos: [0, 1.74, -2.16] }), M.iron);
    p.add(place(cylZ(0.21, 0.21, 0.03, { bevel: 0.01, radialSegments: 22 }), { pos: [0, 1.74, -2.11] }), M.fireGlow);
    for (const sx of [-1, 1]) {
      p.add(place(cylZ(0.1, 0.1, 0.05, { bevel: 0.014, radialSegments: 16 }), { pos: [sx * 0.42, 2.42, -2.14] }), M.brass);
      p.add(place(beveledBox(0.4, 0.08, 0.42, { bevel: 0.02 }), { pos: [sx * 0.62, 1.72, -2.78] }), M.seat);
      p.add(place(beveledBox(0.4, 0.44, 0.07, { bevel: 0.02 }), { pos: [sx * 0.62, 1.94, -2.96] }), M.seat);
    }
    // Regulator and reverser.
    p.add(place(beveledBox(0.05, 0.4, 0.05, { bevel: 0.014 }), { rot: [0.35, 0, 0], pos: [0.42, 2.36, -2.02] }), M.brass);
    p.add(place(beveledBox(0.05, 0.6, 0.05, { bevel: 0.014 }), { rot: [-0.2, 0, 0], pos: [-0.5, 2.06, -2.0] }), M.steelDull);

    // Bunker behind the cab, heaped with coal that sits proud of the sides.
    const bunkerMid = (S.bunkerZ0 + S.bunkerZ1) / 2;
    const bunkerH = S.bunkerTopY - S.cabFloorY;
    p.add(place(beveledBox(1.94, bunkerH, 0.06, { bevel: 0.02 }), { pos: [0, S.cabFloorY + bunkerH / 2, S.bunkerZ0] }), M.crimson);
    for (const sx of [-1, 1]) {
      p.add(
        place(beveledBox(0.06, bunkerH, S.bunkerZ1 - S.bunkerZ0, { bevel: 0.02 }), { pos: [sx * 0.94, S.cabFloorY + bunkerH / 2, bunkerMid] }),
        M.crimson
      );
      p.add(place(beveledBox(0.1, 0.05, S.bunkerZ1 - S.bunkerZ0, { bevel: 0.016 }), { pos: [sx * 0.94, S.bunkerTopY, bunkerMid] }), M.black);
    }
    p.add(place(beveledBox(1.9, 0.06, S.bunkerZ1 - S.bunkerZ0, { bevel: 0.016 }), { pos: [0, S.cabFloorY + 0.04, bunkerMid] }), M.iron);
    for (let i = 0; i < 60; i++) {
      const r = rng.range(0.05, 0.12);
      const dx = rng.range(-0.8, 0.8);
      const dz = rng.range(S.bunkerZ0 + 0.12, S.bunkerZ1 - 0.06);
      // Heaped: highest on the centreline, falling away to the sides.
      const heap = S.bunkerTopY + 0.14 - (dx / 0.8) ** 2 * 0.22 - rng.range(0, 0.5);
      const g = new THREE.IcosahedronGeometry(r, 0);
      place(g, {
        rot: [rng.range(0, 6.283), rng.range(0, 6.283), rng.range(0, 6.283)],
        pos: [dx, Math.max(S.cabFloorY + 0.2, heap), dz],
      });
      p.add(g, M.coal);
    }
  }

  /* --- cylinders, valve chests, slide bars ------------------------------ */

  for (const sx of [-1, 1]) {
    const x = sx * S.mainRodX;
    const len = S.cylFrontZ - S.cylBackZ;
    const zMid = (S.cylBackZ + S.cylFrontZ) / 2;

    // The cylinder is a bored casting inside a square block, not a tube — the
    // flat top and bottom faces are what carry the valve chest and the drains.
    p.add(place(beveledBox(0.56, 0.58, len, { bevel: 0.045, segments: 2 }), { pos: [x, S.cylY, zMid] }), M.graphite);
    for (const z of [S.cylBackZ + 0.03, S.cylFrontZ - 0.03]) {
      const outward = z > zMid ? 1 : -1;
      p.add(place(beveledBox(0.62, 0.64, 0.07, { bevel: 0.026, segments: 2 }), { pos: [x, S.cylY, z] }), M.graphite);
      p.add(place(cylZ(S.cylRadius - 0.01, S.cylRadius - 0.01, 0.06, { bevel: 0.02, radialSegments: 28 }), { pos: [x, S.cylY, z + outward * 0.05] }), M.iron);
      for (let i = 0; i < 10; i++) {
        const a = (i / 10) * Math.PI * 2 + 0.3;
        p.add(
          place(rivetHead(0.015), {
            pos: [x + Math.cos(a) * 0.245, S.cylY + Math.sin(a) * 0.245, z + outward * 0.048],
            dir: new THREE.Vector3(0, 0, outward),
          }),
          M.graphite
        );
      }
    }
    // Fairing up to the running plate: on the prototype the cylinder is bolted
    // straight to the smokebox saddle, and without this the block reads as a
    // black box hanging in mid-air under the front end.
    p.add(
      place(beveledBox(0.5, S.plateY - S.cylY - 0.24, len * 0.94, { bevel: 0.03, segments: 2 }), {
        pos: [x, (S.cylY + 0.29 + S.plateY - 0.03) / 2, zMid],
      }),
      M.graphite
    );
    p.add(place(beveledBox(0.66, 0.06, len * 0.96, { bevel: 0.018 }), { pos: [x, S.plateY - 0.05, zMid] }), M.iron);

    // Valve chest above the block, with its own end covers.
    p.add(place(beveledBox(0.42, 0.34, len * 0.88, { bevel: 0.03, segments: 2 }), { pos: [x, S.valveY, zMid] }), M.graphite);
    for (const sz of [-1, 1]) {
      p.add(place(cylZ(0.15, 0.15, 0.05, { bevel: 0.016, radialSegments: 20 }), { pos: [x, S.valveY, zMid + sz * len * 0.45] }), M.iron);
    }
    // Drain cocks under the cylinder ends.
    for (const z of [S.cylBackZ + 0.13, S.cylFrontZ - 0.16]) {
      p.add(place(beveledCylinder(0.032, 0.032, 0.17, { bevel: 0.01, radialSegments: 10 }), { rot: [0, 0, 0.35 * sx], pos: [x + sx * 0.05, S.cylY - 0.36, z] }), M.brass);
    }

    // Slide bars, carried at the back by a motion plate bolted to the frame.
    for (const sy of [-1, 1]) {
      p.add(
        place(beveledBox(0.11, 0.05, S.slideBarFrontZ - S.slideBarBackZ, { bevel: 0.012, segments: 2 }), {
          pos: [x, S.cylY + sy * 0.19, (S.slideBarBackZ + S.slideBarFrontZ) / 2],
        }),
        M.steel
      );
    }
    p.add(
      place(beveledBox(Math.abs(S.mainRodX) - S.frameX + 0.2, 0.66, 0.1, { bevel: 0.02, segments: 2 }), {
        pos: [sx * (S.frameX + S.mainRodX) * 0.5, S.cylY, S.slideBarBackZ - 0.06],
      }),
      M.iron
    );
    // Expansion-link trunnion, hung off the frame on a cranked bracket.
    p.add(
      place(beveledBox(S.valveX - S.frameX, 0.09, 0.1, { bevel: 0.018 }), {
        pos: [sx * (S.frameX + S.valveX) * 0.5, S.linkPivotY - 0.2, S.linkPivotZ],
      }),
      M.iron
    );
    p.add(place(beveledBox(0.09, 0.24, 0.11, { bevel: 0.018 }), { pos: [sx * S.valveX, S.linkPivotY - 0.11, S.linkPivotZ] }), M.iron);

    // Steps up to the cab.
    for (const [z, y] of [
      [-2.62, S.plateY - 0.36],
      [-2.62, S.plateY - 0.66],
    ]) {
      p.add(place(beveledBox(0.36, 0.045, 0.36, { bevel: 0.014 }), { pos: [sx * 1.06, y, z] }), M.black);
    }
    p.add(place(beveledBox(0.05, 0.78, 0.05, { bevel: 0.014 }), { pos: [sx * 1.18, S.plateY - 0.42, -2.44] }), M.black);
    p.add(place(beveledBox(0.05, 0.78, 0.05, { bevel: 0.014 }), { pos: [sx * 1.18, S.plateY - 0.42, -2.8] }), M.black);

    // Leaf springs over each driving axle, tucked between frame and wheel.
    for (const z of S.axleZ) {
      const spring = leafSpring(0.78, { leaves: 4, camber: 0.05, width: 0.062, leaf: 0.017 });
      p.add(place(spring, { pos: [sx * 0.69, S.plateY - 0.15, z] }), M.steelDull);
      p.add(place(beveledBox(0.075, 0.1, 0.055, { bevel: 0.012 }), { pos: [sx * 0.69, S.plateY - 0.09, z] }), M.iron);

      // Brake shoe hung behind each wheel, with its hanger. Cheap geometry, and
      // it breaks up the dark gap between wheel and frame.
      p.add(
        place(beveledBox(0.07, 0.26, 0.07, { bevel: 0.016, segments: 2 }), {
          rot: [0.1, 0, 0],
          pos: [sx * (S.halfGauge - 0.02), S.driverRadius - 0.2, z - S.driverRadius - 0.07],
        }),
        M.rust
      );
      p.add(
        place(beveledBox(0.038, 0.5, 0.038, { bevel: 0.012 }), {
          rot: [0.08, 0, 0],
          pos: [sx * (S.halfGauge - 0.02), S.driverRadius + 0.08, z - S.driverRadius - 0.04],
        }),
        M.iron
      );
    }
    // Brake pull rod tying the three shoes together.
    p.add(
      place(beveledBox(0.038, 0.038, 2.9, { bevel: 0.01 }), { pos: [sx * (S.halfGauge - 0.02), S.driverRadius - 0.34, -0.72] }),
      M.iron
    );
  }

  /* --- handrails, pipework, lamps --------------------------------------- */

  for (const sx of [-1, 1]) {
    handrail(
      p,
      M.brass,
      [
        [sx * (S.smokeboxR + 0.09), S.boilerY + 0.28, S.smokeboxZ1 - 0.12],
        [sx * (S.tankHalfWidth + 0.085), S.boilerY + 0.32, S.tankZ1 - 0.2],
        [sx * (S.tankHalfWidth + 0.085), S.boilerY + 0.32, S.tankZ0 + 0.3],
      ],
      { radius: 0.025, standoff: 0.085, normal: [sx, 0, 0], stanchions: 4 }
    );
    // Vacuum pipe along the frame.
    p.add(
      tubeAlong(
        [
          new THREE.Vector3(sx * 0.72, 0.94, 3.3),
          new THREE.Vector3(sx * 0.72, 0.9, 1.0),
          new THREE.Vector3(sx * 0.72, 0.9, -1.6),
          new THREE.Vector3(sx * 0.72, 1.0, -3.1),
        ],
        0.035,
        { radialSegments: 8 }
      ),
      M.steelDull
    );
  }
  // Smokebox-front handrail, curved round the door.
  {
    const pts = [];
    for (let i = 0; i <= 12; i++) {
      const a = Math.PI * (0.14 + (i / 12) * 0.72);
      pts.push(new THREE.Vector3(Math.cos(a) * (S.smokeboxR - 0.1), S.boilerY + Math.sin(a) * (S.smokeboxR - 0.1), S.smokeboxZ1 + 0.09));
    }
    p.add(tubeAlong(pts, 0.024, { radialSegments: 8 }), M.brass);
  }
  lamp(p, { caseMaterial: M.steelDull, glassMaterial: M.lampGlass }, { x: 0, y: S.bufferY + 0.55, z: S.bufferBeamZ + 0.06, scale: 1.15 });
  lamp(p, { caseMaterial: M.steelDull, glassMaterial: M.lampGlass }, { x: 0, y: S.boilerY + S.smokeboxR + 0.14, z: S.smokeboxZ1 - 0.1, scale: 1.0 });

  const body = p.build('loco', {
    cast: true,
    receive: true,
    shade: makeWeathering({ seed: (rng.next() * 1e6) | 0, dustTop: 1.9, sootZ: S.chimneyZ, sootY: 2.35 }),
  });
  sprung.add(body);

  /* --- chimney needs to sit at its z; add it as its own mesh ------------- */
  // (built above at the origin and translated here so the lathe stays cheap)

  /* --- wheels and motion ------------------------------------------------- */

  const mainZ = S.axleZ[S.mainAxle];
  const coupled = S.axleZ.filter((_, i) => i !== S.mainAxle);

  const mainPair = makeWheelPair(node, materials, {
    radius: S.driverRadius,
    width: S.driverWidth,
    spokes: 12,
    crankRadius: S.crankRadius,
    pinStart: S.pinStart,
    pinLength: S.pinLengthMain,
    counterweight: true,
    quarter: QUARTER,
    name: 'driver-main',
    offsetsPlusX: [[S.halfGauge, mainZ]],
    offsetsMinusX: [[-S.halfGauge, mainZ]],
  });
  const coupledPair = makeWheelPair(node, materials, {
    radius: S.driverRadius,
    width: S.driverWidth,
    spokes: 12,
    crankRadius: S.crankRadius,
    pinStart: S.pinStart,
    pinLength: S.pinLengthCoupled,
    counterweight: true,
    quarter: QUARTER,
    name: 'driver',
    offsetsPlusX: coupled.map((z) => [S.halfGauge, z]),
    offsetsMinusX: coupled.map((z) => [-S.halfGauge, z]),
  });

  const motion = createMotion(sprung, materials, S);

  let triangles = motion.triangles + mainPair.plusX.triangles + mainPair.minusX.triangles + coupledPair.plusX.triangles + coupledPair.minusX.triangles;
  body.traverse((o) => {
    if (o.isMesh) triangles += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3;
  });

  /** Chimney mouth in vehicle space — the steam plume's origin. */
  const chimney = new THREE.Vector3(0, S.chimneyMouthY, S.chimneyZ);
  const cylinderVents = [
    new THREE.Vector3(S.mainRodX, S.cylY - 0.36, S.cylBackZ + 0.14),
    new THREE.Vector3(-S.mainRodX, S.cylY - 0.36, S.cylBackZ + 0.14),
  ];

  return {
    node,
    sprung,
    chimney,
    cylinderVents,
    triangles,
    halfWheelbase: S.halfWheelbase,
    length: S.length,
    spec: S,
    /**
     * @param {number} distance metres this vehicle has travelled
     * @param {number} lift     sprung frame rise above its static height
     */
    update(distance, lift) {
      // No slip, ever: the wheel angle is derived from ground distance.
      const angle = -distance / S.driverRadius;
      mainPair.plusX.spin(angle);
      mainPair.minusX.spin(angle);
      coupledPair.plusX.spin(angle);
      coupledPair.minusX.spin(angle);
      motion.update(angle, lift);
    },
  };
}
