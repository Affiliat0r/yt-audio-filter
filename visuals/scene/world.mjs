/**
 * Assembles the world and advances it to an arbitrary time `t`.
 *
 * Nothing here holds per-frame state: `update(t)` recomputes every animated
 * quantity from `t` alone, so frames can be rendered in any order and always
 * come out the same.
 */

import * as THREE from 'three';
import { Rng } from './rng.mjs';
import { SKY } from './palette.mjs';
import { createHills, buildTerrain } from './terrain.mjs';
import { buildTrack } from './track.mjs';
import { buildProps } from './props.mjs';
import { buildCharacters } from './characters.mjs';
import { buildTrain, SPEED } from './train.mjs';
import { buildSky } from './sky.mjs';

/**
 * Chase camera. Distances are measured from the locomotive, so CAM_BEHIND has
 * to clear the whole rake — the last coach is ~18 m back — before the train
 * reads as a train rather than a roof filling the lens.
 */
const CAM_BEHIND = 33.0;
const CAM_HEIGHT = 6.6;
const CAM_LOOK_AHEAD = 12.0;
/**
 * Standing off the centreline gives a three-quarter view: the flanks, wheels
 * and coupling rods read, instead of four roofs seen from above.
 */
const CAM_LATERAL = 4.2;

export function buildWorld(config) {
  const rng = new Rng(config.seed >>> 0);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(SKY.horizon, 150, 430);

  const hills = createHills(rng.fork());
  const track = buildTrack(rng.fork(), hills);
  const terrain = buildTerrain(rng.fork(), hills, track);
  const props = buildProps(rng.fork(), track, terrain.heightAt);
  const characters = buildCharacters(rng.fork(), track, terrain.heightAt);
  const train = buildTrain(rng.fork(), track);
  const sky = buildSky(rng.fork());

  scene.add(terrain.group);
  scene.add(track.group);
  scene.add(props.group);
  scene.add(characters.group);
  scene.add(train.group);
  scene.add(sky.group);

  const camera = new THREE.PerspectiveCamera(
    52,
    config.width / config.height,
    0.5,
    2200
  );
  camera.name = 'chase-camera';

  const camPos = new THREE.Vector3();
  const lookTarget = new THREE.Vector3();
  const shadowFocus = new THREE.Vector3();
  const camFrame = {
    pos: new THREE.Vector3(),
    forward: new THREE.Vector3(),
    right: new THREE.Vector3(),
    up: new THREE.Vector3(),
  };

  function update(t) {
    const travel = SPEED * t;

    train.update(t);
    props.update(t);
    characters.update(t, train.headPosition);

    // Chase camera. Everything is a slow sine of `t`, so the drift is smooth
    // and repeatable, and the spline itself supplies the curve motion.
    const behind = CAM_BEHIND + Math.sin(t * 0.047) * 1.5;
    track.frameAt(travel - behind, 2.5, camFrame);
    camPos.copy(camFrame.pos);
    camPos.y += CAM_HEIGHT + Math.sin(t * 0.081 + 1.7) * 0.7;
    camPos.addScaledVector(camFrame.right, CAM_LATERAL + Math.sin(t * 0.063) * 2.8);

    track.pointAt(travel + CAM_LOOK_AHEAD, lookTarget);
    lookTarget.y += 2.0;

    camera.position.copy(camPos);
    camera.up.set(0, 1, 0);
    camera.lookAt(lookTarget);

    // Keep the shadow frustum over the stretch of track the camera can see.
    track.pointAt(travel + 6, shadowFocus);
    sky.update(t, shadowFocus);
  }

  return { scene, camera, update, track, train };
}
