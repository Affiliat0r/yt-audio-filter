/**
 * Local stand-in for `visuals/lib/geom.mjs` (Part A).
 *
 * Part D must never be blocked on Part A landing, and must never write into
 * `visuals/lib/`. So this file implements the contract's geometry helpers with
 * the same signatures. `deps.mjs` prefers the real module when it exists and
 * falls back here otherwise.
 *
 * Everything is pure: no randomness, no clocks, no globals.
 */

import * as THREE from 'three';
import { RoundedBoxGeometry } from '../../node_modules/three/examples/jsm/geometries/RoundedBoxGeometry.js';
import { mergeGeometries } from '../../node_modules/three/examples/jsm/utils/BufferGeometryUtils.js';

const EPS = 1e-6;

/* ---------------------------------------------------------------- profiles */

/**
 * Fillet the interior corners of an open 2D polyline.
 *
 * A fillet rather than a flat chamfer: a two-segment arc rolls the highlight
 * across the corner instead of parking a hard line on it, which is the whole
 * point of bevelling in the first place.
 *
 * @param {Array<[number, number] | [number, number, number] | THREE.Vector2>} points
 *        `[x, y]` pairs; an optional third element overrides the bevel radius
 *        for that corner (0 keeps it sharp).
 * @param {{bevel?: number, segments?: number}} [opts]
 * @returns {THREE.Vector2[]}
 */
export function roundedProfile(points, { bevel = 0.02, segments = 2 } = {}) {
  const raw = points.map((p) =>
    p.isVector2 ? { v: p.clone(), r: bevel } : { v: new THREE.Vector2(p[0], p[1]), r: p.length > 2 ? p[2] : bevel }
  );
  if (raw.length < 3) return raw.map((p) => p.v);

  const out = [raw[0].v.clone()];
  const a = new THREE.Vector2();
  const b = new THREE.Vector2();

  for (let i = 1; i < raw.length - 1; i++) {
    const P = raw[i].v;
    let r = raw[i].r;
    a.subVectors(raw[i - 1].v, P);
    b.subVectors(raw[i + 1].v, P);
    const la = a.length();
    const lb = b.length();
    if (r <= EPS || la < EPS || lb < EPS) {
      out.push(P.clone());
      continue;
    }
    a.divideScalar(la);
    b.divideScalar(lb);

    const cos = THREE.MathUtils.clamp(a.dot(b), -1, 1);
    const theta = Math.acos(cos);
    // Collinear: nothing to round.
    if (theta > Math.PI - 1e-3 || theta < 1e-3) {
      out.push(P.clone());
      continue;
    }

    const half = theta / 2;
    let tangent = r / Math.tan(half);
    const maxTangent = Math.min(la, lb) * 0.499;
    if (tangent > maxTangent) {
      tangent = maxTangent;
      r = tangent * Math.tan(half);
    }

    const p1 = new THREE.Vector2().copy(P).addScaledVector(a, tangent);
    const p2 = new THREE.Vector2().copy(P).addScaledVector(b, tangent);
    const bis = new THREE.Vector2().addVectors(a, b).normalize();
    const centre = new THREE.Vector2().copy(P).addScaledVector(bis, r / Math.sin(half));

    let a1 = Math.atan2(p1.y - centre.y, p1.x - centre.x);
    let a2 = Math.atan2(p2.y - centre.y, p2.x - centre.x);
    let sweep = a2 - a1;
    while (sweep > Math.PI) sweep -= Math.PI * 2;
    while (sweep < -Math.PI) sweep += Math.PI * 2;

    const n = Math.max(1, segments);
    for (let s = 0; s <= n; s++) {
      const ang = a1 + (sweep * s) / n;
      out.push(new THREE.Vector2(centre.x + Math.cos(ang) * r, centre.y + Math.sin(ang) * r));
    }
  }

  out.push(raw[raw.length - 1].v.clone());
  return out;
}

/* -------------------------------------------------------------- primitives */

/** A box with every edge rolled over. `bevel` is the fillet radius. */
export function beveledBox(w, h, d, { bevel = 0.02, segments = 2 } = {}) {
  const r = Math.max(1e-4, Math.min(bevel, Math.min(Math.abs(w), Math.abs(h), Math.abs(d)) * 0.49));
  return new RoundedBoxGeometry(w, h, d, Math.max(1, segments), r);
}

/**
 * A cylinder (or truncated cone) standing on +Y with both rims rolled over.
 * Built as a lathe so the bevel is a true fillet rather than an extra ring of
 * flat-shaded facets.
 */
