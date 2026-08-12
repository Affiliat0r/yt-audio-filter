/**
 * Vegetation.
 *
 * Real trees: a swept, tapering, rotation-minimising tube for every branch,
 * grown recursively, with alpha-mapped leaf clusters hung off the tips. Five
 * species, several variants of each, all instanced.
 *
 * Two details do most of the work here:
 *
 *   - **Canopy normals.** A leaf card lit by its own flat normal reads as
 *     cardboard. Every foliage vertex normal is rotated most of the way toward
 *     "outward from the centre of the crown", which is what makes a canopy
 *     read as a lit volume instead of a stack of billboards.
 *   - **Wind in the vertex shader.** Sway is a pure function of `t` and the
 *     instance origin, so it costs no CPU, never desynchronises, and is
 *     identical on every re-render of the same frame.
 */

import * as THREE from 'three';
import { lerp, clamp, smoothstep } from './rng.mjs';
import { bandPlacement, scatterPlacement, thin, sectorise, instanceField } from './placement.mjs';

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

const _t0 = new THREE.Vector3();
const _t1 = new THREE.Vector3();
const _axis = new THREE.Vector3();
const _q = new THREE.Quaternion();

/**
 * A tapering tube through `points` with a rotation-minimising frame, UVs in
 * metres. Trunks, boughs and twigs all come from here.
 */
function branchTube(points, radii, radial = 7, vStart = 0) {
  const n = points.length;
  const tangents = [];
  for (let i = 0; i < n; i++) {
    const a = points[Math.max(0, i - 1)];
    const b = points[Math.min(n - 1, i + 1)];
    tangents.push(new THREE.Vector3().subVectors(b, a).normalize());
  }

  // Seed a normal perpendicular to the first tangent, then transport it.
  const normals = [];
  let normal = new THREE.Vector3(0, 0, 1);
  if (Math.abs(tangents[0].dot(normal)) > 0.9) normal.set(1, 0, 0);
  normal.crossVectors(tangents[0], normal).normalize();
  normals.push(normal.clone());
  for (let i = 1; i < n; i++) {
    _t0.copy(tangents[i - 1]);
    _t1.copy(tangents[i]);
    _axis.crossVectors(_t0, _t1);
    if (_axis.lengthSq() > 1e-10) {
      _axis.normalize();
      const angle = Math.acos(clamp(_t0.dot(_t1), -1, 1));
      _q.setFromAxisAngle(_axis, angle);
      normal.applyQuaternion(_q);
    }
    normal.normalize();
    normals.push(normal.clone());
  }

  const cols = radial + 1;
  const pos = new Float32Array(n * cols * 3);
  const nor = new Float32Array(n * cols * 3);
  const uv = new Float32Array(n * cols * 2);
  const circumference = Math.PI * 2 * radii[0];
  const bin = new THREE.Vector3();
  const vert = new THREE.Vector3();
  let v = vStart;
  let w = 0;
  let wu = 0;
  for (let i = 0; i < n; i++) {
    if (i > 0) v += points[i].distanceTo(points[i - 1]);
    bin.crossVectors(tangents[i], normals[i]).normalize();
    for (let k = 0; k <= radial; k++) {
      const a = (k / radial) * Math.PI * 2;
      const ca = Math.cos(a);
      const sa = Math.sin(a);
      vert.set(
        normals[i].x * ca + bin.x * sa,
        normals[i].y * ca + bin.y * sa,
        normals[i].z * ca + bin.z * sa
      );
      nor[w] = vert.x;
      nor[w + 1] = vert.y;
      nor[w + 2] = vert.z;
      pos[w] = points[i].x + vert.x * radii[i];
      pos[w + 1] = points[i].y + vert.y * radii[i];
      pos[w + 2] = points[i].z + vert.z * radii[i];
      w += 3;
      uv[wu++] = (k / radial) * circumference;
      uv[wu++] = v;
    }
  }

  const idx = [];
  for (let i = 0; i < n - 1; i++) {
    for (let k = 0; k < radial; k++) {
      const a = i * cols + k;
      const b = a + 1;
      const c = a + cols;
      const d = c + 1;
      idx.push(a, c, b, b, c, d);
    }
  }

  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.setIndex(idx);
  return g;
}

