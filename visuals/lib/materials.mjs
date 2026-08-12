/**
 * lib/materials.mjs — PBR material factory.
 *
 * Three ways to get a material:
 *
 *   await pbr('aerial_grass_rock', {repeat: [0.35, 0.35]})   photoscanned surface
 *   solid({color: 0x8a8f7a, roughness: 0.9})                 untextured
 *   painted({color: 0x8b1a1a})                               toy-train enamel
 *
 * ---------------------------------------------------------------------------
 * COLOUR SPACE — the bug this file exists to prevent
 * ---------------------------------------------------------------------------
 * Albedo is sRGB. Normal, roughness, metalness, AO and displacement are raw
 * numbers and must be Linear. Tag them wrong and the render comes out either
 * washed out or muddy, and it is very hard to spot after the fact because
 * nothing errors. `colorSpace` is therefore set explicitly on every single
 * texture below, never left to the loader's default.
 *
 * ---------------------------------------------------------------------------
 * Poly Haven ARM maps
 * ---------------------------------------------------------------------------
 * Poly Haven ships an `arm` map packing AO in R, roughness in G and metalness
 * in B — the same layout glTF uses, and the same channels three reads. One
 * texture therefore drives `aoMap` + `roughnessMap`, which is a third of the
 * texture memory of three separate greyscale maps.
 *
 * Metalness is deliberately NOT taken from the ARM blue channel by default:
 * on a non-metal scan that channel is black, and JPEG ringing around bright
 * detail can lift it enough to make grass faintly metallic. Only slugs with a
 * dedicated `metal` map in the manifest get a `metalnessMap`.
 *
 * ---------------------------------------------------------------------------
 * UVs
 * ---------------------------------------------------------------------------
 * `geom.mjs` emits UVs in metres, so `repeat` here reads as "texture tiles per
 * metre": `repeat: [0.5, 0.5]` is a 2 m texture. When `repeat` is omitted the
 * manifest's per-slug suggestion is used, which puts every scan at roughly its
 * real-world scale — consistent texel density across the scene is a large part
 * of what stops a render reading as programmer art.
 *
 * `aoMap` samples UV channel 1 (the `uv1` attribute). Everything `geom.mjs`
 * builds carries it; geometry built any other way must be passed through
 * `geom.ensureUV2()` first, or pass `{aoChannel: 0}` here.
 *
 * ---------------------------------------------------------------------------
 * Visible tiling on large surfaces — for whoever builds the terrain
 * ---------------------------------------------------------------------------
 * One tiled scan across tens of metres of ground reads as a repeating grid, and
 * that grid is as strong a "this is CG" tell as a sharp edge. Nothing here can
 * fix it on its own; break it up at the mesh level:
 *
 *   - split the terrain into chunks and give neighbouring chunks materials
 *     built with different `rotation` (0, PI/2, PI) and slightly different
 *     `repeat` — the same base textures are reused, so it costs no extra VRAM;
 *   - blend two ground slugs (`TEX.meadow` and `TEX.dirt`) with vertex colours
 *     or a second draw with a noise-driven alpha;
 *   - `color` multiplies the albedo map, so per-chunk tinting is free.
 *
 * A shader-level macro-variation hook was deliberately not added: `CSM` assigns
 * its own `onBeforeCompile`, and whichever ran last would silently win.
 */

import * as THREE from 'three';
import { texturePath, manifestMaps, suggestedRepeat, MAPS } from './assets.mjs';

/**
 * Anisotropic filtering level. 16 is the maximum every GPU we care about
 * supports and three clamps it to the hardware limit at upload time, so this
 * is already "the renderer max" without needing a renderer. `useRenderer()`
 * can raise it further on hardware that reports more.
 */
let anisotropy = 16;

/** Base textures, keyed `slug|res|map`. Cloned per material, so one upload. */
const textureCache = new Map();
/** Finished materials, keyed by slug + res + the options that affect them. */
const materialCache = new Map();
/** Every texture handed out, so `useRenderer()` and `disposeAll()` can reach them. */
const liveTextures = new Set();

const loader = new THREE.TextureLoader();

/**
 * Adopt the renderer's true anisotropy limit. Optional — the default of 16 is
 * already clamped by three at upload — but harmless and future-proof.
 */
export function useRenderer(renderer) {
  const max = renderer?.capabilities?.getMaxAnisotropy?.();
  if (!max || max <= anisotropy) return anisotropy;
  anisotropy = max;
  for (const t of liveTextures) {
    t.anisotropy = anisotropy;
    t.needsUpdate = true;
  }
  return anisotropy;
}

/** Current anisotropy level. */
export function getAnisotropy() {
  return anisotropy;
}

// ---------------------------------------------------------------------------
// Texture loading
// ---------------------------------------------------------------------------

