/**
 * Where things go.
 *
 * Two distributions cover the whole world: a band hugging the railway, which
 * is the only ground the chase camera ever sees closely, and a thinner scatter
 * over the rest of the map to furnish the horizon. Both reject anything that
 * would stand in the four-foot, wade into the lake, or cling to a cliff.
 */

import * as THREE from 'three';

const _frame = {
  pos: new THREE.Vector3(),
  forward: new THREE.Vector3(),
  right: new THREE.Vector3(),
  up: new THREE.Vector3(),
};

function accept(spot, terrain, opts) {
  if (spot.y < terrain.waterLevel + (opts.minAboveWater ?? 1.1)) return false;
  if (opts.maxSlope !== undefined && terrain.slopeAt(spot.x, spot.z) > opts.maxSlope) return false;
  if (opts.minHeight !== undefined && spot.y < opts.minHeight) return false;
  if (opts.maxHeight !== undefined && spot.y > opts.maxHeight) return false;
  return true;
}

/**
 * Candidate positions in a band beside the track.
 * `power > 1` crowds them toward `minLateral`.
 */
export function bandPlacement(rng, track, terrain, opts) {
  const { count, minLateral, maxLateral, minTrackDist, power = 1.6, jitter = 2.5 } = opts;
  const out = [];
  let attempts = 0;
  while (out.length < count && attempts < count * 20) {
    attempts++;
    const s = rng.next() * track.length;
    const lateral = rng.sign() * rng.power(minLateral, maxLateral, power);
    track.frameAt(s, 2, _frame);
    const x = _frame.pos.x + _frame.right.x * lateral + rng.range(-jitter, jitter);
    const z = _frame.pos.z + _frame.right.z * lateral + rng.range(-jitter, jitter);
    const near = track.nearest(x, z);
    if (near.dist < minTrackDist) continue;
    const y = terrain.heightAt(x, z);
    const spot = { x, y, z, trackDist: near.dist, s };
    if (!accept(spot, terrain, opts)) continue;
    out.push(spot);
  }
  return out;
}

/** Thin scatter over the whole map, to fill the horizon. */
export function scatterPlacement(rng, track, terrain, opts) {
  const { count, minRadius = 70, maxRadius = 620, minTrackDist = 12 } = opts;
  const out = [];
  let attempts = 0;
  const r0 = (minRadius / maxRadius) ** 2;
  while (out.length < count && attempts < count * 20) {
    attempts++;
    const angle = rng.next() * Math.PI * 2;
    const radius = Math.sqrt(rng.range(r0, 1)) * maxRadius;
    const x = Math.cos(angle) * radius;
    const z = Math.sin(angle) * radius;
    const near = track.nearest(x, z);
    if (near.dist < minTrackDist) continue;
    const y = terrain.heightAt(x, z);
    const spot = { x, y, z, trackDist: near.dist, s: -1 };
    if (!accept(spot, terrain, opts)) continue;
    out.push(spot);
  }
  return out;
}

/**
 * Poisson-ish thinning: drop any candidate within `radius` of one already
 * kept. Cheap grid hash, so it stays linear even at tens of thousands.
 */
export function thin(spots, radius) {
  const cell = radius;
  const grid = new Map();
  const key = (cx, cz) => cx * 73856093 + cz;
  const out = [];
  const r2 = radius * radius;
  for (const s of spots) {
    const cx = Math.floor(s.x / cell);
    const cz = Math.floor(s.z / cell);
    let ok = true;
    for (let dx = -1; dx <= 1 && ok; dx++) {
      for (let dz = -1; dz <= 1 && ok; dz++) {
        const bucket = grid.get(key(cx + dx, cz + dz));
        if (!bucket) continue;
        for (const o of bucket) {
          const ex = o.x - s.x;
          const ez = o.z - s.z;
          if (ex * ex + ez * ez < r2) {
            ok = false;
            break;
          }
        }
      }
    }
    if (!ok) continue;
    const k = key(cx, cz);
    let bucket = grid.get(k);
    if (!bucket) grid.set(k, (bucket = []));
    bucket.push(s);
    out.push(s);
  }
  return out;
}

/**
 * Split spots into angular sectors around the origin so each instanced field
 * becomes several meshes with tight bounding spheres. Without this a single
 * InstancedMesh spanning the whole map is never frustum-culled and the GPU
 * chews through the far half of the world on every frame.
 */
export function sectorise(spots, sectors) {
  const out = Array.from({ length: sectors }, () => []);
  for (const s of spots) {
    let a = Math.atan2(s.z, s.x) / (Math.PI * 2);
    if (a < 0) a += 1;
    out[Math.min(sectors - 1, Math.floor(a * sectors))].push(s);
  }
  return out.filter((list) => list.length > 0);
}

/**
 * Build one InstancedMesh from a spot list.
 * `decorate(spot, i, rng)` returns `{scale, yaw, tilt, color}`.
 */
export function instanceField(geometry, material, spots, rng, decorate, opts = {}) {
  const mesh = new THREE.InstancedMesh(geometry, material, spots.length);
  mesh.castShadow = opts.castShadow ?? true;
  mesh.receiveShadow = opts.receiveShadow ?? true;
  mesh.name = opts.name ?? 'field';

  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const e = new THREE.Euler();
  const p = new THREE.Vector3();
  const s = new THREE.Vector3();
  const c = new THREE.Color();
  let anyColor = false;

  for (let i = 0; i < spots.length; i++) {
    const spot = spots[i];
    const d = decorate(spot, i, rng) || {};
    const sc = d.scale ?? 1;
    p.set(spot.x, spot.y + (d.sink ?? 0), spot.z);
    e.set(d.tiltX ?? 0, d.yaw ?? 0, d.tiltZ ?? 0, 'YXZ');
    q.setFromEuler(e);
    s.set(sc * (d.wide ?? 1), sc * (d.tall ?? 1), sc * (d.deep ?? d.wide ?? 1));
    m.compose(p, q, s);
    mesh.setMatrixAt(i, m);
    if (d.color !== undefined) {
      c.set(d.color);
      mesh.setColorAt(i, c);
      anyColor = true;
    }
  }
  mesh.instanceMatrix.needsUpdate = true;
  if (anyColor && mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.computeBoundingSphere();
  // Wind displaces vertices after culling is decided; give the sphere room.
  if (opts.boundsPad) mesh.boundingSphere.radius += opts.boundsPad;
  return mesh;
}
