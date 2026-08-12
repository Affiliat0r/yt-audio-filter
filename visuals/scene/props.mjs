/**
 * Scenery: trees, rocks, bushes, houses and one windmill.
 *
 * Placement is biased toward a band either side of the track, because that is
 * the only part of the world the chase camera ever sees closely, with a
 * thinner scatter further out to furnish the horizon.
 */

import * as THREE from 'three';
import { FOLIAGE, BLOSSOM, TRUNK, ROCK, HOUSE } from './palette.mjs';
import {
  Parts,
  mergeGeometries,
  toyMaterial,
  cylinderOnBase,
  coneOnBase,
  boxOnBase,
} from './geom.mjs';
import { WATER_LEVEL, TERRAIN_SIZE } from './terrain.mjs';

const SCATTER_RADIUS = TERRAIN_SIZE / 2 - 40;

/**
 * Candidate positions in a band beside the track.
 * `power > 1` crowds them toward `minLateral`.
 */
function bandPlacement(rng, track, heightAt, opts) {
  const { count, minLateral, maxLateral, minTrackDist, power = 1.6 } = opts;
  const out = [];
  const frame = {
    pos: new THREE.Vector3(),
    forward: new THREE.Vector3(),
    right: new THREE.Vector3(),
    up: new THREE.Vector3(),
  };
  let attempts = 0;
  while (out.length < count && attempts < count * 14) {
    attempts++;
    const s = rng.next() * track.length;
    const lateral = rng.sign() * rng.power(minLateral, maxLateral, power);
    track.frameAt(s, 2, frame);
    const x = frame.pos.x + frame.right.x * lateral + rng.range(-2.5, 2.5);
    const z = frame.pos.z + frame.right.z * lateral + rng.range(-2.5, 2.5);
    if (track.nearest(x, z).dist < minTrackDist) continue;
    const y = heightAt(x, z);
    if (y < WATER_LEVEL + 1.2) continue;
    out.push({ x, y, z });
  }
  return out;
}

/** Thin scatter over the whole map, to fill the horizon. */
function scatterPlacement(rng, track, heightAt, opts) {
  const { count, minRadius = 60, minTrackDist } = opts;
  const out = [];
  let attempts = 0;
  while (out.length < count && attempts < count * 14) {
    attempts++;
    const angle = rng.next() * Math.PI * 2;
    const radius = Math.sqrt(rng.range((minRadius / SCATTER_RADIUS) ** 2, 1)) * SCATTER_RADIUS;
    const x = Math.cos(angle) * radius;
    const z = Math.sin(angle) * radius;
    if (track.nearest(x, z).dist < minTrackDist) continue;
    const y = heightAt(x, z);
    if (y < WATER_LEVEL + 1.2) continue;
    out.push({ x, y, z });
  }
  return out;
}

/**
 * An instanced layer whose crowns lean gently with `t`.
 * Instance origins sit at the base of the plant, so the rotation reads as a
 * bend rather than a slide.
 */
