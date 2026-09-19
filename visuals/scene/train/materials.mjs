/**
 * The train's material set.
 *
 * Built once per `createTrain` and shared by every part, which keeps the whole
 * consist down to a couple of dozen draw calls even though it is assembled from
 * a few thousand individual pieces.
 *
 * Livery: crimson lake with black frames, gold lining, brass fittings and a
 * copper-capped chimney — a fine-scale model railway livery rather than a toy
 * one. Crimson reads cleanly against grass, which is what the world is mostly
 * made of.
 */

import * as THREE from 'three';
import { painted, solid, tune } from './deps.mjs';
import { grimeTexture, brushedTexture, woodTextures, interiorTexture } from './textures.mjs';

export const LIVERY = {
  crimson: 0x8f2029,
  crimsonDeep: 0x651118,
  black: 0x16181b,
  graphite: 0x2a2d31,
  gold: 0xb08a30,
  cream: 0xe8ddc4,
  brass: 0xc09a45,
  copper: 0xac6338,
  steel: 0xcdd3d8,
  steelDull: 0x8b9299,
  iron: 0x23262a,
  tyre: 0x969da4,
  wood: 0x8a6440,
  coalBlack: 0x0e1012,
  seat: 0x5d2a28,
  roofCanvas: 0x9b978c,
  glass: 0xbcd4dc,
  rust: 0x6f4227,
  coachTeak: 0x7a4a22,
  coachCream: 0xdccfae,
  vanBrown: 0x4a3323,
};

/**
 * @param {number} seed
 * @param {object} [lib] Part A's material module if the caller already has it
 *                       loaded; falls back to whatever `deps.mjs` resolved.
 */
