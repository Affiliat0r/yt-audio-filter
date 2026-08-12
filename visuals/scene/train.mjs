/**
 * The toy train.
 *
 * Every unit is placed by its two axle points on the spline — the body sits on
 * the chord between them — so carriages genuinely track the curve instead of
 * sliding round it rigidly. Wheel spin is derived from distance travelled, so
 * it always matches the ground speed.
 */

import * as THREE from 'three';
import { TRAIN, CARRIAGE } from './palette.mjs';
import {
  Parts,
  mergeGeometries,
  composeFromForward,
  cylinderAlongX,
  cylinderAlongZ,
  toyMaterial,
} from './geom.mjs';
import { RAIL_TOP, GAUGE } from './track.mjs';

/** Metres per second along the track. Calm enough to sit under a recitation. */
export const SPEED = 6.0;

const HALF_GAUGE = GAUGE / 2;
const DRIVE_RADIUS = 0.62;
const PONY_RADIUS = 0.4;
const CARRIAGE_WHEEL_RADIUS = 0.42;

/**
 * A set of identical wheels that all spin together, drawn as three instanced
 * meshes (tyre, rim, brass) regardless of how many wheels there are.
 */
function makeWheelSet(parent, { radius, width, offsets }) {
  const disc = new THREE.CylinderGeometry(radius * 0.9, radius * 0.9, width, 16)
    .rotateZ(Math.PI / 2);

  const rim = new THREE.TorusGeometry(radius - 0.06, 0.07, 5, 20).rotateY(Math.PI / 2);

  const brass = new Parts();
  brass.add(cylinderAlongX(radius * 0.2, radius * 0.2, width * 1.5, 10), TRAIN.wheelRim);
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2;
    brass.add(
      cylinderAlongX(0.055, 0.055, width * 1.25, 6).translate(
        0,
        Math.cos(a) * radius * 0.58,
        Math.sin(a) * radius * 0.58
      ),
      TRAIN.wheelRim
    );
  }

  const layers = [
    new THREE.InstancedMesh(disc, toyMaterial(TRAIN.wheel, { shininess: 26 }), offsets.length),
    new THREE.InstancedMesh(
      rim,
      toyMaterial(TRAIN.rod, { shininess: 60, specular: 0x666666 }),
      offsets.length
    ),
    new THREE.InstancedMesh(
      brass.merged(),
      toyMaterial(TRAIN.wheelRim, { shininess: 55, specular: 0x554400 }),
      offsets.length
    ),
  ];
  for (const layer of layers) {
    layer.castShadow = true;
    layer.receiveShadow = true;
    layer.frustumCulled = false;
    parent.add(layer);
  }

  const matrix = new THREE.Matrix4();
  const quaternion = new THREE.Quaternion();
  const position = new THREE.Vector3();
  const scale = new THREE.Vector3(1, 1, 1);
  const axis = new THREE.Vector3(1, 0, 0);

  return function spin(angle) {
    quaternion.setFromAxisAngle(axis, angle);
    for (let i = 0; i < offsets.length; i++) {
      position.set(offsets[i][0], radius, offsets[i][1]);
      matrix.compose(position, quaternion, scale);
      for (const layer of layers) layer.setMatrixAt(i, matrix);
    }
    for (const layer of layers) layer.instanceMatrix.needsUpdate = true;
  };
}

