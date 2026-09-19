/**
 * Procedural textures for the train.
 *
 * Generated once, on a 2D canvas, from an integer seed — no network, no disk,
 * no `Math.random()`. They are built during `createTrain` and never touched
 * again, so they cannot make frame N depend on frame N-1.
 *
 * These are deliberately low-contrast. Their job is to stop a painted panel
 * from being one perfectly uniform value across a 3-metre span, which is the
 * second-strongest "this is CG" tell after unbevelled edges.
 */

import * as THREE from 'three';

/* ------------------------------------------------------------------- noise */

function hash2(ix, iy, seed) {
  let h = (Math.imul(ix, 374761393) + Math.imul(iy, 668265263) + Math.imul(seed, 1274126177)) | 0;
  h = (h ^ (h >>> 13)) | 0;
  h = Math.imul(h, 1274126177) | 0;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

const fade = (v) => v * v * v * (v * (v * 6 - 15) + 10);

/** Value noise that wraps exactly at `period`, so the texture tiles. */
function tileNoise(x, y, period, seed) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = fade(x - x0);
  const fy = fade(y - y0);
  const wrap = (v) => ((v % period) + period) % period;
  const xa = wrap(x0);
  const xb = wrap(x0 + 1);
  const ya = wrap(y0);
  const yb = wrap(y0 + 1);
  const n00 = hash2(xa, ya, seed);
  const n10 = hash2(xb, ya, seed);
  const n01 = hash2(xa, yb, seed);
  const n11 = hash2(xb, yb, seed);
  const a = n00 + fx * (n10 - n00);
  const b = n01 + fx * (n11 - n01);
  return a + fy * (b - a);
}

function tileFbm(x, y, basePeriod, seed, octaves = 5) {
  let amp = 1;
  let sum = 0;
  let norm = 0;
  let period = basePeriod;
  for (let o = 0; o < octaves; o++) {
    sum += amp * tileNoise((x * period) / basePeriod, (y * period) / basePeriod, period, seed + o * 7919);
    norm += amp;
    amp *= 0.5;
    period *= 2;
  }
  return sum / norm;
}

/* --------------------------------------------------------------- factories */

function makeCanvas(size) {
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  return canvas;
}

function finish(canvas, { srgb = false, repeat = 1 } = {}) {
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat, repeat);
  tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Broad, soft roughness break-up. Used on painted bodywork and on the
 * clearcoat, where it reads as wax swirl and unevenly polished enamel.
 */
export function grimeTexture(seed, { size = 512, contrast = 0.5, bias = 0.5 } = {}) {
  const canvas = makeCanvas(size);
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(size, size);
  const cells = 8;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const n = tileFbm((x / size) * cells, (y / size) * cells, cells, seed, 5);
      const v = THREE.MathUtils.clamp(bias + (n - 0.5) * contrast, 0, 1);
      const i = (y * size + x) * 4;
      const b = Math.round(v * 255);
      img.data[i] = b;
      img.data[i + 1] = b;
      img.data[i + 2] = b;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return finish(canvas);
}

/**
 * Fine directional scratches over a soft base — the honest look of a steel rod
 * that has been wiped down a thousand times with an oily rag.
 */
export function brushedTexture(seed, { size = 512, strength = 0.35 } = {}) {
  const canvas = makeCanvas(size);
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(size, size);
  const cells = 6;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const broad = tileFbm((x / size) * cells, (y / size) * cells, cells, seed, 4);
      // Streaks: high frequency across, very low along.
      const streak = tileFbm((x / size) * 96, (y / size) * 3, 96, seed + 4111, 2);
      const v = THREE.MathUtils.clamp(0.5 + (broad - 0.5) * strength + (streak - 0.5) * strength * 0.8, 0, 1);
      const i = (y * size + x) * 4;
      const b = Math.round(v * 255);
      img.data[i] = b;
      img.data[i + 1] = b;
      img.data[i + 2] = b;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return finish(canvas);
}

