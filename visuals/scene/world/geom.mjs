/**
 * Geometry helpers owned by Part C.
 *
 * These are the *fallbacks*: `deps.mjs` prefers Part A's `visuals/lib/geom.mjs`
 * when it exists and drops back to everything here when it does not, so the
 * world is never blocked on another part landing. Nothing in this file imports
 * from `visuals/lib/`.
 *
 * Everything is pure — no randomness, no clocks.
 */

import * as THREE from 'three';
import { mergeGeometries as _mergeGeometries } from '../../node_modules/three/examples/jsm/utils/BufferGeometryUtils.js';

export const WORLD_UP = new THREE.Vector3(0, 1, 0);

// ---------------------------------------------------------------------------
// Merging
// ---------------------------------------------------------------------------

/**
 * Merge a list of geometries into one. Normalises the attribute sets first —
 * BufferGeometryUtils refuses to merge geometries whose attributes differ, and
 * hand-built parts routinely disagree about whether they carry uv or color.
 */
export function mergeAll(geometries, { groups = false } = {}) {
  const list = geometries.filter(Boolean);
  if (list.length === 0) return new THREE.BufferGeometry();
  if (list.length === 1 && !groups) return list[0];

  const wantsUv = list.some((g) => g.attributes.uv);
  const wantsColor = list.some((g) => g.attributes.color);
  // BufferGeometryUtils refuses a mix of indexed and non-indexed input, and
  // ExtrudeGeometry / PlaneGeometry disagree about this constantly. Give the
  // non-indexed ones a trivial index rather than expanding everything else.
  const wantsIndex = list.some((g) => g.index);
  for (const g of list) {
    if (wantsIndex && !g.index) {
      const n = g.attributes.position.count;
      const idx = n > 65535 ? new Uint32Array(n) : new Uint16Array(n);
      for (let i = 0; i < n; i++) idx[i] = i;
      g.setIndex(new THREE.BufferAttribute(idx, 1));
    }
    if (!g.attributes.normal) g.computeVertexNormals();
    if (wantsUv && !g.attributes.uv) {
      const n = g.attributes.position.count;
      g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(n * 2), 2));
    }
    if (wantsColor && !g.attributes.color) {
      const n = g.attributes.position.count;
      const c = new Float32Array(n * 3).fill(1);
      g.setAttribute('color', new THREE.BufferAttribute(c, 3));
    }
    // Drop anything exotic that would block the merge.
    for (const key of Object.keys(g.attributes)) {
      if (key !== 'position' && key !== 'normal' && key !== 'uv' && key !== 'color') {
        g.deleteAttribute(key);
      }
    }
  }
  const merged = _mergeGeometries(list, groups);
  if (!merged) throw new Error('world/geom: mergeAll failed');
  merged.computeBoundingSphere();
  return merged;
}

/** Alias kept for readability at call sites that merge one model's parts. */
export const mergeGeometries = mergeAll;

// ---------------------------------------------------------------------------
// Beveled primitives
//
// A perfectly sharp 90-degree edge never occurs in reality and is the single
// strongest "this is CG" tell, so every box in the world goes through here.
// The construction is the exact Minkowski sum of a core box and a sphere of
// radius `bevel`: 6 flat faces, 12 quarter-cylinder edges, 8 spherical corners.
// Faces stay perfectly flat, which a "spherified cube" would not manage.
// ---------------------------------------------------------------------------

class Builder {
  constructor() {
    this.pos = [];
    this.nrm = [];
    this.uv = [];
    this.idx = [];
  }

  vertex(px, py, pz, nx, ny, nz, u, v) {
    this.pos.push(px, py, pz);
    this.nrm.push(nx, ny, nz);
    this.uv.push(u, v);
    return this.pos.length / 3 - 1;
  }

  quad(a, b, c, d) {
    this.idx.push(a, b, c, a, c, d);
  }

  /** Emit a (rows x cols) grid of already-pushed vertices starting at `base`. */
  grid(base, rows, cols, flip = false) {
    for (let r = 0; r < rows - 1; r++) {
      for (let c = 0; c < cols - 1; c++) {
        const a = base + r * cols + c;
        const b = a + 1;
        const d = a + cols;
        const e = d + 1;
        if (flip) this.quad(a, d, e, b);
        else this.quad(a, b, e, d);
      }
    }
  }

