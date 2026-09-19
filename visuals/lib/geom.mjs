/**
 * lib/geom.mjs — beveled primitives and geometry helpers.
 *
 * Part A of the v2 quality rebuild. Everything the world and the train are
 * built from goes through here, because of the single most important line in
 * the contract:
 *
 *   > A perfectly sharp 90-degree edge never occurs in reality and is the
 *   > single strongest "this is CG" tell.
 *
 * So there are no `BoxGeometry` calls in this project. `beveledBox()` builds
 * the real thing: six flat faces, twelve quarter-cylinder edges and eight
 * spherical corners, i.e. the Minkowski sum of a box with a small sphere.
 * Normals are analytic, not averaged — the flat faces get a constant face
 * normal (so they shade flat) and the bevel patches get the exact rounded
 * normal (so they shade smooth). That is the same result a normal-angle
 * crease threshold would give, arrived at exactly instead of by tolerance.
 * `creaseNormals()` is exported for the extrude/lathe paths where an analytic
 * normal is not available.
 *
 * ---------------------------------------------------------------------------
 * UV CONVENTION — read this before consuming anything here
 * ---------------------------------------------------------------------------
 * UVs are in WORLD UNITS (metres), not normalised to [0,1].
 *
 *   u, v = position along the surface, in metres, times `uvScale`.
 *
 * A 2 m plank and a 0.2 m bolt therefore get the same texel density from the
 * same shared material, which is what stops a scene reading as programmer art.
 * `materials.pbr(slug, {repeat:[a,b]})` then means literally "the texture
 * tiles `a` times per metre" — so `repeat:[0.5,0.5]` is a 2 m texture.
 *
 * Pass `{uvMode:'unit'}` to any builder for the classic per-face [0,1]
 * mapping instead.
 *
 * Every builder writes `uv`, plus `uv1` and `uv2` as copies. three r181 reads
 * the second UV set from the `uv1` attribute (`aoMap.channel = 1`); `uv2` is
 * carried as an alias for callers that expect the pre-r151 name.
 */

import * as THREE from 'three';
import {
  mergeGeometries,
  toCreasedNormals,
} from '../node_modules/three/examples/jsm/utils/BufferGeometryUtils.js';

/** Default bevel radius, in metres. 2 cm reads as a machined edge at 1 m scale. */
export const DEFAULT_BEVEL = 0.02;

const EPS = 1e-9;

// ---------------------------------------------------------------------------
// Box-projection table
// ---------------------------------------------------------------------------

/**
 * Per-face UV basis, matching three's own BoxGeometry orientation so textures
 * are never mirrored when viewed from outside.
 * Rows are [faceAxis, faceSign, uAxis, uSign, vAxis, vSign] with axes 0=x 1=y 2=z.
 */
const FACES = [
  [0, +1, 2, -1, 1, +1], // +X
  [0, -1, 2, +1, 1, +1], // -X
  [1, +1, 0, +1, 2, +1], // +Y
  [1, -1, 0, +1, 2, -1], // -Y
  [2, +1, 0, +1, 1, +1], // +Z
  [2, -1, 0, -1, 1, +1], // -Z
];

/** Look up the FACES row for a given axis/sign. */
function faceBasis(axis, sign) {
  return FACES[axis * 2 + (sign > 0 ? 0 : 1)];
}

// ---------------------------------------------------------------------------
// Mesher — accumulates one indexed geometry from a series of grid patches
// ---------------------------------------------------------------------------

class Mesher {
  constructor() {
    this.position = [];
    this.normal = [];
    this.uv = [];
    this.index = [];
  }

  get vertexCount() {
    return this.position.length / 3;
  }

  vertex(x, y, z, nx, ny, nz, u, v) {
    this.position.push(x, y, z);
    this.normal.push(nx, ny, nz);
    this.uv.push(u, v);
    return this.vertexCount - 1;
  }