export function createMaterialSet(seed, lib = null) {
  const paintedFn = lib && typeof lib.painted === 'function' ? lib.painted : painted;
  const solidFn = lib && typeof lib.solid === 'function' ? lib.solid : solid;

  const grime = grimeTexture(seed + 1, { contrast: 0.42, bias: 0.56 });
  const grimeFine = grimeTexture(seed + 2, { contrast: 0.6, bias: 0.5, size: 256 });
  const brushed = brushedTexture(seed + 3);
  const wood = woodTextures(seed + 4);
  const woodCrate = woodTextures(seed + 5, { planks: 4 });
  const interior = interiorTexture(seed + 6);

  // Enamel over a filled, rubbed-down surface: the coat is glossy but never
  // mirror-smooth, and the gloss itself is slightly uneven. A perfect clearcoat
  // is what makes CG paint read as injection-moulded plastic.
  const enamel = (color, extra = {}) =>
    tune(paintedFn({ color, roughness: 0.36, clearcoat: true }), {
      roughnessMap: grime,
      clearcoatRoughness: 0.1,
      clearcoatRoughnessMap: grimeFine,
      envMapIntensity: 1.1,
      ...extra,
    });

  const metal = (color, roughness, metalness, extra = {}) =>
    tune(solidFn({ color, roughness, metalness }), {
      roughnessMap: brushed,
      envMapIntensity: 1.25,
      ...extra,
    });

  const m = {
    // --- painted bodywork -------------------------------------------------
    crimson: enamel(LIVERY.crimson),
    crimsonDeep: enamel(LIVERY.crimsonDeep, { roughness: 0.34 }),
    black: enamel(LIVERY.black, { roughness: 0.4, clearcoatRoughness: 0.12 }),
    cream: enamel(LIVERY.cream, { roughness: 0.32 }),
    // Lining is *paint*, not gilding: a straw yellow reads at a distance where
    // metallic gold just goes dark against crimson.
    lining: enamel(0xe3c86c, { roughness: 0.3 }),
    teak: enamel(LIVERY.coachTeak, { roughness: 0.28 }),
    coachCream: enamel(LIVERY.coachCream, { roughness: 0.33 }),
    vanBrown: enamel(LIVERY.vanBrown, { roughness: 0.38 }),

    // --- metals -----------------------------------------------------------
    gold: metal(LIVERY.gold, 0.3, 0.85),
    brass: metal(LIVERY.brass, 0.28, 1.0),
    brassDull: metal(LIVERY.brass, 0.44, 0.95),
    copper: metal(LIVERY.copper, 0.34, 1.0),
    steel: metal(LIVERY.steel, 0.16, 1.0, { envMapIntensity: 1.5 }),
    steelDull: metal(LIVERY.steelDull, 0.42, 0.95),
    iron: metal(LIVERY.iron, 0.55, 0.7, { roughnessMap: grime }),
    graphite: tune(solidFn({ color: LIVERY.graphite, roughness: 0.66, metalness: 0.45 }), {
      roughnessMap: grime,
      envMapIntensity: 0.95,
    }),
    tyre: metal(LIVERY.tyre, 0.36, 1.0, { envMapIntensity: 1.0 }),
    // The wheel centre has to sit a clear step lighter than the frames behind
    // it, or the spokes vanish into the shadow under the running plate.
    wheelIron: metal(0x3a3f45, 0.5, 0.8),
    rust: tune(solidFn({ color: LIVERY.rust, roughness: 0.92, metalness: 0.25 }), { roughnessMap: grime }),

    // --- other surfaces ---------------------------------------------------
    wood: tune(solidFn({ color: 0xffffff, roughness: 0.85, metalness: 0.0 }), {
      map: wood.map,
      roughnessMap: wood.roughnessMap,
    }),
    crate: tune(solidFn({ color: 0xffffff, roughness: 0.9, metalness: 0.0 }), {
      map: woodCrate.map,
      roughnessMap: woodCrate.roughnessMap,
    }),
    coal: tune(solidFn({ color: LIVERY.coalBlack, roughness: 0.38, metalness: 0.2 }), {
      roughnessMap: grimeFine,
    }),
    seat: tune(solidFn({ color: LIVERY.seat, roughness: 0.68, metalness: 0.0 }), { roughnessMap: grime }),
    canvasRoof: tune(solidFn({ color: LIVERY.roofCanvas, roughness: 0.9, metalness: 0.0 }), {
      roughnessMap: grime,
    }),

    // --- glass and light --------------------------------------------------
    glass: new THREE.MeshPhysicalMaterial({
      color: LIVERY.glass,
      roughness: 0.045,
      metalness: 0.0,
      transparent: true,
      opacity: 0.28,
      clearcoat: 1.0,
      clearcoatRoughness: 0.02,
      envMapIntensity: 1.3,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
    interior: tune(solidFn({ color: 0xffffff, roughness: 0.95, metalness: 0.0 }), { map: interior }),
    lampGlass: new THREE.MeshPhysicalMaterial({
      color: 0xfff0c0,
      emissive: 0xffcf72,
      emissiveIntensity: 2.4,
      roughness: 0.18,
      metalness: 0.0,
      transparent: true,
      opacity: 0.86,
    }),
    fireGlow: new THREE.MeshStandardMaterial({
      color: 0x2a0a02,
      emissive: 0xff6a17,
      emissiveIntensity: 3.2,
      roughness: 1.0,
      metalness: 0.0,
    }),
  };

  // Named so that mesh names read as `loco:crimson` in a debugger or profiler.
  for (const [key, value] of Object.entries(m)) {
    if (value && value.isMaterial) value.name = key;
  }

  m._textures = { grime, grimeFine, brushed, wood, woodCrate, interior };
  return m;
}

/** Free every texture and material this module made. */
export function disposeMaterialSet(set) {
  for (const [key, value] of Object.entries(set)) {
    if (key === '_textures') continue;
    if (value && value.isMaterial) value.dispose();
  }
  const t = set._textures || {};
  for (const value of Object.values(t)) {
    if (!value) continue;
    if (value.isTexture) value.dispose();
    else for (const inner of Object.values(value)) if (inner && inner.isTexture) inner.dispose();
  }
}
