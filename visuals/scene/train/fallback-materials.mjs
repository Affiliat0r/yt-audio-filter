/**
 * Local stand-in for `visuals/lib/materials.mjs` (Part A).
 *
 * Same signatures as the contract. `deps.mjs` prefers the real module when it
 * exists. `pbr()` here is a deliberate no-op that resolves to a plain solid:
 * the train is painted metal, brass and steel, all of which we want authored
 * procedurally rather than photoscanned, so nothing in Part D depends on a
 * texture download.
 */

import * as THREE from 'three';

/** Toy-paint enamel: colour under a specular coat. */
export function painted({ color = 0xffffff, roughness = 0.35, clearcoat = true, ...rest } = {}) {
  return new THREE.MeshPhysicalMaterial({
    color,
    roughness,
    metalness: 0.0,
    clearcoat: clearcoat ? 1.0 : 0.0,
    clearcoatRoughness: 0.06,
    envMapIntensity: 1.0,
    ...rest,
  });
}

/** Plain opaque surface. */
export function solid({ color = 0xffffff, roughness = 0.8, metalness = 0.0, ...rest } = {}) {
  return new THREE.MeshStandardMaterial({
    color,
    roughness,
    metalness,
    envMapIntensity: 1.0,
    ...rest,
  });
}

/** Contract-shaped, texture-free. Part A's real one loads Poly Haven maps. */
export async function pbr(slug, { color = 0x999999, roughness = 0.8, metalness = 0.0, ...rest } = {}) {
  return solid({ color, roughness, metalness, ...rest });
}
