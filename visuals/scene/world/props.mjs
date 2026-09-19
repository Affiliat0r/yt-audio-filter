/**
 * Props: rock, fences, telegraph poles and their wires, dry stone walls,
 * lineside signals, mileposts, farm clutter — and the buildings.
 *
 * Everything static is accumulated into a `Bin` keyed by material and merged,
 * so a village of seven buildings, a station and a hundred fence panels costs
 * a handful of draw calls rather than a thousand. Everything repeated is
 * instanced. Nothing here is a bare `BoxGeometry`: every edge that can catch a
 * highlight gets a bevel.
 */

import * as THREE from 'three';
import { lerp, clamp, smoothstep } from './rng.mjs';
import { bandPlacement, scatterPlacement, thin, sectorise, instanceField } from './placement.mjs';

/** Accumulates transformed geometry per material, then emits one mesh each. */
class Bin {
  constructor(geom) {
    this.geom = geom;
    this.byMaterial = new Map();
  }

  add(material, geometry, matrix = null, color = 0xffffff) {
    let g = geometry;
    if (matrix) {
      g = geometry.clone();
      g.applyMatrix4(matrix);
    } else if (geometry.attributes.color) {
      g = geometry.clone();
    }
    this.geom.paintGeometry(g, color);
    let list = this.byMaterial.get(material);
    if (!list) this.byMaterial.set(material, (list = []));
    list.push(g);
    return this;
  }

  build(group, name, opts = {}) {
    for (const [material, list] of this.byMaterial) {
      const mesh = new THREE.Mesh(this.geom.mergeAll(list), material);
      mesh.castShadow = opts.castShadow ?? true;
      mesh.receiveShadow = opts.receiveShadow ?? true;
      mesh.name = `${name}:${material.name || 'mat'}`;
      group.add(mesh);
    }
    return group;
  }

  get drawCalls() {
    return this.byMaterial.size;
  }
}

/** Position/rotate/scale into one matrix, without allocating a Object3D. */
function trs(x, y, z, ry = 0, sx = 1, sy = 1, sz = 1) {
  const m = new THREE.Matrix4();
  m.compose(
    new THREE.Vector3(x, y, z),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(0, ry, 0)),
    new THREE.Vector3(sx, sy, sz)
  );
  return m;
}

// ---------------------------------------------------------------------------
// Rock
// ---------------------------------------------------------------------------

function buildRock(rng, geom, detail = 3) {
  const g = new THREE.IcosahedronGeometry(1, detail);
  const pos = g.attributes.position;
  const waves = [];
  for (let k = 0; k < 7; k++) {
    const d = new THREE.Vector3(rng.normal(), rng.normal(), rng.normal()).normalize();
    waves.push({ d, f: rng.range(1.4, 6.5), a: rng.range(0.05, 0.19), p: rng.range(0, 6.28) });
  }
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    let r = 1;
    for (const w of waves) r += w.a * Math.sin(v.dot(w.d) * w.f + w.p);
    v.multiplyScalar(r);
    // Sit flat: nothing balances on a point.
    v.y = Math.max(v.y, -0.72);
    pos.setXYZ(i, v.x, v.y * 0.82, v.z);
  }
  g.computeVertexNormals();
  geom.boxUVs(g, 1);
  g.computeBoundingSphere();
  return g;
}

// ---------------------------------------------------------------------------
// Buildings
// ---------------------------------------------------------------------------

/**
 * A window with real depth.
 *
 * There is no CSG here, so there is no hole in the wall: anything placed
 * *behind* the wall face is simply invisible. The depth therefore has to come
 * from a surround that stands **proud** of the wall — a projecting picture
 * frame with the glass set at its back. Seen from any angle off-axis you get a
 * genuine shadow line down the reveal, which is the whole point.
 *
 * @param {number} w opening width  @param {number} h opening height
 */
