/**
 * Optional-dependency bridge.
 *
 * Parts A and B land `visuals/lib/geom.mjs`, `materials.mjs`, `assets.mjs` and
 * `lighting.mjs` on their own schedule. Part C must build and look right
 * whether or not they are there yet, so every call into `visuals/lib/` goes
 * through this file. Nothing here creates or edits anything under `visuals/lib/`.
 *
 * **Geometry is adopted per function, and only where the semantics match.**
 * That caveat is not theoretical:
 *
 *   - Part A's `uvScale` is *repeats per metre* (uv = position x uvScale).
 *     Part C's is *metres per repeat* (uv = position / uvScale), because every
 *     material in the world is scaled by metres-per-tile. The two agree only
 *     at 1. `adoptBeveled` inverts the value at the boundary.
 *   - Part A's `extrudeProfile(points, depth, opts)` is a planar extrusion.
 *     Part C's is a sweep of a cross-section along a list of oriented frames —
 *     same name, unrelated operation. It is never adopted.
 *   - Part A's `roundedProfile` duplicates sharp vertices so its own `lathe()`
 *     can shade them flat. Part C feeds the result straight to
 *     `THREE.LatheGeometry`, where the extra vertices change the silhouette.
 *     Also never adopted.
 *
 * Materials are strictly an *upgrade*. The world is fully textured from
 * `world/textures.mjs` the moment it is constructed; if a caller hands us
 * Part A's factory and its cache is warm, the maps are swapped in place
 * afterwards. Any failure leaves the procedural set alone.
 */

import * as local from './geom.mjs';

/** Exports Part A may safely provide, with `uvScale` inverted at the boundary. */
const UV_SCALED = ['beveledBox', 'beveledCylinder'];

/** Everything else Part C uses stays local: see the note above. */
const LOCAL_ONLY = [
  'roundedProfile',
  'mergeAll',
  'extrudeProfile',
  'tubeThrough',
  'taperedCapsule',
  'boxOnBase',
  'cylinderOnBase',
  'composeFromForward',
  'boxUVs',
  'paintGeometry',
  'mergeGeometries',
  'WORLD_UP',
];

/** Wrap so Part C's metres-per-repeat becomes Part A's repeats-per-metre. */
function adoptBeveled(fn) {
  return function beveledAdapter(...args) {
    const opts = args[args.length - 1];
    if (opts && typeof opts === 'object' && !Array.isArray(opts) && 'uvScale' in opts) {
      const scale = opts.uvScale || 1;
      args[args.length - 1] = { ...opts, uvScale: 1 / scale };
    }
    return fn(...args);
  };
}

/**
 * Merge Part A's geom module (if supplied) over the local one, per export.
 * @param {object|null} provided
 */
export function resolveGeom(provided) {
  const out = {};
  for (const key of LOCAL_ONLY) out[key] = local[key];
  for (const key of UV_SCALED) {
    const fromLib = provided ? provided[key] : undefined;
    out[key] = typeof fromLib === 'function' ? adoptBeveled(fromLib) : local[key];
  }
  out._adoptedFromLib = UV_SCALED.filter((k) => provided && typeof provided[k] === 'function');
  return out;
}