  /**
   * Emit an (nI x nJ) quad patch.
   *
   * `sample(i, j)` returns [x, y, z, nx, ny, nz, u, v]. Winding is derived from
   * the patch itself — the first non-degenerate quad's geometric normal is
   * compared against the supplied surface normal — so patch authors never have
   * to reason about index order. Degenerate triangles (sphere poles, zero-width
   * faces) are dropped rather than emitted with zero area.
   */
  grid(nI, nJ, sample) {
    if (nI < 1 || nJ < 1) return;
    const base = this.vertexCount;
    const stride = nJ + 1;
    const verts = new Array((nI + 1) * stride);

    for (let i = 0; i <= nI; i++) {
      for (let j = 0; j <= nJ; j++) {
        const s = sample(i, j);
        verts[i * stride + j] = s;
        this.vertex(s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7]);
      }
    }

    const at = (i, j) => verts[i * stride + j];
    const id = (i, j) => base + i * stride + j;

    // -- winding probe -------------------------------------------------------
    // Compare the geometric normal of the first non-degenerate emitted
    // triangle against the surface normal the sampler reported for it.
    let flip = false;
    probe: for (let i = 0; i < nI; i++) {
      for (let j = 0; j < nJ; j++) {
        for (const tri of [
          [at(i, j), at(i + 1, j), at(i + 1, j + 1)],
          [at(i, j), at(i + 1, j + 1), at(i, j + 1)],
        ]) {
          const dot = orientation(tri[0], tri[1], tri[2]);
          if (dot === null) continue;
          flip = dot < 0;
          break probe;
        }
      }
    }

    // -- triangles -----------------------------------------------------------
    for (let i = 0; i < nI; i++) {
      for (let j = 0; j < nJ; j++) {
        const pa = at(i, j);
        const pb = at(i + 1, j);
        const pc = at(i + 1, j + 1);
        const pd = at(i, j + 1);
        const a = id(i, j);
        const b = id(i + 1, j);
        const c = id(i + 1, j + 1);
        const d = id(i, j + 1);
        if (!degenerate(pa, pb, pc)) this.tri(a, b, c, flip);
        if (!degenerate(pa, pc, pd)) this.tri(a, c, d, flip);
      }
    }
  }

  tri(a, b, c, flip) {
    if (flip) this.index.push(a, c, b);
    else this.index.push(a, b, c);
  }

  toGeometry() {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.position, 3));
    g.setAttribute('normal', new THREE.Float32BufferAttribute(this.normal, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(this.uv, 2));
    g.setIndex(this.index);
    ensureUV2(g);
    g.computeBoundingSphere();
    g.computeBoundingBox();
    return g;
  }
}

function same(a, b) {
  return (
    Math.abs(a[0] - b[0]) < EPS && Math.abs(a[1] - b[1]) < EPS && Math.abs(a[2] - b[2]) < EPS
  );
}

function degenerate(a, b, c) {
  return same(a, b) || same(b, c) || same(a, c);
}

/**
 * Sign of (triangle geometric normal) . (vertex normal), or null when the
 * triangle has no usable area or the two are near-perpendicular.
 */
function orientation(a, b, c) {
  const e1x = b[0] - a[0];
  const e1y = b[1] - a[1];
  const e1z = b[2] - a[2];
  const e2x = c[0] - a[0];
  const e2y = c[1] - a[1];
  const e2z = c[2] - a[2];
  const cx = e1y * e2z - e1z * e2y;
  const cy = e1z * e2x - e1x * e2z;
  const cz = e1x * e2y - e1y * e2x;
  const len = Math.sqrt(cx * cx + cy * cy + cz * cz);
  if (len < 1e-12) return null;
  const nx = a[3] + b[3] + c[3];
  const ny = a[4] + b[4] + c[4];
  const nz = a[5] + b[5] + c[5];
  const nl = Math.sqrt(nx * nx + ny * ny + nz * nz);
  if (nl < 1e-9) return null;
  const dot = (cx * nx + cy * ny + cz * nz) / (len * nl);
  return Math.abs(dot) < 1e-3 ? null : dot;
}

// ---------------------------------------------------------------------------
// beveledBox
// ---------------------------------------------------------------------------