function addWindow(bin, mats, w, h, wallColor) {
  const frameColor = 0xf1ece0;
  const reveal = 0.11; // how far the surround projects from the wall face
  const jamb = 0.09; // thickness of the surround
  const g = mats.g;

  // Glass, at the back of the reveal so the surround shades it.
  bin.add(
    mats.glass,
    g.beveledBox(w + 0.02, h + 0.02, 0.03, { bevel: 0.005, uvScale: 1 }).translate(0, 0, 0.02)
  );

  // The surround: four beveled members boxing the opening, standing proud.
  const jambGeom = g.beveledBox(jamb, h + jamb * 2, reveal, { bevel: 0.014, uvScale: 0.5 });
  bin.add(mats.plaster, jambGeom.clone().translate(-(w + jamb) / 2, 0, reveal / 2), null, wallColor);
  bin.add(mats.plaster, jambGeom.clone().translate((w + jamb) / 2, 0, reveal / 2), null, wallColor);
  const headGeom = g.beveledBox(w + jamb * 2, jamb, reveal, { bevel: 0.014, uvScale: 0.5 });
  bin.add(mats.plaster, headGeom.clone().translate(0, (h + jamb) / 2, reveal / 2), null, wallColor);
  bin.add(mats.plaster, headGeom.clone().translate(0, -(h + jamb) / 2, reveal / 2), null, wallColor);

  // Painted sash: a thin frame plus a mullion and transom, near the front.
  const sashZ = reveal - 0.035;
  const sashSide = g.beveledBox(0.045, h, 0.045, { bevel: 0.01, uvScale: 0.4 });
  bin.add(mats.plankPainted, sashSide.clone().translate(-w / 2 + 0.022, 0, sashZ), null, frameColor);
  bin.add(mats.plankPainted, sashSide.clone().translate(w / 2 - 0.022, 0, sashZ), null, frameColor);
  const sashCap = g.beveledBox(w, 0.045, 0.045, { bevel: 0.01, uvScale: 0.4 });
  bin.add(mats.plankPainted, sashCap.clone().translate(0, h / 2 - 0.022, sashZ), null, frameColor);
  bin.add(mats.plankPainted, sashCap.clone().translate(0, -h / 2 + 0.022, sashZ), null, frameColor);
  bin.add(
    mats.plankPainted,
    g.beveledBox(0.028, h - 0.05, 0.032, { bevel: 0.008, uvScale: 0.4 }).translate(0, 0, sashZ - 0.006),
    null,
    frameColor
  );
  bin.add(
    mats.plankPainted,
    g.beveledBox(w - 0.05, 0.028, 0.032, { bevel: 0.008, uvScale: 0.4 }).translate(0, 0, sashZ - 0.006),
    null,
    frameColor
  );

  // Stone sill, weathered outward, and a lintel over.
  bin.add(
    mats.stoneWall,
    g
      .beveledBox(w + 0.34, 0.085, reveal + 0.13, { bevel: 0.016, uvScale: 1 })
      .translate(0, -(h + jamb * 2) / 2 - 0.03, (reveal + 0.13) / 2 - 0.02)
  );
  bin.add(
    mats.stoneWall,
    g
      .beveledBox(w + 0.28, 0.115, reveal + 0.04, { bevel: 0.016, uvScale: 1 })
      .translate(0, (h + jamb * 2) / 2 + 0.05, (reveal + 0.04) / 2 - 0.02)
  );
}

/** Wrap a local-space builder so its parts land at the building's transform. */
function subBin(bin, matrix) {
  return {
    add(material, geometry, m = null, color = 0xffffff) {
      const full = m ? matrix.clone().multiply(m) : matrix;
      bin.add(material, geometry, full, color);
      return this;
    },
  };
}

/**
 * A cottage / hut / barn, depending on `style`. Gable roof with a real
 * overhang, fascia and barge boards; a stone plinth; recessed windows; a
 * chimney with a cap and pots.
 */
