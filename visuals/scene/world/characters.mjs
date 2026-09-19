/**
 * Lineside figures.
 *
 * Built from lathed, tapering limbs on a small joint hierarchy rather than
 * stacked boxes: real proportions (a 1.75 m adult is 7.3 heads tall here),
 * smooth shading, rounded shoulders and hips. They stay stylised — no faces
 * beyond eyes — but they read as people.
 *
 * Everything they do is a pure function of `t` and the train's position at
 * `t`: a slow weight shift from one foot to the other, breathing, a head that
 * tracks the train as it passes, and a wave that starts only once the
 * locomotive is close enough to see them.
 */

import * as THREE from 'three';
import { smoothstep, clamp, lerp, wrapPi } from './rng.mjs';

const WAVE_RANGE = 52;

/** Elliptical lathe: a circular profile squashed on Z, for torsos and heads. */
function lathe(profile, radialSegments, flattenZ = 1) {
  const g = new THREE.LatheGeometry(profile, radialSegments);
  if (flattenZ !== 1) g.scale(1, 1, flattenZ);
  g.computeVertexNormals();
  return g;
}

/** A limb hanging along -Y from its pivot at the origin. */
function limb(geom, length, rTop, rBottom, radial = 12) {
  return geom.taperedCapsule(length, rBottom, rTop, { radialSegments: radial }).rotateX(Math.PI);
}

function paint(geom, g, color) {
  return geom.paintGeometry(g, color);
}

const SKIN = [0xf0c9a4, 0xdda878, 0xb87b52, 0x8a5a3b, 0x6b4429, 0xf6d9bd];
const SHIRT = [
  0xd85a4a, 0x3f7fb8, 0xe9c46a, 0x5a8f6a, 0xe4e0d6, 0x8a5fa8, 0x2f4858, 0xd98a5f,
];
const TROUSERS = [0x36414f, 0x4c4438, 0x2c3b46, 0x5a4a3a, 0x3b3b45, 0x6b6152];
const HAIR = [0x2a1d14, 0x4d3120, 0x8a6435, 0x1a1a1c, 0xb59a6f, 0x6e6a66];
const HAT = [0xe6dcc4, 0xc0392b, 0x2f4858, 0x4a6741, 0xd6b25e];

/**
 * @returns a figure: `{ root, nodes, meshes }` where every node is a Group and
 * the meshes are merged per node so a figure costs seven draw calls.
 */