/** A shallow dish rather than a flat quad — foliage cards never read as flat. */
function curvedCard(w, h, bend = 0.22, segs = 2) {
  const g = new THREE.PlaneGeometry(w, h, segs, segs);
  const pos = g.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i) / (w / 2);
    const y = pos.getY(i) / (h / 2);
    pos.setZ(i, -(x * x * 0.9 + y * y * 0.5) * bend * w);
  }
  g.computeVertexNormals();
  return g;
}

/**
 * Rotate every normal in `geometry` toward the direction away from `centre`.
 * `amount` 0 keeps the card's own normal, 1 fully replaces it.
 */
function canopyNormals(geometry, centre, amount = 0.78) {
  const pos = geometry.attributes.position;
  const nor = geometry.attributes.normal;
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.set(pos.getX(i) - centre.x, (pos.getY(i) - centre.y) * 0.75, pos.getZ(i) - centre.z);
    if (v.lengthSq() < 1e-8) continue;
    v.normalize();
    nor.setXYZ(
      i,
      lerp(nor.getX(i), v.x, amount),
      lerp(nor.getY(i), v.y, amount),
      lerp(nor.getZ(i), v.z, amount)
    );
  }
  geometry.normalizeNormals?.();
  return geometry;
}

/** Orient a card at `p` with its face along `normal`, rolled by `roll`. */
const _up = new THREE.Vector3(0, 1, 0);
const _mat = new THREE.Matrix4();
const _rot = new THREE.Matrix4();
function placeCard(geometry, p, normalDir, roll, scale = 1) {
  const g = geometry.clone();
  const n = normalDir.clone().normalize();
  const ref = Math.abs(n.y) > 0.95 ? new THREE.Vector3(1, 0, 0) : _up;
  const right = new THREE.Vector3().crossVectors(ref, n).normalize();
  const up = new THREE.Vector3().crossVectors(n, right).normalize();
  _mat.makeBasis(right, up, n);
  _rot.makeRotationZ(roll);
  _mat.multiply(_rot);
  _mat.scale(new THREE.Vector3(scale, scale, scale));
  _mat.setPosition(p);
  g.applyMatrix4(_mat);
  return g;
}

// ---------------------------------------------------------------------------
// Species
// ---------------------------------------------------------------------------

/**
 * Conifer: a straight bole with whorls of drooping branches, needle sprays
 * hung along each one. The silhouette comes from the branch-length falloff.
 */
