/**
 * Procedural PBR texture library.
 *
 * Part A's `visuals/lib/materials.mjs` supplies photoscanned Poly Haven maps
 * when it and its asset cache are present. This module is the guaranteed
 * floor: every surface in the world gets a real albedo, a real tangent-space
 * normal map and real per-texel roughness even with the network off, an empty
 * asset cache, and Part A not yet landed.
 *
 * Convention used throughout:
 *   `map`        RGBA, sRGB.  RGB = albedo, **A = roughness**.
 *   `normalMap`  RGBA, linear. Tangent-space, OpenGL (+Y up) convention.
 *
 * Packing roughness into the albedo alpha halves the sampler count in the
 * terrain splat shader and costs nothing: opaque materials never read that
 * channel, and sRGB decode applies to RGB only, so the value survives intact.
 *
 * Every field is periodic over the tile, so all of these repeat seamlessly.
 */

import * as THREE from 'three';
import { Rng, makeNoise2D, makeWorley2D, fbm, ridged, smoothstep, clamp, lerp } from './rng.mjs';

const DEFAULT_SIZE = 512;
const cache = new Map();

function nameHash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function makeTexture(data, size, { srgb = false, aniso = 8 }) {
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat, THREE.UnsignedByteType);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.anisotropy = aniso;
  tex.needsUpdate = true;
  return tex;
}

/** Tangent-space normal map from a wrapped height field. */
function heightToNormal(height, size, strength) {
  const data = new Uint8Array(size * size * 4);
  const w = size;
  for (let y = 0; y < size; y++) {
    const yp = ((y + 1) % size) * w;
    const ym = ((y - 1 + size) % size) * w;
    const y0 = y * w;
    for (let x = 0; x < size; x++) {
      const xp = (x + 1) % size;
      const xm = (x - 1 + size) % size;
      const dx = (height[y0 + xp] - height[y0 + xm]) * strength;
      const dy = (height[yp + x] - height[ym + x]) * strength;
      const inv = 1 / Math.sqrt(dx * dx + dy * dy + 1);
      const nx = -dx * inv;
      const ny = -dy * inv;
      const nz = inv;
      const o = (y0 + x) * 4;
      data[o] = Math.round((nx * 0.5 + 0.5) * 255);
      data[o + 1] = Math.round((ny * 0.5 + 0.5) * 255);
      data[o + 2] = Math.round((nz * 0.5 + 0.5) * 255);
      data[o + 3] = 255;
    }
  }
  return data;
}

const _out = { r: 0.5, g: 0.5, b: 0.5, rough: 0.8, h: 0.5 };

/**
 * Rasterise a recipe into { map, normalMap }.
 * `recipe.sample(u, v, ctx, out)` fills `out` with sRGB rgb in 0..1, a
 * roughness in 0..1, and a height in roughly 0..1 for the normal map.
 */
function buildSurface(key, recipe, size, seed) {
  const rng = new Rng((nameHash(key) ^ (seed >>> 0)) >>> 0);
  const ctx = recipe.init ? recipe.init(rng) : {};
  const albedo = new Uint8Array(size * size * 4);
  const rough = new Uint8Array(size * size * 4);
  const height = new Float32Array(size * size);
  const inv = 1 / size;
  for (let y = 0; y < size; y++) {
    const v = y * inv;
    for (let x = 0; x < size; x++) {
      const i = y * size + x;
      recipe.sample(x * inv, v, ctx, _out);
      const o = i * 4;
      albedo[o] = clamp(_out.r, 0, 1) * 255;
      albedo[o + 1] = clamp(_out.g, 0, 1) * 255;
      albedo[o + 2] = clamp(_out.b, 0, 1) * 255;
      // Alpha carries roughness too, which is what the terrain splat reads.
      const rv = clamp(_out.rough, 0, 1) * 255;
      albedo[o + 3] = rv;
      // three's roughnessmap_fragment samples the GREEN channel, so the
      // dedicated map is grey — putting roughness in the albedo's alpha and
      // pointing roughnessMap at it silently reads albedo green instead, and
      // every dark material turns into a mirror.
      rough[o] = rv;
      rough[o + 1] = rv;
      rough[o + 2] = rv;
      rough[o + 3] = 255;
      height[i] = _out.h;
    }
  }
  return {
    map: makeTexture(albedo, size, { srgb: true }),
    roughnessMap: makeTexture(rough, size, { srgb: false }),
    normalMap: makeTexture(heightToNormal(height, size, (recipe.normalStrength ?? 3) * size * 0.004), size, {
      srgb: false,
    }),
    /** Metres per tile — callers turn this into a `repeat`. */
    scale: recipe.scale ?? 2,
  };
}

/** Fetch (and memoise) one of the named surfaces below. */
export function surface(name, opts = {}) {
  const size = opts.size ?? DEFAULT_SIZE;
  const seed = opts.seed ?? 1;
  const key = `${name}|${size}|${seed}`;
  let hit = cache.get(key);
  if (!hit) {
    const recipe = RECIPES[name];
    if (!recipe) throw new Error(`world/textures: no recipe "${name}"`);
    hit = buildSurface(name, recipe, size, seed);
    cache.set(key, hit);
  }
  return hit;
}