/**
 * A box with rounded edges and corners, centred on the origin.
 *
 * @param {number} w              width  (x)
 * @param {number} h              height (y)
 * @param {number} d              depth  (z)
 * @param {object} [opts]
 * @param {number} [opts.bevel=0.02]      bevel radius in metres; clamped to
 *                                        half the smallest dimension
 * @param {number} [opts.segments=2]      arc subdivisions per 90 degrees.
 *                                        1 = a flat chamfer (still shades as a
 *                                        rounded edge, since the chamfer
 *                                        interpolates between the two face
 *                                        normals), 2-3 = a rounded edge
 * @param {number} [opts.uvScale=1]       texture repeats per metre multiplier
 * @param {'world'|'unit'} [opts.uvMode='world']
 * @param {number} [opts.widthSegments=1] subdivisions of the flat faces along x
 * @param {number} [opts.heightSegments=1]
 * @param {number} [opts.depthSegments=1]
 * @returns {THREE.BufferGeometry}
 */
export function beveledBox(w, h, d, opts = {}) {
  const {
    bevel = DEFAULT_BEVEL,
    segments = 2,
    uvScale = 1,
    uvMode = 'world',
    widthSegments = 1,
    heightSegments = 1,
    depthSegments = 1,
  } = opts;

  const half = [Math.abs(w) / 2, Math.abs(h) / 2, Math.abs(d) / 2];
  const r = Math.max(0, Math.min(bevel, Math.min(half[0], half[1], half[2])));
  const core = [half[0] - r, half[1] - r, half[2] - r];
  const arc = Math.max(1, Math.round(segments));
  const div = [
    Math.max(1, Math.round(widthSegments)),
    Math.max(1, Math.round(heightSegments)),
    Math.max(1, Math.round(depthSegments)),
  ];
  const unit = uvMode === 'unit';

  /** Map a world coordinate on `axis` to a UV coordinate. */
  const mapUV = (axis, sign, value) =>
    unit ? (sign * value) / (2 * half[axis] || 1) + 0.5 : sign * value * uvScale;

  const m = new Mesher();

  // -- 6 flat faces ---------------------------------------------------------
  for (const [axis, sign, uAxis, uSign, vAxis, vSign] of FACES) {
    const cu = core[uAxis];
    const cv = core[vAxis];
    if (cu <= EPS || cv <= EPS) continue; // fully consumed by the bevel
    const nI = div[uAxis];
    const nJ = div[vAxis];
    const p = [0, 0, 0];
    const n = [0, 0, 0];
    m.grid(nI, nJ, (i, j) => {
      p[0] = p[1] = p[2] = 0;
      n[0] = n[1] = n[2] = 0;
      p[axis] = sign * half[axis];
      p[uAxis] = -cu + (2 * cu * i) / nI;
      p[vAxis] = -cv + (2 * cv * j) / nJ;
      n[axis] = sign;
      return [
        p[0], p[1], p[2],
        n[0], n[1], n[2],
        mapUV(uAxis, uSign, p[uAxis]),
        mapUV(vAxis, vSign, p[vAxis]),
      ];
    });
  }

  if (r > EPS) {
    // -- 12 quarter-cylinder edges -----------------------------------------
    for (let A = 0; A < 3; A++) {
      const B = (A + 1) % 3;
      const C = (A + 2) % 3;
      if (core[A] <= EPS) continue; // zero-length edge
      for (const sB of [-1, 1]) {
        for (const sC of [-1, 1]) {
          // Unroll the bevel's UV from the B face, by arc length, so the
          // texture crosses the bevel without stretching.
          const [, , uAxis, uSign, vAxis, vSign] = faceBasis(B, sB);
          const cAlongC = uAxis === C;
          const p = [0, 0, 0];
          const n = [0, 0, 0];
          m.grid(arc, div[A], (i, j) => {
            const a = (i / arc) * (Math.PI / 2);
            const t = -core[A] + (2 * core[A] * j) / div[A];
            p[0] = p[1] = p[2] = 0;
            n[0] = n[1] = n[2] = 0;
            n[B] = sB * Math.cos(a);
            n[C] = sC * Math.sin(a);
            p[A] = t;
            p[B] = sB * core[B] + r * n[B];
            p[C] = sC * core[C] + r * n[C];
            const unrolled = sC * (core[C] + r * a);
            const uVal = cAlongC ? mapUV(C, uSign, unrolled) : mapUV(A, uSign, t);
            const vVal = cAlongC ? mapUV(A, vSign, t) : mapUV(C, vSign, unrolled);
            return [p[0], p[1], p[2], n[0], n[1], n[2], uVal, vVal];
          });
        }
      }
    }

    // -- 8 spherical corners ------------------------------------------------
    for (const sx of [-1, 1]) {
      for (const sy of [-1, 1]) {
        for (const sz of [-1, 1]) {
          const [, , uAxis, uSign, vAxis, vSign] = faceBasis(1, sy); // Y face basis
          const cx = sx * core[0];
          const cy = sy * core[1];
          const cz = sz * core[2];
          m.grid(arc, arc, (i, j) => {
            const theta = (i / arc) * (Math.PI / 2); // from the Y pole
            const phi = (j / arc) * (Math.PI / 2);
            const lx = Math.sin(theta) * Math.cos(phi);
            const ly = Math.cos(theta);
            const lz = Math.sin(theta) * Math.sin(phi);
            const nx = sx * lx;
            const ny = sy * ly;
            const nz = sz * lz;
            // Unroll both tangent directions by arc length off the Y face.
            const coord = [
              sx * (core[0] + r * Math.atan2(lx, ly)),
              sy * half[1],
              sz * (core[2] + r * Math.atan2(lz, ly)),
            ];
            return [
              cx + r * nx, cy + r * ny, cz + r * nz,
              nx, ny, nz,
              mapUV(uAxis, uSign, coord[uAxis]),
              mapUV(vAxis, vSign, coord[vAxis]),
            ];
          });
        }
      }
    }
  }

  return m.toGeometry();
}