function buildConifer(rng, cardGeom) {
  const H = rng.range(10.5, 16.5);
  const bark = [];
  const leaves = [];

  const N = 12;
  const trunkPts = [];
  const trunkR = [];
  const swayX = rng.range(-0.3, 0.3);
  const swayZ = rng.range(-0.3, 0.3);
  for (let i = 0; i <= N; i++) {
    const t = i / N;
    const y = t * H;
    trunkPts.push(
      new THREE.Vector3(swayX * t * t + Math.sin(t * 4.1) * 0.06, y, swayZ * t * t + Math.cos(t * 3.3) * 0.06)
    );
    trunkR.push(lerp(0.32, 0.016, Math.pow(t, 0.62)) * rng.range(0.9, 1.1));
  }
  bark.push(branchTube(trunkPts, trunkR, 9));

  // A spruce carries foliage over four fifths of its height, in tight whorls.
  // Spacing them out or shrinking the sprays turns the tree into a TV aerial,
  // which is exactly what the first pass produced.
  const crownStart = rng.range(0.10, 0.20);
  const crownRadius = rng.range(2.5, 3.4);
  const centre = new THREE.Vector3(0, H * 0.58, 0);
  let y = crownStart * H;
  let whorl = 0;
  while (y < H * 0.99) {
    const t = y / H;
    const spokes = rng.int(6, 8);
    const yawBase = whorl * 2.399 + rng.range(-0.3, 0.3);
    const maxLen = Math.max(0.32, Math.pow(1 - t, 0.72) * crownRadius) * rng.range(0.85, 1.12);
    const trunkIdx = clamp(Math.round(t * N), 0, N);
    const base = trunkPts[trunkIdx];
    for (let k = 0; k < spokes; k++) {
      const a = yawBase + (k / spokes) * Math.PI * 2;
      const len = maxLen * rng.range(0.72, 1.15);
      const droop = lerp(0.62, 0.06, t) * rng.range(0.6, 1.3);
      const pts = [];
      const rs = [];
      const steps = 3;
      for (let i = 0; i <= steps; i++) {
        const s = i / steps;
        pts.push(
          new THREE.Vector3(
            base.x + Math.cos(a) * len * s,
            base.y + len * s * 0.18 - droop * s * s * len * 0.5,
            base.z + Math.sin(a) * len * s
          )
        );
        rs.push(lerp(Math.min(0.05, trunkR[trunkIdx] * 0.4), 0.007, s));
      }
      bark.push(branchTube(pts, rs, 5));

      // Sprays overlap generously along the bough: the silhouette has to be
      // continuous foliage, not beads on a wire.
      const sprays = clamp(Math.round(len / 0.5), 2, 6);
      for (let i = 0; i < sprays; i++) {
        const s = clamp(0.16 + (i / Math.max(1, sprays - 1)) * 0.95, 0, 1.05);
        const p = pts[0]
          .clone()
          .lerp(pts[steps], Math.min(1, s))
          .add(new THREE.Vector3(rng.range(-0.1, 0.1), rng.range(-0.1, 0.04), rng.range(-0.1, 0.1)));
        const size = clamp(len * 0.78, 0.7, 2.0) * rng.range(0.85, 1.15);
        const n = new THREE.Vector3(
          Math.cos(a) * rng.range(0.15, 0.45),
          rng.range(0.75, 1),
          Math.sin(a) * rng.range(0.15, 0.45)
        );
        leaves.push(placeCard(cardGeom, p, n, rng.range(0, Math.PI * 2), size));
        leaves.push(
          placeCard(
            cardGeom,
            p,
            new THREE.Vector3(Math.cos(a + 1.57), rng.range(-0.25, 0.25), Math.sin(a + 1.57)),
            rng.range(0, Math.PI * 2),
            size * rng.range(0.75, 1.0)
          )
        );
      }
    }
    y += rng.range(0.48, 0.72);
    whorl++;
  }

  return { bark, leaves, centre, height: H };
}

