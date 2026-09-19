/**
 * Carriages: a pair of four-wheel passenger coaches, an open goods wagon and a
 * brake van.
 *
 * All four share one chassis builder — solebars, headstocks, buffers, hooks,
 * axleboxes with leaf springs, footboards — because on a real railway they
 * genuinely were the same underframe with different bodies on top, and because
 * repeating the underframe is what makes a mixed train read as one train.
 *
 * Each vehicle is posed from its two axle points on the spline, exactly like
 * the locomotive, so it tracks the curve instead of sliding round it rigidly.
 */

import * as THREE from 'three';
import { beveledBox, beveledCylinder, latheProfile, extrudeShape, rivetHead, tubeAlong } from './deps.mjs';
import { Parts, place, cylZ, roundedLoop, rivetRun, buffer, couplingHook, axlebox, lamp } from './parts.mjs';
import { makeWheelPair } from './wheels.mjs';
import { CARRIAGE as C } from './spec.mjs';
import { makeWeathering } from './weathering.mjs';

/** Rounded rectangle outline as Vector2s. */
function roundedRect(x0, y0, x1, y1, r, segments = 4) {
  const pts = [];
  for (const [cx, cy, a0, a1] of [
    [x1 - r, y0 + r, -Math.PI / 2, 0],
    [x1 - r, y1 - r, 0, Math.PI / 2],
    [x0 + r, y1 - r, Math.PI / 2, Math.PI],
    [x0 + r, y0 + r, Math.PI, Math.PI * 1.5],
  ]) {
    for (let i = 0; i <= segments; i++) {
      const a = a0 + ((a1 - a0) * i) / segments;
      pts.push(new THREE.Vector2(cx + Math.cos(a) * r, cy + Math.sin(a) * r));
    }
  }
  return pts;
}

/* -------------------------------------------------------------- underframe */

function buildChassis(p, M, { length, deckY = C.deckY, bufferY = C.bufferY, planks = true }) {
  const half = length / 2;

  // Solebars and headstocks.
  for (const sx of [-1, 1]) {
    p.add(place(beveledBox(0.07, 0.34, length, { bevel: 0.018, segments: 2 }), { pos: [sx * C.frameX, C.soleY, 0] }), M.iron);
    rivetRun(p, M.iron, [sx * (C.frameX + 0.038), C.soleY, -half + 0.2], [sx * (C.frameX + 0.038), C.soleY, half - 0.2], 16, {
      radius: 0.018,
      normal: [sx, 0, 0],
    });
  }
  for (const sz of [-1, 1]) {
    p.add(place(beveledBox(2.1, 0.36, 0.1, { bevel: 0.02, segments: 2 }), { pos: [0, C.soleY, sz * half] }), M.crimsonDeep);
    for (const sx of [-1, 1]) {
      buffer(p, { bodyMaterial: M.iron, headMaterial: M.steel }, {
        x: sx * 0.7,
        y: bufferY,
        z: sz * half,
        length: 0.3,
        facing: sz,
        radius: 0.12,
      });
    }
    couplingHook(p, M.steelDull, { y: bufferY - 0.14, z: sz * half, facing: sz });
    // Footboard.
    p.add(place(beveledBox(1.9, 0.05, 0.2, { bevel: 0.014 }), { pos: [0, 0.46, sz * (half - 0.35)] }), M.wood);
  }
  // Cross members.
  for (const z of [-length * 0.28, 0, length * 0.28]) {
    p.add(place(beveledBox(C.frameX * 2, 0.16, 0.07, { bevel: 0.014 }), { pos: [0, C.soleY - 0.02, z] }), M.iron);
  }
  // Axleboxes, hornguides and leaf springs.
  for (const sx of [-1, 1]) {
    for (const z of C.axleZ) {
      axlebox(p, { boxMaterial: M.iron, springMaterial: M.steelDull }, { x: sx * (C.frameX + 0.02), y: C.wheelRadius, z, side: sx });
    }
  }
  // Deck.
  if (planks) {
    const n = 11;
    for (let i = 0; i < n; i++) {
      const w = (C.halfWidth * 2 - 0.06) / n;
      p.add(
        place(beveledBox(w * 0.94, 0.06, length - 0.1, { bevel: 0.012, segments: 1 }), {
          pos: [-C.halfWidth + 0.03 + w * (i + 0.5), deckY, 0],
        }),
        M.wood
      );
    }
  } else {
    p.add(place(beveledBox(C.halfWidth * 2 - 0.06, 0.07, length - 0.1, { bevel: 0.016 }), { pos: [0, deckY, 0] }), M.iron);
  }
}

