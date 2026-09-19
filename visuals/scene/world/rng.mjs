/**
 * Deterministic randomness and noise for the world.
 *
 * Carried across from v1's scene/rng.mjs and extended with tileable value
 * noise, cellular (Worley) noise and a few helpers the texture generator
 * needs.
 *
 * Nothing in visuals/scene/world/ may call Math.random(), Date.now(),
 * performance.now() or THREE.Clock: every frame is a pure function of
 * (seed, t) and every generated asset is a pure function of seed.
 */

/** mulberry32 — small, fast, well-distributed 32-bit PRNG. */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Convenience wrapper around a mulberry32 stream. */
export class Rng {
  constructor(seed) {
    this._seed = seed >>> 0;
    this._next = mulberry32(seed);
  }

  /** Uniform in [0, 1). */
  next() {
    return this._next();
  }

  /** Uniform in [min, max). */
  range(min, max) {
    return min + (max - min) * this._next();
  }

  /** Integer in [min, max] inclusive. */
  int(min, max) {
    return min + Math.floor(this._next() * (max - min + 1));
  }

  /** Uniform element of an array. */
  pick(list) {
    return list[Math.floor(this._next() * list.length) % list.length];
  }

  /** True with probability p. */
  chance(p) {
    return this._next() < p;
  }

  /** -1 or +1. */
  sign() {
    return this._next() < 0.5 ? -1 : 1;
  }

  /** Roughly normal, mean 0, sd ~1 (sum of 4 uniforms). */
  normal() {
    return (this._next() + this._next() + this._next() + this._next() - 2) * 1.732;
  }

  /** Biased toward `min` when power > 1. */
  power(min, max, power) {
    return min + (max - min) * Math.pow(this._next(), power);
  }

  /** A fresh independent stream, so consumers cannot perturb each other. */
  fork() {
    return new Rng((this._next() * 4294967296) >>> 0);
  }
}

/** Coerce whatever the caller handed us into an Rng. */
export function asRng(maybe, fallbackSeed = 1) {
  if (maybe && typeof maybe.fork === 'function' && typeof maybe.range === 'function') return maybe;
  if (typeof maybe === 'function') {
    // A bare next() function: wrap it without disturbing its stream semantics.
    const r = new Rng(1);
    r._next = maybe;
    return r;
  }
  if (typeof maybe === 'number') return new Rng(maybe >>> 0);
  return new Rng(fallbackSeed >>> 0);
}

/**
 * Seeded 2D gradient (Perlin-style) noise, output roughly in [-1, 1].
 *
 * The permutation table wraps at 256, so the field is exactly periodic with
 * period 256 in both axes. Sampling a texture over a power-of-two sub-domain
 * therefore tiles seamlessly, which is what every material here relies on.
 */
export function makeNoise2D(rng) {
  const p = new Uint8Array(256);
  for (let i = 0; i < 256; i++) p[i] = i;
  for (let i = 255; i > 0; i--) {
    const j = Math.floor(rng.next() * (i + 1));
    const tmp = p[i];
    p[i] = p[j];
    p[j] = tmp;
  }
  const perm = new Uint8Array(512);
  for (let i = 0; i < 512; i++) perm[i] = p[i & 255];

  const GX = [1, -1, 1, -1, 0.7071, -0.7071, 0.7071, -0.7071];
  const GY = [0.7071, 0.7071, -0.7071, -0.7071, 1, 1, -1, -1];

  const fade = (v) => v * v * v * (v * (v * 6 - 15) + 10);

  return function noise2d(x, y) {
    const fx = Math.floor(x);
    const fy = Math.floor(y);
    const X = fx & 255;
    const Y = fy & 255;
    const xf = x - fx;
    const yf = y - fy;
    const u = fade(xf);
    const v = fade(yf);

    const g00 = perm[perm[X] + Y] & 7;
    const g10 = perm[perm[X + 1] + Y] & 7;
    const g01 = perm[perm[X] + Y + 1] & 7;
    const g11 = perm[perm[X + 1] + Y + 1] & 7;

    const n00 = GX[g00] * xf + GY[g00] * yf;
    const n10 = GX[g10] * (xf - 1) + GY[g10] * yf;
    const n01 = GX[g01] * xf + GY[g01] * (yf - 1);
    const n11 = GX[g11] * (xf - 1) + GY[g11] * (yf - 1);

    const nx0 = n00 + u * (n10 - n00);
    const nx1 = n01 + u * (n11 - n01);
    return nx0 + v * (nx1 - nx0);
  };
}