// ---------------------------------------------------------------------------
// roundedProfile
// ---------------------------------------------------------------------------

/**
 * Round the corners of a 2D polyline, for lathe and extrude profiles.
 *
 * The profile must be ordered so the solid material lies to the LEFT of the
 * direction of travel (i.e. counter-clockwise around the material). For a
 * lathe profile in the (radius, y) plane that means bottom-axis -> outwards ->
 * up the side -> back to the axis. Outward normals are then the right-hand
 * side of each segment.
 *
 * Returns a plain array of `THREE.Vector2` — so it drops straight into
 * `THREE.LatheGeometry` or `THREE.Shape` — with two extra non-enumerable
 * properties:
 *
 *   `.normals`  parallel array of `THREE.Vector2` outward normals
 *   `.arcLength` parallel array of cumulative arc length, used for lathe v
 *
 * Sharp (unrounded) vertices appear twice, once per adjacent segment normal,
 * so `lathe()` shades them flat. Rounded corners carry their exact arc normals.
 *
 * @param {Array<THREE.Vector2|{x:number,y:number}|[number,number]>} points
 * @param {object} [opts]
 * @param {number} [opts.bevel=0.02]  corner radius, clamped per corner
 * @param {number} [opts.segments=2]  arc subdivisions per corner
 */