function buildBuilding(rng, bin, mats, matrix, style) {
  const b = subBin(bin, matrix);
  const g = mats.g;
  const w = style.width ?? rng.range(5.2, 7.4);
  const d = style.depth ?? rng.range(4.4, 6.2);
  const h = style.height ?? rng.range(2.9, 3.9);
  const pitch = style.pitch ?? rng.range(0.62, 0.85);
  const overhang = 0.36;
  const wallMat = style.wallMat ?? mats.plaster;
  const wallColor = style.wallColor ?? 0xffffff;
  const roofMat = style.roofMat ?? mats.roofTile;
  const roofColor = style.roofColor ?? 0xffffff;

  // Plinth and walls.
  b.add(mats.stoneWall, g.beveledBox(w + 0.22, 0.42, d + 0.22, { bevel: 0.03, uvScale: 1 }), trs(0, 0.21, 0));
  b.add(wallMat, g.beveledBox(w, h, d, { bevel: 0.035, segments: 2, uvScale: 1 }), trs(0, 0.4 + h / 2, 0), wallColor);

  // Corner quoins — the detail that stops a wall reading as an untextured box.
  if (style.quoins ?? false) {
    const quoin = g.beveledBox(0.34, 0.3, 0.34, { bevel: 0.022, uvScale: 1 });
    for (const sx of [-1, 1]) {
      for (const sz of [-1, 1]) {
        for (let k = 0; k < Math.floor(h / 0.62); k++) {
          const y = 0.55 + k * 0.62;
          b.add(mats.stoneWall, quoin, trs(sx * (w / 2 - 0.09), y, sz * (d / 2 - 0.09)));
        }
      }
    }
  }

  // Gable roof. The ridge runs along X, so the roof slopes toward +/-Z and the
  // triangular gable walls close off the +/-X ends.
  const eaveY = 0.4 + h;
  const halfSpan = d / 2 + overhang;
  const ridgeY = eaveY + halfSpan * pitch;
  const slabLen = Math.hypot(halfSpan, halfSpan * pitch);
  const angle = Math.atan(pitch);
  for (const s of [-1, 1]) {
    const slab = g.beveledBox(w + overhang * 2, 0.15, slabLen, { bevel: 0.028, uvScale: 1 });
    // +Z slab: local +Z descends toward +Z. -Z slab: the same, mirrored, which
    // is a rotation of (PI - angle) rather than -angle.
    const m = new THREE.Matrix4().compose(
      new THREE.Vector3(0, (eaveY + ridgeY) / 2, (s * halfSpan) / 2),
      new THREE.Quaternion().setFromEuler(new THREE.Euler(s > 0 ? angle : Math.PI - angle, 0, 0)),
      new THREE.Vector3(1, 1, 1)
    );
    b.add(roofMat, slab, m, roofColor);
    // Fascia board hanging at the eave.
    b.add(
      mats.plankPainted,
      g.beveledBox(w + overhang * 2 + 0.06, 0.18, 0.05, { bevel: 0.012, uvScale: 0.6 }),
      trs(0, eaveY - 0.06, s * halfSpan),
      0xf3eee2
    );
  }
  // Ridge cap.
  b.add(
    roofMat,
    g.beveledCylinder(0.12, 0.12, w + overhang * 2, { bevel: 0.03, radialSegments: 12 }).rotateZ(Math.PI / 2),
    trs(0, ridgeY + 0.04, 0),
    roofColor
  );

  // Gable infill: wall top at eaveY, roof line rising to the ridge at z = 0.
  const rise = halfSpan * pitch;
  const shoulder = overhang * pitch;
  const gable = new THREE.Shape();
  gable.moveTo(-d / 2, 0);
  gable.lineTo(d / 2, 0);
  gable.lineTo(d / 2, shoulder);
  gable.lineTo(0, rise);
  gable.lineTo(-d / 2, shoulder);
  gable.closePath();
  const gableGeom = new THREE.ExtrudeGeometry(gable, {
    depth: 0.2,
    bevelEnabled: true,
    bevelSize: 0.02,
    bevelThickness: 0.02,
    bevelSegments: 1,
    curveSegments: 1,
  });
  for (const s of [-1, 1]) {
    const m = new THREE.Matrix4().compose(
      new THREE.Vector3(s * (w / 2 - 0.1), eaveY, 0),
      new THREE.Quaternion().setFromEuler(new THREE.Euler(0, s > 0 ? Math.PI / 2 : -Math.PI / 2, 0)),
      new THREE.Vector3(1, 1, 1)
    );
    b.add(wallMat, gableGeom, m, wallColor);
  }

  // Chimney.
  if (style.chimney ?? true) {
    const cx = rng.sign() * w * 0.28;
    const chH = ridgeY + rng.range(0.6, 1.1);
    b.add(mats.brick, g.beveledBox(0.62, chH, 0.56, { bevel: 0.028, uvScale: 1 }), trs(cx, chH / 2, 0));
    b.add(mats.stoneWall, g.beveledBox(0.82, 0.13, 0.76, { bevel: 0.022, uvScale: 1 }), trs(cx, chH + 0.06, 0));
    for (const s of [-1, 1]) {
      b.add(
        mats.roofTile,
        g.beveledCylinder(0.11, 0.13, 0.34, { bevel: 0.025, radialSegments: 12 }),
        trs(cx + s * 0.16, chH + 0.28, 0),
        0xd9d2c4
      );
    }
  }

  // The shell is done; openings are placed by the caller against the same
  // transform, because each one needs its own sub-transform.
  return { w, d, h, eaveY, ridgeY, winW: 0.86, winH: 1.06, wallColor, wallMat, roofMat, roofColor };
}

// ---------------------------------------------------------------------------
// Assembly
// ---------------------------------------------------------------------------