  finish() {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.pos, 3));
    g.setAttribute('normal', new THREE.Float32BufferAttribute(this.nrm, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(this.uv, 2));
    g.setIndex(this.idx);
    g.computeBoundingSphere();
    return g;
  }
}

const AXES = ['x', 'y', 'z'];

/**
 * Rounded (beveled) box.
 *
 * @param {number} w
 * @param {number} h
 * @param {number} d
 * @param {{bevel?:number, segments?:number, uvScale?:number, faceSegments?:number}} opts
 *   `bevel` in world units (clamped to a third of the smallest dimension),
 *   `segments` arc samples across the bevel, `uvScale` world units per texture
 *   repeat, `faceSegments` extra subdivision of the flat faces.
 */
export function beveledBox(w, h, d, opts = {}) {
  const segments = Math.max(1, opts.segments ?? 2);
  const uvScale = opts.uvScale ?? 1;
  const fs = Math.max(1, opts.faceSegments ?? 1);
  const r = Math.min(opts.bevel ?? Math.min(w, h, d) * 0.04, w / 3, h / 3, d / 3);

  const half = { x: w / 2, y: h / 2, z: d / 2 };
  const core = { x: half.x - r, y: half.y - r, z: half.z - r };
  const b = new Builder();

  // --- 6 flat faces -------------------------------------------------------
  for (let axis = 0; axis < 3; axis++) {
    const A = AXES[axis];
    const U = AXES[(axis + 1) % 3];
    const V = AXES[(axis + 2) % 3];
    for (const sign of [1, -1]) {
      const base = b.pos.length / 3;
      const n = { x: 0, y: 0, z: 0 };
      n[A] = sign;
      for (let i = 0; i <= fs; i++) {
        const tv = (i / fs) * 2 - 1;
        for (let j = 0; j <= fs; j++) {
          const tu = (j / fs) * 2 - 1;
          const p = { x: 0, y: 0, z: 0 };
          p[A] = sign * half[A];
          p[U] = tu * core[U] * sign;
          p[V] = tv * core[V];
          b.vertex(
            p.x,
            p.y,
            p.z,
            n.x,
            n.y,
            n.z,
            (p[U] + half[U]) / uvScale,
            (p[V] + half[V]) / uvScale
          );
        }
      }
      b.grid(base, fs + 1, fs + 1);
    }
  }

  // --- 12 quarter-cylinder edges -----------------------------------------
  // For each axis pair, sweep an arc from one face normal to the next around
  // the shared edge, running the length of the third axis.
  for (let axis = 0; axis < 3; axis++) {
    const L = AXES[axis]; // edge runs along this axis
    const A = AXES[(axis + 1) % 3];
    const B = AXES[(axis + 2) % 3];
    for (const sa of [1, -1]) {
      for (const sb of [1, -1]) {
        const base = b.pos.length / 3;
        for (let i = 0; i <= 1; i++) {
          const lc = (i === 0 ? -1 : 1) * core[L];
          for (let k = 0; k <= segments; k++) {
            const ang = (k / segments) * (Math.PI / 2);
            const na = sa * Math.cos(ang);
            const nb = sb * Math.sin(ang);
            const p = { x: 0, y: 0, z: 0 };
            p[L] = lc;
            p[A] = sa * core[A] + na * r;
            p[B] = sb * core[B] + nb * r;
            const n = { x: 0, y: 0, z: 0 };
            n[A] = na;
            n[B] = nb;
            b.vertex(
              p.x,
              p.y,
              p.z,
              n.x,
              n.y,
              n.z,
              (p[L] + half[L]) / uvScale,
              ((k / segments) * r * 1.5708 + (sa * sb > 0 ? 0 : r)) / uvScale
            );
          }
        }
        const flip = sa * sb * (axis === 1 ? -1 : 1) > 0;
        b.grid(base, 2, segments + 1, flip);
      }
    }
  }

  // --- 8 spherical corners ------------------------------------------------
  for (const sx of [1, -1]) {
    for (const sy of [1, -1]) {
      for (const sz of [1, -1]) {
        const base = b.pos.length / 3;
        for (let i = 0; i <= segments; i++) {
          const phi = (i / segments) * (Math.PI / 2); // from equator to pole
          for (let j = 0; j <= segments; j++) {
            const th = (j / segments) * (Math.PI / 2);
            const nx = sx * Math.cos(phi) * Math.cos(th);
            const nz = sz * Math.cos(phi) * Math.sin(th);
            const ny = sy * Math.sin(phi);
            b.vertex(
              sx * core.x + nx * r,
              sy * core.y + ny * r,
              sz * core.z + nz * r,
              nx,
              ny,
              nz,
              ((j / segments) * r) / uvScale,
              ((i / segments) * r) / uvScale
            );
          }
        }
        b.grid(base, segments + 1, segments + 1, sx * sy * sz > 0);
      }
    }
  }

  const g = b.finish();
  // Both windings appear in the assembly above; rather than chase every sign,
  // render these double-sided-safe by making them solid from outside. The
  // winding fix below flips any triangle whose face normal disagrees with its
  // vertex normals.
  fixWinding(g);
  return g;
}