export function roundedProfile(points, opts = {}) {
  const { bevel = DEFAULT_BEVEL, segments = 2 } = opts;
  const V = points.map(toVec2);
  const arcSegs = Math.max(1, Math.round(segments));
  const n = V.length;

  const out = [];
  const normals = [];

  if (n < 2) return decorate(V.slice(), V.map(() => new THREE.Vector2(0, 1)));

  // Segment directions and outward (right-hand) normals.
  const dir = [];
  const nrm = [];
  for (let i = 0; i < n - 1; i++) {
    const d = new THREE.Vector2().subVectors(V[i + 1], V[i]);
    const len = d.length();
    if (len < EPS) {
      dir.push(new THREE.Vector2(1, 0));
      nrm.push(new THREE.Vector2(0, -1));
      continue;
    }
    d.divideScalar(len);
    dir.push(d);
    nrm.push(new THREE.Vector2(d.y, -d.x));
  }

  // Per-interior-vertex rounding data.
  const corner = new Array(n).fill(null);
  for (let i = 1; i < n - 1; i++) {
    const dIn = dir[i - 1];
    const dOut = dir[i];
    const cross = dIn.x * dOut.y - dIn.y * dOut.x;
    const dot = THREE.MathUtils.clamp(-dIn.x * dOut.x - dIn.y * dOut.y, -1, 1);
    const phi = Math.acos(dot); // interior angle at the vertex
    if (bevel <= EPS || Math.abs(cross) < 1e-7 || phi < 1e-4 || phi > Math.PI - 1e-4) continue;

    const lenIn = V[i].distanceTo(V[i - 1]);
    const lenOut = V[i].distanceTo(V[i + 1]);
    const tanHalf = Math.tan(phi / 2);
    let tDist = bevel / tanHalf;
    const maxT = 0.5 * Math.min(lenIn, lenOut);
    let rEff = bevel;
    if (tDist > maxT) {
      tDist = maxT;
      rEff = tDist * tanHalf;
    }
    if (rEff <= EPS) continue;

    const back = new THREE.Vector2(-dIn.x, -dIn.y); // from V[i] towards V[i-1]
    const T1 = new THREE.Vector2().copy(V[i]).addScaledVector(back, tDist);
    const T2 = new THREE.Vector2().copy(V[i]).addScaledVector(dOut, tDist);
    const bis = new THREE.Vector2().addVectors(back, dOut);
    if (bis.lengthSq() < EPS) continue;
    bis.normalize();
    const center = new THREE.Vector2()
      .copy(V[i])
      .addScaledVector(bis, rEff / Math.sin(phi / 2));
    corner[i] = { T1, T2, center, r: rEff, convex: cross > 0 };
  }

  // Walk the segments, splicing in the arcs.
  for (let s = 0; s < n - 1; s++) {
    const nr = nrm[s];
    const a = corner[s] ? corner[s].T2 : V[s];
    const b = corner[s + 1] ? corner[s + 1].T1 : V[s + 1];
    out.push(a.clone());
    normals.push(nr.clone());
    out.push(b.clone());
    normals.push(nr.clone());

    const c = corner[s + 1];
    if (c) {
      const a0 = Math.atan2(c.T1.y - c.center.y, c.T1.x - c.center.x);
      let a1 = Math.atan2(c.T2.y - c.center.y, c.T2.x - c.center.x);
      // shortest way round
      while (a1 - a0 > Math.PI) a1 -= Math.PI * 2;
      while (a1 - a0 < -Math.PI) a1 += Math.PI * 2;
      const sign = c.convex ? 1 : -1;
      for (let k = 1; k < arcSegs; k++) {
        const ang = a0 + ((a1 - a0) * k) / arcSegs;
        const px = c.center.x + c.r * Math.cos(ang);
        const py = c.center.y + c.r * Math.sin(ang);
        out.push(new THREE.Vector2(px, py));
        normals.push(new THREE.Vector2(sign * Math.cos(ang), sign * Math.sin(ang)));
      }
    }
  }

  return decorate(out, normals);
}

function decorate(pts, normals) {
  const arcLength = new Array(pts.length);
  let acc = 0;
  for (let i = 0; i < pts.length; i++) {
    if (i > 0) acc += pts[i].distanceTo(pts[i - 1]);
    arcLength[i] = acc;
  }
  Object.defineProperty(pts, 'normals', { value: normals, enumerable: false });
  Object.defineProperty(pts, 'arcLength', { value: arcLength, enumerable: false });
  return pts;
}

function toVec2(p) {
  if (p instanceof THREE.Vector2) return p.clone();
  if (Array.isArray(p)) return new THREE.Vector2(p[0], p[1]);
  return new THREE.Vector2(p.x, p.y);
}

// ---------------------------------------------------------------------------
// lathe
// ---------------------------------------------------------------------------

