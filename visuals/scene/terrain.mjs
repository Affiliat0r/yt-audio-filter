/**
 * Ground: gently rolling hills, a flat corridor for the railway, and a little
 * water in the low ground.
 *
 * The height field is analytic so props can be dropped onto it without
 * raycasting, and so the same numbers drive both the mesh and the placement.
 */

import * as THREE from 'three';
import { makeNoise2D, smoothstep, lerp, clamp } from './rng.mjs';
import { LAND } from './palette.mjs';

export const TERRAIN_SIZE = 660;
const TERRAIN_SEGMENTS = 300;
export const WATER_LEVEL = -6.5;

/** Corridor: fully flat within FLAT metres of the rails, natural beyond BLEND. */
const CORRIDOR_FLAT = 5.5;
const CORRIDOR_BLEND = 19;

/**
 * The landform, independent of the track — the track needs this before it can
 * choose its own control heights, so it is built first.
 */
export function createHills(rng) {
  const shape = makeNoise2D(rng);
  const detail = makeNoise2D(rng);
  const tint = makeNoise2D(rng);

  /** Low-frequency landform only. The track follows a damped version of this. */
  function macro(x, z) {
    return shape(x * 0.0042, z * 0.0042) * 13;
  }

  /** Distant ground rising toward the horizon, so the map has no visible edge. */
  function rim(x, z) {
    const r = Math.hypot(x, z);
    const rise = Math.max(0, r - 155);
    return rise * 0.24 * (0.55 + 0.45 * detail(x * 0.006, z * 0.006));
  }

  /** Full natural height at (x, z), ignoring the railway. */
  function height(x, z) {
    return (
      macro(x, z) +
      detail(x * 0.0115 + 40.5, z * 0.0115 - 17.25) * 5.4 +
      detail(x * 0.033 - 8.5, z * 0.033 + 3.25) * 1.4 +
      rim(x, z)
    );
  }

  return { macro, height, tint };
}

export function buildTerrain(rng, hills, track) {
  /** Height with the railway corridor levelled in. */
  function heightAt(x, z) {
    const near = track.nearest(x, z);
    if (near.dist > CORRIDOR_BLEND) return hills.height(x, z);
    const w = smoothstep(CORRIDOR_FLAT, CORRIDOR_BLEND, near.dist);
    return lerp(near.y, hills.height(x, z), w);
  }

  const geometry = new THREE.PlaneGeometry(
    TERRAIN_SIZE,
    TERRAIN_SIZE,
    TERRAIN_SEGMENTS,
    TERRAIN_SEGMENTS
  );
  geometry.rotateX(-Math.PI / 2);

  const position = geometry.attributes.position;
  const count = position.count;
  const colors = new Float32Array(count * 3);

  const grassLight = new THREE.Color(LAND.grassLight);
  const grassMid = new THREE.Color(LAND.grassMid);
  const grassDeep = new THREE.Color(LAND.grassDeep);
  const grassDry = new THREE.Color(LAND.grassDry);
  const rock = new THREE.Color(LAND.rock);
  const sand = new THREE.Color(LAND.sand);
  const scratch = new THREE.Color();

  // Pass 1: heights.
  const heights = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const x = position.getX(i);
    const z = position.getZ(i);
    const y = heightAt(x, z);
    heights[i] = y;
    position.setY(i, y);
  }

  // Pass 2: colours, using the grid neighbours for slope.
  const row = TERRAIN_SEGMENTS + 1;
  const step = TERRAIN_SIZE / TERRAIN_SEGMENTS;
  for (let i = 0; i < count; i++) {
    const col = i % row;
    const line = (i / row) | 0;
    const hL = heights[col > 0 ? i - 1 : i];
    const hR = heights[col < row - 1 ? i + 1 : i];
    const hD = heights[line > 0 ? i - row : i];
    const hU = heights[line < row - 1 ? i + row : i];
    const slope = Math.hypot(hR - hL, hU - hD) / (2 * step);

    const x = position.getX(i);
    const z = position.getZ(i);
    const y = heights[i];

    const variation = hills.tint(x * 0.02, z * 0.02) * 0.5 + 0.5;
    scratch.copy(grassMid).lerp(grassLight, variation);
    scratch.lerp(grassDeep, smoothstep(0.35, 0.9, hills.tint(x * 0.006 + 11, z * 0.006 - 5) + 0.5) * 0.55);
    scratch.lerp(grassDry, clamp((y - 12) / 16, 0, 1) * 0.5);
    scratch.lerp(rock, smoothstep(0.55, 1.25, slope));
    scratch.lerp(sand, smoothstep(WATER_LEVEL + 3.0, WATER_LEVEL - 1.0, y) * 0.9);

    const near = track.nearest(x, z);
    if (near.dist < 11) {
      scratch.lerp(grassDry, (1 - smoothstep(4, 11, near.dist)) * 0.35);
    }

    colors[i * 3] = scratch.r;
    colors[i * 3 + 1] = scratch.g;
    colors[i * 3 + 2] = scratch.b;
  }

  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();

  const ground = new THREE.Mesh(
    geometry,
    new THREE.MeshLambertMaterial({ vertexColors: true })
  );
  ground.castShadow = false;
  ground.receiveShadow = true;
  ground.name = 'terrain';

  const water = new THREE.Mesh(
    new THREE.PlaneGeometry(TERRAIN_SIZE + 120, TERRAIN_SIZE + 120, 1, 1).rotateX(-Math.PI / 2),
    new THREE.MeshPhongMaterial({
      color: LAND.water,
      shininess: 90,
      specular: 0x99c8ff,
      transparent: true,
      opacity: 0.88,
    })
  );
  water.position.y = WATER_LEVEL;
  water.receiveShadow = false;
  water.castShadow = false;
  water.name = 'water';

  const group = new THREE.Group();
  group.name = 'landscape';
  group.add(ground, water);

  return { group, ground, water, heightAt };
}