/** Flip triangles whose geometric normal points against their vertex normals. */
function fixWinding(geometry) {
  const idx = geometry.index.array;
  const pos = geometry.attributes.position.array;
  const nrm = geometry.attributes.normal.array;
  for (let i = 0; i < idx.length; i += 3) {
    const a = idx[i] * 3;
    const bb = idx[i + 1] * 3;
    const c = idx[i + 2] * 3;
    const ux = pos[bb] - pos[a];
    const uy = pos[bb + 1] - pos[a + 1];
    const uz = pos[bb + 2] - pos[a + 2];
    const vx = pos[c] - pos[a];
    const vy = pos[c + 1] - pos[a + 1];
    const vz = pos[c + 2] - pos[a + 2];
    const fx = uy * vz - uz * vy;
    const fy = uz * vx - ux * vz;
    const fz = ux * vy - uy * vx;
    const dot = fx * nrm[a] + fy * nrm[a + 1] + fz * nrm[a + 2];
    if (dot < 0) {
      const tmp = idx[i + 1];
      idx[i + 1] = idx[i + 2];
      idx[i + 2] = tmp;
    }
  }
  geometry.index.needsUpdate = true;
}

/**
 * Insert circular fillets at every interior corner of a 2D polyline.
 * Feeds LatheGeometry and the sweep below so lathed and extruded silhouettes
 * get the same rounded edges as the boxes.
 *
 * @param {[number,number][]} points
 * @param {{bevel?:number, segments?:number, closed?:boolean}} opts
 * @returns {THREE.Vector2[]}
 */
export function roundedProfile(points, opts = {}) {
  const bevel = opts.bevel ?? 0.02;
  const seg = Math.max(1, opts.segments ?? 3);
  const closed = opts.closed ?? false;
  const n = points.length;
  const out = [];
  const P = (i) => points[((i % n) + n) % n];

  for (let i = 0; i < n; i++) {
    if (!closed && (i === 0 || i === n - 1)) {
      out.push(new THREE.Vector2(P(i)[0], P(i)[1]));
      continue;
    }
    const prev = P(i - 1);
    const cur = P(i);
    const next = P(i + 1);
    let ax = prev[0] - cur[0];
    let ay = prev[1] - cur[1];
    let bx = next[0] - cur[0];
    let by = next[1] - cur[1];
    const la = Math.hypot(ax, ay);
    const lb = Math.hypot(bx, by);
    if (la < 1e-6 || lb < 1e-6) {
      out.push(new THREE.Vector2(cur[0], cur[1]));
      continue;
    }
    ax /= la;
    ay /= la;
    bx /= lb;
    by /= lb;
    const cosA = Math.max(-0.9999, Math.min(0.9999, ax * bx + ay * by));
    const angle = Math.acos(cosA);
    if (angle > Math.PI - 0.05) {
      out.push(new THREE.Vector2(cur[0], cur[1]));
      continue;
    }
    const cut = Math.min(bevel / Math.tan(angle / 2), la * 0.45, lb * 0.45);
    const sx = cur[0] + ax * cut;
    const sy = cur[1] + ay * cut;
    const ex = cur[0] + bx * cut;
    const ey = cur[1] + by * cut;
    for (let k = 0; k <= seg; k++) {
      // Quadratic Bezier through the corner: visually identical to an arc at
      // these radii and free of the degenerate cases a true arc hits.
      const t = k / seg;
      const mt = 1 - t;
      out.push(
        new THREE.Vector2(
          mt * mt * sx + 2 * mt * t * cur[0] + t * t * ex,
          mt * mt * sy + 2 * mt * t * cur[1] + t * t * ey
        )
      );
    }
  }
  return out;
}

