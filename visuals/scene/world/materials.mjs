/**
 * The world's material library.
 *
 * Every surface is a `MeshStandardMaterial` (or `MeshPhysicalMaterial` where a
 * clearcoat earns its keep) carrying a real albedo, a real tangent-space
 * normal map and per-texel roughness. Maps come from `world/textures.mjs`,
 * which generates them procedurally and deterministically from the seed.
 *
 * All geometry in Part C carries **UVs in metres**, so a material whose tile is
 * `s` metres across simply sets `repeat = 1/s`. That means one material can be
 * reused across wildly different object sizes without any per-object UV work.
 *
 * If Part A's `pbr()` and a warm asset cache turn up, `upgrade()` swaps
 * photoscanned maps onto the same materials in place. Anything that fails to
 * load silently keeps its procedural set.
 */

import * as THREE from 'three';
import { surface, foliageCard } from './textures.mjs';

/** Poly Haven slugs to try when Part A's `pbr()` is available. */
const PBR_SLUGS = {
  grass: 'aerial_grass_rock',
  dirt: 'brown_mud_leaves_01',
  rock: 'rock_boulder_dry',
  gravel: 'gravel_floor',
  barkConifer: 'bark_brown_02',
  barkBroadleaf: 'bark_willow',
  sleeperWood: 'wood_planks_dirt',
  plank: 'wood_planks',
  plaster: 'plaster_brick_pattern',
  brick: 'red_brick_03',
  roofTile: 'roof_tiles_14',
  stoneWall: 'castle_brick_broken_06',
};

function repeatOf(tex, metresPerTile) {
  const t = tex.clone();
  t.repeat.set(1 / metresPerTile, 1 / metresPerTile);
  t.needsUpdate = true;
  return t;
}

