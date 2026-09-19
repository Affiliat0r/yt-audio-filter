/**
 * lib/post.mjs — Part B, look pipeline: the post-processing chain.
 *
 * This is where a correctly-lit render becomes a photograph. The chain, in
 * execution order:
 *
 *   RenderPass        scene -> HDR buffer at the supersampled resolution
 *   GTAOPass          ground-truth ambient occlusion, high sample count
 *   UnrealBloomPass   HDR highlight spill
 *   BokehDOFPass      real thin-lens depth of field (ours, see below)
 *   OutputPass        ACES filmic tone map + sRGB transfer
 *   SMAAPass          spatial edge antialiasing, in display space where it works
 *   FilmResolvePass   supersample resolve + chromatic aberration + grade +
 *                     vignette + grain, straight to the canvas
 *
 * Two deliberate departures from the order in CONTRACT.md, both explained at
 * their pass below: SMAA runs *after* OutputPass, and depth of field is our
 * own pass rather than three's `BokehPass`.
 *
 * DETERMINISM
 * -----------
 * Not one pass here accumulates across frames, and no pass reads a frame
 * counter. `render(t)` feeds `t` to the only effect that varies over time
 * (grain); everything else is a pure function of the current frame's buffers.
 * That is why the antialiasing is SMAA and not TAA: TAA jitters the projection
 * matrix on an internal `accumulateIndex` that advances per call, so the same
 * `t` rendered first and rendered last would produce different pixels. With a
 * 2x supersampled buffer plus 4x MSAA there is nothing left for TAA to fix
 * anyway.
 */

import * as THREE from 'three';
import { EffectComposer } from '../node_modules/three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from '../node_modules/three/examples/jsm/postprocessing/RenderPass.js';
import { GTAOPass } from '../node_modules/three/examples/jsm/postprocessing/GTAOPass.js';
import { UnrealBloomPass } from '../node_modules/three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { SMAAPass } from '../node_modules/three/examples/jsm/postprocessing/SMAAPass.js';
import { OutputPass } from '../node_modules/three/examples/jsm/postprocessing/OutputPass.js';
import { Pass, FullScreenQuad } from '../node_modules/three/examples/jsm/postprocessing/Pass.js';