/** A cylinder with its rims rounded off, axis +Y, centred on the origin. */
export function beveledCylinder(rTop, rBottom, h, opts = {}) {
  const bevel = Math.min(opts.bevel ?? 0.02, h / 3, Math.max(rTop, rBottom) / 3);
  const radialSegments = opts.radialSegments ?? 20;
  const capSegments = Math.max(1, opts.segments ?? 2);
  const profile = roundedProfile(
    [
      [0.0001, -h / 2],
      [rBottom, -h / 2],
      [rTop, h / 2],
      [0.0001, h / 2],
    ],
    { bevel, segments: capSegments }
  );
  const g = new THREE.LatheGeometry(profile, radialSegments);
  g.computeVertexNormals();
  return g;
}

/** A capsule-ish tapered limb: radius as a function of t along +Y. */
export function taperedCapsule(length, rTop, rBottom, opts = {}) {
  const radial = opts.radialSegments ?? 14;
  const bevel = opts.bevel ?? Math.min(rTop, rBottom) * 0.6;
  const profile = roundedProfile(
    [
      [0.0001, 0],
      [rBottom, 0],
      [rTop, length],
      [0.0001, length],
    ],
    { bevel, segments: 4 }
  );
  const g = new THREE.LatheGeometry(profile, radial);
  g.computeVertexNormals();
  return g;
}

// ---------------------------------------------------------------------------
// Sweeps
// ---------------------------------------------------------------------------

/**
 * Sweep a closed 2D cross-section along a sequence of oriented frames, with
 * real UVs: u runs along the sweep in world metres, v runs across the profile
 * in world metres. Rails, ballast, kerbs and fence rails all come from here.
 *
 * @param {{pos:THREE.Vector3, right:THREE.Vector3, up:THREE.Vector3}[]} frames
 * @param {[number, number][]} profile cross-section points, [right, up]
 * @param {{closed?:boolean, uvScale?:number, smooth?:boolean, cap?:boolean}} opts
 */