function swayLayer(geometry, material, spots, rng, opts = {}) {
  const amplitude = opts.amplitude ?? 0.035;
  const mesh = new THREE.InstancedMesh(geometry, material, spots.length);
  mesh.castShadow = true;
  mesh.receiveShadow = true;

  const data = spots.map((spot) => ({
    position: new THREE.Vector3(spot.x, spot.y, spot.z),
    scale: new THREE.Vector3(spot.scale, spot.scale * (spot.stretch ?? 1), spot.scale),
    yaw: rng.range(0, Math.PI * 2),
    phase: rng.range(0, Math.PI * 2),
    rate: rng.range(0.55, 1.05),
    amp: amplitude * rng.range(0.5, 1.5),
  }));

  const colorPool = opts.colors;
  if (colorPool) {
    const color = new THREE.Color();
    for (let i = 0; i < data.length; i++) {
      color.set(rng.pick(colorPool));
      color.offsetHSL(0, rng.range(-0.04, 0.04), rng.range(-0.05, 0.05));
      mesh.setColorAt(i, color);
    }
    mesh.instanceColor.needsUpdate = true;
  }

  const matrix = new THREE.Matrix4();
  const quaternion = new THREE.Quaternion();
  const euler = new THREE.Euler();

  function write(t) {
    for (let i = 0; i < data.length; i++) {
      const d = data[i];
      euler.set(
        d.amp * Math.sin(t * d.rate + d.phase),
        d.yaw,
        d.amp * 0.7 * Math.cos(t * d.rate * 0.83 + d.phase * 1.7)
      );
      quaternion.setFromEuler(euler);
      matrix.compose(d.position, quaternion, d.scale);
      mesh.setMatrixAt(i, matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
  }

  write(0);
  mesh.computeBoundingSphere();
  return { mesh, update: write };
}

/** A static instanced layer — no per-frame work at all. */
function staticLayer(geometry, material, spots, rng, colors) {
  const mesh = new THREE.InstancedMesh(geometry, material, spots.length);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  const matrix = new THREE.Matrix4();
  const quaternion = new THREE.Quaternion();
  const euler = new THREE.Euler();
  const position = new THREE.Vector3();
  const scale = new THREE.Vector3();
  const color = new THREE.Color();

  for (let i = 0; i < spots.length; i++) {
    const spot = spots[i];
    position.set(spot.x, spot.y + (spot.sink ?? 0), spot.z);
    euler.set(rng.range(-0.35, 0.35), rng.range(0, Math.PI * 2), rng.range(-0.35, 0.35));
    quaternion.setFromEuler(euler);
    scale.set(spot.scale, spot.scale * (spot.stretch ?? 1), spot.scale * (spot.squash ?? 1));
    matrix.compose(position, quaternion, scale);
    mesh.setMatrixAt(i, matrix);
    if (colors) {
      color.set(rng.pick(colors));
      color.offsetHSL(0, rng.range(-0.05, 0.05), rng.range(-0.06, 0.06));
      mesh.setColorAt(i, color);
    }
  }
  mesh.instanceMatrix.needsUpdate = true;
  if (colors) mesh.instanceColor.needsUpdate = true;
  mesh.computeBoundingSphere();
  return mesh;
}

function buildHouse(rng) {
  const p = new Parts();
  const walls = rng.pick(HOUSE.walls);
  const roof = rng.pick(HOUSE.roofs);
  const width = rng.range(4.2, 6.4);
  const depth = rng.range(4.2, 6.4);
  const height = rng.range(2.6, 3.6);

  p.add(boxOnBase(width, height, depth), walls, { shininess: 8 });

  const roofRadius = Math.hypot(width, depth) / 2 + 0.25;
  p.add(
    new THREE.ConeGeometry(roofRadius, rng.range(2.0, 3.0), 4)
      .rotateY(Math.PI / 4)
      .translate(0, height + rng.range(1.0, 1.5), 0),
    roof,
    { shininess: 12 }
  );

  // Chimney.
  const cx = rng.sign() * width * 0.25;
  const cz = rng.sign() * depth * 0.25;
  p.add(boxOnBase(0.5, height + 3.4, 0.5).translate(cx, 0, cz), HOUSE.chimney, { shininess: 8 });

  // Door and windows on the +Z face.
  p.add(boxOnBase(0.9, 1.7, 0.12).translate(0, 0, depth / 2), HOUSE.door, { shininess: 14 });
  for (const sx of [-1, 1]) {
    p.add(
      new THREE.BoxGeometry(0.85, 0.85, 0.12).translate(sx * width * 0.28, height * 0.62, depth / 2),
      HOUSE.window,
      { shininess: 80, specular: 0x888888 }
    );
    p.add(
      new THREE.BoxGeometry(0.12, 0.85, 0.85).translate(sx * (width / 2), height * 0.62, 0),
      HOUSE.window,
      { shininess: 80, specular: 0x888888 }
    );
  }

  // A second storey or an annexe, sometimes.
  if (rng.chance(0.45)) {
    const aw = width * rng.range(0.4, 0.6);
    const ad = depth * rng.range(0.5, 0.75);
    const ah = height * rng.range(0.55, 0.75);
    const ox = (width / 2 + aw / 2 - 0.2) * rng.sign();
    p.add(boxOnBase(aw, ah, ad).translate(ox, 0, rng.range(-0.6, 0.6)), walls, { shininess: 8 });
    p.add(
      new THREE.ConeGeometry(Math.hypot(aw, ad) / 2 + 0.2, 1.4, 4)
        .rotateY(Math.PI / 4)
        .translate(ox, ah + 0.7, 0),
      roof,
      { shininess: 12 }
    );
  }

  const group = p.build('house');
  group.rotation.y = rng.range(0, Math.PI * 2);
  return group;
}

function buildWindmill(rng) {
  const root = new THREE.Group();
  const p = new Parts();
  p.add(cylinderOnBase(1.5, 2.3, 7.5, 10), rng.pick(HOUSE.walls), { shininess: 8 });
  p.add(coneOnBase(2.1, 2.2, 10).translate(0, 7.5, 0), rng.pick(HOUSE.roofs), { shininess: 12 });
  p.add(boxOnBase(0.9, 1.6, 0.12).translate(0, 0, 2.0), HOUSE.door, { shininess: 14 });
  root.add(p.build('windmill-tower'));

  const hub = new THREE.Group();
  hub.position.set(0, 7.2, 1.7);
  const blades = new Parts();
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2;
    const arm = new THREE.BoxGeometry(0.55, 4.4, 0.14).translate(0, 2.5, 0);
    arm.rotateZ(a);
    blades.add(arm, 0xf4efe2, { shininess: 14 });
  }
  blades.add(new THREE.CylinderGeometry(0.28, 0.28, 0.5, 10).rotateX(Math.PI / 2), 0x8a5f3c, {
    shininess: 10,
  });
  hub.add(blades.build('windmill-blades'));
  root.add(hub);

  return { root, hub, rate: rng.range(0.35, 0.6) };
}

export function buildProps(rng, track, heightAt) {
  const group = new THREE.Group();
  group.name = 'props';
  const updaters = [];

  // --- pines --------------------------------------------------------------

  const pineSpots = [
    ...bandPlacement(rng, track, heightAt, {
      count: 130,
      minLateral: 11,
      maxLateral: 78,
      minTrackDist: 9.5,
    }),
    ...scatterPlacement(rng, track, heightAt, { count: 150, minTrackDist: 12 }),
  ].map((spot) => ({ ...spot, scale: rng.range(0.75, 1.5), stretch: rng.range(0.85, 1.25) }));

  const pineTrunk = new THREE.CylinderGeometry(0.16, 0.26, 1.6, 6).translate(0, 0.8, 0);
  group.add(
    staticLayer(
      pineTrunk,
      toyMaterial(0xffffff, { shininess: 4 }),
      pineSpots,
      rng.fork(),
      TRUNK
    )
  );

  const pineCrown = mergeGeometries([
    new THREE.ConeGeometry(1.75, 2.6, 8).translate(0, 2.6, 0),
    new THREE.ConeGeometry(1.35, 2.4, 8).translate(0, 3.9, 0),
    new THREE.ConeGeometry(0.9, 2.2, 8).translate(0, 5.2, 0),
  ]);
  const pines = swayLayer(
    pineCrown,
    toyMaterial(0xffffff, { shininess: 6 }),
    pineSpots,
    rng.fork(),
    { colors: FOLIAGE, amplitude: 0.028 }
  );
  group.add(pines.mesh);
  updaters.push(pines.update);

  // --- broadleaf trees ----------------------------------------------------

  const leafSpots = [
    ...bandPlacement(rng, track, heightAt, {
      count: 120,
      // Broadleaf crowns are wide: any closer and one can swallow the frame as
      // the camera swings out to its three-quarter offset.
      minLateral: 13,
      maxLateral: 70,
      minTrackDist: 12,
    }),
    ...scatterPlacement(rng, track, heightAt, { count: 90, minTrackDist: 12 }),
  ].map((spot) => ({ ...spot, scale: rng.range(0.8, 1.5), stretch: rng.range(0.85, 1.2) }));

  group.add(
    staticLayer(
      new THREE.CylinderGeometry(0.2, 0.32, 2.2, 6).translate(0, 1.1, 0),
      toyMaterial(0xffffff, { shininess: 4 }),
      leafSpots,
      rng.fork(),
      TRUNK
    )
  );

  const leafCrown = mergeGeometries([
    new THREE.IcosahedronGeometry(1.75, 0).translate(0, 3.4, 0),
    new THREE.IcosahedronGeometry(1.2, 0).translate(0.95, 2.7, 0.5),
    new THREE.IcosahedronGeometry(1.05, 0).translate(-0.85, 2.85, -0.6),
  ]);
  const leaves = swayLayer(
    leafCrown,
    toyMaterial(0xffffff, { shininess: 6 }),
    leafSpots,
    rng.fork(),
    { colors: [...FOLIAGE, ...FOLIAGE, ...FOLIAGE, ...FOLIAGE, ...BLOSSOM], amplitude: 0.045 }
  );
  group.add(leaves.mesh);
  updaters.push(leaves.update);

  // --- bushes -------------------------------------------------------------

  const bushSpots = [
    ...bandPlacement(rng, track, heightAt, {
      count: 200,
      minLateral: 7,
      maxLateral: 55,
      minTrackDist: 6.5,
    }),
    ...scatterPlacement(rng, track, heightAt, { count: 110, minTrackDist: 8 }),
  ].map((spot) => ({
    ...spot,
    scale: rng.range(0.45, 0.95),
    stretch: rng.range(0.55, 0.85),
    sink: -0.15,
  }));
  const bushes = swayLayer(
    new THREE.IcosahedronGeometry(1, 0).translate(0, 0.75, 0),
    toyMaterial(0xffffff, { shininess: 5 }),
    bushSpots,
    rng.fork(),
    { colors: FOLIAGE, amplitude: 0.06 }
  );
  group.add(bushes.mesh);
  updaters.push(bushes.update);

  // --- rocks --------------------------------------------------------------

  const rockSpots = [
    ...bandPlacement(rng, track, heightAt, {
      count: 90,
      minLateral: 7,
      maxLateral: 60,
      minTrackDist: 6.5,
    }),
    ...scatterPlacement(rng, track, heightAt, { count: 80, minTrackDist: 8 }),
  ].map((spot) => ({
    ...spot,
    scale: rng.range(0.35, 1.35),
    stretch: rng.range(0.5, 0.9),
    squash: rng.range(0.8, 1.3),
    sink: -0.25,
  }));
  group.add(
    staticLayer(
      new THREE.DodecahedronGeometry(1, 0),
      toyMaterial(0xffffff, { shininess: 14 }),
      rockSpots,
      rng.fork(),
      ROCK
    )
  );

  // --- houses -------------------------------------------------------------

  const houseSpots = bandPlacement(rng, track, heightAt, {
    count: 11,
    minLateral: 17,
    maxLateral: 62,
    minTrackDist: 16,
    power: 1.3,
  });
  for (const spot of houseSpots) {
    const house = buildHouse(rng);
    house.position.set(spot.x, spot.y - 0.2, spot.z);
    group.add(house);
  }

  // --- windmill -----------------------------------------------------------

  const millSpot = bandPlacement(rng, track, heightAt, {
    count: 1,
    minLateral: 26,
    maxLateral: 46,
    minTrackDist: 24,
    power: 1,
  })[0];
  if (millSpot) {
    const mill = buildWindmill(rng);
    mill.root.position.set(millSpot.x, millSpot.y - 0.2, millSpot.z);
    mill.root.rotation.y = rng.range(0, Math.PI * 2);
    group.add(mill.root);
    updaters.push((t) => {
      mill.hub.rotation.z = t * mill.rate;
    });
  }

  function update(t) {
    for (const fn of updaters) fn(t);
  }

  return { group, update };
}