/* ------------------------------------------------------------------ coach */

function coachSideShape(length, { windows, winZ0, winPitch, winW = 0.62, winH = 0.74, winY = 1.98, eaves = 2.5 }) {
  const half = length / 2;
  const shape = new THREE.Shape(
    roundedLoop(
      [
        [-half, C.deckY, 0.04],
        [half, C.deckY, 0.04],
        [half, eaves, 0.09],
        [-half, eaves, 0.09],
      ],
      { bevel: 0.05, segments: 2 }
    )
  );
  for (let i = 0; i < windows; i++) {
    const z = winZ0 + i * winPitch;
    shape.holes.push(
      new THREE.Path(roundedRect(z - winW / 2, winY - winH / 2, z + winW / 2, winY + winH / 2, 0.09, 4))
    );
  }
  return shape;
}

function buildCoach(rng, M, { livery }) {
  const p = new Parts();
  const length = 5.8;
  const half = length / 2;
  const eaves = 2.5;
  const crown = 2.94;
  const lower = livery.lower;
  const upper = livery.upper;

  buildChassis(p, M, { length, planks: false });

  const windows = 5;
  const winPitch = 1.0;
  const winZ0 = -winPitch * (windows - 1) * 0.5;

  // Sides: real openings, so the interior actually shows.
  for (const sx of [-1, 1]) {
    const sheet = extrudeShape(coachSideShape(length, { windows, winZ0, winPitch, eaves }), 0.07, {
      bevel: 0.016,
      curveSegments: 5,
    });
    sheet.rotateY(-Math.PI / 2);
    p.add(place(sheet, { pos: [sx * C.halfWidth, 0, 0] }), upper);

    const x = sx * (C.halfWidth + 0.04);
    // Lower panel, waist beading and window surrounds.
    const panelH = 1.5 - C.deckY;
    p.add(place(beveledBox(0.03, panelH, length - 0.02, { bevel: 0.01, segments: 2 }), { pos: [x, C.deckY + panelH / 2, 0] }), lower);
    p.add(place(beveledBox(0.05, 0.075, length, { bevel: 0.016, segments: 2 }), { pos: [x, 1.53, 0] }), M.gold);
    p.add(place(beveledBox(0.05, 0.07, length, { bevel: 0.016, segments: 2 }), { pos: [x, eaves - 0.05, 0] }), M.gold);
    for (let i = 0; i < windows; i++) {
      const z = winZ0 + i * winPitch;
      // Glazing, set back behind the opening.
      p.add(place(beveledBox(0.02, 0.74, 0.62, { bevel: 0.006 }), { pos: [sx * (C.halfWidth - 0.035), 1.98, z] }), M.glass);
      // Brass surround.
      for (const [dy, dz, h, w] of [
        [0.4, 0, 0.045, 0.72],
        [-0.4, 0, 0.045, 0.72],
        [0, 0.35, 0.84, 0.045],
        [0, -0.35, 0.84, 0.045],
      ]) {
        p.add(place(beveledBox(0.024, h, w, { bevel: 0.008 }), { pos: [x + sx * 0.008, 1.98 + dy, z + dz] }), M.brass);
      }
    }
    // Doors: two per side, with handles.
    for (const z of [-1.5, 1.5]) {
      for (const dz of [-0.44, 0.44]) {
        p.add(place(beveledBox(0.026, 1.4, 0.035, { bevel: 0.008 }), { pos: [x, 1.82, z + dz] }), M.gold);
      }
      p.add(place(beveledCylinder(0.028, 0.028, 0.12, { bevel: 0.008, radialSegments: 10 }).rotateZ(Math.PI / 2), { pos: [x + sx * 0.05, 1.5, z + 0.3] }), M.brass);
    }
    // Grab rails beside the doors.
    for (const z of [-1.5, 1.5]) {
      p.add(
        tubeAlong(
          [new THREE.Vector3(x + sx * 0.05, 1.35, z - 0.5), new THREE.Vector3(x + sx * 0.05, 2.1, z - 0.5)],
          0.02,
          { radialSegments: 8 }
        ),
        M.brass
      );
    }
  }

  // Ends.
  for (const sz of [-1, 1]) {
    const end = extrudeShape(
      new THREE.Shape(
        roundedLoop(
          [
            [-C.halfWidth, C.deckY, 0.05],
            [C.halfWidth, C.deckY, 0.05],
            [C.halfWidth, eaves, 0.1],
            [-C.halfWidth, eaves, 0.1],
          ],
          { bevel: 0.05, segments: 2 }
        )
      ),
      0.07,
      { bevel: 0.016, curveSegments: 4 }
    );
    p.add(place(end, { pos: [0, 0, sz * (half - 0.035)] }), upper);
    p.add(
      place(beveledBox(C.halfWidth * 2 + 0.02, 1.5 - C.deckY, 0.03, { bevel: 0.01 }), {
        pos: [0, C.deckY + (1.5 - C.deckY) / 2, sz * (half + 0.02)],
      }),
      lower
    );
    // End lamp iron and a lamp on the rear vehicle end.
    p.add(place(beveledBox(0.1, 0.16, 0.05, { bevel: 0.014 }), { pos: [0, 2.06, sz * (half + 0.04)] }), M.iron);
  }

  // Interior: a dark shell with seat backs, read through the glazing.
  p.add(place(beveledBox(C.halfWidth * 2 - 0.14, 1.4, length - 0.16, { bevel: 0.02 }), { pos: [0, 1.72, 0] }), M.interior);
  for (let i = 0; i < 6; i++) {
    const z = -2.1 + i * 0.84;
    p.add(place(beveledBox(1.6, 0.5, 0.14, { bevel: 0.03, segments: 2 }), { pos: [0, 1.72, z] }), M.seat);
    p.add(place(beveledBox(1.6, 0.12, 0.5, { bevel: 0.03, segments: 2 }), { pos: [0, 1.46, z + 0.3] }), M.seat);
  }

  // Roof: an arc plate with a slight overhang, plus vents and lamp tops.
  {
    const outer = [];
    const inner = [];
    const rr = 2.1;
    const span = C.halfWidth + 0.09;
    const cy = crown - rr;
    for (let i = 0; i <= 26; i++) {
      const x = -span + (2 * span * i) / 26;
      const y = cy + Math.sqrt(Math.max(0.0001, rr * rr - x * x));
      outer.push(new THREE.Vector2(x, y));
      inner.push(new THREE.Vector2(x * 0.985, y - 0.075));
    }
    const roof = extrudeShape(new THREE.Shape([...outer, ...inner.reverse()]), length + 0.14, { bevel: 0.022, curveSegments: 4 });
    p.add(place(roof, { pos: [0, 0, 0] }), M.canvasRoof);
    for (const z of [-1.7, 0, 1.7]) {
      p.add(place(beveledCylinder(0.09, 0.11, 0.14, { bevel: 0.022, radialSegments: 14 }), { pos: [0, crown - 0.02, z] }), M.brass);
    }
    for (const z of [-2.5, 2.5]) {
      p.add(place(beveledCylinder(0.14, 0.15, 0.1, { bevel: 0.024, radialSegments: 16 }), { pos: [0, crown - 0.03, z] }), M.canvasRoof);
    }
  }

  void rng;
  return { parts: p, length };
}