export function createMaterials(opts = {}) {
  const seed = opts.seed ?? 1;
  const size = opts.textureSize ?? 512;
  const upgrades = [];

  /**
   * Build a standard material from a named procedural surface.
   * `scale` is metres per texture tile; it defaults to the recipe's own.
   */
  /**
   * `roughness` here is a *multiplier* on the map, so it stays at 1 unless a
   * surface is deliberately being made glossier or flatter than its recipe.
   */
  function std(name, o = {}) {
    const s = surface(name, { size: o.size ?? size, seed });
    const metres = o.scale ?? s.scale;
    const mat = new THREE.MeshStandardMaterial({
      map: repeatOf(s.map, metres),
      normalMap: repeatOf(s.normalMap, metres),
      roughnessMap: o.roughnessMap === false ? null : repeatOf(s.roughnessMap, metres),
      color: o.color ?? 0xffffff,
      roughness: o.roughness ?? 1.0,
      metalness: o.metalness ?? 0.0,
      vertexColors: o.vertexColors ?? false,
      side: o.side ?? THREE.FrontSide,
      envMapIntensity: o.envMapIntensity ?? 1.0,
      flatShading: false,
      dithering: true,
    });
    mat.normalScale.set(o.normalScale ?? 1, o.normalScale ?? 1);
    mat.name = name;
    if (PBR_SLUGS[name]) upgrades.push({ slug: PBR_SLUGS[name], metres, mat });
    return mat;
  }

  function foliage(card, o = {}) {
    const f = foliageCard(card, { size: o.size ?? size, seed });
    const mat = new THREE.MeshStandardMaterial({
      map: f.map,
      normalMap: f.normalMap,
      alphaTest: o.alphaTest ?? 0.42,
      // Alpha-to-coverage resolves the leaf edge against the MSAA samples
      // instead of a hard binary cut. Without it, minified mips average the
      // alpha below alphaTest and whole canopies dissolve at distance, letting
      // fog through and turning far woodland into pale smears.
      alphaToCoverage: true,
      side: THREE.DoubleSide,
      roughness: o.roughness ?? 0.9,
      metalness: 0,
      vertexColors: true,
      // Leaves lit hard by a bright sky go grey; hold the IBL back.
      envMapIntensity: 0.6,
      // Leaves are thin: without this the back faces of every card go black.
      shadowSide: THREE.DoubleSide,
    });
    mat.name = `foliage:${card}`;
    return mat;
  }

  const lib = {
    // --- ground --------------------------------------------------------
    grass: std('grass'),
    dirt: std('dirt'),
    sand: std('sand'),
    moss: std('moss'),

    // --- track ---------------------------------------------------------
    ballast: std('gravel', { normalScale: 1.9 }),
    /** Polished running surface: this is the highlight that says "steel". */
    railHead: (() => {
      const s = surface('steel', { size, seed });
      const m = new THREE.MeshStandardMaterial({
        map: repeatOf(s.map, 1.2),
        normalMap: repeatOf(s.normalMap, 1.2),
        color: 0xc6c8cb,
        // Deliberately uniform: the running face is burnished by the wheels,
        // and one unbroken highlight down its length is what reads as steel.
        roughness: 0.15,
        metalness: 1.0,
        envMapIntensity: 1.6,
      });
      m.normalScale.set(0.25, 0.25);
      m.name = 'railHead';
      return m;
    })(),
    railWeb: std('ironRust', { metalness: 0.7, color: 0x9a8877, scale: 0.9 }),
    sleeper: std('sleeperWood', { normalScale: 1.35 }),

    // --- rock and masonry ---------------------------------------------
    rock: std('rock', { vertexColors: true, normalScale: 1.3 }),
    plaster: std('plaster', { vertexColors: true }),
    brick: std('brick', { normalScale: 1.15 }),
    stoneWall: std('stoneWall', { normalScale: 1.2 }),
    roofTile: std('roofTile', { vertexColors: true, normalScale: 1.2 }),
    roofSlate: std('roofSlate', { normalScale: 1.0 }),

    // --- timber --------------------------------------------------------
    plank: std('plank', { vertexColors: true }),
    /** Sawn timber, no board seams — posts, poles, logs, sleepers of hay. */
    timber: std('timber', { vertexColors: true }),
    plankPainted: std('plankPainted', { vertexColors: true }),

    // --- metal ---------------------------------------------------------
    steel: std('steel', { metalness: 1.0, envMapIntensity: 1.3 }),
    iron: std('ironRust', { metalness: 0.65, vertexColors: true }),
    paintedMetal: (() => {
      const s = surface('paintedMetal', { size, seed });
      const m = new THREE.MeshPhysicalMaterial({
        map: repeatOf(s.map, 1.4),
        normalMap: repeatOf(s.normalMap, 1.4),
        roughnessMap: repeatOf(s.roughnessMap, 1.4),
        roughness: 1.0,
        metalness: 0.1,
        clearcoat: 0.55,
        clearcoatRoughness: 0.22,
        vertexColors: true,
        envMapIntensity: 1.2,
      });
      m.name = 'paintedMetal';
      return m;
    })(),

    // --- glazing -------------------------------------------------------
    glass: new THREE.MeshPhysicalMaterial({
      color: 0x121a22,
      roughness: 0.06,
      metalness: 0.0,
      clearcoat: 1.0,
      clearcoatRoughness: 0.03,
      envMapIntensity: 2.2,
      reflectivity: 0.9,
    }),

    // --- people --------------------------------------------------------
    cloth: std('cloth', { vertexColors: true, roughness: 1.0 }),
    skin: std('skin', { vertexColors: true, roughness: 1.0, envMapIntensity: 0.9 }),
    hair: std('cloth', { vertexColors: true, roughness: 0.85, scale: 0.25 }),

    // --- bark ----------------------------------------------------------
    barkConifer: std('barkConifer', { vertexColors: true, normalScale: 1.25 }),
    barkBroadleaf: std('barkBroadleaf', { vertexColors: true, normalScale: 1.2 }),
    barkBirch: std('barkBirch', { vertexColors: true, normalScale: 0.9 }),

    // --- leaves --------------------------------------------------------
    needle: foliage('needleSpray', { alphaTest: 0.35, roughness: 0.92 }),
    leafBroad: foliage('leafBroad', { roughness: 0.88 }),
    leafBlossom: foliage('leafBlossom', { roughness: 0.86 }),
    leafBirch: foliage('leafBirch', { roughness: 0.88 }),
    scrub: foliage('scrub', { roughness: 0.9 }),

    /** Raw texture sets, for the terrain splat shader and the water surface. */
    tex: {
      grass: surface('grass', { size, seed }),
      dryGrass: surface('sand', { size, seed }),
      dirt: surface('dirt', { size, seed }),
      rock: surface('rock', { size, seed }),
      macro: surface('macro', { size: Math.min(size, 512), seed }),
      water: surface('water', { size: Math.min(size, 512), seed }),
    },

    /** Slot table the async upgrade walks. */
    _upgrades: upgrades,
  };

  /**
   * Swap photoscanned maps in, when Part A's factory and cache are available.
   * Every slug is tried independently: a miss keeps the procedural material.
   * @returns {Promise<number>} how many materials were upgraded
   */
  lib.upgrade = async function upgrade(materialsLib) {
    if (!materialsLib || typeof materialsLib.pbr !== 'function') return 0;
    let n = 0;
    for (const entry of upgrades) {
      try {
        const real = await materialsLib.pbr(entry.slug, {
          repeat: [1 / entry.metres, 1 / entry.metres],
          res: '2k',
        });
        if (!real || !real.map) continue;
        const m = entry.mat;
        m.map = real.map;
        m.normalMap = real.normalMap ?? m.normalMap;
        m.roughnessMap = real.roughnessMap ?? m.roughnessMap;
        if (real.roughnessMap === undefined && real.roughness !== undefined) {
          m.roughness = real.roughness;
        }
        m.aoMap = real.aoMap ?? null;
        m.needsUpdate = true;
        n++;
      } catch {
        // Slug missing from the cache, or Part A not finished — keep procedural.
      }
    }
    return n;
  };

  return lib;
}