/** Which maps are colour data (sRGB) rather than numbers (linear). */
const SRGB_MAPS = new Set(['diffuse']);

function loadTexture(slug, mapName, res) {
  const key = `${slug}|${res}|${mapName}`;
  let entry = textureCache.get(key);
  if (entry) return entry;

  const url = texturePath(slug, mapName, res);
  entry = new Promise((resolve, reject) => {
    loader.load(
      url,
      (tex) => {
        tex.colorSpace = SRGB_MAPS.has(mapName) ? THREE.SRGBColorSpace : THREE.NoColorSpace;
        tex.wrapS = THREE.RepeatWrapping;
        tex.wrapT = THREE.RepeatWrapping;
        tex.anisotropy = anisotropy;
        tex.generateMipmaps = true;
        tex.minFilter = THREE.LinearMipmapLinearFilter;
        tex.magFilter = THREE.LinearFilter;
        tex.name = `${slug}/${mapName}`;
        resolve(tex);
      },
      undefined,
      () => reject(new Error(`materials: could not load ${url}. Run the prefetch: node visuals/lib/assets.mjs`))
    );
  });
  textureCache.set(key, entry);
  return entry;
}

/**
 * A per-material view of a cached texture.
 *
 * `Texture.clone()` shares the underlying `Source`, so the pixels are uploaded
 * to the GPU exactly once no matter how many materials use different repeats
 * or UV channels of the same map.
 */
function view(base, repeat, offset, rotation, channel) {
  const t = base.clone();
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = base.colorSpace;
  t.anisotropy = anisotropy;
  t.repeat.set(repeat[0], repeat[1]);
  if (offset) t.offset.set(offset[0], offset[1]);
  if (rotation) {
    t.center.set(0.5, 0.5);
    t.rotation = rotation;
  }
  if (channel != null) t.channel = channel;
  t.needsUpdate = true;
  liveTextures.add(t);
  return t;
}

// ---------------------------------------------------------------------------
// pbr()
// ---------------------------------------------------------------------------

/**
 * A photoscanned PBR material.
 *
 * @param {string} slug                     Poly Haven slug (see `assets.TEX`)
 * @param {object} [opts]
 * @param {[number,number]} [opts.repeat]   tiles per metre; defaults to the
 *                                          manifest's suggestion for this slug
 * @param {string} [opts.res='2k']
 * @param {[number,number]} [opts.offset]
 * @param {number} [opts.rotation=0]        radians, about the UV centre
 * @param {string[]} [opts.maps]            override which maps to load
 * @param {number} [opts.aoChannel=1]       UV set for aoMap (`uv1`)
 * @param {number} [opts.displacementScale=0]  >0 attaches the displacement
 *                                          map; leave at 0 on low-poly meshes,
 *                                          where it would only tear the surface
 * @param {boolean} [opts.cache=true]
 * @param {...*} [overrides]                any MeshStandardMaterial property
 * @returns {Promise<THREE.MeshStandardMaterial>}
 */
export async function pbr(slug, opts = {}) {
  const {
    repeat = suggestedRepeat(slug),
    res = '2k',
    offset = null,
    rotation = 0,
    maps = null,
    aoChannel = 1,
    displacementScale = 0,
    displacementBias = null,
    cache = true,
    ...overrides
  } = opts;

  // Loading the displacement map costs 2k x 2k of VRAM for nothing when it is
  // not attached, so skip it unless the caller actually wants displacement.
  const wanted = (maps ? maps.slice() : manifestMaps(slug)).filter(
    (m) => m !== 'disp' || displacementScale > 0
  );
  const key = JSON.stringify([
    slug, res, repeat, offset, rotation, wanted, aoChannel, displacementScale, overrides,
  ]);
  if (cache && materialCache.has(key)) return materialCache.get(key);

  const build = (async () => {
    const loaded = {};
    await Promise.all(
      wanted.map(async (m) => {
        if (!MAPS[m]) return;
        try {
          loaded[m] = await loadTexture(slug, m, res);
        } catch (err) {
          // A missing secondary map should degrade, not abort the render.
          if (m === 'diffuse') throw err;
          console.warn(`materials: ${slug}/${m} unavailable — ${err.message}`);
        }
      })
    );

    const material = new THREE.MeshStandardMaterial({ name: `pbr:${slug}` });

    if (loaded.diffuse) material.map = view(loaded.diffuse, repeat, offset, rotation);

    if (loaded.normal) {
      material.normalMap = view(loaded.normal, repeat, offset, rotation);
      material.normalScale = new THREE.Vector2(1, 1);
    }

    // ARM: AO in R, roughness in G. Separate views so aoMap can sit on uv1
    // while roughnessMap stays on uv0 — both still share one GPU upload.
    const armSource = loaded.arm || null;
    if (armSource) {
      material.roughnessMap = view(armSource, repeat, offset, rotation, 0);
      material.aoMap = view(armSource, repeat, offset, rotation, aoChannel);
      material.roughness = 1;
      material.aoMapIntensity = 1;
    } else {
      if (loaded.rough) {
        material.roughnessMap = view(loaded.rough, repeat, offset, rotation, 0);
        material.roughness = 1;
      }
      if (loaded.ao) {
        material.aoMap = view(loaded.ao, repeat, offset, rotation, aoChannel);
        material.aoMapIntensity = 1;
      }
    }

    // Only slugs that ship a real metalness scan become metals.
    if (loaded.metal) {
      material.metalnessMap = view(loaded.metal, repeat, offset, rotation, 0);
      material.metalness = 1;
    } else {
      material.metalness = 0;
    }

    // Displacement costs a vertex texture fetch and needs a dense mesh to mean
    // anything, so it is opt-in rather than wired-and-zeroed.
    if (loaded.disp && displacementScale > 0) {
      material.displacementMap = view(loaded.disp, repeat, offset, rotation, 0);
      material.displacementScale = displacementScale;
      material.displacementBias =
        displacementBias == null ? -displacementScale / 2 : displacementBias;
    }

    material.envMapIntensity = 1;
    applyOverrides(material, overrides);
    material.needsUpdate = true;
    return material;
  })();

  if (cache) materialCache.set(key, build);
  return build;
}