/**
 * Revolve a profile around the Y axis, honouring per-point normals.
 *
 * three's own `LatheGeometry` averages adjacent segment tangents, which
 * softens the join between a flat cap and its bevel. Using the exact normals
 * that `roundedProfile()` already knows keeps flat regions flat and rounded
 * regions round.
 *
 * u is arc length at the widest radius, v is arc length along the profile —
 * both in world units, matching the rest of this module.
 */
export function lathe(profile, opts = {}) {
  const {
    segments = 48,
    phiStart = 0,
    phiLength = Math.PI * 2,
    uvScale = 1,
    uRef = null,
  } = opts;

  const pts = profile.map(toVec2);
  const normals = profile.normals
    ? profile.normals.map(toVec2)
    : implicitNormals(pts);
  const arcLength = profile.arcLength || cumulativeLength(pts);
  const seg = Math.max(3, Math.round(segments));
  const rRef = uRef != null ? uRef : Math.max(EPS, ...pts.map((p) => Math.abs(p.x)));

  const m = new Mesher();
  m.grid(pts.length - 1, seg, (i, j) => {
    const p = pts[i];
    const nn = normals[i];
    const phi = phiStart + (phiLength * j) / seg;
    const sin = Math.sin(phi);
    const cos = Math.cos(phi);
    let nx = nn.x * sin;
    let ny = nn.y;
    let nz = nn.x * cos;
    const nl = Math.hypot(nx, ny, nz) || 1;
    nx /= nl;
    ny /= nl;
    nz /= nl;
    return [
      p.x * sin, p.y, p.x * cos,
      nx, ny, nz,
      phi * rRef * uvScale,
      arcLength[i] * uvScale,
    ];
  });
  return m.toGeometry();
}

function cumulativeLength(pts) {
  const out = new Array(pts.length);
  let acc = 0;
  for (let i = 0; i < pts.length; i++) {
    if (i > 0) acc += pts[i].distanceTo(pts[i - 1]);
    out[i] = acc;
  }
  return out;
}