/* ------------------------------------------------------------- open wagon */

function buildWagon(rng, M) {
  const p = new Parts();
  const length = 5.0;
  const half = length / 2;
  const wall = 0.7;

  buildChassis(p, M, { length, planks: true });

  // Plank sides with iron strapping. Kept low so the load actually shows —
  // a high-sided wagon just reads as a box.
  const planks = 3;
  for (const sx of [-1, 1]) {
    for (let i = 0; i < planks; i++) {
      const h = 0.22;
      p.add(
        place(beveledBox(0.075, h * 0.94, length - 0.12, { bevel: 0.016, segments: 2 }), {
          pos: [sx * C.halfWidth, C.deckY + 0.06 + h * (i + 0.5), 0],
        }),
        M.crate
      );
    }
    for (const z of [-1.6, 0, 1.6]) {
      p.add(place(beveledBox(0.1, wall - 0.04, 0.11, { bevel: 0.02, segments: 2 }), { pos: [sx * C.halfWidth, C.deckY + 0.06 + (wall - 0.06) / 2, z] }), M.iron);
      rivetRun(
        p,
        M.iron,
        [sx * (C.halfWidth + 0.055), C.deckY + 0.16, z],
        [sx * (C.halfWidth + 0.055), C.deckY + 0.58, z],
        3,
        { radius: 0.018, normal: [sx, 0, 0] }
      );
    }
  }
  for (const sz of [-1, 1]) {
    for (let i = 0; i < planks; i++) {
      const h = 0.22;
      p.add(
        place(beveledBox(C.halfWidth * 2, h * 0.94, 0.075, { bevel: 0.016, segments: 2 }), {
          pos: [0, C.deckY + 0.06 + h * (i + 0.5), sz * (half - 0.06)],
        }),
        M.crate
      );
    }
  }
  // Top capping rail.
  for (const sx of [-1, 1]) {
    p.add(place(beveledBox(0.11, 0.055, length - 0.06, { bevel: 0.018 }), { pos: [sx * C.halfWidth, C.deckY + 0.06 + wall - 0.06, 0] }), M.iron);
  }

  // Cargo: crates, barrels and sacks, packed in a deterministic jumble.
  const deck = C.deckY + 0.09;
  for (let i = 0; i < 7; i++) {
    const w = rng.range(0.42, 0.68);
    const h = rng.range(0.34, 0.6);
    const d = rng.range(0.42, 0.68);
    p.add(
      place(beveledBox(w, h, d, { bevel: 0.022, segments: 2 }), {
        rot: [0, rng.range(-0.4, 0.4), 0],
        pos: [rng.range(-0.45, 0.45), deck + h / 2, rng.range(-half + 0.55, half - 0.55)],
      }),
      M.crate
    );
  }
  for (let i = 0; i < 4; i++) {
    const r = rng.range(0.2, 0.26);
    const hgt = rng.range(0.5, 0.66);
    const bx = rng.range(-0.5, 0.5);
    const bz = rng.range(-half + 0.6, half - 0.6);
    const barrel = latheProfile(
      roundedLoop(
        [
          [0, -hgt / 2, 0],
          [r * 0.86, -hgt / 2, 0.03],
          [r, -hgt * 0.18, 0.05],
          [r, hgt * 0.18, 0.05],
          [r * 0.86, hgt / 2, 0.03],
          [0, hgt / 2, 0],
        ],
        { bevel: 0.02, segments: 2 }
      ),
      22
    );
    p.add(place(barrel, { pos: [bx, deck + hgt / 2, bz] }), M.crate);
    for (const sy of [-0.22, 0.22]) {
      p.add(
        place(beveledCylinder(r * 1.015, r * 1.015, 0.05, { bevel: 0.012, radialSegments: 22 }), {
          pos: [bx, deck + hgt / 2 + sy * hgt, bz],
        }),
        M.steelDull
      );
    }
  }
  for (let i = 0; i < 5; i++) {
    const s = rng.range(0.22, 0.34);
    const sack = new THREE.SphereGeometry(s, 10, 7);
    place(sack, {
      scale: [1.25, 0.78, 1.0],
      rot: [rng.range(-0.3, 0.3), rng.range(0, 3.14), rng.range(-0.3, 0.3)],
      pos: [rng.range(-0.55, 0.55), deck + s * 0.6, rng.range(-half + 0.5, half - 0.5)],
    });
    p.add(sack, M.canvasRoof);
  }

  return { parts: p, length };
}