/** Recursive broadleaf: trunk, boughs, twigs, leaf clusters on the tips. */
function growBranch(rng, bark, leaves, cardGeom, p0, dir, len, r0, depth, cfg) {
  const steps = depth > 1 ? 4 : 3;
  const pts = [];
  const rs = [];
  const cur = dir.clone().normalize();
  const p = p0.clone();
  for (let i = 0; i <= steps; i++) {
    pts.push(p.clone());
    rs.push(lerp(r0, r0 * 0.62, i / steps));
    cur.y += cfg.upBias / steps;
    cur.x += rng.range(-cfg.wander, cfg.wander) / steps;
    cur.z += rng.range(-cfg.wander, cfg.wander) / steps;
    cur.normalize();
    p.addScaledVector(cur, len / steps);
  }
  bark.push(branchTube(pts, rs, depth > 1 ? 8 : 5));

  const tip = pts[steps];
  // Cards go on the last two orders of branch, not just the tips: hanging them
  // only at the ends leaves a hollow crown that reads as a lollipop.
  if (depth <= 1) {
    const cards = depth === 0 ? cfg.cardsPerTip : Math.max(1, Math.round(cfg.cardsPerTip * 0.5));
    const size = cfg.cardSize * (depth === 0 ? 1 : 0.85);
    for (let i = 0; i < cards; i++) {
      const along = 0.3 + rng.next() * 0.8;
      const at = pts[0].clone().lerp(tip, Math.min(1, along));
      at.add(
        new THREE.Vector3(
          rng.range(-size, size) * 0.42,
          rng.range(-size, size) * 0.36,
          rng.range(-size, size) * 0.42
        )
      );
      // Biased outward and slightly down, the way a leaf spray actually hangs.
      const n = new THREE.Vector3(rng.normal(), rng.normal() * 0.5 + 0.2, rng.normal()).normalize();
      leaves.push(placeCard(cardGeom, at, n, rng.range(0, Math.PI * 2), size * rng.range(0.78, 1.3)));
    }
    if (depth === 0) return;
  }

  const children = rng.int(cfg.split[0], cfg.split[1]);
  const yaw0 = rng.range(0, Math.PI * 2);
  for (let c = 0; c < children; c++) {
    const yaw = yaw0 + (c / children) * Math.PI * 2 + rng.range(-0.4, 0.4);
    const spread = rng.range(cfg.spread[0], cfg.spread[1]);
    const child = cur
      .clone()
      .applyAxisAngle(new THREE.Vector3(Math.cos(yaw), 0, Math.sin(yaw)), spread)
      .normalize();
    child.y = Math.max(child.y, -0.25);
    growBranch(
      rng,
      bark,
      leaves,
      cardGeom,
      tip.clone(),
      child,
      len * rng.range(cfg.shrink[0], cfg.shrink[1]),
      r0 * cfg.taper,
      depth - 1,
      cfg
    );
  }
}

function buildBroadleaf(rng, cardGeom, style = {}) {
  const bark = [];
  const leaves = [];
  const trunkLen = rng.range(3.0, 4.8) * (style.trunkScale ?? 1);
  const r0 = rng.range(0.26, 0.38) * (style.trunkScale ?? 1);
  const cfg = {
    upBias: style.upBias ?? 0.2,
    wander: style.wander ?? 0.24,
    split: style.split ?? [2, 3],
    spread: style.spread ?? [0.5, 0.95],
    shrink: style.shrink ?? [0.62, 0.78],
    taper: style.taper ?? 0.62,
    cardsPerTip: style.cardsPerTip ?? 5,
    cardSize: style.cardSize ?? 1.7,
  };
  growBranch(
    rng,
    bark,
    leaves,
    cardGeom,
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(rng.range(-0.09, 0.09), 1, rng.range(-0.09, 0.09)),
    trunkLen,
    r0,
    style.depth ?? 4,
    cfg
  );

  let maxY = 0;
  let sumY = 0;
  let count = 0;
  for (const g of leaves) {
    const p = g.attributes.position;
    for (let i = 0; i < p.count; i += 3) {
      maxY = Math.max(maxY, p.getY(i));
      sumY += p.getY(i);
      count++;
    }
  }
  const centre = new THREE.Vector3(0, count ? sumY / count : trunkLen, 0);
  return { bark, leaves, centre, height: Math.max(maxY, trunkLen) };
}

