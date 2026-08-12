/**
 * Ground.
 *
 * The height field is analytic, so props drop onto it without raycasting and
 * the same numbers drive the mesh, the track corridor and every placement
 * test.
 *
 * The surface is a four-way splat — grass, dry/sand, dirt, rock — with the
 * weights baked per vertex from slope, altitude, proximity to the line and
 * noise, then blended in the fragment shader from four real albedo/normal
 * sets. Each layer is sampled at two scales and modulated by a 90-metre macro
 * map, so nothing repeats visibly even under raking light. A flat colour with
 * vertex tinting was v1's single biggest tell and none of it survives here.
 */

import * as THREE from 'three';
import { makeNoise2D, fbm, ridged, smoothstep, lerp, clamp } from './rng.mjs';

export const TERRAIN_SIZE = 760;
const TERRAIN_SEGMENTS = 600;
export const WATER_LEVEL = -7.0;

/** Corridor: level within FLAT metres of the rails, natural beyond BLEND. */
const CORRIDOR_FLAT = 4.6;
const CORRIDOR_BLEND = 21;
/** Top of the formation, relative to the spline: the ballast sits on this. */
const FORMATION_DROP = 0.26;
/** A shallow drainage cess either side of the ballast shoulder. */
const DITCH_CENTRE = 4.2;
const DITCH_WIDTH = 2.1;
const DITCH_DEPTH = 0.34;

/** Outer ring, so the map has a horizon rather than an edge. */
const RING_INNER = TERRAIN_SIZE * 0.48;
const RING_OUTER = 2600;
const RING_RADIAL = 84;
const RING_ANGULAR = 192;

/**
 * The landform, independent of the railway. Built first, because the track
 * picks its own control heights from `macro` before the terrain exists.
 */
export function createHills(rng) {
  const shape = makeNoise2D(rng);
  const detail = makeNoise2D(rng);
  const rocky = makeNoise2D(rng);
  const tint = makeNoise2D(rng);
  const patch = makeNoise2D(rng);

  /** Low-frequency landform only. The track follows a damped version. */
  function macro(x, z) {
    return fbm(shape, x * 0.0034, z * 0.0034, 2) * 15;
  }

  /** Distant ground rising toward the horizon, so the map has no visible edge. */
  function rim(x, z) {
    const r = Math.hypot(x, z);
    const rise = Math.max(0, r - 210);
    const roll = 0.5 + 0.5 * detail(x * 0.0052, z * 0.0052);
    return Math.pow(rise, 1.18) * 0.055 * (0.4 + 0.75 * roll);
  }

  /** Full natural height at (x, z), ignoring the railway. */
  function height(x, z) {
    const base =
      macro(x, z) +
      fbm(detail, x * 0.0102 + 40.5, z * 0.0102 - 17.25, 3) * 7.2 +
      fbm(detail, x * 0.041 - 8.5, z * 0.041 + 3.25, 3) * 1.55 +
      detail(x * 0.145, z * 0.145) * 0.28;
    // Crags on the steeper flanks, so distant hills have some structure.
    const crag = ridged(rocky, x * 0.021, z * 0.021, 3) - 0.45;
    const cragMask = smoothstep(6, 17, base);
    return base + crag * cragMask * 3.4 + rim(x, z);
  }

  return { macro, height, tint, patch, rocky };
}

/**
 * @param {object} deps `{ rng, materials, geom, track, hills }`
 */