/** Warm the texture cache for several slugs at once. */
export async function preload(slugs, opts = {}) {
  const { res = '2k' } = opts;
  await Promise.all(
    slugs.map((slug) =>
      Promise.all(manifestMaps(slug).map((m) => loadTexture(slug, m, res).catch(() => null)))
    )
  );
}

// ---------------------------------------------------------------------------
// solid() / painted() / metal()
// ---------------------------------------------------------------------------

/**
 * An untextured surface. Use for things too small or too far to justify a
 * texture fetch — but still give them a believable roughness; a uniform 0.5 on
 * everything is its own kind of programmer art.
 */
export function solid(opts = {}) {
  const { color = 0xcccccc, roughness = 0.8, metalness = 0, ...overrides } = opts;
  const m = new THREE.MeshStandardMaterial({ color, roughness, metalness });
  m.envMapIntensity = 1;
  applyOverrides(m, overrides);
  return m;
}

/**
 * Toy-train enamel: colour under a clear specular coat.
 *
 * The clearcoat lobe is the whole point. Painted metal is two surfaces — a
 * rough-ish pigment layer and a smooth varnish over it — and a single
 * MeshStandardMaterial can only be one of them. Drop the coat and the same
 * colour reads as moulded plastic.
 */
export function painted(opts = {}) {
  const {
    color = 0x8b1a1a,
    roughness = 0.35,
    metalness = 0,
    clearcoat = true,
    clearcoatRoughness = 0.06,
    ...overrides
  } = opts;
  const m = new THREE.MeshPhysicalMaterial({
    color,
    roughness,
    metalness,
    clearcoat: clearcoat === true ? 1 : clearcoat === false ? 0 : clearcoat,
    clearcoatRoughness,
  });
  m.envMapIntensity = 1;
  applyOverrides(m, overrides);
  return m;
}

/**
 * Bare metal — rails, brass, steel fittings. Metals take their colour from the
 * specular reflection, so `color` tints the reflection rather than a diffuse
 * albedo, and the environment map does almost all the work.
 */
export function metal(opts = {}) {
  const { color = 0xb8bcc0, roughness = 0.25, metalness = 1, ...overrides } = opts;
  const m = new THREE.MeshStandardMaterial({ color, roughness, metalness });
  m.envMapIntensity = 1;
  applyOverrides(m, overrides);
  return m;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/** Assign arbitrary material properties, coercing colours and vectors. */
function applyOverrides(material, overrides) {
  for (const [k, v] of Object.entries(overrides)) {
    if (v === undefined) continue;
    const current = material[k];
    if (current instanceof THREE.Color) current.set(v);
    else if (current instanceof THREE.Vector2 && Array.isArray(v)) current.set(v[0], v[1]);
    else if (current instanceof THREE.Vector2 && typeof v === 'number') current.set(v, v);
    else material[k] = v;
  }
}

/** Drop every cached texture and material. */
export function disposeAll() {
  for (const t of liveTextures) t.dispose();
  liveTextures.clear();
  for (const p of textureCache.values()) Promise.resolve(p).then((t) => t && t.dispose(), () => {});
  textureCache.clear();
  for (const p of materialCache.values()) Promise.resolve(p).then((m) => m && m.dispose(), () => {});
  materialCache.clear();
}

/** Counts, for debugging and for asserting the cache is doing its job. */
export function cacheStats() {
  return {
    textures: textureCache.size,
    materials: materialCache.size,
    views: liveTextures.size,
    anisotropy,
  };
}