function buildLocomotive() {
  const node = new THREE.Group();
  node.name = 'locomotive';
  node.matrixAutoUpdate = false;

  const p = new Parts();

  // Frame and running plate.
  p.add(new THREE.BoxGeometry(1.34, 0.3, 6.0).translate(0, 0.95, -0.05), TRAIN.chassis, {
    shininess: 8,
  });
  p.add(new THREE.BoxGeometry(2.0, 0.12, 5.6).translate(0, 1.14, -0.1), TRAIN.bodyDark, {
    shininess: 14,
  });

  // Boiler, smokebox and front plate.
  p.add(cylinderAlongZ(0.72, 0.72, 3.5, 18).translate(0, 1.78, 0.85), TRAIN.bodyRed, {
    shininess: 34,
  });
  p.add(cylinderAlongZ(0.78, 0.78, 0.5, 18).translate(0, 1.78, 2.78), TRAIN.funnel, {
    shininess: 30,
  });
  p.add(cylinderAlongZ(0.8, 0.8, 0.12, 18).translate(0, 1.78, 3.05), TRAIN.brass, {
    shininess: 70,
    specular: 0x554400,
  });
  // Boiler bands.
  for (const z of [0.0, 1.1, 2.1]) {
    p.add(cylinderAlongZ(0.745, 0.745, 0.11, 18).translate(0, 1.78, z), TRAIN.brass, {
      shininess: 70,
      specular: 0x554400,
    });
  }

  // Funnel.
  p.add(
    new THREE.CylinderGeometry(0.26, 0.34, 0.95, 14).translate(0, 2.9, 2.1),
    TRAIN.funnel,
    { shininess: 22 }
  );
  p.add(
    new THREE.CylinderGeometry(0.4, 0.34, 0.22, 14).translate(0, 3.44, 2.1),
    TRAIN.funnel,
    { shininess: 22 }
  );

  // Steam dome and safety valve.
  p.add(new THREE.SphereGeometry(0.36, 14, 8).translate(0, 2.36, 0.75), TRAIN.brass, {
    shininess: 70,
    specular: 0x554400,
  });
  p.add(
    new THREE.CylinderGeometry(0.13, 0.16, 0.34, 10).translate(0, 2.5, -0.35),
    TRAIN.brass,
    { shininess: 70, specular: 0x554400 }
  );

  // Cab.
  p.add(new THREE.BoxGeometry(1.96, 1.66, 1.9).translate(0, 2.05, -2.05), TRAIN.cabYellow, {
    shininess: 26,
  });
  p.add(new THREE.BoxGeometry(2.16, 0.16, 2.14).translate(0, 2.96, -2.05), TRAIN.roofRed, {
    shininess: 26,
  });
  // Cab windows: side pair plus a rear opening.
  for (const x of [-1, 1]) {
    p.add(
      new THREE.BoxGeometry(0.08, 0.62, 0.68).translate(x * 0.97, 2.42, -1.68),
      TRAIN.window,
      { shininess: 90, specular: 0x888888 }
    );
    p.add(
      new THREE.BoxGeometry(0.08, 0.62, 0.68).translate(x * 0.97, 2.42, -2.5),
      TRAIN.window,
      { shininess: 90, specular: 0x888888 }
    );
  }
  p.add(new THREE.BoxGeometry(0.9, 0.6, 0.08).translate(0, 2.42, -2.96), TRAIN.window, {
    shininess: 90,
    specular: 0x888888,
  });

  // Buffer beam, buffers and lamp.
  p.add(new THREE.BoxGeometry(2.1, 0.62, 0.24).translate(0, 1.0, 3.16), TRAIN.cabYellow, {
    shininess: 26,
  });
  for (const x of [-0.66, 0.66]) {
    p.add(cylinderAlongZ(0.14, 0.14, 0.34, 10).translate(x, 1.02, 3.4), TRAIN.chassis, {
      shininess: 30,
    });
  }
  p.add(cylinderAlongZ(0.19, 0.19, 0.26, 10).translate(0, 2.62, 3.05), TRAIN.lamp, {
    shininess: 80,
    specular: 0x888888,
  });

  // Cowcatcher-ish valance.
  p.add(new THREE.BoxGeometry(1.5, 0.5, 0.18).translate(0, 0.68, 3.05), TRAIN.bodyDark, {
    shininess: 14,
  });

  node.add(p.build('locomotive-body'));

  // Wheels: two coupled drivers plus a leading pony truck.
  const driverZ = [-1.75, -0.3];
  const spinDrivers = makeWheelSet(node, {
    radius: DRIVE_RADIUS,
    width: 0.17,
    offsets: [
      [-HALF_GAUGE, driverZ[0]],
      [HALF_GAUGE, driverZ[0]],
      [-HALF_GAUGE, driverZ[1]],
      [HALF_GAUGE, driverZ[1]],
    ],
  });
  const spinPony = makeWheelSet(node, {
    radius: PONY_RADIUS,
    width: 0.15,
    offsets: [
      [-HALF_GAUGE, 1.85],
      [HALF_GAUGE, 1.85],
    ],
  });

  // Coupling rods. Both crank pins share a phase, so the rod translates on a
  // circle without rotating — exactly like the real thing.
  const CRANK = 0.34;
  const rodMid = (driverZ[0] + driverZ[1]) / 2;
  const rodSpan = Math.abs(driverZ[1] - driverZ[0]) + 0.44;
  const rodMaterial = toyMaterial(TRAIN.rod, { shininess: 60, specular: 0x666666 });
  const rods = [];
  for (const x of [-1, 1]) {
    const rod = new THREE.Mesh(
      mergeGeometries([
        new THREE.BoxGeometry(0.09, 0.15, rodSpan),
        new THREE.CylinderGeometry(0.1, 0.1, 0.13, 8)
          .rotateZ(Math.PI / 2)
          .translate(0, 0, -rodSpan / 2 + 0.22),
        new THREE.CylinderGeometry(0.1, 0.1, 0.13, 8)
          .rotateZ(Math.PI / 2)
          .translate(0, 0, rodSpan / 2 - 0.22),
      ]),
      rodMaterial
    );
    rod.castShadow = true;
    rod.receiveShadow = true;
    rod.position.x = x * (HALF_GAUGE + 0.13);
    rods.push(rod);
    node.add(rod);
  }

  function update(angle) {
    spinDrivers(angle);
    spinPony(angle * (DRIVE_RADIUS / PONY_RADIUS));
    const py = DRIVE_RADIUS + Math.cos(angle) * CRANK;
    const pz = rodMid + Math.sin(angle) * CRANK;
    for (const rod of rods) {
      rod.position.y = py;
      rod.position.z = pz;
    }
  }

  return { node, update, halfWheelbase: 1.8 };
}