export function beveledCylinder(rTop, rBottom, h, opts = {}) {
  const {
    bevel = 0.02,
    radialSegments = 32,
    segments = 2,
    openEnded = false,
    bevelTop = bevel,
    bevelBottom = bevel,
  } = opts;

  const y0 = -h / 2;
  const y1 = h / 2;
  const cap = Math.min(h * 0.45, Math.max(rTop, rBottom) * 0.9);
  const bT = THREE.MathUtils.clamp(bevelTop, 0, Math.min(cap, rTop * 0.9));
  const bB = THREE.MathUtils.clamp(bevelBottom, 0, Math.min(cap, rBottom * 0.9));

  const pts = [];
  if (!openEnded) pts.push([0, y0]);
  pts.push([rBottom, y0, bB]);
  pts.push([rTop, y1, bT]);
  if (!openEnded) pts.push([0, y1]);

  const profile = roundedProfile(pts, { bevel, segments });
  return latheProfile(profile, radialSegments);
}

/** Revolve a 2D profile (x = radius, y = height) about +Y. */
export function latheProfile(points, radialSegments = 32, phiStart = 0, phiLength = Math.PI * 2) {
  const v = points.map((p) => (p.isVector2 ? p : new THREE.Vector2(p[0], p[1])));
  const g = new THREE.LatheGeometry(v, radialSegments, phiStart, phiLength);
  g.computeVertexNormals();
  return g;
}

/** A sphere-ish rivet head: a shallow dome sitting on the +Y plane. */
export function rivetHead(radius, height = radius * 0.62, segments = 8) {
  const profile = [];
  const rings = 4;
  for (let i = 0; i <= rings; i++) {
    const a = (i / rings) * (Math.PI / 2);
    profile.push(new THREE.Vector2(Math.cos(a) * radius, Math.sin(a) * height));
  }
  profile.unshift(new THREE.Vector2(radius, -radius * 0.15));
  return latheProfile(profile, segments);
}

/** A tube swept along a list of Vector3 control points. */
export function tubeAlong(points, radius, { tubularSegments = null, radialSegments = 10, closed = false } = {}) {
  const curve = new THREE.CatmullRomCurve3(points.map((p) => p.clone()), closed, 'catmullrom', 0.5);
  const segs = tubularSegments ?? Math.max(8, Math.round(curve.getLength() / (radius * 3)));
  return new THREE.TubeGeometry(curve, segs, radius, radialSegments, closed);
}

/**
 * Extrude a `THREE.Shape` (holes included) along +Z with a bevel on both faces.
 * This is how the cab gets window openings you can actually see through while
 * still catching a highlight on every edge.
 */
export function extrudeShape(shape, depth, { bevel = 0.015, segments = 2, curveSegments = 12 } = {}) {
  // `bevelSize` alone pushes the *body* of the extrusion outside the authored
  // outline and leaves the end caps on it — an outward flare, not a bevel. So
  // the whole thing is shifted in by `bevelOffset: -bevel`: the body then lands
  // exactly on the outline and the caps are inset by the bevel radius, which is
  // what a rolled edge actually looks like.
  //
  // This matters far more than it sounds: with the naive settings every surface
  // detail placed flush against a panel (lining, seams, rivets) ends up buried
  // inside the panel it was meant to sit on.
  const g = new THREE.ExtrudeGeometry(shape, {
    depth: Math.max(1e-4, depth - bevel * 2),
    bevelEnabled: bevel > 0,
    bevelThickness: bevel,
    bevelSize: bevel,
    bevelOffset: -bevel,
    bevelSegments: Math.max(1, segments),
    curveSegments,
    steps: 1,
  });
  g.translate(0, 0, -(depth - bevel * 2) / 2);
  g.computeVertexNormals();
  return g;
}

/* ------------------------------------------------------------------ merging */

const KEEP = ['position', 'normal', 'uv'];

function normaliseForMerge(geometry) {
  const g = geometry.index ? geometry : geometry.clone();
  if (!g.index) {
    const count = g.attributes.position.count;
    const idx = count > 65535 ? new Uint32Array(count) : new Uint16Array(count);
    for (let i = 0; i < count; i++) idx[i] = i;
    g.setIndex(new THREE.BufferAttribute(idx, 1));
  }
  if (!g.attributes.normal) g.computeVertexNormals();
  if (!g.attributes.uv) {
    const count = g.attributes.position.count;
    g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(count * 2), 2));
  }
  // Drop anything the other geometries will not have; mergeGeometries demands
  // an identical attribute set across the batch.
  for (const name of Object.keys(g.attributes)) {
    if (!KEEP.includes(name)) g.deleteAttribute(name);
  }
  g.clearGroups();
  g.morphAttributes = {};
  return g;
}

/** Merge many geometries into one. Mixed indexed/non-indexed input is fine. */
export function mergeAll(geometries) {
  const list = geometries.filter(Boolean);
  if (list.length === 0) return new THREE.BufferGeometry();
  if (list.length === 1) return normaliseForMerge(list[0]);
  const merged = mergeGeometries(list.map(normaliseForMerge), false);
  if (!merged) throw new Error('train/geom: mergeAll failed');
  merged.computeBoundingSphere();
  return merged;
}