/** Metres per texture tile for a named surface, without building it. */
export function surfaceScale(name) {
  return RECIPES[name]?.scale ?? 2;
}

// ===========================================================================
// Recipes
// ===========================================================================

const RECIPES = {};

// --- ground ---------------------------------------------------------------

RECIPES.grass = {
  scale: 2.2,
  normalStrength: 2.2,
  init(rng) {
    return { n: makeNoise2D(rng), m: makeNoise2D(rng), blade: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    // Contrast-stretched: a low-contrast ground layer averages out to the
    // flat colour v1 shipped with, however good the rest of the shading is.
    const clump = smoothstep(0.22, 0.80, fbm(c.n, u * 8, v * 8, 4) * 0.5 + 0.5);
    const patch = c.m(u * 4, v * 4) * 0.5 + 0.5;
    const fine = fbm(c.blade, u * 96, v * 32, 3);
    const dry = smoothstep(0.55, 0.85, patch);
    // Deep shadowed green -> sunlit yellow-green, then dried straw in patches.
    let r = lerp(0.125, 0.395, clump);
    let g = lerp(0.235, 0.605, clump);
    let b = lerp(0.070, 0.165, clump);
    r = lerp(r, 0.60, dry * 0.75);
    g = lerp(g, 0.56, dry * 0.75);
    b = lerp(b, 0.29, dry * 0.75);
    const speck = fine * 0.12;
    out.r = r + speck;
    out.g = g + speck * 1.15;
    out.b = b + speck * 0.5;
    out.rough = 0.93 - clump * 0.06;
    out.h = clump * 0.55 + fine * 0.45 + 0.2 * (c.blade(u * 40, v * 160) * 0.5 + 0.5);
  },
};

RECIPES.dirt = {
  scale: 2.6,
  normalStrength: 3.4,
  init(rng) {
    return { n: makeNoise2D(rng), w: makeWorley2D(rng, 16), g: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const cell = c.w(u * 16, v * 16);
    const pebble = smoothstep(0.30, 0.06, cell.f1);
    const grain = fbm(c.n, u * 24, v * 24, 4) * 0.5 + 0.5;
    const macro = fbm(c.g, u * 3, v * 3, 3) * 0.5 + 0.5;
    const tone = lerp(0.38, 0.62, macro * 0.6 + grain * 0.4);
    out.r = tone * lerp(1.0, 0.88, pebble);
    out.g = tone * 0.79 * lerp(1.0, 0.91, pebble);
    out.b = tone * 0.60 * lerp(1.0, 0.95, pebble);
    out.rough = 0.95 - pebble * 0.2;
    out.h = grain * 0.35 + pebble * 0.65;
  },
};

RECIPES.rock = {
  scale: 3.4,
  normalStrength: 5.0,
  init(rng) {
    return { n: makeNoise2D(rng), s: makeNoise2D(rng), c: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const strata = ridged(c.s, u * 5, v * 11, 4);
    const rough = fbm(c.n, u * 20, v * 20, 5) * 0.5 + 0.5;
    const crack = smoothstep(0.86, 0.98, ridged(c.c, u * 7, v * 7, 3));
    const tone = lerp(0.30, 0.63, strata * 0.55 + rough * 0.45);
    const warm = fbm(c.c, u * 2, v * 2, 2) * 0.5 + 0.5;
    out.r = tone * lerp(0.94, 1.06, warm) * (1 - crack * 0.55);
    out.g = tone * lerp(0.96, 1.0, warm) * (1 - crack * 0.55);
    out.b = tone * lerp(1.02, 0.92, warm) * (1 - crack * 0.5);
    out.rough = 0.72 + rough * 0.2 - crack * 0.05;
    out.h = strata * 0.45 + rough * 0.35 - crack * 0.6;
  },
};

RECIPES.sand = {
  scale: 1.8,
  normalStrength: 1.6,
  init(rng) {
    return { n: makeNoise2D(rng), r: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const ripple = Math.sin((u * 18 + fbm(c.r, u * 3, v * 3, 2) * 3) * Math.PI * 2) * 0.5 + 0.5;
    const grain = fbm(c.n, u * 128, v * 128, 2) * 0.5 + 0.5;
    const tone = lerp(0.375, 0.575, ripple * 0.4 + grain * 0.6);
    out.r = tone;
    out.g = tone * 0.90;
    out.b = tone * 0.70;
    out.rough = 0.96;
    out.h = ripple * 0.5 + grain * 0.5;
  },
};

/** Track ballast — angular crushed stone, the single most-seen close-up. */
RECIPES.gravel = {
  scale: 0.86,
  normalStrength: 9.0,
  init(rng) {
    return {
      w: makeWorley2D(rng, 14),
      w2: makeWorley2D(rng, 30),
      n: makeNoise2D(rng),
      d: makeNoise2D(rng),
    };
  },
  sample(u, v, c, out) {
    // Warp the lattice so the stones read angular rather than as bubbles.
    const wx = u * 14 + fbm(c.d, u * 6, v * 6, 2) * 0.5;
    const wy = v * 14 + fbm(c.d, u * 6 + 31, v * 6 - 17, 2) * 0.5;
    const cell = c.w(wx, wy);
    const small = c.w2(u * 30 + 5, v * 30 - 3);
    const edge = smoothstep(0.0, 0.16, cell.f2 - cell.f1);
    const dome = clamp(1 - cell.f1 * 2.1, 0, 1);
    const smallDome = clamp(1 - small.f1 * 2.6, 0, 1);
    const grain = fbm(c.n, u * 90, v * 90, 3) * 0.5 + 0.5;
    // Per-stone value so the bed reads as many separate stones.
    const value = lerp(0.17, 0.44, cell.id) * lerp(0.82, 1.18, grain);
    const dust = smoothstep(0.35, 0.0, dome) * 0.4;
    const tone = lerp(value, 0.30, dust);
    out.r = tone * 1.06;
    out.g = tone * 0.99;
    out.b = tone * 0.90;
    out.rough = 0.94 - dome * 0.06 + dust * 0.04;
    out.h = dome * 0.7 + smallDome * 0.22 + grain * 0.12 - (1 - edge) * 0.35;
  },
};

RECIPES.moss = {
  scale: 1.4,
  normalStrength: 3.0,
  init(rng) {
    return { n: makeNoise2D(rng), f: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const clump = fbm(c.n, u * 14, v * 14, 4) * 0.5 + 0.5;
    const fine = fbm(c.f, u * 70, v * 70, 3) * 0.5 + 0.5;
    out.r = lerp(0.055, 0.185, clump) + fine * 0.05;
    out.g = lerp(0.12, 0.33, clump) + fine * 0.07;
    out.b = lerp(0.045, 0.10, clump) + fine * 0.03;
    out.rough = 0.96;
    out.h = clump * 0.6 + fine * 0.4;
  },
};

// --- bark -----------------------------------------------------------------

function barkRecipe({ scale, strength, dark, light, freqU, freqV, plates = 0 }) {
  return {
    scale,
    normalStrength: strength,
    init(rng) {
      return { n: makeNoise2D(rng), f: makeNoise2D(rng), w: makeWorley2D(rng, 10) };
    },
    sample(u, v, c, out) {
      // Bark furrows run along the trunk: stretch the domain hard in v.
      const warp = fbm(c.f, u * 3, v * 1.2, 2) * 0.35;
      const furrow = ridged(c.n, u * freqU + warp, v * freqV, 4);
      const fine = fbm(c.f, u * 60, v * 22, 3) * 0.5 + 0.5;
      let plate = 0;
      if (plates > 0) {
        const cell = c.w(u * 10 + warp * 2, v * 10 * plates);
        plate = smoothstep(0.02, 0.14, cell.f2 - cell.f1);
      }
      const t = clamp(furrow * 0.75 + fine * 0.25, 0, 1);
      const shade = plates > 0 ? lerp(0.7, 1.0, plate) : 1;
      out.r = lerp(dark[0], light[0], t) * shade;
      out.g = lerp(dark[1], light[1], t) * shade;
      out.b = lerp(dark[2], light[2], t) * shade;
      out.rough = 0.92 - t * 0.08;
      out.h = furrow * 0.8 + fine * 0.2 - (plates > 0 ? (1 - plate) * 0.4 : 0);
    },
  };
}

RECIPES.barkConifer = barkRecipe({
  scale: 0.9,
  strength: 5.5,
  dark: [0.145, 0.096, 0.062],
  light: [0.50, 0.345, 0.225],
  freqU: 5,
  freqV: 26,
  plates: 1.2,
});

RECIPES.barkBroadleaf = barkRecipe({
  scale: 1.3,
  strength: 5.0,
  dark: [0.072, 0.060, 0.046],
  light: [0.245, 0.212, 0.172],
  freqU: 6,
  freqV: 18,
});

RECIPES.barkBirch = {
  scale: 1.6,
  normalStrength: 1.8,
  init(rng) {
    return { n: makeNoise2D(rng), f: makeNoise2D(rng), l: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const base = fbm(c.f, u * 6, v * 4, 3) * 0.5 + 0.5;
    // Horizontal lenticels: short dark dashes, stretched across the trunk.
    const dash = smoothstep(0.58, 0.78, fbm(c.l, u * 9, v * 90, 2) * 0.5 + 0.5);
    const dashMask = smoothstep(0.45, 0.7, c.l(u * 4 + 11, v * 22) * 0.5 + 0.5);
    const dark = dash * dashMask;
    const peel = smoothstep(0.62, 0.8, fbm(c.n, u * 5, v * 9, 3) * 0.5 + 0.5);
    let tone = lerp(0.48, 0.72, base);
    tone = lerp(tone, 0.30, dark * 0.85);
    tone = lerp(tone, 0.52, peel * 0.4);
    out.r = tone;
    out.g = tone * 0.985;
    out.b = tone * 0.94;
    out.rough = 0.82 + dark * 0.08;
    out.h = base * 0.3 - dark * 0.5 + peel * 0.4;
  },
};

// --- timber ---------------------------------------------------------------

function woodRecipe({ scale, strength, dark, light, rings, planks, plankAxis = 'v' }) {
  return {
    scale,
    normalStrength: strength,
    init(rng) {
      return { n: makeNoise2D(rng), g: makeNoise2D(rng), s: makeNoise2D(rng) };
    },
    sample(u, v, c, out) {
      const p = plankAxis === 'v' ? v : u;
      const along = plankAxis === 'v' ? u : v;
      const plankIndex = Math.floor(p * planks);
      const plankT = p * planks - plankIndex;
      const jitter = (plankIndex * 0.618) % 1;
      // Grain: sinusoidal rings warped by noise, running along the plank.
      const warp = fbm(c.n, along * 3, p * 12, 3) * 0.6;
      const grain = Math.abs(Math.sin((p * rings + warp + jitter * 5) * Math.PI));
      const fibre = fbm(c.g, along * 70, p * 8, 3) * 0.5 + 0.5;
      const seam = smoothstep(0.03, 0.0, Math.min(plankT, 1 - plankT));
      const split = smoothstep(0.86, 0.98, fbm(c.s, along * 26, p * 3, 3) * 0.5 + 0.5);
      const t = clamp(grain * 0.62 + fibre * 0.38 + (jitter - 0.5) * 0.16, 0, 1);
      const shade = (1 - seam * 0.75) * (1 - split * 0.5);
      out.r = lerp(dark[0], light[0], t) * shade;
      out.g = lerp(dark[1], light[1], t) * shade;
      out.b = lerp(dark[2], light[2], t) * shade;
      out.rough = 0.88 - t * 0.12 + seam * 0.06;
      out.h = t * 0.5 + 0.5 - seam * 0.9 - split * 0.5;
    },
  };
}

/**
 * Creosoted railway sleeper.
 *
 * Two grain fields, one stretched along each axis, averaged. `beveledBox`
 * hands its top and side faces their UV axes in opposite orders, so a single
 * directional grain gives a sleeper a dark top and pale flanks — which is
 * exactly what the first pass produced.
 */
RECIPES.sleeperWood = {
  scale: 1.7,
  normalStrength: 4.4,
  init(rng) {
    return { n: makeNoise2D(rng), m: makeNoise2D(rng), g: makeNoise2D(rng), s: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const along = fbm(c.n, u * 8, v * 40, 4) * 0.5 + 0.5;
    const across = fbm(c.m, u * 40, v * 8, 4) * 0.5 + 0.5;
    const grain = (along + across) * 0.5;
    const wear = smoothstep(0.5, 0.88, fbm(c.g, u * 5, v * 5, 3) * 0.5 + 0.5);
    const split = smoothstep(0.86, 0.99, fbm(c.s, u * 20, v * 20, 3) * 0.5 + 0.5);
    const t = clamp(grain * 0.72 + wear * 0.34, 0, 1);
    const shade = 1 - split * 0.6;
    out.r = lerp(0.082, 0.268, t) * shade;
    out.g = lerp(0.064, 0.198, t) * shade;
    out.b = lerp(0.050, 0.146, t) * shade;
    out.rough = 0.93 - t * 0.08;
    out.h = 0.4 + t * 0.6 - split * 0.9;
  },
};

RECIPES.plank = woodRecipe({
  scale: 2.0,
  strength: 3.2,
  dark: [0.215, 0.165, 0.115],
  light: [0.56, 0.465, 0.345],
  rings: 9,
  planks: 6,
});

/**
 * Weathered sawn timber: fence posts, rails, telegraph poles, logs.
 *
 * Deliberately isotropic. `beveledBox` hands the +X and +Y faces their UV axes
 * in opposite orders, so any directional grain runs along the post on one face
 * and banded across it on the next. Low-contrast mottling reads correctly on
 * every face.
 */
RECIPES.timber = {
  scale: 1.1,
  normalStrength: 2.4,
  init(rng) {
    return { n: makeNoise2D(rng), g: makeNoise2D(rng), k: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const mottle = fbm(c.n, u * 11, v * 11, 4) * 0.5 + 0.5;
    const fibre = fbm(c.g, u * 64, v * 64, 3) * 0.5 + 0.5;
    const knot = smoothstep(0.80, 0.96, fbm(c.k, u * 6, v * 6, 2) * 0.5 + 0.5);
    const t = clamp(mottle * 0.62 + fibre * 0.38, 0, 1);
    const shade = 1 - knot * 0.45;
    out.r = lerp(0.20, 0.50, t) * shade;
    out.g = lerp(0.158, 0.405, t) * shade;
    out.b = lerp(0.108, 0.295, t) * shade;
    out.rough = 0.9 - t * 0.08;
    out.h = t * 0.7 + knot * 0.3;
  },
};

RECIPES.plankPainted = {
  scale: 2.0,
  normalStrength: 3.0,
  init(rng) {
    return { base: woodRecipe({ scale: 2, strength: 3, dark: [0.2, 0.14, 0.09], light: [0.5, 0.38, 0.25], rings: 9, planks: 6 }).init(rng), n: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const plankIndex = Math.floor(v * 6);
    const plankT = v * 6 - plankIndex;
    const seam = smoothstep(0.035, 0.0, Math.min(plankT, 1 - plankT));
    const wear = smoothstep(0.55, 0.9, fbm(c.n, u * 9, v * 9, 4) * 0.5 + 0.5);
    const grain = Math.abs(Math.sin((v * 40) * Math.PI)) * 0.06;
    out.r = (0.86 - wear * 0.22 + grain) * (1 - seam * 0.6);
    out.g = (0.83 - wear * 0.24 + grain) * (1 - seam * 0.6);
    out.b = (0.77 - wear * 0.26 + grain) * (1 - seam * 0.6);
    out.rough = 0.45 + wear * 0.4;
    out.h = 0.6 - seam * 0.9 - wear * 0.15;
  },
};

// --- masonry --------------------------------------------------------------

RECIPES.plaster = {
  scale: 3.0,
  normalStrength: 1.5,
  init(rng) {
    return { n: makeNoise2D(rng), s: makeNoise2D(rng), d: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const mottle = fbm(c.n, u * 10, v * 10, 4) * 0.5 + 0.5;
    const fine = fbm(c.s, u * 110, v * 110, 2) * 0.5 + 0.5;
    // Damp staining creeping up from the base of the wall.
    const stain = smoothstep(0.55, 0.9, fbm(c.d, u * 4, v * 6, 3) * 0.5 + 0.5) * smoothstep(0.5, 0.0, v);
    const tone = lerp(0.72, 0.90, mottle * 0.6 + fine * 0.4) - stain * 0.16;
    out.r = tone;
    out.g = tone * 0.975;
    out.b = tone * 0.925 - stain * 0.02;
    out.rough = 0.86 + fine * 0.08;
    out.h = mottle * 0.3 + fine * 0.7;
  },
};

RECIPES.brick = {
  scale: 2.2,
  normalStrength: 7.0,
  init(rng) {
    return { n: makeNoise2D(rng), g: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    // 30 courses and 10 stretchers over a 2.2 m tile puts a brick at
    // 220 x 73 mm — life size. Anything coarser reads as a cartoon.
    const rows = 30;
    const cols = 10;
    const row = Math.floor(v * rows);
    const offset = row % 2 === 0 ? 0 : 0.5;
    const uu = u * cols + offset;
    const col = Math.floor(uu);
    const fu = uu - col;
    const fv = v * rows - row;
    const mortarU = smoothstep(0.03, 0.055, Math.min(fu, 1 - fu));
    const mortarV = smoothstep(0.07, 0.13, Math.min(fv, 1 - fv));
    const brickMask = Math.min(mortarU, mortarV);
    const id = ((col * 7919 + row * 104729) % 1000) / 1000;
    const id2 = ((col * 2654435 + row * 40503) % 997) / 997;
    const grime = fbm(c.n, u * 30, v * 30, 3) * 0.5 + 0.5;
    const face = fbm(c.n, u * 90 + col * 3.1, v * 90 + row * 2.7, 2) * 0.5 + 0.5;
    // Real stock brick runs from a dull plum through to a burnt orange.
    const rBase = lerp(0.235, 0.475, id) * lerp(0.82, 1.12, grime) * lerp(0.92, 1.06, face);
    const warm = lerp(0.42, 0.60, id2);
    const mortarTone = lerp(0.40, 0.51, fbm(c.g, u * 40, v * 40, 2) * 0.5 + 0.5);
    out.r = lerp(mortarTone, rBase, brickMask);
    out.g = lerp(mortarTone * 0.98, rBase * warm, brickMask);
    out.b = lerp(mortarTone * 0.93, rBase * warm * 0.72, brickMask);
    out.rough = lerp(0.95, 0.84 + grime * 0.1, brickMask);
    out.h = brickMask * 0.85 + grime * 0.12 + face * 0.05;
  },
};

RECIPES.stoneWall = {
  scale: 2.6,
  normalStrength: 7.5,
  init(rng) {
    return { w: makeWorley2D(rng, 9), n: makeNoise2D(rng), d: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const wx = u * 9 + fbm(c.d, u * 4, v * 4, 2) * 0.45;
    const wy = v * 9 + fbm(c.d, u * 4 + 9, v * 4 + 3, 2) * 0.45;
    const cell = c.w(wx, wy);
    const gap = smoothstep(0.02, 0.11, cell.f2 - cell.f1);
    const grain = fbm(c.n, u * 55, v * 55, 3) * 0.5 + 0.5;
    const value = lerp(0.26, 0.58, cell.id) * lerp(0.88, 1.12, grain);
    const mortar = 0.44 + grain * 0.1;
    const tone = lerp(mortar, value, gap);
    out.r = tone * 1.01;
    out.g = tone * 0.99;
    out.b = tone * 0.95;
    out.rough = 0.9 - gap * 0.06;
    out.h = gap * 0.8 + grain * 0.2;
  },
};

RECIPES.roofTile = {
  scale: 1.7,
  normalStrength: 6.0,
  init(rng) {
    return { n: makeNoise2D(rng), m: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const rows = 9;
    const cols = 11;
    const row = Math.floor(v * rows);
    const offset = row % 2 === 0 ? 0 : 0.5;
    const uu = u * cols + offset;
    const col = Math.floor(uu);
    const fu = uu - col;
    const fv = v * rows - row;
    // Half-round pantile: a cylindrical bulge across, a lap shadow along.
    const bulge = Math.sqrt(Math.max(0, 1 - Math.pow(fu * 2 - 1, 2)));
    const lap = smoothstep(0.0, 0.14, fv);
    const groove = smoothstep(0.055, 0.0, Math.min(fu, 1 - fu));
    const id = ((col * 6151 + row * 24593) % 1000) / 1000;
    const weather = fbm(c.n, u * 22, v * 22, 4) * 0.5 + 0.5;
    const mossMask = smoothstep(0.62, 0.85, fbm(c.m, u * 7, v * 7, 3) * 0.5 + 0.5);
    let r = lerp(0.34, 0.52, id) * lerp(0.82, 1.1, weather);
    let g = r * 0.44;
    let b = r * 0.32;
    // Moss in the laps.
    r = lerp(r, 0.13, mossMask * 0.65);
    g = lerp(g, 0.20, mossMask * 0.65);
    b = lerp(b, 0.09, mossMask * 0.65);
    const shade = lerp(0.55, 1.0, bulge) * lerp(0.6, 1.0, lap) * (1 - groove * 0.4);
    out.r = r * shade;
    out.g = g * shade;
    out.b = b * shade;
    out.rough = 0.8 + weather * 0.15;
    out.h = bulge * 0.6 + lap * 0.35 - groove * 0.5 + weather * 0.08;
  },
};

RECIPES.roofSlate = {
  scale: 1.8,
  normalStrength: 5.0,
  init(rng) {
    return { n: makeNoise2D(rng), m: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const rows = 12;
    const cols = 7;
    const row = Math.floor(v * rows);
    const offset = row % 2 === 0 ? 0 : 0.5;
    const uu = u * cols + offset;
    const col = Math.floor(uu);
    const fu = uu - col;
    const fv = v * rows - row;
    const gapU = smoothstep(0.0, 0.03, Math.min(fu, 1 - fu));
    const lap = smoothstep(0.0, 0.1, fv);
    const id = ((col * 3571 + row * 8191) % 1000) / 1000;
    const grain = fbm(c.n, u * 60, v * 30, 3) * 0.5 + 0.5;
    const tone = lerp(0.09, 0.20, id) * lerp(0.85, 1.15, grain);
    const shade = lerp(0.45, 1.0, gapU) * lerp(0.7, 1.0, lap);
    out.r = tone * shade * 0.95;
    out.g = tone * shade * 1.0;
    out.b = tone * shade * 1.12;
    out.rough = 0.62 + grain * 0.2;
    out.h = gapU * 0.5 + lap * 0.4 + grain * 0.1;
  },
};

// --- metal ----------------------------------------------------------------

RECIPES.steel = {
  scale: 1.0,
  normalStrength: 1.2,
  init(rng) {
    return { n: makeNoise2D(rng), r: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const brush = fbm(c.n, u * 250, v * 6, 3) * 0.5 + 0.5;
    const rust = smoothstep(0.62, 0.92, fbm(c.r, u * 9, v * 9, 4) * 0.5 + 0.5);
    const base = 0.40 + brush * 0.22;
    out.r = lerp(base, 0.36, rust);
    out.g = lerp(base * 0.99, 0.19, rust);
    out.b = lerp(base * 1.0, 0.10, rust);
    out.rough = lerp(0.24 + brush * 0.1, 0.85, rust);
    out.h = brush * 0.4 + rust * 0.6;
  },
};

RECIPES.ironRust = {
  scale: 1.2,
  normalStrength: 3.0,
  init(rng) {
    return { n: makeNoise2D(rng), r: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const rust = fbm(c.r, u * 12, v * 12, 5) * 0.5 + 0.5;
    const pit = smoothstep(0.7, 0.95, fbm(c.n, u * 45, v * 45, 3) * 0.5 + 0.5);
    out.r = lerp(0.22, 0.44, rust) + pit * 0.06;
    out.g = lerp(0.14, 0.22, rust);
    out.b = lerp(0.10, 0.12, rust);
    out.rough = 0.68 + rust * 0.25;
    out.h = rust * 0.6 - pit * 0.5;
  },
};

RECIPES.paintedMetal = {
  scale: 1.4,
  normalStrength: 1.0,
  init(rng) {
    return { n: makeNoise2D(rng), c: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const orange = smoothstep(0.72, 0.95, fbm(c.c, u * 14, v * 14, 4) * 0.5 + 0.5);
    const dirt = fbm(c.n, u * 40, v * 40, 3) * 0.5 + 0.5;
    // White base; instance colour tints it, chips show rust through.
    const base = 0.93 - dirt * 0.06;
    out.r = lerp(base, 0.35, orange);
    out.g = lerp(base, 0.18, orange);
    out.b = lerp(base, 0.10, orange);
    out.rough = lerp(0.32 + dirt * 0.1, 0.85, orange);
    out.h = 0.5 - orange * 0.5;
  },
};

// --- soft goods -----------------------------------------------------------

RECIPES.cloth = {
  scale: 0.55,
  normalStrength: 2.0,
  init(rng) {
    return { n: makeNoise2D(rng), f: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const weaveU = Math.abs(Math.sin(u * 128 * Math.PI));
    const weaveV = Math.abs(Math.sin(v * 128 * Math.PI));
    const weave = Math.max(weaveU, weaveV) * 0.5 + 0.5;
    const fold = fbm(c.n, u * 6, v * 6, 3) * 0.5 + 0.5;
    const tone = 0.86 + weave * 0.1 - (1 - fold) * 0.1;
    out.r = tone;
    out.g = tone;
    out.b = tone;
    out.rough = 0.85 + weave * 0.08;
    out.h = weave * 0.5 + fold * 0.5;
  },
};

RECIPES.skin = {
  scale: 0.4,
  normalStrength: 1.0,
  init(rng) {
    return { n: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const pore = fbm(c.n, u * 180, v * 180, 2) * 0.5 + 0.5;
    const blotch = fbm(c.n, u * 9, v * 9, 3) * 0.5 + 0.5;
    const tone = 0.93 + pore * 0.06 - blotch * 0.05;
    out.r = tone;
    out.g = tone * 0.965;
    out.b = tone * 0.94;
    out.rough = 0.58 + pore * 0.12;
    out.h = pore * 0.7 + blotch * 0.3;
  },
};

// --- misc -----------------------------------------------------------------

/**
 * Low-frequency greyscale used to break up the tiling of the ground layers.
 * Sampled at a far larger world scale than the layers themselves, so nothing
 * ever repeats visibly under raking light.
 */
RECIPES.macro = {
  scale: 90,
  normalStrength: 0.5,
  init(rng) {
    return { n: makeNoise2D(rng), m: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const a = fbm(c.n, u * 3, v * 3, 4) * 0.5 + 0.5;
    const b = fbm(c.m, u * 7, v * 7, 3) * 0.5 + 0.5;
    const t = clamp(a * 0.7 + b * 0.3, 0, 1);
    const g = lerp(0.34, 0.66, t);
    out.r = g;
    out.g = g;
    out.b = g;
    out.rough = t;
    out.h = t;
  },
};

/** Water surface normals — two wave scales, animated by scrolling offsets. */
RECIPES.water = {
  scale: 9,
  normalStrength: 1.5,
  init(rng) {
    return { n: makeNoise2D(rng), m: makeNoise2D(rng) };
  },
  sample(u, v, c, out) {
    const a = fbm(c.n, u * 6, v * 6, 4);
    const b = fbm(c.m, u * 17, v * 13, 3);
    out.r = 0.5;
    out.g = 0.5;
    out.b = 0.5;
    out.rough = 0.06;
    out.h = a * 0.65 + b * 0.35;
  },
};

// ===========================================================================
// Alpha-mapped foliage cards
// ===========================================================================

const foliageCache = new Map();

/**
 * A cluster of leaves rasterised into one RGBA tile: RGB albedo, A opacity.
 * Two or three of these on crossed quads is what turns a "cone tree" into a
 * tree — the silhouette comes from the alpha, not from the mesh.
 */
export function foliageCard(name, opts = {}) {
  const size = opts.size ?? 512;
  const seed = opts.seed ?? 1;
  const key = `${name}|${size}|${seed}`;
  let hit = foliageCache.get(key);
  if (hit) return hit;

  const spec = FOLIAGE[name];
  if (!spec) throw new Error(`world/textures: no foliage card "${name}"`);
  const rng = new Rng((nameHash(name) ^ (seed >>> 0)) >>> 0);
  const noise = makeNoise2D(rng);

  const leaves = [];
  for (let i = 0; i < spec.count; i++) {
    const a = rng.range(0, Math.PI * 2);
    const rad = Math.sqrt(rng.next()) * spec.spread;
    leaves.push({
      cx: 0.5 + Math.cos(a) * rad,
      cy: 0.5 + Math.sin(a) * rad,
      ang: spec.radial ? a + rng.range(-0.35, 0.35) : rng.range(0, Math.PI * 2),
      len: rng.range(spec.len[0], spec.len[1]),
      wid: rng.range(spec.wid[0], spec.wid[1]),
      shade: rng.range(0.74, 1.10),
      hue: rng.range(-1, 1),
      depth: rng.next(),
    });
  }
  leaves.sort((p, q) => p.depth - q.depth);

  const rgba = new Uint8Array(size * size * 4);
  const height = new Float32Array(size * size);
  const inv = 1 / size;

  for (const leaf of leaves) {
    const reach = Math.max(leaf.len, leaf.wid) * 0.75 + 0.01;
    const x0 = Math.floor((leaf.cx - reach) * size);
    const x1 = Math.ceil((leaf.cx + reach) * size);
    const y0 = Math.floor((leaf.cy - reach) * size);
    const y1 = Math.ceil((leaf.cy + reach) * size);
    const ca = Math.cos(leaf.ang);
    const sa = Math.sin(leaf.ang);
    for (let py = y0; py <= y1; py++) {
      const yw = ((py % size) + size) % size;
      for (let px = x0; px <= x1; px++) {
        const xw = ((px % size) + size) % size;
        let dx = px * inv - leaf.cx;
        let dy = py * inv - leaf.cy;
        // Leaf-local coordinates: s along the midrib, tt across it.
        const s = (dx * ca + dy * sa) / leaf.len + 0.5;
        if (s < 0 || s > 1) continue;
        const tt = (-dx * sa + dy * ca) / leaf.wid;
        const halfW = 0.5 * Math.pow(Math.sin(Math.PI * s), spec.taper);
        const at = Math.abs(tt);
        if (at > halfW) continue;

        const i = yw * size + xw;
        const across = at / Math.max(1e-4, halfW);
        const rib = smoothstep(0.16, 0.0, across);
        const veinPhase = Math.sin((s * spec.veins + across * 3.5) * Math.PI * 2);
        const vein = smoothstep(0.72, 0.98, veinPhase) * 0.35;
        const shade =
          leaf.shade *
          lerp(0.74, 1.06, s) *
          (1 - vein * 0.5) *
          (1 - smoothstep(0.75, 1.0, across) * 0.28);
        const wobble = noise(px * inv * 40, py * inv * 40) * 0.06;

        const c0 = spec.dark;
        const c1 = spec.light;
        const mix = clamp(0.5 + leaf.hue * 0.5 + wobble, 0, 1);
        const o = i * 4;
        rgba[o] = clamp((lerp(c0[0], c1[0], mix) * shade) , 0, 1) * 255;
        rgba[o + 1] = clamp((lerp(c0[1], c1[1], mix) * shade), 0, 1) * 255;
        rgba[o + 2] = clamp((lerp(c0[2], c1[2], mix) * shade), 0, 1) * 255;
        rgba[o + 3] = 255;
        height[i] = 0.35 + rib * 0.4 + leaf.depth * 0.25 + (1 - across) * 0.15;
      }
    }
  }

  const map = makeTexture(rgba, size, { srgb: true, aniso: 4 });
  const normalMap = makeTexture(heightToNormal(height, size, 2.2 * size * 0.004), size, {
    srgb: false,
    aniso: 4,
  });
  hit = { map, normalMap };
  foliageCache.set(key, hit);
  return hit;
}

const FOLIAGE = {
  leafBroad: {
    count: 105,
    spread: 0.44,
    len: [0.075, 0.165],
    wid: [0.040, 0.085],
    taper: 0.62,
    veins: 5,
    radial: false,
    dark: [0.088, 0.225, 0.052],
    light: [0.375, 0.640, 0.145],
  },
  leafBlossom: {
    count: 110,
    spread: 0.44,
    len: [0.062, 0.135],
    wid: [0.048, 0.098],
    taper: 0.5,
    veins: 5,
    radial: false,
    dark: [0.34, 0.185, 0.245],
    light: [0.86, 0.70, 0.775],
  },
  leafBirch: {
    count: 120,
    spread: 0.44,
    len: [0.05, 0.11],
    wid: [0.034, 0.066],
    taper: 0.5,
    veins: 6,
    radial: false,
    dark: [0.130, 0.260, 0.068],
    light: [0.450, 0.700, 0.200],
  },
  needleSpray: {
    count: 128,
    spread: 0.43,
    len: [0.22, 0.44],
    wid: [0.011, 0.021],
    taper: 0.32,
    veins: 2,
    radial: true,
    dark: [0.058, 0.150, 0.076],
    light: [0.205, 0.395, 0.165],
  },
  scrub: {
    count: 118,
    spread: 0.45,
    len: [0.055, 0.125],
    wid: [0.028, 0.062],
    taper: 0.55,
    veins: 4,
    radial: false,
    dark: [0.108, 0.172, 0.058],
    light: [0.372, 0.475, 0.165],
  },
};

/** Clear every cache — used only by tests. */
export function _resetTextureCache() {
  cache.clear();
  foliageCache.clear();
}
