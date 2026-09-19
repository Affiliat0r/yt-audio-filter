/**
 * Weathering.
 *
 * Nothing on a working railway is one flat colour. Dust climbs the first half
 * metre off the ballast, soot settles on every upward face downwind of the
 * chimney, and the paint itself is blotchy where it has been touched up. This
 * file turns those three observations into a per-vertex tint that the body
 * meshes carry as a colour attribute — free at render time, and it does more
 * for realism than any amount of extra geometry.
 *
 * Pure: a function of position and an integer seed, nothing else.
 */

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

function blotch(x, y, z, seed) {
  // Cheap 3D value hash, smoothed by summing two frequencies.
  const h = (a, b, c) => {
    const s = Math.sin(a * 12.9898 + b * 78.233 + c * 37.719 + seed * 0.113) * 43758.5453;
    return s - Math.floor(s);
  };
  return h(x * 0.9, y * 0.9, z * 0.9) * 0.6 + h(x * 3.1, y * 2.6, z * 2.2) * 0.4;
}

/**
 * @param {object} opts
 * @param {number} opts.seed
 * @param {number} opts.dustTop   height at which rail dust has faded out
 * @param {number} opts.sootZ     z of the chimney (soot source), or null
 * @param {number} opts.sootY     height above which soot settles
 * @returns {(x:number,y:number,z:number)=>[number,number,number]}
 */
export function makeWeathering({ seed = 1, dustTop = 2.2, sootZ = null, sootY = 2.3, strength = 1 } = {}) {
  return function shade(x, y, z) {
    // Rail dust: warm, strongest at the bottom, gone by `dustTop`.
    const dust = clamp01(1 - y / dustTop) ** 1.5 * 0.36 * strength;

    // Soot: settles on high surfaces, thickest just behind the chimney and
    // trailing back over the boiler and cab roof.
    let soot = 0;
    if (sootZ !== null) {
      const along = clamp01(1 - Math.abs(z - (sootZ - 1.8)) / 4.2);
      soot = along * clamp01((y - sootY) / 0.45) * 0.3 * strength;
    }

    // Blotch: touched-up paint and uneven polish.
    const b = (blotch(x, y, z, seed) - 0.5) * 0.13 * strength;

    const k = clamp01(1 - dust - soot + b);
    // Dust is warm, soot is cold: tint the channels apart rather than just
    // darkening, or it reads as shadow instead of dirt.
    return [
      clamp01(k + dust * 0.34),
      clamp01(k + dust * 0.24 - soot * 0.03),
      clamp01(k + dust * 0.1 - soot * 0.06),
    ];
  };
}