function buildCoach(rng, index) {
  const scheme = CARRIAGE[index % CARRIAGE.length];
  const node = new THREE.Group();
  node.name = `coach-${index}`;
  node.matrixAutoUpdate = false;

  const p = new Parts();
  const open = index === 2;

  // Underframe.
  p.add(new THREE.BoxGeometry(1.7, 0.26, 4.9).translate(0, 0.9, 0), TRAIN.chassis, {
    shininess: 8,
  });
  // Couplers front and back.
  for (const z of [-2.66, 2.66]) {
    p.add(new THREE.BoxGeometry(0.22, 0.2, 0.6).translate(0, 0.98, z), TRAIN.chassis, {
      shininess: 8,
    });
  }

  if (open) {
    // An open wagon carrying bright toy crates.
    const wallH = 0.85;
    p.add(new THREE.BoxGeometry(2.0, 0.16, 4.8).translate(0, 1.11, 0), scheme.body, {
      shininess: 26,
    });
    for (const x of [-1, 1]) {
      p.add(
        new THREE.BoxGeometry(0.14, wallH, 4.8).translate(x * 0.93, 1.19 + wallH / 2, 0),
        scheme.body,
        { shininess: 26 }
      );
    }
    for (const z of [-1, 1]) {
      p.add(
        new THREE.BoxGeometry(2.0, wallH, 0.14).translate(0, 1.19 + wallH / 2, z * 2.33),
        scheme.body,
        { shininess: 26 }
      );
    }
    const crateColors = [0xf2f2f2, 0x4fc0c0, 0xf7c948, 0xe2564f, 0x7fd6c4, 0xb073d6];
    for (let i = 0; i < 5; i++) {
      const w = rng.range(0.5, 0.82);
      const h = rng.range(0.45, 0.78);
      const d = rng.range(0.5, 0.82);
      p.add(
        new THREE.BoxGeometry(w, h, d)
          .rotateY(rng.range(-0.35, 0.35))
          .translate(rng.range(-0.5, 0.5), 1.19 + h / 2, rng.range(-1.9, 1.9)),
        rng.pick(crateColors),
        { shininess: 30 }
      );
    }
  } else {
    p.add(new THREE.BoxGeometry(2.0, 1.5, 4.8).translate(0, 1.83, 0), scheme.body, {
      shininess: 28,
    });
    // Roof: a squashed cylinder reads as a rounded coach roof.
    p.add(
      cylinderAlongZ(1.05, 1.05, 4.9, 16).scale(1, 0.4, 1).translate(0, 2.32, 0),
      scheme.roof,
      { shininess: 20 }
    );
    // Waist stripe.
    p.add(new THREE.BoxGeometry(2.04, 0.16, 4.82).translate(0, 1.36, 0), scheme.trim, {
      shininess: 30,
    });
    // Windows.
    for (const x of [-1, 1]) {
      for (const z of [-1.75, -0.58, 0.58, 1.75]) {
        p.add(
          new THREE.BoxGeometry(0.08, 0.66, 0.78).translate(x * 1.01, 2.06, z),
          TRAIN.window,
          { shininess: 90, specular: 0x888888 }
        );
      }
      // Door strips.
      p.add(new THREE.BoxGeometry(0.06, 1.4, 0.1).translate(x * 1.02, 1.83, 2.34), scheme.trim, {
        shininess: 26,
      });
      p.add(new THREE.BoxGeometry(0.06, 1.4, 0.1).translate(x * 1.02, 1.83, -2.34), scheme.trim, {
        shininess: 26,
      });
    }
  }

  node.add(p.build(`coach-${index}-body`));

  const spin = makeWheelSet(node, {
    radius: CARRIAGE_WHEEL_RADIUS,
    width: 0.15,
    offsets: [
      [-HALF_GAUGE, -1.55],
      [HALF_GAUGE, -1.55],
      [-HALF_GAUGE, 1.55],
      [HALF_GAUGE, 1.55],
    ],
  });

  function update(angle) {
    spin(angle * (DRIVE_RADIUS / CARRIAGE_WHEEL_RADIUS));
  }

  return { node, update, halfWheelbase: 1.6 };
}