/**
 * Cellular / Worley noise on a jittered grid, wrapping every `cells` units so
 * it tiles. Returns { f1, f2, id } — f1/f2 are distances to the nearest and
 * second-nearest feature point (roughly 0..1), id is a per-cell hash in [0,1).
 *
 * Pebbles, ballast, cobbles and stone walls all come out of this.
 */
export function makeWorley2D(rng, cells = 8) {
  const px = new Float32Array(cells * cells);
  const py = new Float32Array(cells * cells);
  const pid = new Float32Array(cells * cells);
  for (let i = 0; i < cells * cells; i++) {
    px[i] = rng.next();
    py[i] = rng.next();
    pid[i] = rng.next();
  }
  const out = { f1: 0, f2: 0, id: 0 };

  return function worley(x, y) {
    // x, y in cell units.
    const cx = Math.floor(x);
    const cy = Math.floor(y);
    let f1 = 1e9;
    let f2 = 1e9;
    let id = 0;
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const gx = cx + dx;
        const gy = cy + dy;
        const i = (((gy % cells) + cells) % cells) * cells + (((gx % cells) + cells) % cells);
        const ex = gx + px[i] - x;
        const ey = gy + py[i] - y;
        const d = Math.sqrt(ex * ex + ey * ey);
        if (d < f1) {
          f2 = f1;
          f1 = d;
          id = pid[i];
        } else if (d < f2) {
          f2 = d;
        }
      }
    }
    out.f1 = f1;
    out.f2 = f2;
    out.id = id;
    return out;
  };
}

/** Fractional Brownian motion over a noise2d function. */
export function fbm(noise, x, y, octaves = 4, lacunarity = 2.0, gain = 0.5) {
  let amp = 1;
  let freq = 1;
  let sum = 0;
  let norm = 0;
  for (let i = 0; i < octaves; i++) {
    sum += amp * noise(x * freq, y * freq);
    norm += amp;
    amp *= gain;
    freq *= lacunarity;
  }
  return sum / norm;
}

/** Ridged multifractal — sharp crests, good for rock strata and bark. */
export function ridged(noise, x, y, octaves = 4, lacunarity = 2.0, gain = 0.5) {
  let amp = 1;
  let freq = 1;
  let sum = 0;
  let norm = 0;
  for (let i = 0; i < octaves; i++) {
    const n = 1 - Math.abs(noise(x * freq, y * freq));
    sum += amp * n * n;
    norm += amp;
    amp *= gain;
    freq *= lacunarity;
  }
  return sum / norm;
}

/** Hermite smoothstep. */
export function smoothstep(edge0, edge1, x) {
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

/** Linear interpolation. */
export function lerp(a, b, t) {
  return a + (b - a) * t;
}

/** Clamp. */
export function clamp(x, lo, hi) {
  return x < lo ? lo : x > hi ? hi : x;
}

/** Integer hash → [0, 1). Handy where an Rng stream would be inconvenient. */
export function hash2(x, y, seed = 0) {
  let h = Math.imul(x | 0, 0x27d4eb2d) ^ Math.imul(y | 0, 0x165667b1) ^ Math.imul(seed | 0, 0x9e3779b1);
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Wrap an angle into [-PI, PI]. */
export function wrapPi(a) {
  let v = (a + Math.PI) % (Math.PI * 2);
  if (v < 0) v += Math.PI * 2;
  return v - Math.PI;
}