function buildFigure(rng, geom, material) {
  const skin = rng.pick(SKIN);
  const shirt = rng.pick(SHIRT);
  const trousers = rng.pick(TROUSERS);
  const hairColor = rng.pick(HAIR);
  const shoe = rng.pick([0x2a2622, 0x40342a, 0x1e1e22]);
  const child = rng.chance(0.28);
  const scale = child ? rng.range(0.60, 0.74) : rng.range(0.92, 1.06);

  const root = new THREE.Group();
  const body = new THREE.Group();
  body.scale.setScalar(scale);
  root.add(body);

  const HIP = 0.92;
  const SHOULDER = 1.44;

  // --- legs (static; the feet stay planted) -------------------------------
  const legParts = [];
  for (const sx of [-1, 1]) {
    const x = sx * 0.095;
    legParts.push(
      paint(geom, limb(geom, HIP - 0.44, 0.082, 0.062).translate(x, HIP, 0), trousers)
    );
    legParts.push(
      paint(geom, limb(geom, 0.36, 0.061, 0.046).translate(x, 0.46, 0), trousers)
    );
    legParts.push(
      paint(
        geom,
        geom
          .beveledBox(0.105, 0.075, 0.245, { bevel: 0.028, segments: 2, uvScale: 0.5 })
          .translate(x, 0.04, 0.035),
        shoe
      )
    );
  }
  const legs = new THREE.Mesh(geom.mergeAll(legParts), material);
  legs.castShadow = true;
  legs.receiveShadow = true;
  body.add(legs);

  // --- torso --------------------------------------------------------------
  const torsoNode = new THREE.Group();
  torsoNode.position.set(0, HIP, 0);
  body.add(torsoNode);

  const torsoProfile = geom.roundedProfile(
    [
      [0.001, -0.06],
      [0.155, -0.05],
      [0.150, 0.10],
      [0.185, 0.30],
      [0.196, 0.46],
      [0.150, 0.55],
      [0.062, 0.58],
      [0.001, 0.60],
    ],
    { bevel: 0.03, segments: 2 }
  );
  const torsoParts = [
    paint(geom, lathe(torsoProfile, 22, 0.68), shirt),
    // Neck.
    paint(
      geom,
      geom.taperedCapsule(0.13, 0.05, 0.062, { radialSegments: 14 }).translate(0, 0.5, 0),
      skin
    ),
  ];
  const torso = new THREE.Mesh(geom.mergeAll(torsoParts), material);
  torso.castShadow = true;
  torso.receiveShadow = true;
  torsoNode.add(torso);

  // --- head ---------------------------------------------------------------
  const headNode = new THREE.Group();
  headNode.position.set(0, 0.60, 0);
  torsoNode.add(headNode);

  const headParts = [];
  const skull = new THREE.SphereGeometry(0.108, 20, 16);
  skull.scale(1.0, 1.13, 0.98);
  skull.translate(0, 0.085, 0);
  headParts.push(paint(geom, skull, skin));
  // Nose and ears, so the profile is not a perfect egg.
  const nose = new THREE.SphereGeometry(0.024, 8, 6);
  nose.scale(0.8, 0.9, 1.6);
  nose.translate(0, 0.078, 0.104);
  headParts.push(paint(geom, nose, skin));
  for (const sx of [-1, 1]) {
    const ear = new THREE.SphereGeometry(0.022, 8, 6);
    ear.scale(0.5, 1.2, 0.9);
    ear.translate(sx * 0.104, 0.082, 0);
    headParts.push(paint(geom, ear, skin));
  }
  for (const sx of [-1, 1]) {
    const eye = new THREE.SphereGeometry(0.0155, 8, 6);
    eye.scale(1, 1, 0.6);
    eye.translate(sx * 0.041, 0.098, 0.093);
    headParts.push(paint(geom, eye, 0x241d18));
  }
  const wearsHat = rng.chance(0.4);
  if (wearsHat) {
    const hat = rng.pick(HAT);
    headParts.push(
      paint(
        geom,
        geom.beveledCylinder(0.098, 0.112, 0.085, { bevel: 0.022, radialSegments: 18 }).translate(0, 0.175, 0),
        hat
      )
    );
    headParts.push(
      paint(
        geom,
        geom.beveledCylinder(0.175, 0.175, 0.018, { bevel: 0.008, radialSegments: 20 }).translate(0, 0.135, 0.012),
        hat
      )
    );
  } else {
    const hair = new THREE.SphereGeometry(0.115, 18, 14, 0, Math.PI * 2, 0, Math.PI * 0.62);
    hair.scale(1.0, 1.13, 1.0);
    hair.translate(0, 0.082, -0.006);
    headParts.push(paint(geom, hair, hairColor));
  }
  const head = new THREE.Mesh(geom.mergeAll(headParts), material);
  head.castShadow = true;
  head.receiveShadow = true;
  headNode.add(head);

  // --- arms ---------------------------------------------------------------
  const arms = [];
  for (const sx of [-1, 1]) {
    const shoulderNode = new THREE.Group();
    shoulderNode.position.set(sx * 0.176, SHOULDER - HIP, 0);
    torsoNode.add(shoulderNode);

    const upper = new THREE.Mesh(
      geom.mergeAll([
        paint(geom, limb(geom, 0.30, 0.062, 0.048), shirt),
        // Deltoid cap, so the shoulder is round rather than a stub.
        paint(geom, new THREE.SphereGeometry(0.076, 16, 12).scale(1.0, 0.86, 0.94), shirt),
      ]),
      material
    );
    upper.castShadow = true;
    upper.receiveShadow = true;
    shoulderNode.add(upper);

    const elbowNode = new THREE.Group();
    elbowNode.position.set(0, -0.30, 0);
    shoulderNode.add(elbowNode);

    const fore = new THREE.Mesh(
      geom.mergeAll([
        paint(geom, limb(geom, 0.26, 0.048, 0.038), rng.chance(0.5) ? shirt : skin),
        paint(
          geom,
          new THREE.SphereGeometry(0.046, 12, 10).scale(0.8, 1.15, 0.55).translate(0, -0.29, 0),
          skin
        ),
      ]),
      material
    );
    fore.castShadow = true;
    fore.receiveShadow = true;
    elbowNode.add(fore);

    arms.push({ shoulder: shoulderNode, elbow: elbowNode, side: sx });
  }

  return {
    root,
    body,
    torsoNode,
    headNode,
    arms,
    scale,
    child,
    waveArm: rng.int(0, 1),
    phase: rng.range(0, Math.PI * 2),
    idleRate: rng.range(0.42, 0.72),
    breathRate: rng.range(1.15, 1.65),
    waver: rng.chance(0.7),
    lookBias: rng.range(-0.35, 0.35),
  };
}

