/**
 * Track access.
 *
 * Part C owns the track. Its exact API is not pinned down in the contract
 * beyond "position and tangent at a normalised distance", so `adaptTrack`
 * sniffs whatever it was handed and normalises it to one interface. If there is
 * no track at all — which is the case whenever Part D is being worked on or
 * tested alone — it generates its own closed loop so nothing is ever blocked.
 *
 * Whatever the source, the curve is resampled once into flat arrays. Every
 * lookup after that is an array index and a lerp, which matters because the
 * steam does a few hundred of them per frame to find where the chimney *was*
 * when each puff left it.
 */

import * as THREE from 'three';

const SAMPLE_SPACING = 0.25;

/* ------------------------------------------------------- fallback geometry */

/** A closed, gently rolling ring — stand-in for Part C's track. */
export function fallbackCurve(rng, { radius = 104, points = 14, jitter = 20 } = {}) {
  const controls = [];
  for (let i = 0; i < points; i++) {
    const a = (i / points) * Math.PI * 2 + rng.range(-0.1, 0.1);
    const r = radius + rng.range(-jitter, jitter);
    controls.push(new THREE.Vector3(Math.cos(a) * r, rng.range(-1.5, 1.5), Math.sin(a) * r));
  }
  const curve = new THREE.CatmullRomCurve3(controls, true, 'catmullrom', 0.5);
  curve.arcLengthDivisions = 4000;
  return curve;
}

/* -------------------------------------------------------------- adaptation */

const _a = new THREE.Vector3();
const _b = new THREE.Vector3();

function asVector3(value, target) {
  if (!value) return null;
  if (value.isVector3) return target.copy(value);
  if (Array.isArray(value) && value.length >= 3) return target.set(value[0], value[1], value[2]);
  if (typeof value.x === 'number' && typeof value.y === 'number' && typeof value.z === 'number') {
    return target.set(value.x, value.y, value.z);
  }
  return null;
}

/**
 * Decide whether a positional accessor is parameterised by metres or by a
 * normalised 0..1. On a closed loop f(0) and f(1) are the same point when the
 * argument is normalised, and metres apart when it is not.
 */
function isNormalised(fn, length) {
  try {
    const p0 = asVector3(fn(0), _a);
    const p1 = asVector3(fn(1), _b);
    if (!p0 || !p1) return null;
    if (p0.distanceTo(p1) < 1e-3) return true;
    // A one-metre step on a hundred-metre loop moves about a metre.
    return p0.distanceTo(p1) > length * 0.2;
  } catch {
    return null;
  }
}

function resolveAccessor(track) {
  if (!track) return null;
  const length =
    (typeof track.length === 'number' && track.length > 1 && track.length) ||
    (typeof track.getLength === 'function' && track.getLength()) ||
    (track.curve && typeof track.curve.getLength === 'function' && track.curve.getLength()) ||
    0;

  const candidates = [
    ['pointAt', (d, out) => track.pointAt(d, out)],
    ['positionAt', (d, out) => asVector3(track.positionAt(d), out)],
    ['getPointAt', (d, out) => track.getPointAt(d, out)],
    ['pointAtDistance', (d, out) => asVector3(track.pointAtDistance(d), out)],
    ['sample', (d, out) => asVector3(track.sample(d), out)],
  ];
  for (const [name, fn] of candidates) {
    if (typeof track[name] !== 'function') continue;
    const probe = asVector3(fn(0, new THREE.Vector3()), _a);
    if (!probe) continue;
    const normalised = isNormalised((v) => fn(v, new THREE.Vector3()), length || 100);
    if (normalised === null) continue;
    return { length: length || 100, normalised, read: fn };
  }
  if (track.curve && typeof track.curve.getPointAt === 'function') {
    const len = length || track.curve.getLength();
    return { length: len, normalised: true, read: (u, out) => track.curve.getPointAt(u, out) };
  }
  if (typeof track.getPoint === 'function') {
    return { length: length || 100, normalised: true, read: (u, out) => track.getPoint(u, out) };
  }
  return null;
}

