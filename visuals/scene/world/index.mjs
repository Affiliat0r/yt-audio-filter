/**
 * Part C — the world.
 *
 *   const world = createWorld(scene, { seed, rng, materials, geom });
 *   world.update(t, trainState);
 *   world.track.getPointAt(u), world.track.getTangentAt(u)
 *
 * `createWorld` returns a fully built, fully textured world **synchronously**,
 * so a caller that does not await it is never left with a Promise. It is also
 * a thenable: `await createWorld(...)` additionally tries Part A's `pbr()` and
 * swaps photoscanned maps onto the same materials before resolving. Either way
 * the object you get back has `update` and `track` on it.
 *
 * Determinism: generation is a pure function of `seed`, and `update(t, ...)`
 * is a pure function of `(t, trainState)`. Nothing in this directory reads a
 * clock, a frame counter or `Math.random`. Re-rendering frame 150 after frame
 * 900 produces the identical image.
 */

import * as THREE from 'three';
import { Rng, asRng } from './rng.mjs';
import { resolveGeom } from './deps.mjs';
import { createMaterials } from './materials.mjs';
import { createWind } from './wind.mjs';
import { createTrack } from './track.mjs';
import { createHills, createTerrain, TERRAIN_SIZE, WATER_LEVEL } from './terrain.mjs';
import { createVegetation } from './vegetation.mjs';
import { createProps } from './props.mjs';
import { createCharacters } from './characters.mjs';

export { TERRAIN_SIZE, WATER_LEVEL };

function countTriangles(root) {
  let tris = 0;
  let draws = 0;
  root.traverse((o) => {
    if (!o.isMesh) return;
    const g = o.geometry;
    const n = g.index ? g.index.count / 3 : g.attributes.position.count / 3;
    tris += n * (o.isInstancedMesh ? o.count : 1);
    draws += 1;
  });
  return { triangles: Math.round(tris), drawCalls: draws };
}

/**
 * @param {THREE.Scene} scene
 * @param {{
 *   seed?: number,
 *   rng?: object,
 *   materials?: object,   // Part A's visuals/lib/materials.mjs, if it exists
 *   geom?: object,        // Part A's visuals/lib/geom.mjs, if it exists
 *   textureSize?: number, // procedural map resolution, default 512
 *   density?: number,     // vegetation multiplier, default 1
 *   characters?: number,
 * }} opts
 */
export function createWorld(scene, opts = {}) {
  const seed = (opts.seed ?? 1) >>> 0;
  const rng = asRng(opts.rng, seed);
  const geom = resolveGeom(opts.geom);

  const materials = createMaterials({ seed, textureSize: opts.textureSize ?? 512 });
  const wind = createWind({ dirX: 0.82, dirZ: 0.57 });

  const hills = createHills(rng.fork());
  const track = createTrack({
    rng: rng.fork(),
    materials,
    geom,
    macroHeight: hills.macro,
  });
  const terrain = createTerrain({ rng: rng.fork(), materials, geom, track, hills });
  const vegetation = createVegetation({
    rng: rng.fork(),
    materials,
    geom,
    track,
    terrain,
    wind,
    density: opts.density ?? 1,
  });
  const props = createProps({ rng: rng.fork(), materials, geom, track, terrain, wind });
  const characters = createCharacters({
    rng: rng.fork(),
    materials,
    geom,
    track,
    terrain,
    count: opts.characters ?? 16,
  });

  const group = new THREE.Group();
  group.name = 'world';
  group.add(terrain.group, track.group, vegetation.group, props.group, characters.group);
  if (scene) scene.add(group);

  const stats = {
    ...countTriangles(group),
    trees: vegetation.stats.trees,
    grassTufts: vegetation.stats.grass,
    shrubs: vegetation.stats.shrubs,
    rocks: props.stats.rocks,
    buildings: props.stats.buildings,
    housePositions: props.stats.housePositions,
    characters: characters.count,
    trackLength: Math.round(track.length),
    windMaterials: wind.materialCount,
  };

  const _trainPos = new THREE.Vector3();

  /**
   * Advance the world to time `t`.
   * @param {number} t seconds
   * @param {{position?:THREE.Vector3, headPosition?:THREE.Vector3}} [trainState]
   */
  function update(t, trainState) {
    wind.update(t);
    terrain.update(t);
    props.update(t);
    let target = null;
    if (trainState) {
      const p = trainState.headPosition || trainState.position;
      if (p) target = _trainPos.copy(p);
    }
    characters.update(t, target);
  }

  update(0, null);

  const world = {
    group,
    track,
    terrain,
    materials,
    wind,
    stats,
    update,
  };

  /**
   * Thenable: awaiting the world additionally tries Part A's photoscanned
   * materials, but only when the caller actually hands us the factory —
   * wiring it in is the signal that its cache is warm. Never dynamic-imports
   * it speculatively, because a cold `pbr()` would go to the network and the
   * contract says renders must work with the network off.
   *
   * Resolves to a plain object (no `then`), so `await` terminates.
   */
  world.then = (onFulfilled, onRejected) =>
    Promise.resolve(opts.materials ? materials.upgrade(opts.materials) : 0)
      .then((n) => {
        const plain = { group, track, terrain, materials, wind, update, stats };
        plain.stats.pbrUpgraded = n;
        return plain;
      })
      .then(onFulfilled, onRejected);

  return world;
}

export default createWorld;
