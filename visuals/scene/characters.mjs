/**
 * Blocky lineside figures.
 *
 * They idle, and they turn their heads to watch the train go by — the wave
 * only starts when the locomotive is close. All of it is derived from `t` and
 * the train's position at `t`, so it stays a pure function of time.
 */

import * as THREE from 'three';
import { PEOPLE } from './palette.mjs';
import { Parts, boxOnBase } from './geom.mjs';
import { smoothstep, clamp } from './rng.mjs';
import { WATER_LEVEL } from './terrain.mjs';

const WAVE_RANGE = 46;

function wrapPi(a) {
  let v = (a + Math.PI) % (Math.PI * 2);
  if (v < 0) v += Math.PI * 2;
  return v - Math.PI;
}

function buildFigure(rng) {
  const shirt = rng.pick(PEOPLE.shirt);
  const trousers = rng.pick(PEOPLE.trousers);
  const skin = rng.pick(PEOPLE.skin);
  const height = rng.chance(0.35) ? rng.range(0.62, 0.78) : rng.range(0.92, 1.12);

  const root = new THREE.Group();
  const body = new THREE.Group();
  body.scale.setScalar(height);
  root.add(body);

  const p = new Parts();

  // Legs.
  for (const x of [-0.16, 0.16]) {
    p.add(boxOnBase(0.24, 0.78, 0.26).translate(x, 0, 0), trousers, { shininess: 10 });
    p.add(boxOnBase(0.27, 0.14, 0.36).translate(x, 0, 0.05), 0x33373d, { shininess: 16 });
  }

  // Torso and head.
  p.add(boxOnBase(0.66, 0.82, 0.36).translate(0, 0.78, 0), shirt, { shininess: 12 });
  p.add(boxOnBase(0.2, 0.16, 0.2).translate(0, 1.6, 0), skin, { shininess: 10 });

  const head = new THREE.Group();
  head.position.set(0, 1.72, 0);
  const hp = new Parts();
  hp.add(new THREE.BoxGeometry(0.46, 0.44, 0.44), skin, { shininess: 10 });
  // Eyes, so it reads as facing forward even at distance.
  for (const x of [-0.12, 0.12]) {
    hp.add(new THREE.BoxGeometry(0.09, 0.11, 0.05).translate(x, 0.04, 0.23), 0x2a2f38, {
      shininess: 20,
    });
  }
  if (rng.chance(0.55)) {
    const hat = rng.pick(PEOPLE.hat);
    hp.add(new THREE.BoxGeometry(0.5, 0.12, 0.5).translate(0, 0.26, 0), hat, { shininess: 14 });
    hp.add(new THREE.BoxGeometry(0.5, 0.06, 0.24).translate(0, 0.2, 0.3), hat, { shininess: 14 });
  } else {
    hp.add(new THREE.BoxGeometry(0.48, 0.14, 0.46).translate(0, 0.26, -0.01), rng.pick([0x2f2118, 0x5a3a22, 0x8a6a3a, 0x1f1b18]), {
      shininess: 8,
    });
  }
  head.add(hp.build('head'));
  body.add(head);
  body.add(p.build('figure'));

  // Arms pivot at the shoulder; the geometry hangs below the pivot.
  const arms = [];
  for (const side of [-1, 1]) {
    const arm = new THREE.Group();
    arm.position.set(side * 0.42, 1.5, 0);
    const ap = new Parts();
    ap.add(boxOnBase(0.18, 0.62, 0.2).translate(0, -0.62, 0), shirt, { shininess: 12 });
    ap.add(boxOnBase(0.19, 0.16, 0.21).translate(0, -0.78, 0), skin, { shininess: 10 });
    arm.add(ap.build('arm'));
    body.add(arm);
    arms.push(arm);
  }

  return {
    root,
    body,
    head,
    arms,
    height,
    waveSide: rng.chance(0.5) ? 0 : 1,
    phase: rng.range(0, Math.PI * 2),
    bobRate: rng.range(1.3, 2.2),
    bobAmount: rng.range(0.02, 0.06),
    waver: rng.chance(0.65),
  };
}

export function buildCharacters(rng, track, heightAt) {
  const group = new THREE.Group();
  group.name = 'characters';

  const frame = {
    pos: new THREE.Vector3(),
    forward: new THREE.Vector3(),
    right: new THREE.Vector3(),
    up: new THREE.Vector3(),
  };

  const figures = [];
  const TARGET = 14;
  let attempts = 0;
  while (figures.length < TARGET && attempts < 400) {
    attempts++;
    // Place in little clusters so the world reads as inhabited rather than
    // evenly sprinkled with people.
    const s = rng.next() * track.length;
    const side = rng.sign();
    const members = rng.int(1, 3);
    for (let i = 0; i < members; i++) {
      const lateral = side * rng.range(6.5, 13.5);
      track.frameAt(s + rng.range(-2.2, 2.2), 2, frame);
      const x = frame.pos.x + frame.right.x * lateral;
      const z = frame.pos.z + frame.right.z * lateral;
      if (track.nearest(x, z).dist < 6) continue;
      const y = heightAt(x, z);
      if (y < WATER_LEVEL + 1.2) continue;

      const figure = buildFigure(rng);
      figure.root.position.set(x, y, z);
      // Face roughly toward the track, with a little scatter.
      figure.baseYaw = Math.atan2(-frame.right.x * side, -frame.right.z * side) + rng.range(-0.5, 0.5);
      figure.root.rotation.y = figure.baseYaw;
      figure.baseY = y;
      group.add(figure.root);
      figures.push(figure);
      if (figures.length >= TARGET) break;
    }
  }

  const toTrain = new THREE.Vector3();

  function update(t, trainPosition) {
    for (const figure of figures) {
      toTrain.set(
        trainPosition.x - figure.root.position.x,
        0,
        trainPosition.z - figure.root.position.z
      );
      const distance = toTrain.length();
      const near = 1 - smoothstep(WAVE_RANGE * 0.45, WAVE_RANGE, distance);

      // Watch the train pass.
      const desired = Math.atan2(toTrain.x, toTrain.z);
      figure.head.rotation.y = clamp(wrapPi(desired - figure.baseYaw), -1.0, 1.0) * near;

      // Idle bob.
      const bob = Math.sin(t * figure.bobRate + figure.phase);
      figure.root.position.y = figure.baseY + bob * figure.bobAmount * (0.4 + near);

      const swing = Math.sin(t * figure.bobRate * 1.15 + figure.phase) * 0.22;
      figure.arms[0].rotation.x = swing;
      figure.arms[1].rotation.x = -swing;
      figure.arms[0].rotation.z = 0;
      figure.arms[1].rotation.z = 0;

      if (figure.waver && near > 0.01) {
        const arm = figure.arms[figure.waveSide];
        const side = figure.waveSide === 0 ? -1 : 1;
        const lift = near * (2.45 + Math.sin(t * 6.5 + figure.phase) * 0.42);
        arm.rotation.z = side * lift;
        arm.rotation.x = 0;
      }
    }
  }

  return { group, update };
}