/**
 * Normalise any track into `{ length, pointAt(distance, target), railTop }` and
 * resample it for O(1) lookup, curvature and gradient.
 */
export function adaptTrack(track, rng, { railTop = null, fallback = null } = {}) {
  let accessor = resolveAccessor(track);
  let source = 'part-c';

  if (!accessor) {
    const curve = fallback || fallbackCurve(rng);
    const length = curve.getLength();
    accessor = { length, normalised: true, read: (u, out) => curve.getPointAt(u, out) };
    source = 'fallback';
  }

  const length = accessor.length;
  const wrap = (d) => {
    const v = (d / length) % 1;
    return v < 0 ? v + 1 : v;
  };
  // Some accessors write into the supplied target, some return a fresh vector
  // and ignore it. Normalise both into `out`.
  const rawPoint = (distance, out) => {
    const value = accessor.normalised ? accessor.read(wrap(distance), out) : accessor.read(distance, out);
    return value === out ? out : asVector3(value, out) || out;
  };

  // --- resample -----------------------------------------------------------
  const count = Math.max(64, Math.round(length / SAMPLE_SPACING));
  const step = length / count;
  const px = new Float32Array(count);
  const py = new Float32Array(count);
  const pz = new Float32Array(count);
  const tmp = new THREE.Vector3();
  for (let i = 0; i < count; i++) {
    rawPoint(i * step, tmp);
    px[i] = tmp.x;
    py[i] = tmp.y;
    pz[i] = tmp.z;
  }

  // Forward vectors as chords, and signed curvature about world up.
  const fx = new Float32Array(count);
  const fy = new Float32Array(count);
  const fz = new Float32Array(count);
  const curv = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const j = (i + 1) % count;
    let dx = px[j] - px[i];
    let dy = py[j] - py[i];
    let dz = pz[j] - pz[i];
    const len = Math.hypot(dx, dy, dz) || 1;
    fx[i] = dx / len;
    fy[i] = dy / len;
    fz[i] = dz / len;
  }
  for (let i = 0; i < count; i++) {
    const j = (i + 1) % count;
    // Signed curvature: the y component of T(i) x T(j), per metre.
    curv[i] = (fz[i] * fx[j] - fx[i] * fz[j]) / step;
  }
  // One smoothing pass: the sway should respond to the curve, not to sampling
  // noise in Part C's control points.
  const smooth = new Float32Array(count);
  const half = Math.max(1, Math.round(3 / step));
  let running = 0;
  for (let i = -half; i <= half; i++) running += curv[((i % count) + count) % count];
  const window = half * 2 + 1;
  for (let i = 0; i < count; i++) {
    smooth[i] = running / window;
    running -= curv[((i - half) % count + count) % count];
    running += curv[((i + half + 1) % count + count) % count];
  }

  const index = (distance) => {
    const u = wrap(distance) * count;
    const i0 = Math.floor(u);
    return { i0: i0 % count, i1: (i0 + 1) % count, f: u - i0 };
  };

  function pointAt(distance, target = new THREE.Vector3()) {
    const { i0, i1, f } = index(distance);
    return target.set(
      px[i0] + (px[i1] - px[i0]) * f,
      py[i0] + (py[i1] - py[i0]) * f,
      pz[i0] + (pz[i1] - pz[i0]) * f
    );
  }

  function forwardAt(distance, target = new THREE.Vector3()) {
    const { i0, i1, f } = index(distance);
    return target
      .set(fx[i0] + (fx[i1] - fx[i0]) * f, fy[i0] + (fy[i1] - fy[i0]) * f, fz[i0] + (fz[i1] - fz[i0]) * f)
      .normalize();
  }

  function curvatureAt(distance) {
    const { i0, i1, f } = index(distance);
    return smooth[i0] + (smooth[i1] - smooth[i0]) * f;
  }

  const top =
    railTop ??
    (typeof track?.railTop === 'number' ? track.railTop : null) ??
    (typeof track?.RAIL_TOP === 'number' ? track.RAIL_TOP : null) ??
    0;

  return { source, length, pointAt, forwardAt, curvatureAt, railTop: top, sampleCount: count };
}