/* -------------------------------------------------------------- brake van */

function buildBrakeVan(rng, M) {
  const p = new Parts();
  const length = 5.0;
  const half = length / 2;
  const cabinHalf = 1.5;
  const eaves = 2.42;
  const crown = 2.8;

  buildChassis(p, M, { length, planks: true });

  // Cabin between two open verandas.
  for (const sx of [-1, 1]) {
    const shape = new THREE.Shape(
      roundedLoop(
        [
          [-cabinHalf, C.deckY, 0.04],
          [cabinHalf, C.deckY, 0.04],
          [cabinHalf, eaves, 0.09],
          [-cabinHalf, eaves, 0.09],
        ],
        { bevel: 0.05, segments: 2 }
      )
    );
    shape.holes.push(new THREE.Path(roundedRect(-1.02, 1.78, -0.34, 2.24, 0.08, 4)));
    shape.holes.push(new THREE.Path(roundedRect(0.34, 1.78, 1.02, 2.24, 0.08, 4)));
    const sheet = extrudeShape(shape, 0.08, { bevel: 0.016, curveSegments: 5 });
    sheet.rotateY(-Math.PI / 2);
    p.add(place(sheet, { pos: [sx * C.halfWidth, 0, 0] }), M.vanBrown);
    for (const z of [-0.68, 0.68]) {
      p.add(place(beveledBox(0.02, 0.46, 0.68, { bevel: 0.006 }), { pos: [sx * (C.halfWidth - 0.04), 2.01, z] }), M.glass);
    }
    // Ducket: the bay window the guard leans out of.
    p.add(place(beveledBox(0.24, 0.66, 0.5, { bevel: 0.04, segments: 2 }), { pos: [sx * (C.halfWidth + 0.11), 2.02, 0] }), M.vanBrown);
    p.add(place(beveledBox(0.06, 0.42, 0.34, { bevel: 0.01 }), { pos: [sx * (C.halfWidth + 0.22), 2.02, 0] }), M.glass);
    // Waist beading.
    p.add(place(beveledBox(0.04, 0.06, cabinHalf * 2, { bevel: 0.014 }), { pos: [sx * (C.halfWidth + 0.03), 1.6, 0] }), M.gold);
  }
  for (const sz of [-1, 1]) {
    const end = extrudeShape(
      new THREE.Shape(
        roundedLoop(
          [
            [-C.halfWidth, C.deckY, 0.05],
            [C.halfWidth, C.deckY, 0.05],
            [C.halfWidth, eaves, 0.1],
            [-C.halfWidth, eaves, 0.1],
          ],
          { bevel: 0.05, segments: 2 }
        )
      ),
      0.08,
      { bevel: 0.016, curveSegments: 4 }
    );
    p.add(place(end, { pos: [0, 0, sz * cabinHalf] }), M.vanBrown);
    p.add(place(beveledBox(0.62, 1.0, 0.03, { bevel: 0.012 }), { pos: [0, 1.72, sz * (cabinHalf + 0.045)] }), M.vanBrown);

    // Veranda railings.
    for (const sx of [-1, 1]) {
      p.add(place(beveledBox(0.06, 0.98, 0.06, { bevel: 0.016 }), { pos: [sx * (C.halfWidth - 0.08), C.deckY + 0.52, sz * (half - 0.14)] }), M.iron);
      p.add(
        tubeAlong(
          [
            new THREE.Vector3(sx * (C.halfWidth - 0.08), C.deckY + 0.99, sz * (half - 0.14)),
            new THREE.Vector3(sx * (C.halfWidth - 0.08), C.deckY + 0.99, sz * cabinHalf),
          ],
          0.026,
          { radialSegments: 8 }
        ),
        M.iron
      );
    }
    p.add(
      tubeAlong(
        [
          new THREE.Vector3(-(C.halfWidth - 0.08), C.deckY + 0.99, sz * (half - 0.14)),
          new THREE.Vector3(C.halfWidth - 0.08, C.deckY + 0.99, sz * (half - 0.14)),
        ],
        0.026,
        { radialSegments: 8 }
      ),
      M.iron
    );
    // Brake standard.
    p.add(place(beveledCylinder(0.035, 0.04, 0.9, { bevel: 0.012, radialSegments: 12 }), { pos: [0.62, C.deckY + 0.48, sz * (half - 0.4)] }), M.steelDull);
    p.add(place(new THREE.TorusGeometry(0.17, 0.028, 6, 20).rotateX(Math.PI / 2), { pos: [0.62, C.deckY + 0.94, sz * (half - 0.4)] }), M.steelDull);
    lamp(p, { caseMaterial: M.iron, glassMaterial: M.lampGlass }, { x: 0, y: 2.14, z: sz * (half - 0.06), scale: 1.0, facing: sz });
  }

  // Roof and stove chimney.
  {
    const outer = [];
    const inner = [];
    const rr = 2.4;
    const span = C.halfWidth + 0.1;
    const cy = crown - rr;
    for (let i = 0; i <= 22; i++) {
      const x = -span + (2 * span * i) / 22;
      const y = cy + Math.sqrt(Math.max(0.0001, rr * rr - x * x));
      outer.push(new THREE.Vector2(x, y));
      inner.push(new THREE.Vector2(x * 0.985, y - 0.07));
    }
    const roof = extrudeShape(new THREE.Shape([...outer, ...inner.reverse()]), cabinHalf * 2 + 0.24, { bevel: 0.022, curveSegments: 4 });
    p.add(place(roof, { pos: [0, 0, 0] }), M.canvasRoof);
    p.add(place(beveledCylinder(0.075, 0.085, 0.34, { bevel: 0.018, radialSegments: 14 }), { pos: [0.52, crown - 0.02, -0.9] }), M.graphite);
    p.add(place(beveledCylinder(0.1, 0.09, 0.06, { bevel: 0.018, radialSegments: 14 }), { pos: [0.52, crown + 0.17, -0.9] }), M.graphite);
  }

  // Interior: a stove, a desk and a stool.
  p.add(place(beveledBox(C.halfWidth * 2 - 0.16, 1.3, cabinHalf * 2 - 0.14, { bevel: 0.02 }), { pos: [0, 1.66, 0] }), M.interior);
  p.add(place(beveledCylinder(0.16, 0.18, 0.6, { bevel: 0.03, radialSegments: 14 }), { pos: [0.52, 1.3, -0.9] }), M.graphite);
  p.add(place(beveledBox(0.5, 0.06, 0.7, { bevel: 0.016 }), { pos: [-0.5, 1.6, 0.4] }), M.wood);

  void rng;
  return { parts: p, length };
}

