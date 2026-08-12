/**
 * Part assembly helpers.
 *
 * Models here are authored the way a kit is: hundreds of small geometries, each
 * transformed into place in the model's local space, then merged per material.
 * The locomotive is roughly 1,400 individual pieces and eleven draw calls.
 */

import * as THREE from 'three';
import { mergeAll, rivetHead, tubeAlong, beveledBox, beveledCylinder, roundedProfile, extrudeShape } from './deps.mjs';

const _m = new THREE.Matrix4();
const _q = new THREE.Quaternion();
const _v = new THREE.Vector3();
const UP = new THREE.Vector3(0, 1, 0);

/**
 * Transform a geometry into place. `rot` is XYZ Euler in radians; `dir` (a
 * direction vector) is an alternative that aims the geometry's +Y at it.
 */
export function place(geometry, { pos = null, rot = null, dir = null, scale = null } = {}) {
  if (dir) {
    _v.copy(dir).normalize();
    _q.setFromUnitVectors(UP, _v);
    geometry.applyQuaternion(_q);
  } else if (rot) {
    _m.makeRotationFromEuler(new THREE.Euler(rot[0] || 0, rot[1] || 0, rot[2] || 0, 'XYZ'));
    geometry.applyMatrix4(_m);
  }
  if (scale) geometry.scale(scale[0], scale[1], scale[2]);
  if (pos) geometry.translate(pos[0], pos[1], pos[2]);
  return geometry;
}

/** A cylinder lying along +Z (boilers, wheels, coach roofs). */
export function cylZ(rTop, rBottom, length, opts = {}) {
  return beveledCylinder(rTop, rBottom, length, opts).rotateX(Math.PI / 2);
}

/**
 * Fillet every corner of a *closed* 2D profile. `roundedProfile` only handles
 * interior corners of an open polyline, so wrap the loop, round it, and trim.
 * Returns a point list whose first point is repeated at the end, ready to lathe.
 */
export function roundedLoop(points, opts = {}) {
  const n = points.length;
  const extended = [points[n - 1], ...points, points[0]];
  const rounded = roundedProfile(extended, opts);
  const loop = rounded.slice(1, rounded.length - 1);
  loop.push(loop[0].clone ? loop[0].clone() : new THREE.Vector2(loop[0].x, loop[0].y));
  return loop;
}

/** Flat annular sector (a pie slice with a hole), extruded and beveled. */
export function annularSector(rInner, rOuter, a0, a1, thickness, { bevel = 0.01, divisions = 28 } = {}) {
  const pts = [];
  for (let i = 0; i <= divisions; i++) {
    const a = a0 + ((a1 - a0) * i) / divisions;
    pts.push(new THREE.Vector2(Math.cos(a) * rOuter, Math.sin(a) * rOuter));
  }
  for (let i = divisions; i >= 0; i--) {
    const a = a0 + ((a1 - a0) * i) / divisions;
    pts.push(new THREE.Vector2(Math.cos(a) * rInner, Math.sin(a) * rInner));
  }
  return extrudeShape(new THREE.Shape(pts), thickness, { bevel, segments: 2, curveSegments: 8 });
}

/* ------------------------------------------------------------- weathering */

/**
 * Bake a per-vertex tint into a geometry.
 *
 * Uniform paint across a three-metre panel is the second-strongest "this is
 * CG" tell after unbevelled edges: real vehicles are dusty low down, sooty
 * behind the chimney, and blotchy everywhere. A vertex-colour multiplier costs
 * one attribute and nothing per frame.
 *
 * @param {(x:number,y:number,z:number)=>[number,number,number]} shade
 */
export function applyShade(geometry, shade) {
  const pos = geometry.attributes.position;
  const colors = new Float32Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++) {
    const c = shade(pos.getX(i), pos.getY(i), pos.getZ(i));
    colors[i * 3] = c[0];
    colors[i * 3 + 1] = c[1];
    colors[i * 3 + 2] = c[2];
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  return geometry;
}

/**
 * Materials are shared across every vehicle, so a shaded mesh needs its own
 * `vertexColors: true` variant — switching the shared one on would black out
 * every mesh that has no colour attribute.
 */
