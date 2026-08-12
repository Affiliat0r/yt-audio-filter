/**
 * Dependency broker for Part D.
 *
 * Parts A and B land `visuals/lib/materials.mjs` and `visuals/lib/geom.mjs` on
 * their own schedule. Part D owns `visuals/scene/train/` only, so it may not
 * write stubs into `visuals/lib/`. Instead:
 *
 *   - if the real module is present and exports a working function, use it;
 *   - otherwise use the local fallback with the same signature;
 *   - if a real function is present but throws or returns the wrong kind of
 *     thing, latch permanently onto the fallback.
 *
 * All of this resolves at module-evaluation time (top-level await) and all
 * geometry/material construction happens once during `createTrain`, so none of
 * it can vary between frames. `update(t)` never touches this file.
 */

import * as THREE from 'three';
import * as fallbackGeom from './fallback-geom.mjs';
import * as fallbackMaterials from './fallback-materials.mjs';

async function tryImport(specifier) {
  try {
    const mod = await import(specifier);
    return mod && typeof mod === 'object' ? mod : null;
  } catch {
    return null;
  }
}

const libGeom = await tryImport('../../lib/geom.mjs');
const libMaterials = await tryImport('../../lib/materials.mjs');

/** Which of the two helper libraries we actually ended up on. */
export const provenance = {
  geom: libGeom ? 'lib' : 'fallback',
  materials: libMaterials ? 'lib' : 'fallback',
  notes: [],
};

function bind(mod, name, fallback, validate) {
  const real = mod && typeof mod[name] === 'function' ? mod[name] : null;
  if (!real) {
    if (mod) provenance.notes.push(`${name}: missing upstream, using fallback`);
    return fallback;
  }
  let latched = null;
  return function broker(...args) {
    if (latched) return latched(...args);
    try {
      const out = real(...args);
      if (validate(out)) return out;
      provenance.notes.push(`${name}: upstream returned an unexpected value, using fallback`);
    } catch (err) {
      provenance.notes.push(`${name}: upstream threw (${err && err.message}), using fallback`);
    }
    latched = fallback;
    return fallback(...args);
  };
}

const isGeometry = (g) => !!(g && g.isBufferGeometry);
const isMaterial = (m) => !!(m && m.isMaterial);

export const beveledBox = bind(libGeom, 'beveledBox', fallbackGeom.beveledBox, isGeometry);
export const beveledCylinder = bind(libGeom, 'beveledCylinder', fallbackGeom.beveledCylinder, isGeometry);
export const roundedProfile = bind(libGeom, 'roundedProfile', fallbackGeom.roundedProfile, Array.isArray);
export const mergeAll = bind(libGeom, 'mergeAll', fallbackGeom.mergeAll, isGeometry);

// Not in the published contract, so always local.
export const latheProfile = fallbackGeom.latheProfile;
export const rivetHead = fallbackGeom.rivetHead;
export const tubeAlong = fallbackGeom.tubeAlong;
export const extrudeShape = fallbackGeom.extrudeShape;

export const painted = bind(libMaterials, 'painted', fallbackMaterials.painted, isMaterial);
export const solid = bind(libMaterials, 'solid', fallbackMaterials.solid, isMaterial);

/**
 * Apply extra properties to a factory-made material without risking a shared
 * cache upstream: clone first, then assign. Assignments the material does not
 * understand are silently ignored by three, which is what we want when the
 * upstream factory hands back a Standard rather than a Physical material.
 */
export function tune(material, props = {}) {
  const m = material.clone();
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined) continue;
    const current = m[key];
    if (current && current.isColor && !(value && value.isColor)) current.set(value);
    else if (current && current.isVector2 && Array.isArray(value)) current.set(value[0], value[1]);
    else m[key] = value;
  }
  m.needsUpdate = true;
  return m;
}

export { THREE };
