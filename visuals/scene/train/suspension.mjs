/**
 * Springs.
 *
 * A vehicle body does not sit rigidly on its wheels: it floats on springs, and
 * the springs are excited by track irregularity below and by cornering forces
 * from the side. Adding that costs almost nothing and is one of the largest
 * authenticity wins available — a perfectly rigid train reads as a diagram.
 *
 * Everything here is a closed-form function of distance along the track, so it
 * is automatically deterministic and automatically consistent between vehicles:
 * the second coach hits the same dip the first one did, one vehicle-length
 * later, because it is evaluated at its own distance on the same function.
 */

const TWO_PI = Math.PI * 2;

/**
 * A sum of sines is an honest model here. Real track irregularity has a roughly
 * 1/f spectrum, and the sprung mass acts as a lightly damped band-pass on it,
 * so what actually reaches the body is a handful of dominant wavelengths. Six
 * of them, weighted toward the body's resonance, is indistinguishable.
 */
function makeWave(rng, { bands, gain }) {
  const terms = [];
  for (const [minLambda, maxLambda, amp] of bands) {
    terms.push({
      k: TWO_PI / rng.range(minLambda, maxLambda),
      a: amp * gain * rng.range(0.6, 1.4),
      phase: rng.range(0, TWO_PI),
    });
  }
  return function evaluate(d) {
    let sum = 0;
    for (let i = 0; i < terms.length; i++) sum += terms[i].a * Math.sin(terms[i].k * d + terms[i].phase);
    return sum;
  };
}

/**
 * @param {Rng}    rng    forked stream, so each vehicle rides differently
 * @param {object} opts
 * @param {number} opts.sleeperSpacing metres between sleepers
 * @param {number} opts.softness       0 = cart-sprung, 1 = plush
 * @param {number} opts.rollGain       radians of body roll per m/s^2 of lateral
 */
export function createSuspension(rng, opts = {}) {
  const {
    sleeperSpacing = 0.65,
    softness = 1,
    rollGain = 0.055,
    heaveScale = 1,
    lateralScale = 1,
  } = opts;

  // Long-wave heave: the part you actually see. Weighted toward 5-9 m, which is
  // where a body with a ~1 Hz bounce mode resonates at these speeds.
  const heave = makeWave(rng, {
    gain: 0.016 * softness * heaveScale,
    bands: [
      [3.0, 4.5, 0.35],
      [5.0, 7.0, 1.0],
      [7.5, 10.0, 0.85],
      [12.0, 17.0, 0.6],
      [22.0, 34.0, 0.4],
    ],
  });

  // Cross-level: the two rails are never exactly level, so the body rocks.
  const crossLevel = makeWave(rng, {
    gain: 0.012 * softness,
    bands: [
      [4.0, 6.0, 0.9],
      [8.0, 12.0, 1.0],
      [18.0, 28.0, 0.7],
    ],
  });

  // Hunting: the slow lateral weave of a rigid wheelset between the rails.
  const lateral = makeWave(rng, {
    gain: 0.011 * lateralScale,
    bands: [
      [9.0, 13.0, 1.0],
      [16.0, 24.0, 0.6],
    ],
  });

  // Sleeper passing. Kept deliberately tiny: at any sane speed it lands near
  // the frame rate and would alias into a slow wobble if it were visible.
  const sleeperAmp = 0.0022 * softness;
  const sleeperK = TWO_PI / Math.max(0.2, sleeperSpacing);
  const sleeperPhase = rng.range(0, TWO_PI);

  const out = { bounce: 0, roll: 0, pitch: 0, lateral: 0, lateralAccel: 0 };

  /**
   * @param {number} distance metres along the track
   * @param {number} speed    metres per second
   * @param {number} curvature signed 1/radius about world up
   * @param {number} wheelbase distance between the vehicle's axle points
   */
  function evaluate(distance, speed, curvature, wheelbase) {
    const h = heave(distance) + sleeperAmp * Math.sin(sleeperK * distance + sleeperPhase);
    // Pitch from the difference in heave under the two ends: exactly what the
    // body does when the front axle drops into a dip before the rear one.
    const front = heave(distance + wheelbase / 2);
    const back = heave(distance - wheelbase / 2);
    const pitch = Math.atan2(front - back, wheelbase) * 0.85 * softness;

    // Unbanked curve: the body's centre of mass sits above the roll centre, so
    // it leans *outward* under lateral acceleration, like a car.
    const aLat = speed * speed * curvature;
    const roll = crossLevel(distance) + aLat * rollGain;

    out.bounce = h;
    out.roll = roll;
    out.pitch = pitch;
    out.lateral = lateral(distance) + aLat * 0.012;
    out.lateralAccel = aLat;
    return out;
  }

  return { evaluate };
}
