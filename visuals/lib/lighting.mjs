/**
 * lib/lighting.mjs — Part B, look pipeline: sun, sky, image-based lighting,
 * cascaded shadows and aerial perspective.
 *
 * Everything here is driven by ONE sun vector, derived from
 * `sunElevation` / `sunAzimuth`. The physical sky, the directional light
 * colour, the fog colour and the environment rotation all read from it, so
 * they cannot disagree with each other. Disagreement — a warm key light under
 * a blue sky, fog that is a different grey from the horizon — is the loudest
 * "this is CG" tell after unbevelled edges.
 *
 * What each piece is actually for:
 *
 *   Environment (IBL). A PMREM-prefiltered environment map is what makes a
 *   PBR material read as real. The directional light gives you one highlight
 *   and one shadow; the environment gives you every other photon in the scene
 *   — the sky reflected in paint, the ground bounce under a chassis, the soft
 *   wrap around a rounded edge. If only one of the two can be right, make it
 *   this one.
 *
 *   Sky. `objects/Sky.js` is the Preetham analytic daylight model. It is
 *   baked once into a cube map and used as `scene.background`, so it costs
 *   nothing per frame, never clips against the camera far plane, and never
 *   confuses the depth/normal prepasses that GTAO and DOF run.
 *
 *   CSM. A single shadow map stretched over a large outdoor scene is the
 *   usual cause of blocky or vanishing shadows. Cascades give the near ground
 *   a dense map and the distance a coarse one.
 *
 *   Aerial perspective. Distance haze whose colour is *sampled from the sky
 *   itself*, in the direction the camera is generally looking, in linear
 *   radiance. Distant geometry then melts into the horizon at exactly the
 *   right value, which is the cue that sells scale.
 *
 * Determinism: no clocks, no `Math.random()`. `update(camera, t)` derives any
 * sun motion from `t` alone. The expensive work (HDRI decode, PMREM, sky
 * bake) happens once in `createLighting`.
 */

import * as THREE from 'three';
import { Sky } from '../node_modules/three/examples/jsm/objects/Sky.js';
import { RGBELoader } from '../node_modules/three/examples/jsm/loaders/RGBELoader.js';
import { CSM } from '../node_modules/three/examples/jsm/csm/CSM.js';

const DEG = Math.PI / 180;

/** Materials CSM can legally patch. Anything else is unlit or hand-written. */
function isLitMaterial(m) {
  return !!(
    m &&
    (m.isMeshStandardMaterial ||
      m.isMeshPhysicalMaterial ||
      m.isMeshLambertMaterial ||
      m.isMeshPhongMaterial ||
      m.isMeshToonMaterial)
  );
}

/**
 * Unit vector pointing from the scene *towards* the sun.
 * Elevation is degrees above the horizon; azimuth is degrees clockwise, with
 * 0 along +Z, matching the way `Sky` expects its sun position.
 */
export function sunVector(elevationDeg, azimuthDeg, target = new THREE.Vector3()) {
  const phi = (90 - elevationDeg) * DEG;
  const theta = azimuthDeg * DEG;
  return target.setFromSphericalCoords(1, phi, theta);
}

/**
 * Turn whatever `hdriPath()` handed back into something the browser can load.
 * Part A's contract says "local file path"; over file:// that has to become a
 * `file:///C:/...` URL, and relative paths have to resolve against the page.
 */