export function extrudeProfile(frames, profile, opts = {}) {
  const closed = opts.closed ?? true;
  const uvScale = opts.uvScale ?? 1;
  const ringCount = frames.length;
  const profCount = profile.length;

  // Arc length across the profile, for the v coordinate.
  const vCoord = new Float32Array(profCount + 1);
  for (let k = 1; k <= profCount; k++) {
    const a = profile[k - 1];
    const bb = profile[k % profCount];
    vCoord[k] = vCoord[k - 1] + Math.hypot(bb[0] - a[0], bb[1] - a[1]);
  }

  // Duplicate the seam column so u is continuous, and duplicate every profile
  // vertex so the cross-section keeps hard corners while the sweep stays
  // smooth. (Sharing across a 90-degree profile corner smears the highlight.)
  const cols = profCount * 2;
  const rows = closed ? ringCount + 1 : ringCount;
  const positions = new Float32Array(rows * cols * 3);
  const uvs = new Float32Array(rows * cols * 2);

  let u = 0;
  let w = 0;
  let wu = 0;
  for (let i = 0; i < rows; i++) {
    const f = frames[i % ringCount];
    if (i > 0) {
      const prev = frames[(i - 1) % ringCount];
      u += f.pos.distanceTo(prev.pos);
    }
    for (let k = 0; k < profCount; k++) {
      for (let dup = 0; dup < 2; dup++) {
        const pi = dup === 0 ? k : (k + 1) % profCount;
        const [pr, pu] = profile[pi];
        positions[w++] = f.pos.x + f.right.x * pr + f.up.x * pu;
        positions[w++] = f.pos.y + f.right.y * pr + f.up.y * pu;
        positions[w++] = f.pos.z + f.right.z * pr + f.up.z * pu;
        uvs[wu++] = u / uvScale;
        uvs[wu++] = (dup === 0 ? vCoord[k] : vCoord[k + 1]) / uvScale;
      }
    }
  }

  const indices = [];
  for (let i = 0; i < rows - 1; i++) {
    for (let k = 0; k < profCount; k++) {
      const a = i * cols + k * 2;
      const bb = a + 1;
      const c = (i + 1) * cols + k * 2 + 1;
      const dd = (i + 1) * cols + k * 2;
      indices.push(a, bb, c, a, c, dd);
    }
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geom.setIndex(indices);
  geom.computeVertexNormals();
  geom.computeBoundingSphere();
  return geom;
}

/** A smooth tube through a list of points — wires, branches, pipes, handrails. */
export function tubeThrough(points, radius, opts = {}) {
  const radial = opts.radialSegments ?? 8;
  const tubular = opts.tubularSegments ?? Math.max(8, points.length * 4);
  const curve = new THREE.CatmullRomCurve3(points, opts.closed ?? false, 'catmullrom', 0.5);
  const g = new THREE.TubeGeometry(curve, tubular, radius, radial, opts.closed ?? false);
  return g;
}

// ---------------------------------------------------------------------------
// Placement helpers
// ---------------------------------------------------------------------------

const _right = new THREE.Vector3();
const _up = new THREE.Vector3();
const _fwd = new THREE.Vector3();

/** Write an orthonormal basis into `matrix` from a forward direction (local +Z). */
export function composeFromForward(matrix, position, forward, scale = null) {
  _fwd.copy(forward).normalize();
  _right.crossVectors(WORLD_UP, _fwd);
  if (_right.lengthSq() < 1e-8) _right.set(1, 0, 0);
  _right.normalize();
  _up.crossVectors(_fwd, _right).normalize();
  matrix.makeBasis(_right, _up, _fwd);
  matrix.setPosition(position);
  if (scale) matrix.scale(scale);
  return matrix;
}

/** A box whose origin sits at the centre of its base. */
export function boxOnBase(width, height, depth, opts = {}) {
  return beveledBox(width, height, depth, opts).translate(0, height / 2, 0);
}

/** A cylinder standing on its base, axis +Y. */
export function cylinderOnBase(rTop, rBottom, height, opts = {}) {
  return beveledCylinder(rTop, rBottom, height, opts).translate(0, height / 2, 0);
}

/** Give a geometry planar UVs from its dominant face normal, in world units. */
export function boxUVs(geometry, scale = 1) {
  const pos = geometry.attributes.position;
  const nrm = geometry.attributes.normal;
  const uv = new Float32Array(pos.count * 2);
  for (let i = 0; i < pos.count; i++) {
    const nx = Math.abs(nrm.getX(i));
    const ny = Math.abs(nrm.getY(i));
    const nz = Math.abs(nrm.getZ(i));
    let u;
    let v;
    if (ny >= nx && ny >= nz) {
      u = pos.getX(i);
      v = pos.getZ(i);
    } else if (nx >= nz) {
      u = pos.getZ(i);
      v = pos.getY(i);
    } else {
      u = pos.getX(i);
      v = pos.getY(i);
    }
    uv[i * 2] = u / scale;
    uv[i * 2 + 1] = v / scale;
  }
  geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  return geometry;
}

/** Paint a whole geometry a single vertex colour (linear-space floats). */
export function paintGeometry(geometry, color) {
  const n = geometry.attributes.position.count;
  const c = new Float32Array(n * 3);
  const col = color.isColor ? color : new THREE.Color(color);
  for (let i = 0; i < n; i++) {
    c[i * 3] = col.r;
    c[i * 3 + 1] = col.g;
    c[i * 3 + 2] = col.b;
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(c, 3));
  return geometry;
}