/** Low multi-stem scrub — the layer that stops the ground meeting the trunks. */
function buildShrub(rng, cardGeom) {
  const bark = [];
  const leaves = [];
  const stems = rng.int(3, 5);
  const H = rng.range(1.1, 2.2);
  for (let s = 0; s < stems; s++) {
    const a = (s / stems) * Math.PI * 2 + rng.range(-0.5, 0.5);
    const lean = rng.range(0.3, 0.6);
    const pts = [];
    const rs = [];
    for (let i = 0; i <= 3; i++) {
      const t = i / 3;
      pts.push(new THREE.Vector3(Math.cos(a) * lean * t * H * 0.5, t * H, Math.sin(a) * lean * t * H * 0.5));
      rs.push(lerp(0.045, 0.012, t));
    }
    bark.push(branchTube(pts, rs, 5));
    const tip = pts[3];
    for (let i = 0; i < 3; i++) {
      const p = tip
        .clone()
        .add(new THREE.Vector3(rng.range(-0.35, 0.35), rng.range(-0.3, 0.25), rng.range(-0.35, 0.35)));
      const n = new THREE.Vector3(rng.normal(), rng.normal() * 0.6 + 0.5, rng.normal()).normalize();
      leaves.push(placeCard(cardGeom, p, n, rng.range(0, Math.PI * 2), rng.range(0.7, 1.15)));
    }
  }
  return { bark, leaves, centre: new THREE.Vector3(0, H * 0.7, 0), height: H * 1.2 };
}

// ---------------------------------------------------------------------------
// Grass
// ---------------------------------------------------------------------------