export function buildTrain(rng, track) {
  const group = new THREE.Group();
  group.name = 'train';

  const units = [];

  const loco = buildLocomotive();
  loco.offset = 0;
  units.push(loco);
  group.add(loco.node);

  const COACH_SPACING = 5.6;
  for (let i = 0; i < 3; i++) {
    const coach = buildCoach(rng, i);
    coach.offset = 6.9 + i * COACH_SPACING;
    units.push(coach);
    group.add(coach.node);
  }

  const front = new THREE.Vector3();
  const back = new THREE.Vector3();
  const centre = new THREE.Vector3();
  const forward = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  const headPosition = new THREE.Vector3();

  function update(t) {
    const travel = SPEED * t;
    const angle = -travel / DRIVE_RADIUS;

    for (const unit of units) {
      const s = travel - unit.offset;
      track.pointAt(s + unit.halfWheelbase, front);
      track.pointAt(s - unit.halfWheelbase, back);
      centre.addVectors(front, back).multiplyScalar(0.5);
      forward.subVectors(front, back).normalize();
      // Ride height is measured from the rail head, along world up: the track
      // never banks, so this stays simple and stable.
      centre.addScaledVector(up, RAIL_TOP);
      composeFromForward(unit.node.matrix, centre, forward);
      unit.node.matrixWorldNeedsUpdate = true;
      unit.update(angle);
    }

    track.pointAt(travel + 2.4, headPosition);
  }

  return { group, update, headPosition, distanceAt: (t) => SPEED * t };
}