const FULLSCREEN_VERTEX = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
}
`;

/* ------------------------------------------------------------------ DOF -- */

/**
 * Thin-lens depth of field.
 *
 * three ships `BokehPass`, and we do not use it, for two reasons. Its depth
 * prepass uses a hand-written vertex shader with no instancing support, so a
 * scene with instanced vegetation gets a wrong depth buffer and the foliage
 * defocuses incorrectly. And its blur is a fixed ~17-tap pattern that bands
 * badly at large radii. Both matter here.
 *
 * This pass instead:
 *
 *   - reads the depth buffer GTAO already renders (free) or, if GTAO is off,
 *     runs a depth-only prepass with a *built-in* material so three handles
 *     instancing, skinning and morph targets for us;
 *   - computes a real circle of confusion from focal length, f-stop and
 *     sensor height, so "f/2.8 on a 50mm" means what a photographer expects;
 *   - gathers on a golden-angle spiral with a per-pixel rotation, weighting
 *     each tap by whether that tap's own blur circle actually reaches the
 *     centre pixel. That reach test is what stops a blurred background from
 *     bleeding over a sharp foreground edge — the artifact that makes cheap
 *     DOF look like a smear filter.
 *
 * It runs before tone mapping, on linear HDR values, which is why highlights
 * form real bokeh discs instead of grey blobs: a 40x-over-white specular
 * spread over a disc is still over white.
 *
 * The per-pixel spiral rotation comes from the pixel coordinate, never from
 * time, so the bokeh pattern is identical for a given camera regardless of
 * which frame is rendered.
 */
class BokehDOFPass extends Pass {
  constructor(scene, camera, options = {}) {
    super();
    const {
      width = 1,
      height = 1,
      samples = 64,
      focusDistance = 12,
      focalLength = 50, // mm
      fStop = 4.0,
      sensorHeight = 24, // mm, full frame
      maxCoCFraction = 0.02, // radius, as a fraction of buffer height
      strength = 1.0,
      depthTexture = null,
    } = options;

    this.scene = scene;
    this.camera = camera;
    this.needsSwap = true;

    this._externalDepth = depthTexture;
    this._depthRT = null;
    this._depthOverride = new THREE.MeshBasicMaterial({ colorWrite: false });
    this._maxCoCFraction = maxCoCFraction;

    this.material = new THREE.ShaderMaterial({
      name: 'BokehDOF',
      defines: { SAMPLES: samples },
      uniforms: {
        tDiffuse: { value: null },
        tDepth: { value: depthTexture },
        resolution: { value: new THREE.Vector2(width, height) },
        cameraNear: { value: 0.1 },
        cameraFar: { value: 1000 },
        focusDistance: { value: focusDistance },
        focalLength: { value: focalLength },
        fStop: { value: fStop },
        sensorHeight: { value: sensorHeight },
        maxCoC: { value: maxCoCFraction * height },
        dofStrength: { value: strength },
      },
      vertexShader: FULLSCREEN_VERTEX,
      fragmentShader: /* glsl */ `
        uniform sampler2D tDiffuse;
        uniform sampler2D tDepth;
        uniform vec2 resolution;
        uniform float cameraNear;
        uniform float cameraFar;
        uniform float focusDistance;
        uniform float focalLength;
        uniform float fStop;
        uniform float sensorHeight;
        uniform float maxCoC;
        uniform float dofStrength;
        varying vec2 vUv;

        const float GOLDEN_ANGLE = 2.39996323;
        const float TAU = 6.28318530718;

        float viewDepth( vec2 uv ) {
          float d = texture2D( tDepth, uv ).x;
          float ndc = d * 2.0 - 1.0;
          return ( 2.0 * cameraNear * cameraFar ) /
                 ( cameraFar + cameraNear - ndc * ( cameraFar - cameraNear ) );
        }

        // Circle-of-confusion radius in pixels for a point at view distance z.
        float cocRadius( float z ) {
          float f = focalLength * 0.001;
          float zf = max( focusDistance, f * 1.001 );
          float aperture = f / max( fStop, 0.5 );
          float diameter = abs( aperture * f * ( z - zf ) ) / max( z * ( zf - f ), 1e-5 );
          float px = ( diameter / ( sensorHeight * 0.001 ) ) * resolution.y * 0.5;
          // dofStrength is an honest cheat. The thin-lens result above is
          // physically right, and physically right on a scene a few metres
          // deep means almost no far-field separation. Scaling the circle of
          // confusion is how every film-look renderer buys the miniature
          // look without lying about focus *distance*, which is the part a
          // viewer would actually notice.
          return min( px * dofStrength, maxCoC );
        }

        // Interleaved gradient noise: a stable, banding-free per-pixel rotation.
        float ign( vec2 p ) {
          return fract( 52.9829189 * fract( dot( p, vec2( 0.06711056, 0.00583715 ) ) ) );
        }

        void main() {
          vec2 texel = 1.0 / resolution;
          vec3 centerColor = texture2D( tDiffuse, vUv ).rgb;
          float zCenter = viewDepth( vUv );
          float cocCenter = cocRadius( zCenter );

          // A blurred foreground has to be able to spill over a sharp
          // background, which a centre-radius-only gather can never do. Probe
          // a coarse ring at the maximum radius and widen if anything nearer
          // than the focal plane is blurred there.
          float radius = cocCenter;
          for ( int i = 0; i < 8; i ++ ) {
            float a = float( i ) * 0.7853981634;
            vec2 probeUv = vUv + vec2( cos( a ), sin( a ) ) * maxCoC * texel;
            float pz = viewDepth( probeUv );
            if ( pz < focusDistance ) radius = max( radius, cocRadius( pz ) );
          }

          float blend = smoothstep( 0.6, 1.6, radius );
          if ( blend <= 0.0 ) {
            gl_FragColor = vec4( centerColor, 1.0 );
            return;
          }

          float rotation = ign( gl_FragCoord.xy ) * TAU;
          vec3 acc = centerColor * 0.6;
          float weightSum = 0.6;

          for ( int i = 0; i < SAMPLES; i ++ ) {
            float fi = float( i ) + 0.5;
            float r = sqrt( fi / float( SAMPLES ) ) * radius;
            float a = fi * GOLDEN_ANGLE + rotation;
            vec2 sampleUv = vUv + vec2( cos( a ), sin( a ) ) * r * texel;

            float sz = viewDepth( sampleUv );
            float sCoc = cocRadius( sz );

            // Does this tap's own blur circle reach the centre pixel?
            float w = clamp( ( sCoc - r + 1.0 ) * 0.5, 0.0, 1.0 );

            // A tap behind the centre may only contribute within the centre's
            // own circle of confusion. Without this, blurred background leaks
            // across sharp foreground silhouettes.
            if ( sz > zCenter ) w *= clamp( ( cocCenter - r + 1.0 ) * 0.5, 0.0, 1.0 );

            acc += texture2D( tDiffuse, sampleUv ).rgb * w;
            weightSum += w;
          }

          vec3 blurred = acc / max( weightSum, 1e-4 );
          gl_FragColor = vec4( mix( centerColor, blurred, blend ), 1.0 );
        }
      `,
      depthTest: false,
      depthWrite: false,
    });

    this._quad = new FullScreenQuad(this.material);
  }

  get uniforms() {
    return this.material.uniforms;
  }

  setSamples(n) {
    if (this.material.defines.SAMPLES === n) return;
    this.material.defines.SAMPLES = n;
    this.material.needsUpdate = true;
  }

  setDepthTexture(texture) {
    this._externalDepth = texture;
    this.material.uniforms.tDepth.value = texture;
  }

  setSize(width, height) {
    this.material.uniforms.resolution.value.set(width, height);
    this.material.uniforms.maxCoC.value = this._maxCoCFraction * height;
    if (this._depthRT) {
      this._depthRT.setSize(width, height);
      this._depthRT.depthTexture.image.width = width;
      this._depthRT.depthTexture.image.height = height;
    }
  }

  /** Depth-only prepass, used only when GTAO is not already producing one. */
  _renderOwnDepth(renderer) {
    const size = this.material.uniforms.resolution.value;
    if (!this._depthRT) {
      const depth = new THREE.DepthTexture(size.x, size.y, THREE.UnsignedIntType);
      this._depthRT = new THREE.WebGLRenderTarget(size.x, size.y, {
        depthTexture: depth,
        depthBuffer: true,
        stencilBuffer: false,
      });
      this.material.uniforms.tDepth.value = depth;
    }
    const background = this.scene.background;
    const override = this.scene.overrideMaterial;
    this.scene.background = null;
    this.scene.overrideMaterial = this._depthOverride;
    const prev = renderer.getRenderTarget();
    renderer.setRenderTarget(this._depthRT);
    renderer.clear(true, true, false);
    renderer.render(this.scene, this.camera);
    renderer.setRenderTarget(prev);
    this.scene.overrideMaterial = override;
    this.scene.background = background;
  }

  render(renderer, writeBuffer, readBuffer) {
    if (!this._externalDepth) this._renderOwnDepth(renderer);

    const u = this.material.uniforms;
    u.tDiffuse.value = readBuffer.texture;
    u.cameraNear.value = this.camera.near;
    u.cameraFar.value = this.camera.far;

    if (this.renderToScreen) {
      renderer.setRenderTarget(null);
    } else {
      renderer.setRenderTarget(writeBuffer);
      if (this.clear) renderer.clear();
    }
    this._quad.render(renderer);
  }

  dispose() {
    this.material.dispose();
    this._depthOverride.dispose();
    if (this._depthRT) this._depthRT.dispose();
    this._quad.dispose();
  }
}

/* -------------------------------------------------------------- resolve -- */

/**
 * Final pass: supersample resolve, then the film layer.
 *
 * The resolve is an exact NxN box average of the supersampled buffer, done
 * *after* tone mapping. Resolving after the tone map rather than before is
 * deliberate: averaging linear HDR samples lets one 60x-over-white sample
 * dominate its four neighbours and produces crawling white specks on
 * specular edges, whereas averaging display-referred samples is what a
 * camera sensor's per-photosite response actually does.
 *
 * Then chromatic aberration, a gentle grade, a vignette and grain. Every one
 * of these is dialled to the point where you would not name it if asked what
 * was applied — that is the target. Grain in particular does an
 * unreasonable amount of work: it breaks up the gradient banding that gives
 * away 8-bit output and it makes a clean synthetic image read as captured.
 *
 * Grain is the only time-varying thing in the chain and it derives its seed
 * from `t`, so it is reproducible per frame.
 */
class FilmResolvePass extends Pass {
  constructor(options = {}) {
    super();
    const {
      supersample = 1,
      inputWidth = 1,
      inputHeight = 1,
      grain = 0.032,
      vignette = 0.3,
      chromaticAberration = 0.0016,
      contrast = 1.03,
      saturation = 1.06,
      lift = 0.004,
    } = options;

    this.needsSwap = true;

    this.material = new THREE.ShaderMaterial({
      name: 'FilmResolve',
      defines: {
        SS: Math.max(1, Math.round(supersample)),
        USE_CHROMATIC_ABERRATION: chromaticAberration > 0 ? 1 : 0,
      },
      uniforms: {
        tDiffuse: { value: null },
        inputResolution: { value: new THREE.Vector2(inputWidth, inputHeight) },
        uTime: { value: 0 },
        grain: { value: grain },
        vignette: { value: vignette },
        chroma: { value: chromaticAberration },
        contrast: { value: contrast },
        saturation: { value: saturation },
        lift: { value: lift },
      },
      vertexShader: FULLSCREEN_VERTEX,
      fragmentShader: /* glsl */ `
        uniform sampler2D tDiffuse;
        uniform vec2 inputResolution;
        uniform float uTime;
        uniform float grain;
        uniform float vignette;
        uniform float chroma;
        uniform float contrast;
        uniform float saturation;
        uniform float lift;
        varying vec2 vUv;

        const vec3 LUMA = vec3( 0.2126, 0.7152, 0.0722 );

        vec3 boxResolve( vec2 uv, vec2 texel ) {
          vec3 acc = vec3( 0.0 );
          for ( int y = 0; y < SS; y ++ ) {
            for ( int x = 0; x < SS; x ++ ) {
              vec2 o = ( vec2( float( x ), float( y ) ) + 0.5 - float( SS ) * 0.5 ) * texel;
              acc += texture2D( tDiffuse, uv + o ).rgb;
            }
          }
          return acc / float( SS * SS );
        }

        float hash13( vec3 p ) {
          p = fract( p * 0.1031 );
          p += dot( p, p.zyx + 31.32 );
          return fract( ( p.x + p.y ) * p.z );
        }

        void main() {
          vec2 texel = 1.0 / inputResolution;
          vec2 d = vUv - 0.5;

          vec3 color;
          #if USE_CHROMATIC_ABERRATION
            // Radial, quadratic with distance from the optical axis — the way
            // a real lens disperses. Sub-pixel at the corners.
            vec2 offset = d * chroma * dot( d, d ) * 4.0;
            color.r = boxResolve( vUv + offset, texel ).r;
            color.g = boxResolve( vUv, texel ).g;
            color.b = boxResolve( vUv - offset, texel ).b;
          #else
            color = boxResolve( vUv, texel );
          #endif

          // Grade, display-referred.
          float luma = dot( color, LUMA );
          color = mix( vec3( luma ), color, saturation );
          color = ( color - 0.5 ) * contrast + 0.5;
          color = color * ( 1.0 - lift ) + lift;

          // Vignette.
          float r = length( d ) * 1.41421356;
          color *= 1.0 - vignette * pow( clamp( r, 0.0, 1.0 ), 2.2 );

          // Grain: strongest through the midtones, nearly gone in the
          // highlights, exactly like film.
          float slot = mod( floor( uTime * 997.0 ), 733.0 );
          float n0 = hash13( vec3( gl_FragCoord.xy, slot ) );
          float n1 = hash13( vec3( gl_FragCoord.yx + 19.7, slot + 53.0 ) );
          float n2 = hash13( vec3( gl_FragCoord.xy + 71.3, slot + 131.0 ) );
          float mono = n0 - 0.5;
          vec3 perChannel = vec3( n0, n1, n2 ) - 0.5;
          float mid = 1.0 - abs( clamp( dot( color, LUMA ), 0.0, 1.0 ) * 2.0 - 1.0 );
          float weight = mix( 0.3, 1.0, mid );
          color += ( vec3( mono ) * 0.75 + perChannel * 0.25 ) * grain * weight;

          gl_FragColor = vec4( clamp( color, 0.0, 1.0 ), 1.0 );
        }
      `,
      depthTest: false,
      depthWrite: false,
    });

    this._quad = new FullScreenQuad(this.material);
  }

  get uniforms() {
    return this.material.uniforms;
  }

  /** Called by EffectComposer with the *internal* (supersampled) size. */
  setSize(width, height) {
    this.material.uniforms.inputResolution.value.set(width, height);
  }

  setSupersample(ss) {
    const n = Math.max(1, Math.round(ss));
    if (this.material.defines.SS === n) return;
    this.material.defines.SS = n;
    this.material.needsUpdate = true;
  }

  render(renderer, writeBuffer, readBuffer) {
    this.material.uniforms.tDiffuse.value = readBuffer.texture;
    if (this.renderToScreen) {
      renderer.setRenderTarget(null);
    } else {
      renderer.setRenderTarget(writeBuffer);
      if (this.clear) renderer.clear();
    }
    this._quad.render(renderer);
  }

  dispose() {
    this.material.dispose();
    this._quad.dispose();
  }
}

/* ------------------------------------------------------------- composer -- */

const QUALITY_PRESETS = {
  draft: { gtao: false, gtaoSamples: 8, pdSamples: 4, dof: false, dofSamples: 16, smaa: false, msaa: 0 },
  medium: { gtao: true, gtaoSamples: 16, pdSamples: 8, dof: true, dofSamples: 32, smaa: true, msaa: 4 },
  high: { gtao: true, gtaoSamples: 32, pdSamples: 16, dof: true, dofSamples: 64, smaa: true, msaa: 4 },
  ultra: { gtao: true, gtaoSamples: 64, pdSamples: 16, dof: true, dofSamples: 96, smaa: true, msaa: 8 },
};

/**
 * A pass that renders the scene with an override material must not have the
 * scene background drawn into its buffer. GTAO's normal prepass is one; this
 * wraps it so the background is out of the way for the duration.
 */
function suppressBackground(pass, scene) {
  const original = pass.render.bind(pass);
  pass.render = function wrapped(...args) {
    const background = scene.background;
    scene.background = null;
    try {
      return original(...args);
    } finally {
      scene.background = background;
    }
  };
}

/**
 * @typedef {Object} ComposerOptions
 * @property {number} width                  Output width (canvas pixels).
 * @property {number} height                 Output height (canvas pixels).
 * @property {'draft'|'medium'|'high'|'ultra'} [quality='high']
 * @property {number} [supersample]          Defaults to `renderer.supersample`.
 * @property {number} [msaa]                 Multisample count on the HDR buffer.
 * @property {boolean} [gtao]
 * @property {number} [aoRadius=0.35]        World units.
 * @property {number} [aoIntensity=1]
 * @property {boolean} [bloom=true]
 * @property {number} [bloomStrength=0.35]
 * @property {number} [bloomRadius=0.6]
 * @property {number} [bloomThreshold=1.25]  In display units: 1.0 is the value that tone-maps to
 *   white. Divided by the renderer's exposure internally. Below 1.0 the sky itself blooms.
 * @property {boolean} [dof=true]
 * @property {number} [focusDistance=12]     World units. Also settable per frame via `setFocus`.
 * @property {number} [focalLength=50]       Millimetres, on a 24mm-high sensor.
 * @property {number} [fStop=2.8]
 * @property {number} [maxCoCFraction=0.022] Blur radius ceiling, as a fraction of buffer height.
 * @property {number} [dofStrength=2.2]      Circle-of-confusion multiplier. 1.0 is physically
 *   exact; above that buys the miniature look without moving the focal plane.
 * @property {number} [grain=0.032]
 * @property {number} [vignette=0.3]
 * @property {number} [chromaticAberration=0.0016]
 * @property {number} [contrast=1.03]
 * @property {number} [saturation=1.06]
 * @property {number} [lift=0.004]
 */

/**
 * @param {THREE.WebGLRenderer} renderer
 * @param {THREE.Scene} scene
 * @param {THREE.Camera} camera
 * @param {ComposerOptions} options
 * @returns {{
 *   composer: EffectComposer,
 *   passes: Object,
 *   render: (t?: number) => void,
 *   setFocus: (distance: number) => void,
 *   focusOn: (worldPosition: THREE.Vector3) => void,
 *   setAperture: (fStop: number) => void,
 *   setFocalLength: (mm: number) => void,
 *   setDofStrength: (scale: number) => void,
 *   setBloom: (o: {strength?:number, radius?:number, threshold?:number}) => void,
 *   setFilm: (o: Object) => void,
 *   setSceneClipBox: (box: THREE.Box3) => void,
 *   resize: (w: number, h: number) => void,
 *   internalSize: () => {width:number, height:number, supersample:number},
 *   dispose: () => void,
 * }}
 */
export function createComposer(renderer, scene, camera, options = {}) {
  const {
    width = renderer.outputWidth || 1280,
    height = renderer.outputHeight || 720,
    quality = 'high',
  } = options;

  const preset = QUALITY_PRESETS[quality] || QUALITY_PRESETS.high;

  const supersample = Math.max(
    1,
    Math.round(options.supersample ?? renderer.supersample ?? 1)
  );
  const msaa = options.msaa ?? preset.msaa;
  const useGTAO = options.gtao ?? preset.gtao;
  const useDOF = options.dof ?? preset.dof;
  const useSMAA = options.smaa ?? preset.smaa;
  const useBloom = options.bloom ?? true;

  let internalWidth = width * supersample;
  let internalHeight = height * supersample;

  // HDR, float, and multisampled. Half float is what keeps a 60x-over-white
  // specular highlight intact all the way to the tone mapper instead of
  // clipping it to 1.0 in the very first buffer.
  const hdrTarget = new THREE.WebGLRenderTarget(internalWidth, internalHeight, {
    type: THREE.HalfFloatType,
    format: THREE.RGBAFormat,
    colorSpace: THREE.LinearSRGBColorSpace,
    depthBuffer: true,
    stencilBuffer: false,
    samples: msaa,
  });

  const composer = new EffectComposer(renderer, hdrTarget);
  composer.renderToScreen = true;

  const passes = {};

  // 1. scene
  passes.render = new RenderPass(scene, camera);
  composer.addPass(passes.render);

  // 2. ambient occlusion
  //    GTAO over SSAO: SSAO's hemisphere sampling produces a grey wash that
  //    reads as dirt, while GTAO computes an actual visibility integral, so
  //    contact shadows land tight against the contact instead of haloing.
  //    Its normal+depth prepass is also reused by the DOF pass below.
  if (useGTAO) {
    passes.gtao = new GTAOPass(scene, camera, internalWidth, internalHeight);
    passes.gtao.output = GTAOPass.OUTPUT.Default;
    passes.gtao.blendIntensity = options.aoIntensity ?? 1.0;
    passes.gtao.updateGtaoMaterial({
      radius: options.aoRadius ?? 0.35,
      distanceExponent: 1.0,
      thickness: 1.0,
      scale: 1.0,
      samples: preset.gtaoSamples,
      screenSpaceRadius: false,
    });
    passes.gtao.updatePdMaterial({
      lumaPhi: 10,
      depthPhi: 2,
      normalPhi: 3,
      radius: 4,
      radiusExponent: 1,
      rings: 2,
      samples: preset.pdSamples,
    });
    suppressBackground(passes.gtao, scene);
    composer.addPass(passes.gtao);
  }

  // 3. bloom
  //    The threshold is given in *display* units, where 1.0 means "the value
  //    that tone-maps to white". The pass sees pre-exposure scene-linear
  //    values, so it has to be divided by the exposure. Getting this wrong is
  //    what turns bloom into haze: a threshold that sits below the sky's own
  //    radiance blooms the entire sky, and the render fogs up.
  if (useBloom) {
    //    Threshold above 1.0 on purpose. Only things that would clip anyway
    //    are allowed to spill — chrome glints, the sun off a rail, a bright
    //    edge — so the effect reads as light behaving like light. Drop it
    //    below 1.0 and the sky itself starts blooming, which is haze.
    const bloomThreshold = (options.bloomThreshold ?? 1.25) / Math.max(1e-3, renderer.toneMappingExposure);
    passes.bloom = new UnrealBloomPass(
      new THREE.Vector2(internalWidth, internalHeight),
      options.bloomStrength ?? 0.35,
      options.bloomRadius ?? 0.6,
      bloomThreshold
    );
    composer.addPass(passes.bloom);
  }

  // 4. depth of field
  if (useDOF) {
    passes.dof = new BokehDOFPass(scene, camera, {
      width: internalWidth,
      height: internalHeight,
      samples: preset.dofSamples,
      focusDistance: options.focusDistance ?? 12,
      focalLength: options.focalLength ?? 50,
      fStop: options.fStop ?? 2.8,
      maxCoCFraction: options.maxCoCFraction ?? 0.022,
      strength: options.dofStrength ?? 2.2,
      depthTexture: passes.gtao ? passes.gtao.depthTexture : null,
    });
    composer.addPass(passes.dof);
  }

  // 5. tone map + transfer
  passes.output = new OutputPass();
  composer.addPass(passes.output);

  // 6. antialiasing
  //    CONTRACT.md places this before OutputPass. SMAA's edge detection works
  //    on perceptual luma and its thresholds assume display-referred input;
  //    run on linear HDR it misses every edge between two values above 1.0.
  //    So it goes after the tone map, where it is the same spatial filter the
  //    algorithm was designed as. (TAA would have to go before OutputPass —
  //    and TAA is out, see the file header.)
  if (useSMAA) {
    passes.smaa = new SMAAPass();
    composer.addPass(passes.smaa);
  }

  // 7. resolve + film
  passes.film = new FilmResolvePass({
    supersample,
    inputWidth: internalWidth,
    inputHeight: internalHeight,
    grain: options.grain,
    vignette: options.vignette,
    chromaticAberration: options.chromaticAberration,
    contrast: options.contrast,
    saturation: options.saturation,
    lift: options.lift,
  });
  composer.addPass(passes.film);

  composer.setSize(internalWidth, internalHeight);

  /* --------------------------------------------------------------- api -- */

  function render(t = 0) {
    passes.film.uniforms.uTime.value = t;
    // Explicit delta: EffectComposer would otherwise call its internal Clock,
    // which is wall-clock time and would make the render order observable.
    composer.render(0);
  }

  function setFocus(distance) {
    if (passes.dof) passes.dof.uniforms.focusDistance.value = Math.max(0.05, distance);
  }

  const _v = new THREE.Vector3();
  function focusOn(worldPosition) {
    camera.updateMatrixWorld();
    _v.copy(worldPosition).sub(camera.getWorldPosition(new THREE.Vector3()));
    const forward = camera.getWorldDirection(new THREE.Vector3());
    setFocus(Math.max(0.05, _v.dot(forward)));
  }

  function setAperture(fStop) {
    if (passes.dof) passes.dof.uniforms.fStop.value = fStop;
  }

  function setFocalLength(mm) {
    if (passes.dof) passes.dof.uniforms.focalLength.value = mm;
  }

  function setDofStrength(scale) {
    if (passes.dof) passes.dof.uniforms.dofStrength.value = scale;
  }

  function setBloom({ strength, radius, threshold } = {}) {
    if (!passes.bloom) return;
    if (strength != null) passes.bloom.strength = strength;
    if (radius != null) passes.bloom.radius = radius;
    // Display units in, scene-linear out — see the construction site above.
    if (threshold != null) {
      passes.bloom.threshold = threshold / Math.max(1e-3, renderer.toneMappingExposure);
    }
  }

  function setFilm(values = {}) {
    for (const [key, value] of Object.entries(values)) {
      const u = passes.film.uniforms[key];
      if (u) u.value = value;
    }
  }

  /** GTAO is meaningfully more accurate when it knows the scene bounds. */
  function setSceneClipBox(box) {
    if (passes.gtao) passes.gtao.setSceneClipBox(box);
  }

  function resize(w, h) {
    internalWidth = w * supersample;
    internalHeight = h * supersample;
    if (typeof renderer.setOutputSize === 'function') renderer.setOutputSize(w, h);
    else renderer.setSize(w, h, false);
    composer.setSize(internalWidth, internalHeight);
    if (passes.dof && passes.gtao) passes.dof.setDepthTexture(passes.gtao.depthTexture);
  }

  function internalSize() {
    return { width: internalWidth, height: internalHeight, supersample };
  }

  function dispose() {
    composer.dispose();
    for (const pass of Object.values(passes)) {
      if (pass && typeof pass.dispose === 'function') pass.dispose();
    }
    hdrTarget.dispose();
  }

  return {
    composer,
    passes,
    render,
    setFocus,
    focusOn,
    setAperture,
    setFocalLength,
    setDofStrength,
    setBloom,
    setFilm,
    setSceneClipBox,
    resize,
    internalSize,
    dispose,
  };
}

export { BokehDOFPass, FilmResolvePass, QUALITY_PRESETS };