const SHADED_VARIANTS = new WeakMap();
function shadedVariant(material) {
  let v = SHADED_VARIANTS.get(material);
  if (!v) {
    v = material.clone();
    v.vertexColors = true;
    v.name = `${material.name || 'mat'}+vc`;
    SHADED_VARIANTS.set(material, v);
  }
  return v;
}

/** Free the cloned vertex-colour variants a material set spawned. */
export function disposeShadedVariants(materials) {
  for (const value of Object.values(materials)) {
    if (!value || !value.isMaterial) continue;
    const v = SHADED_VARIANTS.get(value);
    if (v) {
      v.dispose();
      SHADED_VARIANTS.delete(value);
    }
  }
}

/**
 * Collects geometry per material and emits one merged mesh for each.
 *
 * `add` is chainable and tolerates null so a caller can inline conditionals.
 */
export class Parts {
  constructor() {
    this._byMaterial = new Map();
    this.count = 0;
  }

  add(geometry, material) {
    if (!geometry || !material) return this;
    let bucket = this._byMaterial.get(material);
    if (!bucket) this._byMaterial.set(material, (bucket = []));
    bucket.push(geometry);
    this.count++;
    return this;
  }

  /** Merge in another Parts collection, optionally transformed. */
  addParts(other, transform = null) {
    for (const [material, list] of other._byMaterial) {
      for (const g of list) this.add(transform ? g.clone().applyMatrix4(transform) : g, material);
    }
    return this;
  }

  /** One merged geometry per material, in insertion order. */
  build(name = 'parts', { cast = true, receive = true, shade = null } = {}) {
    const group = new THREE.Group();
    group.name = name;
    for (const [material, list] of this._byMaterial) {
      const geometry = mergeAll(list);
      const mesh = new THREE.Mesh(geometry, shade ? shadedVariant(material) : material);
      if (shade) applyShade(geometry, shade);
      mesh.castShadow = cast;
      mesh.receiveShadow = receive;
      mesh.name = `${name}:${material.name || material.uuid.slice(0, 6)}`;
      group.add(mesh);
    }
    return group;
  }
}

/* ------------------------------------------------------------------ rivets */

/**
 * A run of rivet heads between two points, each sitting proud of the surface
 * along `normal`. Rivets are why a boiler photographs as riveted iron rather
 * than as a smooth tube: they are tiny, but each one puts a specular dot on the
 * surface and the eye counts them.
 */
export function rivetRun(parts, material, from, to, count, { radius = 0.022, normal = null, inset = 0 } = {}) {
  if (count < 1) return;
  const a = new THREE.Vector3(...from);
  const b = new THREE.Vector3(...to);
  const dir = new THREE.Vector3().subVectors(b, a);
  const n = normal ? new THREE.Vector3(...normal).normalize() : null;
  for (let i = 0; i < count; i++) {
    const s = count === 1 ? 0.5 : i / (count - 1);
    const p = new THREE.Vector3().copy(a).addScaledVector(dir, s);
    const nn = n || p.clone().setComponent(2, 0).normalize();
    p.addScaledVector(nn, -inset);
    parts.add(place(rivetHead(radius), { pos: [p.x, p.y, p.z], dir: nn }), material);
  }
}

/**
 * A ring of rivets around a cylinder whose axis is +Z — boiler bands, smokebox
 * wrappers, tank seams.
 */
export function rivetRing(parts, material, { z, radius, count, centreY = 0, from = 0, to = Math.PI * 2, rivet = 0.022 }) {
  for (let i = 0; i < count; i++) {
    const a = from + ((to - from) * i) / count;
    const nx = Math.sin(a);
    const ny = Math.cos(a);
    parts.add(
      place(rivetHead(rivet), {
        pos: [nx * radius, centreY + ny * radius, z],
        dir: new THREE.Vector3(nx, ny, 0),
      }),
      material
    );
  }
}

/* ------------------------------------------------------- fittings & fixings */