/** One tapered, curved blade as a triangle strip, dark at the root. */
function grassBlade(height, width, lean, twist, segs, rng) {
  const cols = 2;
  const pos = new Float32Array((segs + 1) * cols * 3);
  const nor = new Float32Array((segs + 1) * cols * 3);
  const col = new Float32Array((segs + 1) * cols * 3);
  const uv = new Float32Array((segs + 1) * cols * 2);
  const yaw = twist;
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const tint = rng.range(-0.06, 0.09);
  let w = 0;
  let wu = 0;
  for (let i = 0; i <= segs; i++) {
    const t = i / segs;
    const y = t * height;
    const bendX = lean * t * t * height;
    const halfW = (width / 2) * (1 - Math.pow(t, 1.6));
    const shade = 0.14 + 0.86 * Math.pow(t, 0.85);
    const r = clamp(0.030 + 0.185 * shade + tint * 0.5, 0, 1);
    const g = clamp(0.058 + 0.310 * shade + tint * 0.8, 0, 1);
    const b = clamp(0.018 + 0.070 * shade, 0, 1);
    for (let k = 0; k < cols; k++) {
      const off = (k === 0 ? -halfW : halfW);
      pos[w] = cy * (bendX) - sy * off;
      pos[w + 1] = y;
      pos[w + 2] = sy * (bendX) + cy * off;
      nor[w] = -sy * 0.25;
      nor[w + 1] = 0.86;
      nor[w + 2] = cy * 0.25;
      col[w] = r;
      col[w + 1] = g;
      col[w + 2] = b;
      w += 3;
      uv[wu++] = k;
      uv[wu++] = t;
    }
  }
  const idx = [];
  for (let i = 0; i < segs; i++) {
    const a = i * 2;
    idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.setIndex(idx);
  return g;
}

function grassTuft(rng, geom, blades, height) {
  const parts = [];
  for (let i = 0; i < blades; i++) {
    const a = rng.range(0, Math.PI * 2);
    const h = height * rng.range(0.65, 1.25);
    const blade = grassBlade(h, rng.range(0.009, 0.017), rng.range(0.35, 1.0), a, 4, rng);
    blade.translate(rng.range(-0.145, 0.145), 0, rng.range(-0.145, 0.145));
    parts.push(blade);
  }
  return geom.mergeAll(parts);
}

// ---------------------------------------------------------------------------
// Assembly
// ---------------------------------------------------------------------------

export function createVegetation({ rng, materials, geom, track, terrain, wind, density = 1 }) {
  const group = new THREE.Group();
  group.name = 'vegetation';
  const stats = { trees: 0, grass: 0, shrubs: 0 };

  // --- prototypes ---------------------------------------------------------

  const protoRng = rng.fork();
  const needleCard = curvedCard(1, 1, 0.26, 2);
  const leafCard = curvedCard(1, 1, 0.22, 2);

  /** @type {{bark:THREE.BufferGeometry, leaf:THREE.BufferGeometry, mats:string[], height:number}[]} */
  const prototypes = [];

  function register(model, barkMat, leafMat) {
    const bark = geom.mergeAll(model.bark);
    geom.paintGeometry(bark, 0xffffff);
    const leaf = geom.mergeAll(model.leaves);
    canopyNormals(leaf, model.centre, 0.6);
    geom.paintGeometry(leaf, 0xffffff);
    bark.computeBoundingSphere();
    leaf.computeBoundingSphere();
    prototypes.push({ bark, leaf, barkMat, leafMat, height: model.height });
    return prototypes.length - 1;
  }

  const conifers = [];
  for (let i = 0; i < 3; i++) {
    conifers.push(register(buildConifer(protoRng, needleCard), materials.barkConifer, materials.needle));
  }
  const oaks = [];
  for (let i = 0; i < 3; i++) {
    oaks.push(
      register(
        buildBroadleaf(protoRng, leafCard, { depth: 4, cardSize: 1.55, split: [2, 3] }),
        materials.barkBroadleaf,
        materials.leafBroad
      )
    );
  }
  const birches = [];
  for (let i = 0; i < 2; i++) {
    birches.push(
      register(
        buildBroadleaf(protoRng, leafCard, {
          depth: 4,
          trunkScale: 0.72,
          upBias: 0.42,
          spread: [0.3, 0.6],
          shrink: [0.66, 0.8],
          cardSize: 1.15,
          cardsPerTip: 3,
        }),
        materials.barkBirch,
        materials.leafBirch
      )
    );
  }
  const blossoms = [
    register(
      buildBroadleaf(protoRng, leafCard, {
        depth: 3,
        trunkScale: 0.62,
        upBias: 0.18,
        spread: [0.55, 1.0],
        split: [3, 4],
        cardSize: 1.35,
        cardsPerTip: 4,
      }),
      materials.barkBroadleaf,
      materials.leafBlossom
    ),
  ];
  const shrubs = [];
  for (let i = 0; i < 3; i++) {
    shrubs.push(register(buildShrub(protoRng, leafCard), materials.barkBroadleaf, materials.scrub));
  }

  // Wind, per material. Trunks bend slowly and barely; leaf cards flutter.
  const barkMats = new Set();
  const leafMats = new Set();
  for (const p of prototypes) {
    barkMats.add(p.barkMat);
    leafMats.add(p.leafMat);
  }
  for (const m of barkMats) wind.apply(m, { amp: 0.30, rate: 0.62, height: 14, flutter: 0 });
  for (const m of leafMats) wind.apply(m, { amp: 0.55, rate: 0.74, height: 14, flutter: 0.055 });

  // --- placement ----------------------------------------------------------

  const placeRng = rng.fork();

  /** Assign every spot to a prototype, then build one field per prototype. */
  function plantField(spots, protoIds, opts) {
    const buckets = new Map();
    for (const spot of spots) {
      const id = protoIds[Math.floor(placeRng.next() * protoIds.length) % protoIds.length];
      let list = buckets.get(id);
      if (!list) buckets.set(id, (list = []));
      list.push(spot);
    }
    for (const [id, list] of buckets) {
      const proto = prototypes[id];
      for (const sector of sectorise(list, opts.sectors ?? 1)) {
        // One decoration per instance, shared by the bark and foliage fields:
        // if the two draw independently from the rng they drift apart and the
        // leaves end up hovering beside their own trunk.
        const r = placeRng.fork();
        const decorations = sector.map(() => {
          // Foliage and bark get separate tints. Sharing one drags trunks
          // toward the leaf hue and, worse, brightens them: a stand of pale
          // grey trunks was the loudest artefact of the first pass.
          const leaf = 0.80 + r.range(-0.12, 0.16);
          const barkTone = 0.74 + r.range(-0.14, 0.20);
          return {
            scale: r.range(opts.scale[0], opts.scale[1]),
            tall: r.range(0.88, 1.18),
            yaw: r.range(0, Math.PI * 2),
            tiltX: r.range(-0.045, 0.045),
            tiltZ: r.range(-0.045, 0.045),
            sink: -0.12,
            leafColor: new THREE.Color(
              leaf * (0.88 + r.range(-0.06, 0.06)),
              leaf,
              leaf * (0.74 + r.range(-0.06, 0.06))
            ),
            barkColor: new THREE.Color(
              barkTone * 1.03,
              barkTone * 0.98,
              barkTone * 0.92
            ),
          };
        });
        const barkPick = (spot, i) => ({ ...decorations[i], color: decorations[i].barkColor });
        const leafPick = (spot, i) => ({ ...decorations[i], color: decorations[i].leafColor });
        group.add(
          instanceField(proto.bark, proto.barkMat, sector, r, barkPick, {
            name: 'tree-bark',
            castShadow: true,
            receiveShadow: true,
            boundsPad: 2,
          })
        );
        group.add(
          instanceField(proto.leaf, proto.leafMat, sector, r, leafPick, {
            name: 'tree-foliage',
            castShadow: true,
            receiveShadow: true,
            boundsPad: 3,
          })
        );
        stats.trees += sector.length;
      }
    }
  }

  const nearTrees = thin(
    bandPlacement(placeRng, track, terrain, {
      count: Math.round(300 * density),
      minLateral: 13,
      maxLateral: 95,
      minTrackDist: 11.5,
      maxSlope: 1.1,
      power: 1.5,
    }),
    5.5
  );
  const farTrees = thin(
    scatterPlacement(placeRng, track, terrain, {
      count: Math.round(560 * density),
      minRadius: 60,
      maxRadius: 700,
      minTrackDist: 14,
      maxSlope: 1.35,
    }),
    9
  );

  plantField(nearTrees, [...conifers, ...oaks, ...oaks, birches[0], ...blossoms], {
    scale: [0.72, 1.18],
    sectors: 6,
  });
  plantField(farTrees, [...conifers, ...conifers, ...oaks, ...birches], {
    scale: [0.8, 1.35],
    sectors: 8,
  });

  // --- shrubs -------------------------------------------------------------

  const shrubSpots = thin(
    [
      ...bandPlacement(placeRng, track, terrain, {
        count: Math.round(420 * density),
        minLateral: 6.5,
        maxLateral: 60,
        minTrackDist: 5.6,
        maxSlope: 1.4,
      }),
      ...scatterPlacement(placeRng, track, terrain, {
        count: Math.round(300 * density),
        minRadius: 50,
        maxRadius: 620,
        minTrackDist: 8,
      }),
    ],
    2.6
  );
  plantField(shrubSpots, shrubs, { scale: [0.7, 1.5], sectors: 8 });
  stats.shrubs = shrubSpots.length;

  // --- grass --------------------------------------------------------------

  const grassRng = rng.fork();
  const grassMaterial = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    vertexColors: true,
    roughness: 0.92,
    metalness: 0,
    side: THREE.DoubleSide,
    // A bright sky washes thin blades out to grey; hold the IBL back.
    envMapIntensity: 0.6,
  });
  grassMaterial.name = 'grass-blades';
  wind.apply(grassMaterial, { amp: 0.10, rate: 1.55, height: 0.4, flutter: 0.012 });

  const tufts = [];
  for (let i = 0; i < 6; i++) tufts.push(grassTuft(grassRng, geom, 13, 0.235));

  // A dense verge either side of the line, thinning out with distance. This is
  // the ground the camera is closest to for the whole run, so it carries the
  // most instances by an order of magnitude.
  const grassSpots = [];
  const frame = {
    pos: new THREE.Vector3(),
    forward: new THREE.Vector3(),
    right: new THREE.Vector3(),
    up: new THREE.Vector3(),
  };
  const target = Math.round(185000 * density);
  let guard = 0;
  while (grassSpots.length < target && guard < target * 3) {
    guard++;
    const s = grassRng.next() * track.length;
    const lateral = grassRng.sign() * grassRng.power(3.1, 30, 2.0);
    track.frameAt(s, 2, frame);
    const x = frame.pos.x + frame.right.x * lateral + grassRng.range(-1.1, 1.1);
    const z = frame.pos.z + frame.right.z * lateral + grassRng.range(-1.1, 1.1);
    const near = track.nearest(x, z);
    if (near.dist < 3.05) continue;
    const y = terrain.heightAt(x, z);
    if (y < terrain.waterLevel + 0.8) continue;
    if (terrain.slopeAt(x, z, 2) > 1.25) continue;
    grassSpots.push({ x, y, z, trackDist: near.dist });
  }
  stats.grass = grassSpots.length;

  const grassBuckets = new Map();
  for (const spot of grassSpots) {
    const id = Math.floor(grassRng.next() * tufts.length) % tufts.length;
    let list = grassBuckets.get(id);
    if (!list) grassBuckets.set(id, (list = []));
    list.push(spot);
  }
  for (const [id, list] of grassBuckets) {
    for (const sector of sectorise(list, 20)) {
      group.add(
        instanceField(
          tufts[id],
          grassMaterial,
          sector,
          grassRng.fork(),
          (spot, i, r) => {
            // Trackside grass is beaten down and dusty; further out it is lush.
            const lush = smoothstep(3.5, 16, spot.trackDist);
            const v = 0.62 + lush * 0.30 + r.range(-0.12, 0.12);
            return {
              scale: r.range(0.82, 1.28) * (0.82 + lush * 0.3),
              tall: r.range(0.85, 1.35),
              yaw: r.range(0, Math.PI * 2),
              sink: -0.03,
              color: new THREE.Color(v * r.range(0.9, 1.06), v * r.range(0.96, 1.1), v * 0.8),
            };
          },
          { name: 'grass', castShadow: false, receiveShadow: true, boundsPad: 0.5 }
        )
      );
    }
  }

  // --- wildflowers --------------------------------------------------------

  const flowerRng = rng.fork();
  const flowerParts = [];
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI;
    const petal = new THREE.PlaneGeometry(0.032, 0.036, 1, 1);
    petal.rotateX(-Math.PI / 2 + 0.35);
    petal.rotateY(a);
    petal.translate(0, 0.155, 0);
    flowerParts.push(petal);
  }
  const stem = new THREE.PlaneGeometry(0.008, 0.155, 1, 1).translate(0, 0.078, 0);
  flowerParts.push(stem);
  const flowerGeom = geom.mergeAll(flowerParts);
  geom.paintGeometry(flowerGeom, 0xffffff);
  const flowerMaterial = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.78,
    metalness: 0,
    side: THREE.DoubleSide,
  });
  wind.apply(flowerMaterial, { amp: 0.05, rate: 2.1, height: 0.2, flutter: 0.006 });

  const flowerPalette = [0xfff3d0, 0xffe066, 0xf2f7ff, 0xf0a6c0, 0xc8b6ff, 0xffd6a0];
  const flowerSpots = thin(
    bandPlacement(flowerRng, track, terrain, {
      count: Math.round(6500 * density),
      minLateral: 5,
      maxLateral: 38,
      minTrackDist: 4.6,
      maxSlope: 0.9,
      jitter: 1.5,
    }),
    0.55
  );
  for (const sector of sectorise(flowerSpots, 10)) {
    group.add(
      instanceField(
        flowerGeom,
        flowerMaterial,
        sector,
        flowerRng.fork(),
        (spot, i, r) => ({
          scale: r.range(0.65, 1.25),
          yaw: r.range(0, Math.PI * 2),
          color: new THREE.Color(r.pick(flowerPalette)),
        }),
        { name: 'flowers', castShadow: false, receiveShadow: false, boundsPad: 0.3 }
      )
    );
  }

  return { group, stats };
}