export function createTerrain({ rng, materials, geom, track, hills }) {
  const noiseRng = rng.fork();
  const splatNoise = makeNoise2D(noiseRng);
  const dryNoise = makeNoise2D(noiseRng);
  const dirtNoise = makeNoise2D(noiseRng);

  /** Natural height with the railway corridor levelled and drained in. */
  function heightAt(x, z) {
    const near = track.nearest(x, z);
    const natural = hills.height(x, z);
    if (near.dist > CORRIDOR_BLEND) return natural;
    const w = smoothstep(CORRIDOR_FLAT, CORRIDOR_BLEND, near.dist);
    const formation = near.y - FORMATION_DROP;
    let y = lerp(formation, natural, w);
    // Drainage cess: a shallow trough just outside the ballast shoulder.
    const d = (near.dist - DITCH_CENTRE) / DITCH_WIDTH;
    y -= DITCH_DEPTH * Math.exp(-d * d) * (1 - smoothstep(9, 14, near.dist));
    return y;
  }

  /** Central-difference slope magnitude (rise over run). */
  function slopeAt(x, z, h = 1.2) {
    const hx = heightAt(x + h, z) - heightAt(x - h, z);
    const hz = heightAt(x, z + h) - heightAt(x, z - h);
    return Math.hypot(hx, hz) / (2 * h);
  }

  // --- splat weights ------------------------------------------------------

  const _w = [0, 0, 0, 0];

  /** Layer weights at (x, z): [grass, dry/sand, dirt, rock]. */
  function splatAt(x, z, y, slope, trackDist) {
    const macroPatch = fbm(splatNoise, x * 0.013, z * 0.013, 3) * 0.5 + 0.5;
    // Two scales of patchiness: the 120 m one gives whole fields a character,
    // the 25 m one keeps any single field from reading as a painted plane.
    const dryPatch =
      fbm(dryNoise, x * 0.0085, z * 0.0085, 3) * 0.34 +
      fbm(dryNoise, x * 0.041 + 13, z * 0.041 - 7, 2) * 0.16 +
      0.5;
    const dirtPatch =
      fbm(dirtNoise, x * 0.028, z * 0.028, 3) * 0.33 +
      fbm(dirtNoise, x * 0.0065 - 21, z * 0.0065 + 5, 2) * 0.17 +
      0.5;

    let rock = smoothstep(0.38, 0.92, slope) * 1.25;
    rock += smoothstep(21, 36, y) * 0.5;
    rock *= 0.55 + 0.9 * macroPatch;

    let dry = smoothstep(0.46, 0.82, dryPatch) * 0.95;
    dry += smoothstep(WATER_LEVEL + 2.4, WATER_LEVEL - 0.5, y) * 2.6; // beach
    dry *= 1 - smoothstep(0.5, 0.95, slope) * 0.6;

    let dirt = smoothstep(0.18, 0.52, slope) * 0.85;
    dirt += smoothstep(0.56, 0.86, dirtPatch) * 0.9;
    // Worn, ballast-dusted ground beside the line.
    dirt += (1 - smoothstep(2.8, 6.4, trackDist)) * 1.35;

    const grass = 1.0;
    const total = grass + dry + dirt + rock;
    _w[0] = grass / total;
    _w[1] = dry / total;
    _w[2] = dirt / total;
    _w[3] = rock / total;
    return _w;
  }

  // --- the splat material -------------------------------------------------

  const tex = materials.tex;
  const layerScale = new THREE.Vector4(
    1 / tex.grass.scale,
    1 / tex.dryGrass.scale,
    1 / tex.dirt.scale,
    1 / tex.rock.scale
  );

  const groundMaterial = new THREE.MeshStandardMaterial({
    map: tex.grass.map,
    normalMap: tex.grass.normalMap,
    vertexColors: true,
    roughness: 1.0,
    metalness: 0.0,
    dithering: true,
  });
  groundMaterial.name = 'terrain';

  const groundUniforms = {
    tA1: { value: tex.dryGrass.map },
    tA2: { value: tex.dirt.map },
    tA3: { value: tex.rock.map },
    tN1: { value: tex.dryGrass.normalMap },
    tN2: { value: tex.dirt.normalMap },
    tN3: { value: tex.rock.normalMap },
    tMacro: { value: tex.macro.map },
    uLayerScale: { value: layerScale },
    uMacroScale: { value: 1 / 90 },
    uNormalStrength: { value: 1.25 },
  };

  groundMaterial.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, groundUniforms);

    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nattribute vec4 aSplat;\nvarying vec4 vSplat;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvSplat = aSplat;');

    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        /* glsl */ `#include <common>
        uniform sampler2D tA1, tA2, tA3, tN1, tN2, tN3, tMacro;
        uniform vec4 uLayerScale;
        uniform float uMacroScale;
        uniform float uNormalStrength;
        varying vec4 vSplat;

        // Each layer twice: once at its own tile size, once at 3.5x that, the
        // large sample modulating the small one. Kills the repeat outright.
        vec4 terrainLayer( sampler2D tex, vec2 uv, float s ) {
          vec4 a = texture2D( tex, uv * s );
          float m = dot( texture2D( tex, uv * s * 0.2837 ).rgb, vec3( 0.3333 ) );
          return vec4( a.rgb * ( 0.48 + 1.02 * m ), a.a );
        }
        vec3 terrainNormal( sampler2D tex, vec2 uv, float s ) {
          return texture2D( tex, uv * s ).xyz * 2.0 - 1.0;
        }`
      )
      .replace(
        '#include <map_fragment>',
        /* glsl */ `
        vec4 tw = vSplat / max( 1e-4, dot( vSplat, vec4( 1.0 ) ) );
        vec2 tuv = vNormalMapUv;
        vec4 L0 = terrainLayer( map, tuv, uLayerScale.x );
        vec4 L1 = terrainLayer( tA1, tuv, uLayerScale.y );
        vec4 L2 = terrainLayer( tA2, tuv, uLayerScale.z );
        vec4 L3 = terrainLayer( tA3, tuv, uLayerScale.w );
        vec3 terrainAlbedo = L0.rgb * tw.x + L1.rgb * tw.y + L2.rgb * tw.z + L3.rgb * tw.w;
        float terrainRough = L0.a * tw.x + L1.a * tw.y + L2.a * tw.z + L3.a * tw.w;
        float macro = dot( texture2D( tMacro, tuv * uMacroScale ).rgb, vec3( 0.3333 ) );
        terrainAlbedo *= 0.72 + 0.66 * macro;
        diffuseColor.rgb *= terrainAlbedo;`
      )
      .replace(
        '#include <roughnessmap_fragment>',
        'float roughnessFactor = roughness * clamp( terrainRough * 1.05, 0.05, 1.0 );'
      )
      .replace(
        '#include <normal_fragment_maps>',
        /* glsl */ `
        vec3 mapN = normalize(
          terrainNormal( normalMap, tuv, uLayerScale.x ) * tw.x +
          terrainNormal( tN1, tuv, uLayerScale.y ) * tw.y +
          terrainNormal( tN2, tuv, uLayerScale.z ) * tw.z +
          terrainNormal( tN3, tuv, uLayerScale.w ) * tw.w
        );
        mapN.xy *= uNormalStrength;
        normal = normalize( tbn * mapN );`
      );
  };
  groundMaterial.customProgramCacheKey = () => 'terrainSplat';

  // --- inner terrain mesh -------------------------------------------------

  // Deliberately close to white: this is a *tint*, and anything stronger drags
  // the four splat layers back toward the flat vertex colour v1 shipped with.
  const grassLight = new THREE.Color(0xfaf6e6);
  const grassDeep = new THREE.Color(0xd6dcc4);
  const warm = new THREE.Color(0xf3e8cf);
  const scratch = new THREE.Color();

  function buildField(positions, uvs, indices) {
    const count = positions.length / 3;
    const splat = new Float32Array(count * 4);
    const colors = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const x = positions[i * 3];
      const y = positions[i * 3 + 1];
      const z = positions[i * 3 + 2];
      const slope = slopeAt(x, z);
      const near = track.nearest(x, z);
      const w = splatAt(x, z, y, slope, near.dist);
      splat[i * 4] = w[0];
      splat[i * 4 + 1] = w[1];
      splat[i * 4 + 2] = w[2];
      splat[i * 4 + 3] = w[3];

      // Subtle large-scale colour drift so no two hillsides match, plus a
      // warm dusting where the ballast has been walked out onto the cess.
      const drift = hills.tint(x * 0.0068, z * 0.0068) * 0.5 + 0.5;
      scratch.copy(grassDeep).lerp(grassLight, drift);
      scratch.lerp(warm, (1 - smoothstep(2.6, 13, near.dist)) * 0.5);
      colors[i * 3] = scratch.r;
      colors[i * 3 + 1] = scratch.g;
      colors[i * 3 + 2] = scratch.b;
    }

    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    g.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
    g.setAttribute('aSplat', new THREE.BufferAttribute(splat, 4));
    g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    g.setIndex(new THREE.BufferAttribute(indices, 1));
    g.computeVertexNormals();
    g.computeBoundingSphere();
    return g;
  }

  // Regular grid over the playable area.
  const row = TERRAIN_SEGMENTS + 1;
  const step = TERRAIN_SIZE / TERRAIN_SEGMENTS;
  const innerCount = row * row;
  const iPos = new Float32Array(innerCount * 3);
  const iUv = new Float32Array(innerCount * 2);
  for (let j = 0; j < row; j++) {
    for (let i = 0; i < row; i++) {
      const n = j * row + i;
      const x = -TERRAIN_SIZE / 2 + i * step;
      const z = -TERRAIN_SIZE / 2 + j * step;
      iPos[n * 3] = x;
      iPos[n * 3 + 1] = heightAt(x, z);
      iPos[n * 3 + 2] = z;
      // UVs in metres: every material in the world is scaled the same way.
      iUv[n * 2] = x;
      iUv[n * 2 + 1] = z;
    }
  }
  const iIdx = new Uint32Array(TERRAIN_SEGMENTS * TERRAIN_SEGMENTS * 6);
  let w = 0;
  for (let j = 0; j < TERRAIN_SEGMENTS; j++) {
    for (let i = 0; i < TERRAIN_SEGMENTS; i++) {
      const a = j * row + i;
      const b = a + 1;
      const c = a + row;
      const d = c + 1;
      iIdx[w++] = a;
      iIdx[w++] = c;
      iIdx[w++] = b;
      iIdx[w++] = b;
      iIdx[w++] = c;
      iIdx[w++] = d;
    }
  }

  const ground = new THREE.Mesh(buildField(iPos, iUv, iIdx), groundMaterial);
  ground.castShadow = false;
  ground.receiveShadow = true;
  ground.name = 'terrain';

  // Coarse annulus carrying the horizon. Radial spacing is geometric so the
  // near edge matches the inner grid and the far edge costs almost nothing.
  const ringCount = (RING_RADIAL + 1) * (RING_ANGULAR + 1);
  const rPos = new Float32Array(ringCount * 3);
  const rUv = new Float32Array(ringCount * 2);
  for (let j = 0; j <= RING_RADIAL; j++) {
    const t = j / RING_RADIAL;
    const radius = RING_INNER * Math.pow(RING_OUTER / RING_INNER, t);
    for (let i = 0; i <= RING_ANGULAR; i++) {
      const a = (i / RING_ANGULAR) * Math.PI * 2;
      const n = j * (RING_ANGULAR + 1) + i;
      const x = Math.cos(a) * radius;
      const z = Math.sin(a) * radius;
      rPos[n * 3] = x;
      rPos[n * 3 + 1] = hills.height(x, z);
      rPos[n * 3 + 2] = z;
      rUv[n * 2] = x;
      rUv[n * 2 + 1] = z;
    }
  }
  const rIdx = new Uint32Array(RING_RADIAL * RING_ANGULAR * 6);
  w = 0;
  for (let j = 0; j < RING_RADIAL; j++) {
    for (let i = 0; i < RING_ANGULAR; i++) {
      const a = j * (RING_ANGULAR + 1) + i;
      const b = a + 1;
      const c = a + RING_ANGULAR + 1;
      const d = c + 1;
      // Opposite winding to the inner grid, deliberately.
      //
      // The grid maps (i, j) -> (+X, +Z); the ring maps (i, j) -> (angle,
      // radius), and sweeping the angle from +X toward +Z is the opposite
      // orientation in the xz-plane. Reusing the grid's a,c,b order here gives
      // every triangle a downward normal, so the whole horizon is backface-
      // culled and you see sky through the mountains — which is exactly how
      // this presented: white shards tracing triangle edges across the skyline.
      rIdx[w++] = a;
      rIdx[w++] = b;
      rIdx[w++] = c;
      rIdx[w++] = b;
      rIdx[w++] = d;
      rIdx[w++] = c;
    }
  }
  // The horizon ring gets a plain vertex-coloured material, NOT the splat
  // shader the playable terrain uses.
  //
  // UVs here are world metres and the ring runs out to RING_OUTER, so a single
  // far triangle spans hundreds of texture repeats. Sampling four albedo maps
  // at that minification aliases into a shimmering faceted mess that reads as
  // broken geometry — it was the most obvious defect in the first assembled
  // render. At 380 m+ under fog there is no texture detail to resolve anyway,
  // so the colour ramp plus aerial perspective is strictly better and cheaper.
  const horizonMaterial = new THREE.MeshStandardMaterial({
    // buildField's vertex colours sit around 0.8 in linear space — they are a
    // TINT meant to multiply the grass albedo (~0.2), not standalone albedo.
    // Dropping the map without this base colour leaves the horizon at ~90%
    // white and the hills bleach out under sun. This stands in for the mean
    // albedo the texture would have contributed.
    color: 0x647456,
    vertexColors: true,
    roughness: 1.0,
    metalness: 0.0,
    dithering: true,
  });
  horizonMaterial.name = 'terrain-horizon';

  const ring = new THREE.Mesh(buildField(rPos, rUv, rIdx), horizonMaterial);
  ring.castShadow = false;
  ring.receiveShadow = false;
  ring.name = 'terrain-horizon';

  // --- water --------------------------------------------------------------

  const waterNormal = tex.water.normalMap.clone();
  waterNormal.repeat.set(1 / 9, 1 / 9);
  waterNormal.needsUpdate = true;
  const waterNormal2 = tex.water.normalMap.clone();
  waterNormal2.repeat.set(1 / 2.6, 1 / 2.6);
  waterNormal2.needsUpdate = true;

  const waterMaterial = new THREE.MeshPhysicalMaterial({
    color: 0x16323a,
    roughness: 0.045,
    metalness: 0.02,
    transparent: true,
    opacity: 0.88,
    normalMap: waterNormal,
    // A clearcoat with its own normal map gives a second, finer wave scale
    // without a custom shader.
    clearcoat: 1.0,
    clearcoatRoughness: 0.04,
    clearcoatNormalMap: waterNormal2,
    envMapIntensity: 1.9,
    side: THREE.FrontSide,
  });
  waterMaterial.normalScale.set(1.15, 1.15);
  waterMaterial.clearcoatNormalScale = new THREE.Vector2(0.45, 0.45);

  const waterGeom = new THREE.PlaneGeometry(TERRAIN_SIZE * 1.6, TERRAIN_SIZE * 1.6, 1, 1);
  waterGeom.rotateX(-Math.PI / 2);
  const uvAttr = waterGeom.attributes.uv;
  for (let i = 0; i < uvAttr.count; i++) {
    uvAttr.setXY(i, waterGeom.attributes.position.getX(i), waterGeom.attributes.position.getZ(i));
  }
  const water = new THREE.Mesh(waterGeom, waterMaterial);
  water.position.y = WATER_LEVEL;
  water.receiveShadow = false;
  water.castShadow = false;
  water.name = 'water';

  const group = new THREE.Group();
  group.name = 'landscape';
  group.add(ground, ring, water);

  return {
    group,
    ground,
    water,
    heightAt,
    slopeAt,
    splatAt,
    waterLevel: WATER_LEVEL,
    groundMaterial,
    /** Pure function of `t`: the wave sets simply scroll. */
    update(t) {
      waterNormal.offset.set(t * 0.0135, t * 0.0092);
      waterNormal2.offset.set(-t * 0.031, t * 0.0245);
    },
  };
}