export function createProps({ rng, materials, geom, track, terrain, wind }) {
  const group = new THREE.Group();
  group.name = 'props';
  const updaters = [];
  const stats = {};
  const mats = { ...materials, g: geom };

  const staticBin = new Bin(geom);
  /**
   * Instanced geometry drawn with a `vertexColors` material must carry a
   * colour attribute: three has no default for it on MeshStandardMaterial, so
   * a missing one renders black. Every prototype below goes through here.
   */
  const white = (g) => geom.paintGeometry(g, 0xffffff);
  const frame = {
    pos: new THREE.Vector3(),
    forward: new THREE.Vector3(),
    right: new THREE.Vector3(),
    up: new THREE.Vector3(),
  };

  // --- rocks --------------------------------------------------------------

  const rockRng = rng.fork();
  const rockProtos = [
    white(buildRock(rockRng, geom, 3)),
    white(buildRock(rockRng, geom, 3)),
    white(buildRock(rockRng, geom, 2)),
    white(buildRock(rockRng, geom, 2)),
  ];
  const rockSpots = thin(
    [
      ...bandPlacement(rockRng, track, terrain, {
        count: 240,
        minLateral: 6.5,
        maxLateral: 70,
        minTrackDist: 5.6,
      }),
      ...scatterPlacement(rockRng, track, terrain, {
        count: 340,
        minRadius: 55,
        maxRadius: 700,
        minTrackDist: 9,
      }),
    ],
    2.2
  );
  const rockPalette = [0xb9b4ab, 0xa8a49c, 0xc4bcae, 0x968f86, 0xb0a898];
  const rockBuckets = new Map();
  for (const spot of rockSpots) {
    const id = rockRng.int(0, rockProtos.length - 1);
    let list = rockBuckets.get(id);
    if (!list) rockBuckets.set(id, (list = []));
    list.push(spot);
  }
  for (const [id, list] of rockBuckets) {
    for (const sector of sectorise(list, 6)) {
      group.add(
        instanceField(
          rockProtos[id],
          materials.rock,
          sector,
          rockRng.fork(),
          (spot, i, r) => ({
            scale: r.power(0.32, 2.1, 2.2),
            wide: r.range(0.85, 1.35),
            tall: r.range(0.55, 1.0),
            deep: r.range(0.85, 1.35),
            yaw: r.range(0, Math.PI * 2),
            tiltX: r.range(-0.16, 0.16),
            tiltZ: r.range(-0.16, 0.16),
            sink: -0.2,
            color: new THREE.Color(r.pick(rockPalette)),
          }),
          { name: 'rocks' }
        )
      );
    }
  }
  stats.rocks = rockSpots.length;

  // --- lineside fencing ---------------------------------------------------

  const fenceRng = rng.fork();
  const postGeom = white(geom.mergeAll([
    geom.beveledBox(0.11, 1.24, 0.11, { bevel: 0.014, uvScale: 0.6 }).translate(0, 0.62, 0),
    geom.beveledBox(0.14, 0.06, 0.14, { bevel: 0.016, uvScale: 0.6 }).translate(0, 1.26, 0),
  ]));
  const railGeomProto = white(geom.beveledBox(0.09, 0.075, 1, { bevel: 0.012, uvScale: 0.6 }));
  const postSpots = [];
  const railSpots = [];
  const fenceRuns = 9;
  for (let run = 0; run < fenceRuns; run++) {
    const start = fenceRng.next() * track.length;
    const runLength = fenceRng.range(45, 150);
    const side = fenceRng.sign();
    const lateral = fenceRng.range(5.6, 8.4);
    const spacing = 2.15;
    const n = Math.round(runLength / spacing);
    let prev = null;
    for (let i = 0; i <= n; i++) {
      const s = start + i * spacing;
      track.frameAt(s, 2, frame);
      const x = frame.pos.x + frame.right.x * side * lateral;
      const z = frame.pos.z + frame.right.z * side * lateral;
      const y = terrain.heightAt(x, z);
      const yaw = Math.atan2(frame.forward.x, frame.forward.z);
      postSpots.push({ x, y, z, yaw });
      if (prev) {
        const dx = x - prev.x;
        const dz = z - prev.z;
        const len = Math.hypot(dx, dz);
        const my = (y + prev.y) / 2;
        for (const rail of [0.45, 0.95]) {
          railSpots.push({
            x: (x + prev.x) / 2,
            y: my + rail,
            z: (z + prev.z) / 2,
            yaw: Math.atan2(dx, dz),
            len,
          });
        }
      }
      prev = { x, y, z };
    }
  }
  const fenceColor = [0xcfc3ad, 0xbfb49f, 0xd8cfbb];
  group.add(
    instanceField(postGeom, materials.timber, postSpots, fenceRng.fork(), (spot, i, r) => ({
      yaw: spot.yaw + r.range(-0.05, 0.05),
      tall: r.range(0.94, 1.06),
      sink: -0.06,
      color: new THREE.Color(r.pick(fenceColor)),
    }), { name: 'fence-posts' })
  );
  group.add(
    instanceField(railGeomProto, materials.timber, railSpots, fenceRng.fork(), (spot, i, r) => ({
      yaw: spot.yaw,
      deep: spot.len * 1.02,
      color: new THREE.Color(r.pick(fenceColor)),
    }), { name: 'fence-rails' })
  );
  stats.fencePosts = postSpots.length;

  // --- telegraph poles and wires -----------------------------------------

  const poleRng = rng.fork();
  const poleH = 7.6;
  const poleGeom = white(geom.mergeAll([
    geom.beveledCylinder(0.10, 0.16, poleH, { bevel: 0.03, radialSegments: 12 }).translate(0, poleH / 2, 0),
    geom.beveledBox(1.5, 0.09, 0.1, { bevel: 0.016, uvScale: 0.5 }).translate(0, poleH - 0.5, 0),
    geom.beveledBox(1.1, 0.09, 0.1, { bevel: 0.016, uvScale: 0.5 }).translate(0, poleH - 1.05, 0),
    // Diagonal braces.
    geom.beveledBox(0.06, 0.5, 0.06, { bevel: 0.012, uvScale: 0.5 })
      .rotateZ(0.62)
      .translate(-0.28, poleH - 0.78, 0),
    geom.beveledBox(0.06, 0.5, 0.06, { bevel: 0.012, uvScale: 0.5 })
      .rotateZ(-0.62)
      .translate(0.28, poleH - 0.78, 0),
  ]));
  const insulatorParts = [];
  const wireOffsets = [];
  for (const [armY, half, n] of [[poleH - 0.5, 0.62, 3], [poleH - 1.05, 0.44, 2]]) {
    for (let i = 0; i < n; i++) {
      const x = n === 1 ? 0 : lerp(-half, half, i / (n - 1));
      insulatorParts.push(
        geom.beveledCylinder(0.055, 0.075, 0.14, { bevel: 0.022, radialSegments: 10 }).translate(x, armY + 0.11, 0)
      );
      wireOffsets.push(new THREE.Vector3(x, armY + 0.19, 0));
    }
  }
  const insulatorGeom = white(geom.mergeAll(insulatorParts));

  const poleSpacing = 46;
  const poleCount = Math.max(6, Math.round(track.length / poleSpacing));
  const poleSpots = [];
  const poleSide = poleRng.sign();
  for (let i = 0; i < poleCount; i++) {
    const s = (i * track.length) / poleCount;
    track.frameAt(s, 3, frame);
    const lateral = poleSide * poleRng.range(7.5, 9.5);
    const x = frame.pos.x + frame.right.x * lateral;
    const z = frame.pos.z + frame.right.z * lateral;
    const y = terrain.heightAt(x, z);
    const yaw = Math.atan2(frame.forward.x, frame.forward.z) + Math.PI / 2;
    poleSpots.push({ x, y, z, yaw, s });
  }
  group.add(
    instanceField(poleGeom, materials.timber, poleSpots, poleRng.fork(), (spot, i, r) => ({
      yaw: spot.yaw,
      sink: -0.15,
      tall: r.range(0.94, 1.05),
      color: new THREE.Color(0x8a7358).multiplyScalar(r.range(0.85, 1.15)),
    }), { name: 'telegraph-poles' })
  );
  group.add(
    instanceField(insulatorGeom, materials.plankPainted, poleSpots, poleRng.fork(), (spot) => ({
      yaw: spot.yaw,
      sink: -0.15,
      color: 0xe8e2d2,
    }), { name: 'insulators' })
  );

  // Wires: a real catenary sag between consecutive poles.
  const wireParts = [];
  const wireMat = new THREE.MeshStandardMaterial({
    color: 0x241f1b,
    roughness: 0.55,
    metalness: 0.7,
  });
  wireMat.name = 'wire';
  for (let i = 0; i < poleSpots.length; i++) {
    const a = poleSpots[i];
    const bpt = poleSpots[(i + 1) % poleSpots.length];
    const span = Math.hypot(bpt.x - a.x, bpt.z - a.z);
    if (span > poleSpacing * 2.2) continue;
    const sag = clamp(span * 0.055, 0.15, 0.9);
    for (const off of wireOffsets) {
      const rotA = new THREE.Vector3(off.x, 0, 0).applyAxisAngle(new THREE.Vector3(0, 1, 0), a.yaw);
      const rotB = new THREE.Vector3(off.x, 0, 0).applyAxisAngle(new THREE.Vector3(0, 1, 0), bpt.yaw);
      const p0 = new THREE.Vector3(a.x + rotA.x, a.y - 0.15 + off.y, a.z + rotA.z);
      const p1 = new THREE.Vector3(bpt.x + rotB.x, bpt.y - 0.15 + off.y, bpt.z + rotB.z);
      const pts = [];
      for (let k = 0; k <= 6; k++) {
        const t = k / 6;
        const p = p0.clone().lerp(p1, t);
        p.y -= Math.sin(t * Math.PI) * sag;
        pts.push(p);
      }
      wireParts.push(geom.tubeThrough(pts, 0.017, { radialSegments: 5, tubularSegments: 12 }));
    }
  }
  if (wireParts.length) {
    const wires = new THREE.Mesh(geom.mergeAll(wireParts), wireMat);
    wires.castShadow = false;
    wires.receiveShadow = false;
    wires.name = 'telegraph-wires';
    group.add(wires);
  }

  // --- mileposts and lineside signals ------------------------------------

  const signRng = rng.fork();
  const milepostGeom = white(geom.mergeAll([
    geom.beveledCylinder(0.055, 0.07, 0.95, { bevel: 0.018, radialSegments: 10 }).translate(0, 0.475, 0),
    geom.beveledBox(0.34, 0.24, 0.05, { bevel: 0.012, uvScale: 0.4 }).translate(0, 1.0, 0.02),
  ]));
  const milepostSpots = [];
  const milepostCount = Math.round(track.length / 26);
  for (let i = 0; i < milepostCount; i++) {
    const s = (i * track.length) / milepostCount;
    track.frameAt(s, 2, frame);
    const side = i % 2 === 0 ? 1 : -1;
    const x = frame.pos.x + frame.right.x * side * 4.1;
    const z = frame.pos.z + frame.right.z * side * 4.1;
    milepostSpots.push({
      x,
      y: terrain.heightAt(x, z),
      z,
      yaw: Math.atan2(frame.forward.x, frame.forward.z) + (side > 0 ? Math.PI / 2 : -Math.PI / 2),
    });
  }
  group.add(
    instanceField(milepostGeom, materials.plankPainted, milepostSpots, signRng.fork(), (spot) => ({
      yaw: spot.yaw,
      sink: -0.05,
      color: 0xf2ece0,
    }), { name: 'mileposts' })
  );

  // Semaphore signals: post, ladder, arm and spectacle plate.
  const signalCount = 3;
  for (let i = 0; i < signalCount; i++) {
    const s = ((i + 0.35) * track.length) / signalCount;
    track.frameAt(s, 3, frame);
    const side = signRng.sign();
    const x = frame.pos.x + frame.right.x * side * 5.2;
    const z = frame.pos.z + frame.right.z * side * 5.2;
    const y = terrain.heightAt(x, z);
    const yaw = Math.atan2(frame.forward.x, frame.forward.z);
    const base = trs(x, y, z, yaw);
    const b = subBin(staticBin, base);
    const H = 5.4;
    b.add(mats.stoneWall, geom.beveledBox(0.5, 0.34, 0.5, { bevel: 0.03, uvScale: 1 }), trs(0, 0.1, 0));
    b.add(
      materials.plankPainted,
      geom.beveledCylinder(0.075, 0.11, H, { bevel: 0.03, radialSegments: 12 }).translate(0, H / 2, 0),
      trs(0, 0.25, 0),
      0xdcd6c8
    );
    // Ladder.
    for (const sx of [-0.11, 0.11]) {
      b.add(
        materials.iron,
        geom.beveledBox(0.03, H - 0.6, 0.03, { bevel: 0.008, uvScale: 0.4 }).translate(sx, (H - 0.6) / 2, 0.19),
        trs(0, 0.3, 0),
        0x5b5750
      );
    }
    for (let k = 0; k < 14; k++) {
      b.add(
        materials.iron,
        geom.beveledBox(0.23, 0.022, 0.022, { bevel: 0.006, uvScale: 0.4 }).translate(0, 0.35 + k * 0.34, 0.19),
        null,
        0x5b5750
      );
    }
    // Arm and spectacle.
    b.add(
      materials.plankPainted,
      geom.beveledBox(1.15, 0.19, 0.035, { bevel: 0.012, uvScale: 0.4 }).translate(0.42, H + 0.05, 0.13),
      null,
      0xd23b28
    );
    b.add(
      materials.plankPainted,
      geom.beveledBox(0.2, 0.06, 0.045, { bevel: 0.01, uvScale: 0.3 }).translate(0.78, H + 0.05, 0.155),
      null,
      0xf5f0e4
    );
    b.add(
      materials.iron,
      geom.beveledBox(0.4, 0.3, 0.05, { bevel: 0.02, uvScale: 0.4 }).translate(-0.13, H + 0.05, 0.13),
      null,
      0x3b3b3b
    );
    // Finial.
    b.add(
      materials.plankPainted,
      geom.beveledCylinder(0.02, 0.1, 0.3, { bevel: 0.025, radialSegments: 10 }).translate(0, H + 0.42, 0),
      trs(0, 0.25, 0),
      0xdcd6c8
    );
  }

  // --- dry stone walls ----------------------------------------------------

  const wallRng = rng.fork();
  const wallSegments = [];
  for (let run = 0; run < 7; run++) {
    const a0 = wallRng.range(0, Math.PI * 2);
    const r0 = wallRng.range(90, 330);
    let x = Math.cos(a0) * r0;
    let z = Math.sin(a0) * r0;
    let dir = wallRng.range(0, Math.PI * 2);
    const n = wallRng.int(14, 34);
    for (let i = 0; i < n; i++) {
      dir += wallRng.range(-0.16, 0.16);
      const step = 2.6;
      const nx = x + Math.cos(dir) * step;
      const nz = z + Math.sin(dir) * step;
      if (track.nearest(nx, nz).dist < 14) break;
      const y = terrain.heightAt(nx, nz);
      if (y < terrain.waterLevel + 1.2) break;
      wallSegments.push({
        x: (x + nx) / 2,
        y: (terrain.heightAt(x, z) + y) / 2,
        z: (z + nz) / 2,
        yaw: -dir,
      });
      x = nx;
      z = nz;
    }
  }
  const wallGeom = white(
    geom.beveledBox(0.52, 1.05, 2.7, { bevel: 0.05, segments: 2, uvScale: 1 }).translate(0, 0.5, 0)
  );
  group.add(
    instanceField(wallGeom, materials.stoneWall, wallSegments, wallRng.fork(), (spot, i, r) => ({
      yaw: spot.yaw,
      sink: -0.24,
      tall: r.range(0.85, 1.12),
      wide: r.range(0.9, 1.1),
    }), { name: 'dry-stone-wall' })
  );
  stats.wallSegments = wallSegments.length;

  // --- buildings ----------------------------------------------------------

  const houseRng = rng.fork();
  const wallColors = [0xf2ead6, 0xe9dcc0, 0xdfe4dd, 0xf0dfc8, 0xe4d7c4];
  const roofColors = [0xffffff, 0xd8cdc0, 0xc8b5a4, 0xb9a894];
  const houseSpots = thin(
    bandPlacement(houseRng, track, terrain, {
      count: 9,
      minLateral: 19,
      maxLateral: 78,
      minTrackDist: 18,
      maxSlope: 0.45,
      power: 1.25,
    }),
    26
  );
  for (const spot of houseSpots) {
    const yaw = houseRng.range(0, Math.PI * 2);
    const matrix = trs(spot.x, spot.y - 0.12, spot.z, yaw);
    const brickWall = houseRng.chance(0.3);
    const spec = buildBuilding(houseRng, staticBin, mats, matrix, {
      wallMat: brickWall ? materials.brick : materials.plaster,
      wallColor: brickWall ? 0xffffff : houseRng.pick(wallColors),
      roofMat: houseRng.chance(0.3) ? materials.roofSlate : materials.roofTile,
      roofColor: houseRng.pick(roofColors),
    });
    // Openings, placed against the building transform.
    const b = subBin(staticBin, matrix);
    for (const face of [1, -1]) {
      // The door goes in the middle of the +Z face, so the windows sit either
      // side of it. Any other split lands a sash on top of the doorway.
      const cols = 2;
      for (let i = 0; i < cols; i++) {
        const x = (i === 0 ? -1 : 1) * spec.w * 0.31;
        const wm = trs(x, 0.4 + spec.h * 0.58, face * (spec.d / 2 + 0.005), face > 0 ? 0 : Math.PI);
        addWindow(subBin(staticBin, matrix.clone().multiply(wm)), mats, spec.winW, spec.winH, spec.wallColor);
      }
    }
    // Door, built proud of the wall for the same reason the windows are.
    const doorW = 0.98;
    const doorH = 2.05;
    const doorReveal = 0.10;
    const dm = trs(0, 0.4 + doorH / 2, spec.d / 2 + 0.005);
    const db = subBin(staticBin, matrix.clone().multiply(dm));
    const doorColor = houseRng.pick([0x2f5d52, 0x7a3327, 0x2b4166, 0x4a4238]);
    // Leaf, with two raised panels.
    db.add(
      materials.plankPainted,
      geom.beveledBox(doorW, doorH, 0.05, { bevel: 0.012, uvScale: 0.8 }).translate(0, 0, 0.025),
      null,
      doorColor
    );
    for (const py of [doorH * 0.22, -doorH * 0.22]) {
      db.add(
        materials.plankPainted,
        geom
          .beveledBox(doorW * 0.62, doorH * 0.34, 0.02, { bevel: 0.01, uvScale: 0.5 })
          .translate(0, py, 0.055),
        null,
        doorColor
      );
    }
    // Casing.
    const casing = geom.beveledBox(0.09, doorH + 0.18, doorReveal, { bevel: 0.014, uvScale: 0.6 });
    db.add(materials.plankPainted, casing.clone().translate(-(doorW + 0.09) / 2, 0, doorReveal / 2), null, 0xf1ece0);
    db.add(materials.plankPainted, casing.clone().translate((doorW + 0.09) / 2, 0, doorReveal / 2), null, 0xf1ece0);
    db.add(
      materials.plankPainted,
      geom
        .beveledBox(doorW + 0.18, 0.09, doorReveal, { bevel: 0.014, uvScale: 0.6 })
        .translate(0, (doorH + 0.09) / 2, doorReveal / 2),
      null,
      0xf1ece0
    );
    db.add(
      materials.steel,
      geom
        .beveledCylinder(0.032, 0.032, 0.06, { bevel: 0.013, radialSegments: 12 })
        .rotateX(Math.PI / 2)
        .translate(doorW * 0.32, 0, 0.075)
    );
    // Step.
    b.add(mats.stoneWall, geom.beveledBox(1.5, 0.14, 0.62, { bevel: 0.025, uvScale: 1 }), trs(0, 0.33, spec.d / 2 + 0.3));
  }
  stats.buildings = houseSpots.length;
  stats.housePositions = houseSpots.map((s) => [s.x, s.y, s.z]);

  // --- farm clutter -------------------------------------------------------

  const yardRng = rng.fork();
  const logGeom = white(
    geom.beveledCylinder(0.16, 0.17, 2.4, { bevel: 0.05, radialSegments: 12 }).rotateZ(Math.PI / 2)
  );
  const logSpots = [];
  for (const spot of houseSpots) {
    if (!yardRng.chance(0.6)) continue;
    const a = yardRng.range(0, Math.PI * 2);
    const bx = spot.x + Math.cos(a) * 7;
    const bz = spot.z + Math.sin(a) * 7;
    const by = terrain.heightAt(bx, bz);
    const yaw = yardRng.range(0, Math.PI * 2);
    for (let row = 0; row < 3; row++) {
      for (let k = 0; k < 4 - row; k++) {
        const ox = (k - (3 - row) / 2) * 0.35;
        logSpots.push({
          x: bx + Math.cos(yaw) * ox,
          y: by + 0.17 + row * 0.3,
          z: bz + Math.sin(yaw) * ox,
          yaw,
        });
      }
    }
  }
  if (logSpots.length) {
    group.add(
      instanceField(logGeom, materials.timber, logSpots, yardRng.fork(), (spot, i, r) => ({
        yaw: spot.yaw + r.range(-0.04, 0.04),
        scale: r.range(0.9, 1.08),
        color: new THREE.Color(0xb08a5e).multiplyScalar(r.range(0.8, 1.15)),
      }), { name: 'log-pile' })
    );
  }

  // Haystacks in the fields.
  const hayGeom = white(geom.mergeAll([
    geom.beveledCylinder(1.05, 1.15, 1.5, { bevel: 0.22, radialSegments: 16 }).translate(0, 0.75, 0),
    geom.beveledCylinder(0.02, 1.1, 0.95, { bevel: 0.2, radialSegments: 16 }).translate(0, 1.85, 0),
  ]));
  const haySpots = thin(
    scatterPlacement(yardRng, track, terrain, {
      count: 26,
      minRadius: 60,
      maxRadius: 400,
      minTrackDist: 20,
      maxSlope: 0.32,
    }),
    30
  );
  group.add(
    instanceField(hayGeom, materials.timber, haySpots, yardRng.fork(), (spot, i, r) => ({
      scale: r.range(0.8, 1.25),
      yaw: r.range(0, Math.PI * 2),
      sink: -0.1,
      color: new THREE.Color(0xd9bd7e).multiplyScalar(r.range(0.88, 1.12)),
    }), { name: 'haystacks' })
  );

  staticBin.build(group, 'props');
  stats.staticDrawCalls = staticBin.drawCalls;

  return {
    group,
    stats,
    update(t) {
      for (const fn of updaters) fn(t);
    },
  };
}
