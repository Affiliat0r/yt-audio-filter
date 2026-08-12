/**
 * Small geometry helpers shared by the track, train and props.
 *
 * Everything here is pure: no randomness, no clocks.
 */

import * as THREE from 'three';

export const WORLD_UP = new THREE.Vector3(0, 1, 0);

/**
 * Sweep a closed 2D cross-section along a sequence of oriented frames.
 *
 * Used for the rails and the ballast bed: a proper rectangular / trapezoidal
 * profile reads far better than a TubeGeometry, and one helper covers both.
 *
 * @param {{pos:THREE.Vector3, right:THREE.Vector3, up:THREE.Vector3}[]} frames
 * @param {[number, number][]} profile cross-section points, [right, up]
 * @param {boolean} closed join the last frame back to the first
 * @returns {THREE.BufferGeometry}
 */
export function extrudeProfile(frames, profile, closed = true) {
  const ringCount = frames.length;
  const profCount = profile.length;
  const vertexCount = ringCount * profCount;

  const positions = new Float32Array(vertexCount * 3);
  let w = 0;
  for (let i = 0; i < ringCount; i++) {
    const f = frames[i];
    for (let k = 0; k < profCount; k++) {
      const [pr, pu] = profile[k];
      positions[w++] = f.pos.x + f.right.x * pr + f.up.x * pu;
      positions[w++] = f.pos.y + f.right.y * pr + f.up.y * pu;
      positions[w++] = f.pos.z + f.right.z * pr + f.up.z * pu;
    }
  }

  const segCount = closed ? ringCount : ringCount - 1;
  const indices = new Uint32Array(segCount * profCount * 6);
  let ii = 0;
  for (let i = 0; i < segCount; i++) {
    const a0 = i * profCount;
    const b0 = ((i + 1) % ringCount) * profCount;
    for (let k = 0; k < profCount; k++) {
      const k1 = (k + 1) % profCount;
      const a = a0 + k;
      const b = a0 + k1;
      const c = b0 + k1;
      const d = b0 + k;
      indices[ii++] = a;
      indices[ii++] = b;
      indices[ii++] = c;
      indices[ii++] = a;
      indices[ii++] = c;
      indices[ii++] = d;
    }
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setIndex(new THREE.BufferAttribute(indices, 1));
  geom.computeVertexNormals();
  geom.computeBoundingSphere();
  return geom;
}

/**
 * Write an orthonormal basis into `matrix` from a forward direction.
 * The object's local +Z ends up pointing along `forward`.
 */
const _right = new THREE.Vector3();
const _up = new THREE.Vector3();
const _fwd = new THREE.Vector3();

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

/** A box whose origin sits at the centre of its base rather than its middle. */
export function boxOnBase(width, height, depth) {
  const g = new THREE.BoxGeometry(width, height, depth);
  g.translate(0, height / 2, 0);
  return g;
}

/** A cylinder standing on its base, axis +Y. */
export function cylinderOnBase(rTop, rBottom, height, segments = 12) {
  const g = new THREE.CylinderGeometry(rTop, rBottom, height, segments);
  g.translate(0, height / 2, 0);
  return g;
}

/** A cylinder lying along +Z (wheels, boilers, carriage roofs). */
export function cylinderAlongZ(rTop, rBottom, length, segments = 14) {
  const g = new THREE.CylinderGeometry(rTop, rBottom, length, segments);
  g.rotateX(Math.PI / 2);
  return g;
}

/** A cylinder lying along +X (axles). */
export function cylinderAlongX(rTop, rBottom, length, segments = 12) {
  const g = new THREE.CylinderGeometry(rTop, rBottom, length, segments);
  g.rotateZ(Math.PI / 2);
  return g;
}

/** A cone standing on its base, axis +Y. */
export function coneOnBase(radius, height, segments = 8) {
  const g = new THREE.ConeGeometry(radius, height, segments);
  g.translate(0, height / 2, 0);
  return g;
}

/** Opaque, flat-shaded, mildly glossy toy plastic. */
export function toyMaterial(color, opts = {}) {
  return new THREE.MeshPhongMaterial({
    color,
    shininess: opts.shininess ?? 18,
    specular: opts.specular ?? 0x222222,
    flatShading: opts.flatShading ?? true,
    ...(opts.extra || {}),
  });
}

/** Matte, smooth-shaded surface for organic shapes. */
export function mattMaterial(color, opts = {}) {
  return new THREE.MeshLambertMaterial({
    color,
    flatShading: opts.flatShading ?? true,
    ...(opts.extra || {}),
  });
}

/**
 * Concatenate simple position/normal geometries into one.
 *
 * Deliberately minimal — the scene uses no textures or skinning, so only
 * position and normal need to survive. Merging keeps the draw-call count for
 * a detailed model like the locomotive down to one mesh per colour.
 */
export function mergeGeometries(geometries) {
  if (geometries.length === 1) return geometries[0];
  const flat = geometries.map((g) => (g.index ? g.toNonIndexed() : g));
  let total = 0;
  for (const g of flat) total += g.attributes.position.count;

  const positions = new Float32Array(total * 3);
  const normals = new Float32Array(total * 3);
  let offset = 0;
  for (const g of flat) {
    if (!g.attributes.normal) g.computeVertexNormals();
    positions.set(g.attributes.position.array, offset * 3);
    normals.set(g.attributes.normal.array, offset * 3);
    offset += g.attributes.position.count;
  }

  const merged = new THREE.BufferGeometry();
  merged.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  merged.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  merged.computeBoundingSphere();
  return merged;
}

/**
 * Collects pre-transformed part geometries and emits one merged mesh per
 * distinct material. Models are authored in local space with `.translate()` /
 * `.rotate*()` on the geometry, then handed here.
 */
export class Parts {
  constructor() {
    this._byMaterial = new Map();
  }

  add(geometry, color, opts = {}) {
    const key = `${color}|${opts.shininess ?? 18}|${opts.specular ?? 0x222222}|${opts.flatShading === false ? 0 : 1}`;
    let entry = this._byMaterial.get(key);
    if (!entry) this._byMaterial.set(key, (entry = { color, opts, geometries: [] }));
    entry.geometries.push(geometry);
    return this;
  }

  /** Merged geometry for a single-material part set. */
  merged() {
    const all = [];
    for (const entry of this._byMaterial.values()) all.push(...entry.geometries);
    return mergeGeometries(all);
  }

  build(name = '', opts = {}) {
    const group = new THREE.Group();
    group.name = name;
    for (const entry of this._byMaterial.values()) {
      const mesh = new THREE.Mesh(
        mergeGeometries(entry.geometries),
        toyMaterial(entry.color, entry.opts)
      );
      mesh.castShadow = opts.cast ?? true;
      mesh.receiveShadow = opts.receive ?? true;
      group.add(mesh);
    }
    return group;
  }
}

/** Attach a mesh to a parent with shadow flags in one call. */
export function addMesh(parent, geometry, material, position = null, opts = {}) {
  const mesh = new THREE.Mesh(geometry, material);
  if (position) mesh.position.set(position[0], position[1], position[2]);
  mesh.castShadow = opts.cast ?? true;
  mesh.receiveShadow = opts.receive ?? true;
  parent.add(mesh);
  return mesh;
}