function toLoadableURL(p) {
  if (!p) return null;
  const s = String(p).replace(/\\/g, '/');
  if (/^[a-z]+:\/\//i.test(s) || s.startsWith('data:') || s.startsWith('blob:')) return s;
  if (/^[A-Za-z]:\//.test(s)) return 'file:///' + s;
  if (s.startsWith('/')) return 'file://' + s;
  return s;
}

/** Part A builds assets.mjs in parallel; never hard-fail if it is not there yet. */
async function resolveHdriURL(hdri, res) {
  if (!hdri) return null;
  const candidates = Array.isArray(hdri) ? hdri : [hdri];
  let hdriPath = null;
  try {
    ({ hdriPath } = await import('./assets.mjs'));
  } catch {
    hdriPath = null;
  }
  const urls = [];
  for (const c of candidates) {
    if (typeof c !== 'string') continue;
    // An explicit URL or path wins over the asset registry.
    if (/^[a-z]+:\/\//i.test(c) || c.endsWith('.hdr') || c.includes('/')) {
      urls.push(toLoadableURL(c));
    } else if (typeof hdriPath === 'function') {
      try {
        urls.push(toLoadableURL(hdriPath(c, res)));
      } catch {
        /* slug not in the manifest — fine, try the next one */
      }
    }
  }
  return urls.filter(Boolean);
}

function loadRGBE(url, dataType) {
  return new Promise((resolve, reject) => {
    const loader = new RGBELoader();
    loader.setDataType(dataType);
    loader.load(url, resolve, undefined, reject);
  });
}

/**
 * Read two things out of an equirectangular HDRI: where it is brightest, and
 * what it averages.
 *
 * The azimuth is used to spin the HDRI so its baked-in sun lines up with ours.
 * Without that, the environment's specular highlight sits somewhere other than
 * where the directional light casts its shadow, which is subtly, unplaceably
 * wrong on every glossy surface in the frame.
 *
 * The mean is used to tint the ground-bounce disc, below.
 *
 * @returns {{azimuth:number|null, mean:[number,number,number]}|null}
 */
function analyzeEquirect(texture) {
  const img = texture.image;
  const data = img && img.data;
  if (!data || !data.length || typeof data[0] !== 'number') return null;
  const w = img.width;
  const h = img.height;
  const rowStep = Math.max(1, Math.floor(h / 128));
  const colStep = Math.max(1, Math.floor(w / 512));
  let best = -1;
  let bestCol = 0;
  let sr = 0;
  let sg = 0;
  let sb = 0;
  let n = 0;
  // Rows are stored top-first. Skip the bottom third when hunting for the sun:
  // ground detail is bright in some HDRIs but is not the key light.
  const sunRowLimit = Math.floor(h * 0.66);
  for (let y = 0; y < h; y += rowStep) {
    for (let x = 0; x < w; x += colStep) {
      const i = (y * w + x) * 4;
      const r = data[i];
      const g = data[i + 1];
      const b = data[i + 2];
      sr += r;
      sg += g;
      sb += b;
      n++;
      if (y < sunRowLimit) {
        const lum = r * 0.2126 + g * 0.7152 + b * 0.0722;
        if (lum > best) {
          best = lum;
          bestCol = x;
        }
      }
    }
  }
  if (!n) return null;
  const u = (bestCol + 0.5) / w;
  return {
    azimuth: best > 0 ? (u - 0.5) * Math.PI * 2 : null,
    mean: [sr / n, sg / n, sb / n],
  };
}

/**
 * @typedef {Object} LightingOptions
 * @property {string|string[]|null} [hdri=null] Poly Haven slug(s) resolved through `assets.mjs`,
 *   an explicit `.hdr` URL, or null for sky-only IBL. An array is tried in order.
 * @property {string} [hdriRes='2k']
 * @property {number} [hdriSunClamp=6]          Radiance ceiling as a multiple of the HDRI's mean
 *   luminance. This is what removes the HDRI's baked sun so the directional light is not a second one.
 * @property {'auto'|'hdri'|'sky'} [environment='auto']  Where the IBL comes from.
 * @property {'sky'|'hdri'} [background='sky']  What the camera actually sees behind the world.
 * @property {number} [sunElevation=24]         Degrees above the horizon.
 * @property {number} [sunAzimuth=145]          Degrees; 0 is along +Z.
 * @property {number} [sunIntensity]            Leave unset. Derived from the measured sky radiance
 *   via `sunSkyRatio`, which keeps the sun/sky balance right at any sun angle.
 * @property {number} [sunSkyRatio=36]          Sun irradiance / mean sky radiance. Higher is a
 *   clearer, harsher day; lower is hazier with softer shadows.
 * @property {boolean} [autoLevel=true]         Normalise absolute brightness so
 *   `renderer.toneMappingExposure = 1` is correct exposure.
 * @property {THREE.Camera} [camera]            Needed by CSM; may instead arrive via `update()`.
 * @property {number} [cascades=4]
 * @property {number} [shadowMapSize=2048]
 * @property {number} [shadowMaxFar=180]        Distance the cascades cover.
 * @property {number} [shadowSoftness=0]        >0 switches the renderer to PCF with this radius,
 *   which widens the penumbra. PCFSoft (the default) ignores `shadow.radius` entirely.
 * @property {number} [turbidity=2.4]           Preetham haze. Higher = whiter, hazier sky.
 * @property {number} [rayleigh=2.7]            Higher = deeper blue.
 * @property {number} [mieCoefficient=0.005]
 * @property {number} [mieDirectionalG=0.8]
 * @property {boolean} [fog=true]
 * @property {number} [fogVisibility]           Distance at which aerial perspective reaches 50%.
 *   Defaults to 1.5 x shadowMaxFar. Easier to reason about than raw density.
 * @property {number} [fogDensity]              Raw FogExp2 density; overrides `fogVisibility`.
 * @property {number} [fogStrength=0.7]         Scales the sampled haze colour.
 * @property {number} [envIntensity=1]          Multiplier on the (already normalised) environment.
 * @property {number} [backgroundIntensity=1]
 * @property {number|string} [groundAlbedo=0x6f6656]  Colour of the ground-bounce disc baked into the IBL.
 * @property {number} [groundBounce=0.6]        How much of that bounce to include.
 * @property {number} [ambient=0]               Flat AmbientLight. Leave at 0; IBL is strictly better.
 * @property {boolean} [autoRegisterMaterials=true]  Sweep the scene each frame for materials CSM
 *   has not patched yet. Turning this off means anything added after `createLighting` is lit by
 *   every cascade at once.
 * @property {{elevationPerSecond?:number, azimuthPerSecond?:number}|null} [sunMotion=null]
 */

/**
 * @param {THREE.Scene} scene
 * @param {THREE.WebGLRenderer} renderer
 * @param {LightingOptions} [opts]
 * @returns {Promise<{
 *   sun: THREE.DirectionalLight,
 *   sunDirection: THREE.Vector3,
 *   sunColor: THREE.Color,
 *   sunIntensity: number,
 *   csm: CSM,
 *   envMap: THREE.Texture,
 *   envSource: 'hdri'|'sky',
 *   environmentRotationY: number,
 *   skyRadiance: number,
 *   levelScale: number,
 *   envNormalise: number,
 *   envStats: {mean:number[], meanLuma:number, clampedMeanLuma:number, ceiling:number}|null,
 *   sky: Sky,
 *   skyScene: THREE.Scene,
 *   fog: THREE.FogExp2|null,
 *   registerMaterial: (m: THREE.Material) => void,
 *   registerScene: (root: THREE.Object3D) => void,
 *   setSun: (elevationDeg: number, azimuthDeg: number) => void,
 *   setFog: (o: {density?: number, color?: THREE.Color}) => void,
 *   update: (camera: THREE.Camera, t: number) => void,
 *   dispose: () => void,
 * }>}
 */
export async function createLighting(scene, renderer, opts = {}) {
  const {
    hdri = null,
    hdriRes = '2k',
    hdriSunClamp = 6,
    environment = 'auto',
    background = 'sky',
    sunElevation = 24,
    sunAzimuth = 145,
    camera = null,
    cascades = 4,
    shadowMapSize = 2048,
    shadowMaxFar = 180,
    shadowSoftness = 0,
    turbidity = 2.4,
    rayleigh = 2.7,
    mieCoefficient = 0.005,
    mieDirectionalG = 0.8,
    fog = true,
    fogDensity = null,
    fogVisibility = null,
    fogStrength = 0.7,
    envIntensity = 1.0,
    backgroundIntensity = 1.0,
    groundAlbedo = 0x6f6656,
    groundBounce = 0.6,
    ambient = 0,
    autoRegisterMaterials = true,
    sunMotion = null,
  } = opts;

  const state = {
    elevation: sunElevation,
    azimuth: sunAzimuth,
  };

  const sunDirection = sunVector(state.elevation, state.azimuth);

  // ---------------------------------------------------------------- sky ---
  // Lives in its own scene. It is baked to a cube map for the background and
  // to a PMREM for the fallback environment; the mesh itself is never added
  // to the caller's scene, which keeps it out of the depth and normal
  // prepasses that GTAO and the DOF pass run.
  const skyScene = new THREE.Scene();
  const sky = new Sky();
  sky.scale.setScalar(100);
  const skyU = sky.material.uniforms;
  skyU.turbidity.value = turbidity;
  skyU.rayleigh.value = rayleigh;
  skyU.mieCoefficient.value = mieCoefficient;
  skyU.mieDirectionalG.value = mieDirectionalG;
  skyU.sunPosition.value.copy(sunDirection);
  skyScene.add(sky);

  // --- sampling the sky for real radiance values -------------------------
  // Renders a tiny float target looking in a given direction and averages it.
  // Costs a few hundred microseconds and gives us the sun colour, the fog
  // colour and the ground-bounce colour without a single hand-tuned constant.
  let sampleRT = null;
  const sampleCam = new THREE.PerspectiveCamera(30, 1, 0.1, 1000);
  const sampleTarget = new THREE.Vector3();

  function sampleSky(direction, fovDeg = 30, size = 16) {
    try {
      if (!sampleRT) {
        sampleRT = new THREE.WebGLRenderTarget(size, size, {
          type: THREE.FloatType,
          format: THREE.RGBAFormat,
          depthBuffer: false,
          stencilBuffer: false,
        });
      }
      if (sampleRT.width !== size) sampleRT.setSize(size, size);
      sampleCam.fov = fovDeg;
      sampleCam.position.set(0, 0, 0);
      sampleCam.lookAt(sampleTarget.copy(direction).multiplyScalar(10));
      sampleCam.updateProjectionMatrix();
      sampleCam.updateMatrixWorld(true);

      const prevTarget = renderer.getRenderTarget();
      renderer.setRenderTarget(sampleRT);
      renderer.clear(true, true, false);
      renderer.render(skyScene, sampleCam);
      const buf = new Float32Array(size * size * 4);
      renderer.readRenderTargetPixels(sampleRT, 0, 0, size, size, buf);
      renderer.setRenderTarget(prevTarget);

      let r = 0;
      let g = 0;
      let b = 0;
      const n = size * size;
      for (let i = 0; i < n; i++) {
        r += buf[i * 4];
        g += buf[i * 4 + 1];
        b += buf[i * 4 + 2];
      }
      return new THREE.Color().setRGB(r / n, g / n, b / n, THREE.LinearSRGBColorSpace);
    } catch {
      // Float render targets are unreadable on some drivers. Fall back to a
      // daylight-ish neutral so nothing downstream sees a null.
      return new THREE.Color().setRGB(0.55, 0.66, 0.85, THREE.LinearSRGBColorSpace);
    }
  }

  /**
   * Mean sky radiance over the upper hemisphere, cosine-weighted — i.e. the
   * sky's contribution to the irradiance landing on flat ground, divided by
   * pi.
   *
   * This is the number the whole exposure hangs off. Sample directions inside
   * 32 degrees of the sun are skipped: the solar disc is four orders of
   * magnitude brighter than the sky around it and one tap that clips it would
   * swamp the average.
   */
  function measureSkyRadiance() {
    const samples = [];
    samples.push({ dir: new THREE.Vector3(0, 1, 0), weight: 1.0, fov: 50 });
    for (let i = 0; i < 8; i++) {
      const az = i * 45;
      samples.push({ dir: sunVector(50, az), weight: Math.cos(50 * DEG), fov: 35 });
      samples.push({ dir: sunVector(15, az), weight: Math.cos(15 * DEG), fov: 25 });
    }
    let total = 0;
    let weightSum = 0;
    for (const s of samples) {
      if (s.dir.dot(sunDirection) > Math.cos(32 * DEG)) continue;
      const c = sampleSky(s.dir, s.fov, 8);
      total += (c.r * 0.2126 + c.g * 0.7152 + c.b * 0.0722) * s.weight;
      weightSum += s.weight;
    }
    return weightSum > 0 ? total / weightSum : 1.0;
  }

  /** Sun tint: sample the solar disc, then normalise to unit luminance. */
  function deriveSunColor() {
    const raw = sampleSky(sunDirection, 1.2, 8);
    const lum = Math.max(1e-6, raw.r * 0.2126 + raw.g * 0.7152 + raw.b * 0.0722);
    const c = new THREE.Color().setRGB(
      raw.r / lum,
      raw.g / lum,
      raw.b / lum,
      THREE.LinearSRGBColorSpace
    );
    // Preetham's disc is aggressively saturated near the horizon. Pull it
    // 35% back towards white: a fully orange key light reads as a sunset
    // filter rather than as sunlight.
    return c.lerp(new THREE.Color(1, 1, 1), 0.35);
  }

  /**
   * Aerial-perspective colour, sampled just above the horizon.
   *
   * Weighted 60/40 towards the sun side, because haze scatters forward: the
   * distance is measurably brighter looking into the sun than away from it,
   * and matching that is most of what makes distance read as distance. The
   * camera looks off-sun more often than at it, hence the bias rather than a
   * straight sun-direction sample.
   */
  function sampleHorizonHaze() {
    const nearSun = sampleSky(sunVector(8, state.azimuth), 25, 16);
    const awaySun = sampleSky(sunVector(8, state.azimuth + 180), 25, 16);
    return new THREE.Color().setRGB(
      (nearSun.r * 0.6 + awaySun.r * 0.4) * fogStrength,
      (nearSun.g * 0.6 + awaySun.g * 0.4) * fogStrength,
      (nearSun.b * 0.6 + awaySun.b * 0.4) * fogStrength,
      THREE.LinearSRGBColorSpace
    );
  }

  function deriveFogColor() {
    return sampleHorizonHaze().multiplyScalar(levelScale);
  }

  const derivedSunColor = deriveSunColor();
  const skyRadiance = measureSkyRadiance();

  /**
   * Sun intensity, derived rather than dialled.
   *
   * `DirectionalLight.intensity` is irradiance on a surface facing the light;
   * the environment contributes `PI * meanSkyRadiance` to a flat surface. So
   * the number that decides whether a render looks like noon or looks like an
   * overcast afternoon is not either value on its own, it is their ratio, and
   * a hand-picked absolute intensity is wrong the moment anyone changes the
   * sky. On a clear day the sun outruns the sky by roughly 50:1 in these
   * units; hazier air lowers it. Deriving it means the sun, the sky, the fog
   * and the reflections stay locked together whatever the sun angle is.
   */
  const sunSkyRatio = opts.sunSkyRatio ?? 36;

  /**
   * Absolute level normalisation.
   *
   * The Preetham sky hands us radiance in its own units, and those units
   * change with sun elevation and turbidity. Left alone, every change to the
   * sun angle would need a matching change to `toneMappingExposure` or the
   * frame would blow out. Scaling the sun, the sky, the environment and the
   * fog by one factor derived from the measured sky radiance keeps their
   * ratios — which are the physics — while pinning the absolute level, so
   * `toneMappingExposure = 1` means "correctly exposed" and anything a caller
   * sets from there is an artistic stop up or down. It is what a camera's
   * meter does.
   *
   * The constant is empirical: it is the value that puts a sunlit surface of
   * ~18% albedo at middle grey through the ACES curve.
   */
  const levelScale = opts.autoLevel === false ? 1 : 0.185 / Math.max(1e-4, skyRadiance);

  const resolvedSunIntensity =
    (opts.sunIntensity != null ? opts.sunIntensity : sunSkyRatio * skyRadiance) * levelScale;

  // --------------------------------------------------- background bake ---
  const cubeRT = new THREE.WebGLCubeRenderTarget(1024, {
    type: THREE.HalfFloatType,
    format: THREE.RGBAFormat,
    generateMipmaps: false,
    minFilter: THREE.LinearFilter,
    magFilter: THREE.LinearFilter,
  });
  const cubeCam = new THREE.CubeCamera(0.1, 1000, cubeRT);

  /**
   * Bake the sky into a cube map, with a haze-coloured floor under it.
   *
   * The floor is not decoration. three's Preetham sky clamps the zenith angle
   * at the horizon, so every direction *below* the horizon returns the
   * horizon colour — which, looking towards the sun, is nearly white. Any gap
   * where the terrain does not reach the horizon therefore reads as a band of
   * blown white sky underneath the world. Filling the lower hemisphere with
   * the aerial-perspective colour instead means those gaps read as infinite
   * distance, which is what they are, and it matches the fog exactly because
   * both come from the same sample.
   */
  let horizonHaze = sampleHorizonHaze();

  function bakeBackground() {
    const floorMat = new THREE.MeshBasicMaterial({
      color: horizonHaze,
      side: THREE.DoubleSide,
      toneMapped: false,
      fog: false,
    });
    // Dropped a full unit below the cube camera and made very wide: any
    // shallower and the disc falls inside the camera's near plane for steep
    // downward directions, which punches the blown-white sky straight back
    // through the hole this exists to plug.
    const floor = new THREE.Mesh(new THREE.CircleGeometry(600, 64), floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -1.0;
    skyScene.add(floor);
    cubeCam.update(renderer, skyScene);
    skyScene.remove(floor);
    floor.geometry.dispose();
    floorMat.dispose();
    if (background !== 'hdri') scene.background = cubeRT.texture;
    scene.backgroundIntensity = backgroundIntensity * levelScale;
  }
  bakeBackground();

  // --------------------------------------------------------- environment --
  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();

  /**
   * A matte disc standing in for the ground, tinted by whatever light is
   * falling on it.
   *
   * This matters far more than it looks like it should. Every sky-only
   * environment — our Preetham bake and every Poly Haven "puresky" HDRI
   * alike — lights the world from above and leaves the underside of
   * everything black. Real outdoor scenes get a large warm bounce off the
   * ground, and putting it back is a good part of the difference between
   * "3D model" and "photograph".
   */
  function makeBounceDisc(incident) {
    const albedo = new THREE.Color(groundAlbedo).convertSRGBToLinear();
    const mat = new THREE.MeshBasicMaterial({
      side: THREE.DoubleSide,
      toneMapped: false,
      fog: false,
    });
    mat.color.setRGB(
      albedo.r * incident[0] * groundBounce,
      albedo.g * incident[1] * groundBounce,
      albedo.b * incident[2] * groundBounce,
      THREE.LinearSRGBColorSpace
    );
    const disc = new THREE.Mesh(new THREE.CircleGeometry(90, 48), mat);
    disc.rotation.x = -Math.PI / 2;
    disc.position.y = -0.05;
    return disc;
  }

  /** Procedural IBL: the physical sky plus the bounce disc. */
  function buildSkyEnvironment() {
    const horizon = sampleSky(sunVector(6, state.azimuth + 90), 40, 16);
    const disc = makeBounceDisc([horizon.r, horizon.g, horizon.b]);
    skyScene.add(disc);
    const rt = pmrem.fromScene(skyScene, 0.02, 0.1, 500);
    skyScene.remove(disc);
    disc.geometry.dispose();
    disc.material.dispose();
    return rt;
  }

  /**
   * HDRI IBL, baked through a scene rather than straight from the
   * equirectangular texture, for two reasons: it lets us rotate the HDRI so
   * its sun agrees with ours, and it lets us add the same bounce disc.
   */
  function buildHdriEnvironment(texture, rotationY, mean) {
    const envScene = new THREE.Scene();
    envScene.background = texture;
    envScene.backgroundRotation.y = rotationY;
    const disc = makeBounceDisc(mean);
    envScene.add(disc);
    const rt = pmrem.fromScene(envScene, 0.0, 0.1, 500);
    disc.geometry.dispose();
    disc.material.dispose();
    return rt;
  }

  /**
   * Remove the sun from a captured HDRI, in place.
   *
   * THE double-counting trap. Every Poly Haven "puresky" HDRI contains the
   * actual solar disc at four-figure radiance, and that disc is most of the
   * total irradiance in the file. Light the scene with the untouched HDRI
   * *and* a directional sun and you have lit it with two suns: shadows go
   * pale, contrast collapses, and the render looks like an overcast day no
   * matter how you tune the key light. That is exactly the failure this
   * function exists to prevent.
   *
   * Clamping the top end leaves the sky, the clouds and the horizon gradient
   * intact — all the parts we actually want the environment for — while the
   * directional light supplies the sun. Returns the mean of what is left,
   * which is a decent estimate of sky-only radiance.
   */
  function stripSun(texture, clampMultiple) {
    const img = texture.image;
    const data = img && img.data;
    if (!data || typeof data[0] !== 'number') return null;

    let sum = 0;
    let n = 0;
    for (let i = 0; i < data.length; i += 4) {
      sum += data[i] * 0.2126 + data[i + 1] * 0.7152 + data[i + 2] * 0.0722;
      n++;
    }
    const meanLuma = sum / Math.max(1, n);
    const ceiling = Math.max(1e-3, meanLuma * clampMultiple);

    let sr = 0;
    let sg = 0;
    let sb = 0;
    for (let i = 0; i < data.length; i += 4) {
      const luma = data[i] * 0.2126 + data[i + 1] * 0.7152 + data[i + 2] * 0.0722;
      if (luma > ceiling) {
        // Scale rather than hard-clip, so the sun's colour survives as a
        // bright patch of sky instead of turning into a flat white disc.
        const k = ceiling / luma;
        data[i] *= k;
        data[i + 1] *= k;
        data[i + 2] *= k;
      }
      sr += data[i];
      sg += data[i + 1];
      sb += data[i + 2];
    }
    texture.needsUpdate = true;
    const mean = [sr / n, sg / n, sb / n];
    return {
      mean,
      meanLuma,
      clampedMeanLuma: mean[0] * 0.2126 + mean[1] * 0.7152 + mean[2] * 0.0722,
      ceiling,
    };
  }

  let envRT = null;
  let envMap = null;
  let envSource = 'sky';
  let hdriTexture = null;
  let environmentRotationY = 0;
  let envStats = null;

  const wantHdri = environment !== 'sky' && hdri;
  if (wantHdri) {
    const urls = (await resolveHdriURL(hdri, hdriRes)) || [];
    for (const url of urls) {
      try {
        hdriTexture = await loadRGBE(url, THREE.FloatType);
        hdriTexture.mapping = THREE.EquirectangularReflectionMapping;
        const analysis = analyzeEquirect(hdriTexture);
        if (analysis && analysis.azimuth !== null) {
          const sunAz = Math.atan2(sunDirection.z, sunDirection.x);
          environmentRotationY = sunAz - analysis.azimuth;
        }
        envStats = stripSun(hdriTexture, hdriSunClamp);
        envRT = buildHdriEnvironment(
          hdriTexture,
          environmentRotationY,
          (envStats && envStats.mean) || (analysis ? analysis.mean : [1, 1, 1])
        );
        envMap = envRT.texture;
        envSource = 'hdri';
        break;
      } catch {
        hdriTexture = null;
      }
    }
  }

  if (!envMap) {
    if (environment === 'hdri') {
      // Asked for an HDRI and there is not one on disk. Not fatal — the
      // procedural sky environment is a genuinely good substitute — but say so.
      console.warn('createLighting: no HDRI available, falling back to procedural sky IBL');
    }
    envRT = buildSkyEnvironment();
    envMap = envRT.texture;
    envSource = 'sky';
  }

  scene.environment = envMap;
  // A captured HDRI has its own absolute exposure, which has nothing to do
  // with our sky's. Renormalising it to the same mean radiance means the
  // reflection in a chrome sphere sits at the same brightness as the sky
  // behind it, and one `envIntensity` knob keeps meaning the same thing
  // whichever environment is in use.
  const envNormalise =
    envSource === 'hdri' && envStats && envStats.clampedMeanLuma > 1e-4
      ? skyRadiance / envStats.clampedMeanLuma
      : 1;
  scene.environmentIntensity = envIntensity * envNormalise * levelScale;
  // The rotation is baked into the PMREM above, so the scene's own
  // environment rotation stays at identity.
  if (scene.environmentRotation) scene.environmentRotation.set(0, 0, 0);

  // The visible sky stays the Preetham bake by default even when the IBL
  // comes from an HDRI. Rotating an HDRI lines its sun up in azimuth but not
  // in elevation, and a visible sun disc that does not match the direction
  // the shadows fall is worse than no sun disc at all. `background: 'hdri'`
  // overrides this when the HDRI genuinely is the better picture.
  if (background === 'hdri' && hdriTexture) {
    scene.background = hdriTexture;
    if (scene.backgroundRotation) scene.backgroundRotation.y = environmentRotationY;
  }

  // ---------------------------------------------------------------- fog ---
  let sceneFog = null;
  if (fog) {
    // FogExp2 reaches 50% at `0.8326 / density`. Expressing the default as a
    // half-fade distance rather than a raw density is the difference between
    // a knob anyone can reason about and one that has to be found by trial:
    // the default puts the halfway point at 1.5x the shadow range, which
    // leaves near geometry clean and still melts the far hills into the sky.
    const halfFade = fogVisibility ?? shadowMaxFar * 1.5;
    const density = fogDensity == null ? 0.8326 / Math.max(1, halfFade) : fogDensity;
    sceneFog = new THREE.FogExp2(0x000000, density);
    sceneFog.color.copy(deriveFogColor());
    scene.fog = sceneFog;
  }

  // ---------------------------------------------------------------- csm ---
  // CSM needs a camera up front. If the caller has not built theirs yet, a
  // stand-in keeps construction legal and update() swaps in the real one.
  const placeholderCamera = new THREE.PerspectiveCamera(50, 16 / 9, 0.1, shadowMaxFar * 2);
  const csmCamera = camera || placeholderCamera;

  const csm = new CSM({
    maxFar: shadowMaxFar,
    cascades,
    mode: 'practical',
    parent: scene,
    shadowMapSize,
    lightDirection: sunDirection.clone().negate().normalize(),
    camera: csmCamera,
    lightIntensity: resolvedSunIntensity,
    lightNear: 1,
    lightFar: Math.max(600, shadowMaxFar * 4),
    lightMargin: Math.max(120, shadowMaxFar),
  });
  csm.fade = true;

  for (const light of csm.lights) {
    light.color.copy(derivedSunColor);
    light.intensity = resolvedSunIntensity;
  }

  if (shadowSoftness > 0) {
    // PCFSoft ignores `shadow.radius`; plain PCF honours it and gives a
    // visibly broader, more sunlike penumbra for five extra taps.
    renderer.shadowMap.type = THREE.PCFShadowMap;
    for (const light of csm.lights) light.shadow.radius = shadowSoftness;
  }

  /**
   * Depth bias per cascade, in world units.
   *
   * `shadow.bias` is in normalised depth and has to grow with cascade size;
   * `normalBias` is in world units and scales with the cascade's texel
   * footprint, which is exactly the quantity that causes acne. Deriving it
   * from the shadow camera's own extent means it stays correct when the
   * cascade splits move.
   */
  function tuneShadowBias() {
    csm.lights.forEach((light, i) => {
      const cam = light.shadow.camera;
      const texelWorld = Math.max(
        (cam.right - cam.left) / shadowMapSize,
        (cam.top - cam.bottom) / shadowMapSize
      );
      light.shadow.normalBias = Math.max(0.02, texelWorld * 1.6);
      light.shadow.bias = -0.00005 * (1 + i);
      light.shadow.blurSamples = 16;
    });
  }

  if (ambient > 0) {
    const amb = new THREE.AmbientLight(0xffffff, ambient);
    scene.add(amb);
  }

  // -------------------------------------------- material auto-registration --
  // CSM patches `lights_fragment_begin` so that only the cascade covering a
  // fragment's depth contributes. A material that is NOT patched sees all
  // `cascades` directional lights at full strength and comes out four times
  // too bright — the single most confusing failure mode in this file. So we
  // sweep the scene and register anything new.
  const registered = new WeakSet();

  function registerMaterial(material) {
    if (!isLitMaterial(material) || registered.has(material)) return;
    registered.add(material);
    // Part A's materials may already carry an onBeforeCompile of their own.
    // CSM assigns over the top of it, so preserve and chain.
    const previous = material.onBeforeCompile;
    csm.setupMaterial(material);
    const csmHook = material.onBeforeCompile;
    if (previous && previous !== csmHook) {
      material.onBeforeCompile = function chained(shader, r) {
        csmHook.call(this, shader, r);
        previous.call(this, shader, r);
      };
    }
    material.needsUpdate = true;
  }

  function registerScene(root) {
    root.traverse((obj) => {
      const m = obj.material;
      if (!m) return;
      if (Array.isArray(m)) m.forEach(registerMaterial);
      else registerMaterial(m);
    });
  }

  registerScene(scene);
  csm.updateFrustums();
  tuneShadowBias();

  // -------------------------------------------------------------- update --
  let lastCamera = csmCamera;
  let lastFrustumKey = '';

  function frustumKey(cam) {
    return `${cam.fov}|${cam.aspect}|${cam.near}|${cam.far}|${cam.zoom}`;
  }

  function applySun() {
    sunVector(state.elevation, state.azimuth, sunDirection);
    skyU.sunPosition.value.copy(sunDirection);
    csm.lightDirection.copy(sunDirection).negate().normalize();
    const c = deriveSunColor();
    for (const light of csm.lights) light.color.copy(c);
    derivedSunColor.copy(c);
    horizonHaze = sampleHorizonHaze();
    bakeBackground();
    if (sceneFog) sceneFog.color.copy(deriveFogColor());
  }

  /**
   * Per-frame. `t` is the movie time in seconds and is the ONLY time source —
   * no clock, no frame counter, so frame 150 is frame 150 whatever order the
   * frames were rendered in.
   */
  function update(cam, t = 0) {
    const activeCamera = cam || lastCamera;

    if (sunMotion) {
      const e = state.elevation;
      const a = state.azimuth;
      state.elevation = sunElevation + (sunMotion.elevationPerSecond || 0) * t;
      state.azimuth = sunAzimuth + (sunMotion.azimuthPerSecond || 0) * t;
      if (state.elevation !== e || state.azimuth !== a) applySun();
    }

    if (activeCamera !== lastCamera) {
      csm.camera = activeCamera;
      lastCamera = activeCamera;
      lastFrustumKey = '';
    }

    activeCamera.updateMatrixWorld();

    const key = frustumKey(activeCamera);
    if (key !== lastFrustumKey) {
      lastFrustumKey = key;
      csm.updateFrustums();
      tuneShadowBias();
    }

    if (autoRegisterMaterials) registerScene(scene);

    csm.update();
  }

  function setSun(elevationDeg, azimuthDeg) {
    state.elevation = elevationDeg;
    state.azimuth = azimuthDeg;
    applySun();
  }

  function setFog({ density, color } = {}) {
    if (!sceneFog) return;
    if (density != null) sceneFog.density = density;
    if (color) sceneFog.color.copy(color);
  }

  function dispose() {
    csm.dispose();
    csm.remove();
    if (envRT) envRT.dispose();
    if (hdriTexture) hdriTexture.dispose();
    cubeRT.dispose();
    if (sampleRT) sampleRT.dispose();
    pmrem.dispose();
    sky.geometry.dispose();
    sky.material.dispose();
  }

  return {
    sun: csm.lights[0],
    sunDirection,
    sunColor: derivedSunColor,
    csm,
    envMap,
    envSource,
    environmentRotationY,
    sky,
    skyScene,
    envStats,
    skyRadiance,
    levelScale,
    sunIntensity: resolvedSunIntensity,
    envNormalise,
    fog: sceneFog,
    registerMaterial,
    registerScene,
    setSun,
    setFog,
    update,
    dispose,
  };
}