/** Plank grain for decking, wagon sides and crates. Returns {map, rough}. */
export function woodTextures(seed, { size = 512, planks = 6 } = {}) {
  const colourCanvas = makeCanvas(size);
  const roughCanvas = makeCanvas(size);
  const cImg = colourCanvas.getContext('2d').createImageData(size, size);
  const rImg = roughCanvas.getContext('2d').createImageData(size, size);

  const light = [0.62, 0.46, 0.30];
  const dark = [0.34, 0.23, 0.14];

  for (let y = 0; y < size; y++) {
    const plank = Math.floor((y / size) * planks);
    const plankShade = 0.86 + hash2(plank, 17, seed) * 0.28;
    const plankPhase = hash2(plank, 91, seed) * 40;
    const edge = Math.abs(((y / size) * planks) % 1 - 0.5) * 2; // 0 mid, 1 at joint
    const joint = edge > 0.94 ? 0.42 : 1.0;
    for (let x = 0; x < size; x++) {
      const u = (x / size) * 10 + plankPhase;
      const v = (y / size) * 10;
      const wobble = tileFbm(u * 0.6, v * 3.0, 10, seed + 33, 3);
      const rings = 0.5 + 0.5 * Math.sin((u + wobble * 2.6) * 6.0);
      const fibre = tileFbm(u * 12, v * 1.5, 120, seed + 77, 2);
      const g = THREE.MathUtils.clamp(rings * 0.62 + fibre * 0.38, 0, 1);
      const shade = g * plankShade * joint;
      const i = (y * size + x) * 4;
      cImg.data[i] = Math.round(255 * (dark[0] + (light[0] - dark[0]) * shade) ** (1 / 1.0));
      cImg.data[i + 1] = Math.round(255 * (dark[1] + (light[1] - dark[1]) * shade));
      cImg.data[i + 2] = Math.round(255 * (dark[2] + (light[2] - dark[2]) * shade));
      cImg.data[i + 3] = 255;
      const r = THREE.MathUtils.clamp(0.94 - g * 0.22 + (fibre - 0.5) * 0.2, 0, 1);
      rImg.data[i] = Math.round(r * 255);
      rImg.data[i + 1] = rImg.data[i];
      rImg.data[i + 2] = rImg.data[i];
      rImg.data[i + 3] = 255;
    }
  }
  colourCanvas.getContext('2d').putImageData(cImg, 0, 0);
  roughCanvas.getContext('2d').putImageData(rImg, 0, 0);
  return { map: finish(colourCanvas, { srgb: true }), roughnessMap: finish(roughCanvas) };
}

/**
 * Soft, lumpy smoke puff. Alpha carries a radial falloff multiplied by a few
 * octaves of noise so the silhouette is ragged instead of a perfect disc.
 */
export function smokeSprite(seed, { size = 256 } = {}) {
  const canvas = makeCanvas(size);
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(size, size);
  const cells = 5;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = (x + 0.5) / size - 0.5;
      const dy = (y + 0.5) / size - 0.5;
      const r = Math.sqrt(dx * dx + dy * dy) * 2; // 0 centre -> 1 edge
      const n = tileFbm((x / size) * cells, (y / size) * cells, cells, seed, 5);
      // Push the noise into the falloff radius rather than multiplying the
      // alpha: that keeps the interior dense and only chews the silhouette.
      const rr = THREE.MathUtils.clamp(r * (0.72 + n * 0.62), 0, 1.4);
      let a = 1 - THREE.MathUtils.smoothstep(rr, 0.05, 1.0);
      a *= 0.55 + 0.45 * n;
      const i = (y * size + x) * 4;
      img.data[i] = 255;
      img.data[i + 1] = 255;
      img.data[i + 2] = 255;
      img.data[i + 3] = Math.round(THREE.MathUtils.clamp(a, 0, 1) * 255);
    }
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.NoColorSpace;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.anisotropy = 4;
  tex.needsUpdate = true;
  return tex;
}

/** Warm, blurry interior seen through coach glass — a hint, not a model. */
export function interiorTexture(seed, { size = 256 } = {}) {
  const canvas = makeCanvas(size);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#241a14';
  ctx.fillRect(0, 0, size, size);
  for (let i = 0; i < 26; i++) {
    const h = hash2(i, 3, seed);
    const w = hash2(i, 9, seed);
    ctx.globalAlpha = 0.25 + h * 0.4;
    ctx.fillStyle = `hsl(${20 + w * 26}, ${35 + h * 30}%, ${18 + w * 26}%)`;
    const bx = w * size;
    const by = size * (0.35 + h * 0.6);
    ctx.fillRect(bx - size * 0.14, by, size * 0.28, size * 0.5);
  }
  ctx.globalAlpha = 1;
  return finish(canvas, { srgb: true });
}
