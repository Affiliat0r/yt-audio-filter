/**
 * The locomotive's dimension table, in metres, in the vehicle frame:
 * +Z forward, +Y up, y = 0 at the rail head, x = 0 on the centreline.
 *
 * Kept in one file because the motion gear and the body have to agree on it to
 * the millimetre — a coupling rod that misses its crank pin by a centimetre is
 * the most obvious possible modelling error.
 *
 * The prototype is an 0-6-0 saddle tank: three coupled axles, outside
 * cylinders, outside Walschaerts valve gear. Substantial enough to carry the
 * detail, short enough to sit inside a curve without looking rigid.
 */

export const LOCO = {
  // --- running gear -------------------------------------------------------
  halfGauge: 0.8,
  driverRadius: 0.62,
  driverWidth: 0.2,
  axleZ: [1.35, 0.0, -1.35],
  mainAxle: 1, // index into axleZ that the connecting rod drives
  crankRadius: 0.3,

  couplingRodX: 0.97,
  mainRodX: 1.14,
  valveX: 1.31,
  /** Crank-pin reach from the wheel centre, outboard. */
  pinStart: 0.1,
  pinLengthCoupled: 0.145,
  pinLengthMain: 0.46,

  // --- cylinders and crosshead -------------------------------------------
  cylY: 0.62,
  cylRadius: 0.3,
  cylBackZ: 2.62,
  cylFrontZ: 3.44,
  mainRodLength: 2.15,
  pistonRodLength: 0.85,
  slideBarBackZ: 1.66,
  slideBarFrontZ: 2.62,

  // --- Walschaerts valve gear --------------------------------------------
  returnCrankRadius: 0.155,
  returnCrankPhase: 2.62, // radians ahead of the main crank
  eccRodLength: 1.3,
  linkPivotZ: 1.28,
  linkPivotY: 0.95,
  linkArmLower: 0.34,
  linkArmDie: 0.3,
  radiusRodLength: 1.0,
  combLeverLength: 0.62,
  combLeverValveFrac: 0.62,
  /** Combination-lever bottom pin, relative to the crosshead centre. */
  unionPinDZ: -0.3,
  unionPinDY: -0.34,
  /** Union-link anchor on the crosshead itself. */
  crossLugDZ: -0.02,
  crossLugDY: -0.1,
  valveRodLength: 0.92,
  valveY: 1.12,

  // --- body ---------------------------------------------------------------
  frameX: 0.62,
  frameY0: 0.26,
  frameY1: 0.74,
  frameZ0: -3.15,
  frameZ1: 3.6,
  plateY: 1.3,
  plateX: 1.2,

  boilerY: 2.0,
  boilerR: 0.68,
  boilerZ0: -0.95,
  boilerZ1: 2.5,
  smokeboxR: 0.74,
  smokeboxZ0: 2.44,
  smokeboxZ1: 3.46,

  tankZ0: -0.88,
  tankZ1: 2.42,
  tankHalfWidth: 0.98,
  tankTopY: 2.72,
  tankSideY: 1.33,

  fireboxZ0: -2.4,
  fireboxZ1: -0.9,
  fireboxHalfWidth: 0.78,
  fireboxTopY: 2.78,

  cabFrontZ: -1.5,
  cabBackZ: -3.05,
  cabHalfWidth: 0.99,
  cabFloorY: 1.3,
  cabRoofY: 3.05,

  bunkerZ0: -3.6,
  bunkerZ1: -3.02,
  bunkerTopY: 2.15,

  chimneyZ: 3.0,
  chimneyBaseY: 2.6,
  chimneyMouthY: 3.52,

  domeZ: -1.12,
  bufferBeamZ: 3.62,
  bufferY: 0.98,

  // Two axle points define the vehicle's pose on the spline.
  halfWheelbase: 1.35,
  /** Total length used for consist spacing. */
  length: 8.0,
};

/** Standard four-wheel vehicle chassis shared by the coaches and wagons. */
export const CARRIAGE = {
  halfGauge: 0.8,
  wheelRadius: 0.44,
  wheelWidth: 0.16,
  axleZ: [-1.7, 1.7],
  halfWheelbase: 1.7,
  frameX: 0.66,
  /** Solebar centre height; the buffers line up with the locomotive's. */
  soleY: 0.86,
  deckY: 1.06,
  halfWidth: 1.02,
  bufferY: 0.98,
  length: 6.4,
};