function implicitNormals(pts) {
  const out = [];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[Math.max(0, i - 1)];
    const b = pts[Math.min(pts.length - 1, i + 1)];
    const d = new THREE.Vector2().subVectors(b, a);
    if (d.lengthSq() < EPS) out.push(new THREE.Vector2(1, 0));
    else {
      d.normalize();
      out.push(new THREE.Vector2(d.y, -d.x));
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// beveledCylinder
// ---------------------------------------------------------------------------

/**
 * A cylinder or frustum with rounded rims, centred on the origin, axis +Y.
 *
 * Built as three pieces so the caps get a planar UV projection instead of the
 * polar swirl a plain lathe would give them: bottom disc, lathed body
 * (rim arc -> side -> rim arc), top disc. Cap and body share coincident
 * vertices with identical normals, so there is no shading seam.
 *
 * @param {number} rTop
 * @param {number} rBottom
 * @param {number} h
 * @param {object} [opts]
 * @param {number} [opts.bevel=0.02]
 * @param {number} [opts.radialSegments=48]
 * @param {number} [opts.segments=2]  bevel arc subdivisions
 * @param {boolean} [opts.capTop=true]
 * @param {boolean} [opts.capBottom=true]
 * @param {number} [opts.uvScale=1]
 */
export function beveledCylinder(rTop, rBottom, h, opts = {}) {
  const {
    bevel = DEFAULT_BEVEL,
    radialSegments = 48,
    segments = 2,
    capTop = true,
    capBottom = true,
    uvScale = 1,
  } = opts;

  const rT = Math.max(0, rTop);
  const rB = Math.max(0, rBottom);
  const hy = Math.abs(h) / 2;
  const seg = Math.max(3, Math.round(radialSegments));

  // Material lies to the left: axis-bottom -> out -> up the side -> back in.
  // A radius of zero is an apex, not a face, so it contributes no axis point
  // and no cap — `beveledCylinder(0, r, h)` is a cone.
  const hasBottomFace = rB > EPS;
  const hasTopFace = rT > EPS;
  const raw = [];
  if (hasBottomFace) raw.push(new THREE.Vector2(0, -hy));
  raw.push(new THREE.Vector2(rB, -hy));
  raw.push(new THREE.Vector2(rT, hy));
  if (hasTopFace) raw.push(new THREE.Vector2(0, hy));
  const prof = roundedProfile(raw, { bevel, segments });

  // roundedProfile emits [axisPoint, capEdge, ...arc..., ...side..., capEdge, axisPoint].
  // Dropping the axis points leaves exactly the lathed body.
  const from = hasBottomFace ? 1 : 0;
  const to = prof.length - (hasTopFace ? 1 : 0);
  const bodyPts = prof.slice(from, to);
  const bodyNormals = prof.normals.slice(from, to);
  const bodyArc = prof.arcLength.slice(from, to);
  Object.defineProperty(bodyPts, 'normals', { value: bodyNormals, enumerable: false });
  Object.defineProperty(bodyPts, 'arcLength', { value: bodyArc, enumerable: false });

  const parts = [
    lathe(bodyPts, { segments: seg, uvScale, uRef: Math.max(rT, rB) }),
  ];
  if (capBottom && hasBottomFace) parts.push(disc(prof[1].x, -hy, -1, seg, uvScale));
  if (capTop && hasTopFace) parts.push(disc(prof[prof.length - 2].x, hy, +1, seg, uvScale));

  return mergeAll(parts);
}

/** Flat disc at y, facing ±Y, with planar world UVs. */
function disc(radius, y, sign, segments, uvScale) {
  const m = new Mesher();
  if (radius <= EPS) return m.toGeometry();
  const [, , uAxis, uSign, vAxis, vSign] = faceBasis(1, sign);
  m.grid(1, segments, (i, j) => {
    const rr = i === 0 ? 0 : radius;
    const phi = (Math.PI * 2 * j) / segments;
    const x = rr * Math.sin(phi);
    const z = rr * Math.cos(phi);
    const coord = [x, y, z];
    return [
      x, y, z,
      0, sign, 0,
      uSign * coord[uAxis] * uvScale,
      vSign * coord[vAxis] * uvScale,
    ];
  });
  return m.toGeometry();
}

// ---------------------------------------------------------------------------
// extrudeProfile
// ---------------------------------------------------------------------------

/**
 * Extrude a 2D outline along +Z with a real bevel on both caps.
 *
 * Wraps `ExtrudeGeometry` — which cannot bevel the edges running along the
 * extrusion axis, so pre-round those in the outline with `roundedProfile()` —
 * then reassigns normals with a crease threshold and rewrites UVs to the
 * world-unit convention used everywhere else here.
 */
export function extrudeProfile(points, depth, opts = {}) {
  const {
    bevel = DEFAULT_BEVEL,
    segments = 2,
    curveSegments = 12,
    creaseAngle = 35,
    uvScale = 1,
  } = opts;

  const shape = new THREE.Shape(points.map(toVec2));
  const b = Math.max(0, Math.min(bevel, Math.abs(depth) / 2 - EPS));
  let g = new THREE.ExtrudeGeometry(shape, {
    depth: Math.max(EPS, Math.abs(depth) - 2 * b),
    curveSegments,
    bevelEnabled: b > EPS,
    bevelThickness: b,
    bevelSize: b,
    bevelOffset: 0,
    bevelSegments: Math.max(1, Math.round(segments)),
  });
  // ExtrudeGeometry runs z from -bevelThickness to innerDepth+bevelThickness;
  // recentre so the caller gets a part centred on the origin like everything else.
  g.translate(0, 0, -(Math.abs(depth) - 2 * b) / 2);
  g = toCreasedNormals(g, THREE.MathUtils.degToRad(creaseAngle));
  scaleUVs(g, uvScale);
  ensureUV2(g);
  g.computeBoundingSphere();
  return g;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/**
 * Copy `uv` into `uv1` (three r181's second UV channel, used by aoMap when
 * `aoMap.channel = 1`) and `uv2` (the pre-r151 alias).
 */
export function ensureUV2(geometry) {
  const uv = geometry.attributes.uv;
  if (!uv) return geometry;
  if (!geometry.attributes.uv1) {
    geometry.setAttribute('uv1', new THREE.BufferAttribute(uv.array.slice(), uv.itemSize));
  }
  if (!geometry.attributes.uv2) {
    geometry.setAttribute('uv2', new THREE.BufferAttribute(uv.array.slice(), uv.itemSize));
  }
  return geometry;
}

/** Multiply every UV channel in place. Handy after three's own generators. */
export function scaleUVs(geometry, scale = 1, scaleV = null) {
  const sv = scaleV == null ? scale : scaleV;
  for (const name of ['uv', 'uv1', 'uv2']) {
    const attr = geometry.attributes[name];
    if (!attr) continue;
    const a = attr.array;
    for (let i = 0; i < a.length; i += attr.itemSize) {
      a[i] *= scale;
      a[i + 1] *= sv;
    }
    attr.needsUpdate = true;
  }
  return geometry;
}

/**
 * Recompute normals with a crease threshold: shared vertices whose face
 * normals are within `angleDeg` are averaged (smooth), the rest are kept
 * separate (flat). Only needed for geometry built by three's own generators —
 * everything in this module already carries analytic normals.
 *
 * Returns a NON-indexed geometry (that is how `toCreasedNormals` works).
 */
export function creaseNormals(geometry, angleDeg = 35) {
  const g = toCreasedNormals(geometry, THREE.MathUtils.degToRad(angleDeg));
  ensureUV2(g);
  return g;
}

/**
 * Merge geometries into one draw call.
 *
 * `BufferGeometryUtils.mergeGeometries` demands that every input carry exactly
 * the same attributes and the same index-ness, which almost never holds when
 * mixing this module's output with three's built-in generators. So normalise
 * first: index everything, and give every geometry the union of attributes,
 * synthesising `normal` / `uv` / `uv1` / `uv2` where they are missing.
 *
 * Apply any per-part transform with `geometry.applyMatrix4()` before merging.
 *
 * @param {THREE.BufferGeometry[]} geometries
 * @param {object} [opts]
 * @param {boolean} [opts.useGroups=false] keep one material group per input
 * @param {boolean} [opts.dispose=false]   dispose the inputs after merging
 */
export function mergeAll(geometries, opts = {}) {
  const { useGroups = false, dispose = false } = opts;
  const list = (geometries || []).filter(
    (g) => g && g.attributes && g.attributes.position && g.attributes.position.count > 0
  );
  if (list.length === 0) return new THREE.BufferGeometry();
  if (list.length === 1 && !useGroups) return list[0];

  const names = new Set();
  const itemSizes = new Map();
  for (const g of list) {
    for (const [name, attr] of Object.entries(g.attributes)) {
      names.add(name);
      if (!itemSizes.has(name)) itemSizes.set(name, attr.itemSize);
    }
  }

  const prepared = list.map((src) => {
    const g = src.clone();
    if (!g.attributes.normal && names.has('normal')) g.computeVertexNormals();
    const count = g.attributes.position.count;
    for (const name of names) {
      if (g.attributes[name]) continue;
      const size = itemSizes.get(name);
      if (name === 'uv' || name === 'uv1' || name === 'uv2') {
        const uv = g.attributes.uv;
        if (uv) {
          g.setAttribute(name, new THREE.BufferAttribute(uv.array.slice(), uv.itemSize));
          continue;
        }
      }
      g.setAttribute(name, new THREE.BufferAttribute(new Float32Array(count * size), size));
    }
    // Drop anything outside the union (cannot happen) and normalise index-ness.
    if (!g.index) {
      const idx = new Uint32Array(count);
      for (let i = 0; i < count; i++) idx[i] = i;
      g.setIndex(new THREE.BufferAttribute(idx, 1));
    }
    g.morphAttributes = {};
    if (!useGroups) g.clearGroups();
    return g;
  });

  const merged = mergeGeometries(prepared, useGroups);
  if (!merged) {
    throw new Error('geom.mergeAll: mergeGeometries returned null (incompatible inputs)');
  }
  for (const g of prepared) g.dispose();
  if (dispose) for (const g of list) g.dispose();
  merged.computeBoundingSphere();
  merged.computeBoundingBox();
  return merged;
}