/** Brass handrail: a swept tube plus knob stanchions holding it off the body. */
export function handrail(parts, railMaterial, points, { radius = 0.026, standoff = 0.075, normal = [1, 0, 0], stanchions = null } = {}) {
  const pts = points.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
  parts.add(tubeAlong(pts, radius, { radialSegments: 10 }), railMaterial);
  const n = new THREE.Vector3(...normal).normalize();
  const posts = stanchions ?? pts.length;
  for (let i = 0; i < posts; i++) {
    const s = posts === 1 ? 0.5 : i / (posts - 1);
    const idx = s * (pts.length - 1);
    const i0 = Math.floor(idx);
    const i1 = Math.min(pts.length - 1, i0 + 1);
    const p = pts[i0].clone().lerp(pts[i1], idx - i0);
    const base = p.clone().addScaledVector(n, -standoff);
    const shaft = beveledCylinder(radius * 0.62, radius * 0.62, standoff, { bevel: 0.006, radialSegments: 10 });
    place(shaft, { pos: [(p.x + base.x) / 2, (p.y + base.y) / 2, (p.z + base.z) / 2], dir: n });
    parts.add(shaft, railMaterial);
    const boss = beveledCylinder(radius * 1.5, radius * 1.7, 0.035, { bevel: 0.008, radialSegments: 12 });
    place(boss, { pos: [base.x, base.y, base.z], dir: n });
    parts.add(boss, railMaterial);
  }
}

/**
 * A laminated leaf spring: several progressively shorter leaves bowed into a
 * shallow arc, with a buckle round the middle.
 */
export function leafSpring(length, { leaves = 4, camber = 0.09, width = 0.09, leaf = 0.022 } = {}) {
  const geoms = [];
  for (let l = 0; l < leaves; l++) {
    const frac = 1 - l * (0.82 / leaves);
    const half = (length * frac) / 2;
    const segs = 7;
    for (let s = 0; s < segs; s++) {
      const z0 = -half + (2 * half * s) / segs;
      const z1 = -half + (2 * half * (s + 1)) / segs;
      const zc = (z0 + z1) / 2;
      const bow = camber * (1 - (zc / (length / 2)) ** 2);
      const slope = Math.atan2(
        camber * ((z0 / (length / 2)) ** 2 - (z1 / (length / 2)) ** 2),
        z1 - z0
      );
      const seg = beveledBox(width, leaf, (z1 - z0) * 1.08, { bevel: leaf * 0.3, segments: 1 });
      place(seg, { pos: [0, bow - l * leaf * 1.02, zc], rot: [-slope, 0, 0] });
      geoms.push(seg);
    }
  }
  // Buckle.
  geoms.push(place(beveledBox(width * 1.5, leaves * leaf * 1.5, 0.075, { bevel: 0.012 }), {
    pos: [0, camber - ((leaves - 1) * leaf) / 2, 0],
  }));
  return mergeAll(geoms);
}

/** Sprung buffer: a ribbed housing, a shank and a domed head. */
export function buffer(parts, { bodyMaterial, headMaterial }, { x, y, z, length = 0.34, radius = 0.13, facing = 1 }) {
  const f = Math.sign(facing) || 1;
  const L = Math.abs(length);
  const housing = cylZ(radius * 0.92, radius * 1.22, L * 0.72, { bevel: 0.02, radialSegments: 20 });
  parts.add(place(housing, { pos: [x, y, z + f * L * 0.36] }), bodyMaterial);
  const shank = cylZ(radius * 0.5, radius * 0.5, L, { bevel: 0.012, radialSegments: 16 });
  parts.add(place(shank, { pos: [x, y, z + f * L * 0.62] }), headMaterial);
  const head = cylZ(radius * 1.12, radius * 1.02, 0.07, { bevel: 0.022, radialSegments: 24 });
  parts.add(place(head, { pos: [x, y, z + f * L * 1.05] }), headMaterial);
  // Mounting flange with four bolts.
  parts.add(place(cylZ(radius * 1.35, radius * 1.35, 0.035, { bevel: 0.01, radialSegments: 20 }), { pos: [x, y, z] }), bodyMaterial);
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
    parts.add(
      place(rivetHead(0.02), {
        pos: [x + Math.cos(a) * radius * 1.1, y + Math.sin(a) * radius * 1.1, z - 0.018],
        dir: new THREE.Vector3(0, 0, -1),
      }),
      bodyMaterial
    );
  }
}