/* ------------------------------------------------------------------ facade */

const KINDS = {
  coach: buildCoach,
  wagon: buildWagon,
  brake: buildBrakeVan,
};

/**
 * @param {string} kind 'coach' | 'wagon' | 'brake'
 * @returns {{node, sprung, update(angle, lift), halfWheelbase, length, triangles}}
 */
export function buildCarriage(rng, materials, kind, options = {}) {
  const node = new THREE.Group();
  node.name = `carriage-${kind}`;
  node.matrixAutoUpdate = false;
  const sprung = new THREE.Group();
  sprung.name = `${kind}-body`;
  node.add(sprung);

  const builder = KINDS[kind] || buildCoach;
  const { parts, length } = builder(rng, materials, options);
  const body = parts.build(kind, {
    cast: true,
    receive: true,
    // Carriage roofs live directly in the locomotive exhaust, so they get
    // the soot pass too — a clean roof behind a steam engine looks wrong.
    shade: makeWeathering({ seed: (rng.next() * 1e6) | 0, dustTop: 1.5, sootZ: 1.8, sootY: 2.35, strength: 0.9 }),
  });
  sprung.add(body);

  const pair = makeWheelPair(node, materials, {
    radius: C.wheelRadius,
    width: C.wheelWidth,
    spokes: 10,
    web: 0.06,
    hubRadius: 0.13,
    hubWidth: 0.24,
    flange: 0.045,
    crankRadius: 0,
    counterweight: false,
    name: `${kind}-wheels`,
    offsetsPlusX: C.axleZ.map((z) => [C.halfGauge, z]),
    offsetsMinusX: C.axleZ.map((z) => [-C.halfGauge, z]),
  });

  let triangles = pair.plusX.triangles + pair.minusX.triangles;
  body.traverse((o) => {
    if (o.isMesh) triangles += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3;
  });

  // Axles, added after the wheels so they inherit nothing.
  {
    const axleParts = new Parts();
    for (const z of C.axleZ) {
      axleParts.add(
        place(beveledCylinder(0.075, 0.075, C.halfGauge * 2 - 0.04, { bevel: 0.01, radialSegments: 12 }).rotateZ(Math.PI / 2), {
          pos: [0, C.wheelRadius, z],
        }),
        materials.steelDull
      );
    }
    node.add(axleParts.build(`${kind}-axles`));
  }

  return {
    node,
    sprung,
    length,
    triangles,
    halfWheelbase: C.halfWheelbase,
    /**
     * @param {number} distance metres this vehicle has travelled
     * @param {number} lift     sprung frame rise (the wheels do not follow it)
     */
    update(distance, lift) {
      const angle = -distance / C.wheelRadius;
      pair.plusX.spin(angle, 0);
      pair.minusX.spin(angle, 0);
      void lift;
    },
  };
}