export function createCharacters({ rng, materials, geom, track, terrain, count = 14 }) {
  const group = new THREE.Group();
  group.name = 'characters';

  // One material for every figure: skin, cloth and hair are all separated by
  // vertex colour, which keeps a whole village to seven draw calls a head.
  const material = materials.skin;

  const figures = [];
  const frame = {
    pos: new THREE.Vector3(),
    forward: new THREE.Vector3(),
    right: new THREE.Vector3(),
    up: new THREE.Vector3(),
  };

  let attempts = 0;
  while (figures.length < count && attempts < 500) {
    attempts++;
    // Little clusters, so the world reads as inhabited rather than sprinkled.
    const s = rng.next() * track.length;
    const side = rng.sign();
    const members = rng.int(1, 3);
    for (let i = 0; i < members && figures.length < count; i++) {
      const lateral = side * rng.range(6.6, 14.5);
      track.frameAt(s + rng.range(-2.6, 2.6), 2, frame);
      const x = frame.pos.x + frame.right.x * lateral + rng.range(-0.8, 0.8);
      const z = frame.pos.z + frame.right.z * lateral + rng.range(-0.8, 0.8);
      const near = track.nearest(x, z);
      if (near.dist < 6) continue;
      const y = terrain.heightAt(x, z);
      if (y < terrain.waterLevel + 1.2) continue;
      if (terrain.slopeAt(x, z, 1.5) > 0.6) continue;

      const figure = buildFigure(rng, geom, material);
      figure.root.position.set(x, y, z);
      // Face roughly toward the line, with a little scatter.
      figure.baseYaw =
        Math.atan2(-frame.right.x * side, -frame.right.z * side) + rng.range(-0.55, 0.55);
      figure.root.rotation.y = figure.baseYaw;
      figure.baseY = y;
      group.add(figure.root);
      figures.push(figure);
    }
  }

  const toTrain = new THREE.Vector3();
  const fallbackTarget = new THREE.Vector3(0, 0, 0);

  /**
   * @param {number} t seconds
   * @param {THREE.Vector3|null} trainPosition where the locomotive is at `t`
   */
  function update(t, trainPosition) {
    const target = trainPosition || fallbackTarget;
    for (const f of figures) {
      toTrain.set(target.x - f.root.position.x, 0, target.z - f.root.position.z);
      const distance = toTrain.length();
      const near = 1 - smoothstep(WAVE_RANGE * 0.42, WAVE_RANGE, distance);

      // Weight shift: hips sway, shoulders counter-rotate, and the whole body
      // settles a few millimetres onto the loaded leg.
      const sway = Math.sin(t * f.idleRate + f.phase);
      const sway2 = Math.sin(t * f.idleRate * 0.63 + f.phase * 1.7);
      f.torsoNode.rotation.z = sway * 0.035;
      f.torsoNode.rotation.y = sway2 * 0.075 + f.lookBias * 0.2;
      f.torsoNode.position.x = -sway * 0.022;
      f.root.position.y = f.baseY - Math.abs(sway) * 0.006;

      // Breathing.
      const breath = 1 + Math.sin(t * f.breathRate + f.phase) * 0.012;
      f.torsoNode.scale.set(breath, 1 + (breath - 1) * 0.5, breath);

      // Watch the train past.
      const desired = Math.atan2(toTrain.x, toTrain.z);
      const relative = wrapPi(desired - f.baseYaw - f.torsoNode.rotation.y);
      f.headNode.rotation.y = lerp(
        Math.sin(t * 0.31 + f.phase) * 0.22 + f.lookBias,
        clamp(relative, -1.15, 1.15),
        near
      );
      f.headNode.rotation.x = -near * 0.06 + Math.sin(t * 0.47 + f.phase * 2) * 0.03;

      // Arms: a small idle swing, then a wave once the engine is close.
      const swing = Math.sin(t * f.idleRate * 1.4 + f.phase) * 0.05;
      for (let a = 0; a < 2; a++) {
        const arm = f.arms[a];
        arm.shoulder.rotation.set(swing * (a === 0 ? 1 : -1), 0, arm.side * 0.06);
        arm.elbow.rotation.set(0.14 + swing * 0.5, 0, 0);
      }
      if (f.waver && near > 0.02) {
        const arm = f.arms[f.waveArm];
        const beat = Math.sin(t * 6.9 + f.phase);
        const lift = near * (2.32 + beat * 0.14);
        arm.shoulder.rotation.set(0, 0, arm.side * lift);
        arm.elbow.rotation.set(0, 0, arm.side * (0.55 + beat * 0.42) * near);
      }
    }
  }

  update(0, null);

  return { group, update, count: figures.length };
}