/**
 * Three-link coupling: a hook and a short chain hanging in a catenary. Purely
 * decorative and completely static, but its absence is conspicuous.
 */
export function couplingHook(parts, material, { x = 0, y, z, facing = 1 }) {
  const shank = cylZ(0.032, 0.032, 0.16, { bevel: 0.008, radialSegments: 10 });
  parts.add(place(shank, { pos: [x, y, z + facing * 0.08] }), material);
  const hook = new THREE.TorusGeometry(0.075, 0.028, 8, 16, Math.PI * 1.35);
  place(hook, { pos: [x, y - 0.06, z + facing * 0.17], rot: [0, Math.PI / 2, -Math.PI / 2] });
  parts.add(hook, material);
  for (let i = 0; i < 3; i++) {
    const drop = 0.055 + i * 0.05;
    const link = new THREE.TorusGeometry(0.045, 0.014, 6, 12);
    place(link, {
      pos: [x, y - drop - 0.03, z + facing * (0.2 + i * 0.012)],
      rot: [0, i % 2 === 0 ? 0 : Math.PI / 2, 0.25 * (i % 2 ? 1 : -1)],
    });
    parts.add(link, material);
  }
}

/**
 * Oil-lamp: brass body, chimney, and a lens that reads as lit glass. Lamps sit
 * on brackets and are one of the few pure-highlight elements on the model.
 */
export function lamp(parts, { caseMaterial, glassMaterial }, { x, y, z, scale = 1, facing = 1 }) {
  const s = scale;
  const body = beveledBox(0.2 * s, 0.24 * s, 0.19 * s, { bevel: 0.016 * s, segments: 2 });
  parts.add(place(body, { pos: [x, y, z] }), caseMaterial);
  const top = beveledCylinder(0.055 * s, 0.085 * s, 0.09 * s, { bevel: 0.012 * s, radialSegments: 14 });
  parts.add(place(top, { pos: [x, y + 0.16 * s, z] }), caseMaterial);
  const bezel = cylZ(0.085 * s, 0.085 * s, 0.03 * s, { bevel: 0.008 * s, radialSegments: 20 });
  parts.add(place(bezel, { pos: [x, y, z + facing * 0.1 * s] }), caseMaterial);
  const lens = cylZ(0.068 * s, 0.068 * s, 0.026 * s, { bevel: 0.008 * s, radialSegments: 20 });
  parts.add(place(lens, { pos: [x, y, z + facing * 0.107 * s] }), glassMaterial);
  const bracket = beveledBox(0.05 * s, 0.06 * s, 0.06 * s, { bevel: 0.008 * s });
  parts.add(place(bracket, { pos: [x, y - 0.15 * s, z] }), caseMaterial);
}

/** Axlebox and hornguide: the cast block the axle runs in, seen on every wagon. */
export function axlebox(parts, { boxMaterial, springMaterial }, { x, y, z, side = 1 }) {
  const box = beveledBox(0.15, 0.2, 0.26, { bevel: 0.024, segments: 2 });
  parts.add(place(box, { pos: [x + side * 0.06, y, z] }), boxMaterial);
  const cover = beveledCylinder(0.07, 0.07, 0.05, { bevel: 0.012, radialSegments: 14 }).rotateZ(Math.PI / 2);
  parts.add(place(cover, { pos: [x + side * 0.15, y + 0.02, z] }), boxMaterial);
  for (const dz of [-0.16, 0.16]) {
    const guide = beveledBox(0.05, 0.34, 0.06, { bevel: 0.012 });
    parts.add(place(guide, { pos: [x - side * 0.03, y + 0.02, z + dz] }), boxMaterial);
  }
  const spring = leafSpring(0.86, { leaves: 4, camber: 0.075, width: 0.075, leaf: 0.02 });
  parts.add(place(spring, { pos: [x + side * 0.06, y + 0.24, z] }), springMaterial);
  // Spring hangers.
  for (const dz of [-0.4, 0.4]) {
    parts.add(
      place(beveledBox(0.05, 0.16, 0.045, { bevel: 0.01 }), { pos: [x + side * 0.06, y + 0.3, z + dz] }),
      springMaterial
    );
  }
}
