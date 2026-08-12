/**
 * Wind.
 *
 * A single shader injection drives every plant in the world: trunks bend,
 * crowns lag, leaf cards flutter, grass ripples. The displacement is applied
 * in object space after `begin_vertex`, so an `InstancedMesh` whose geometry
 * origin sits at the base of the plant bends about its root rather than
 * sliding.
 *
 * The only input is `uTime`, which `update(t)` sets to `t`. There is no
 * accumulator, no frame counter and no clock, so frame 150 is frame 150
 * however long the machine took to get there.
 */

import * as THREE from 'three';

const WIND_PARS = /* glsl */ `
uniform float uWindTime;
uniform vec2  uWindDir;
uniform float uWindAmp;
uniform float uWindRate;
uniform float uWindHeight;
uniform float uWindFlutter;
uniform float uWindPhase;

float windHash( vec3 p ) {
  return fract( sin( dot( p, vec3( 12.9898, 78.233, 37.719 ) ) ) * 43758.5453123 );
}
`;

const WIND_BODY = /* glsl */ `
{
  vec3 windOrigin = vec3( 0.0 );
  #ifdef USE_INSTANCING
    windOrigin = instanceMatrix[ 3 ].xyz;
  #endif

  float ph = uWindPhase + windHash( floor( windOrigin * 2.13 + 0.5 ) ) * 6.2831853;

  // Height above the plant's own root, squared so the base stays planted.
  float hN = clamp( transformed.y / uWindHeight, 0.0, 1.0 );
  float bend = hN * hN;

  // A slow gust front travelling across the map, so the whole field does not
  // breathe in unison.
  float gust = 0.60 + 0.40 * sin( uWindTime * 0.23 - windOrigin.x * 0.0125 + windOrigin.z * 0.0085 );

  float s = sin( uWindTime * uWindRate + ph ) + 0.42 * sin( uWindTime * uWindRate * 2.31 + ph * 1.73 );
  vec2 off = uWindDir * ( uWindAmp * bend * gust * s );
  transformed.x += off.x;
  transformed.z += off.y;

  if ( uWindFlutter > 0.0 ) {
    float lp = windHash( floor( position * 9.7 + 0.5 ) );
    float f = sin( uWindTime * 4.1 + lp * 6.2831853 + ph ) * uWindFlutter * hN;
    transformed.x += f * 0.85;
    transformed.z += f * 0.60;
    transformed.y += f * 0.30;
  }
}
`;

export function createWind(opts = {}) {
  const dir = new THREE.Vector2(opts.dirX ?? 0.82, opts.dirZ ?? 0.57).normalize();
  /** @type {{value:number}[]} */
  const clocks = [];

  /**
   * Patch a material so anything drawn with it sways.
   *
   * @param {THREE.Material} material
   * @param {{amp:number, rate?:number, height:number, flutter?:number, phase?:number}} o
   *   `amp` metres of lateral travel at the crown, `height` the plant's height
   *   in the geometry's own units, `flutter` per-vertex leaf chatter.
   */
  function apply(material, o) {
    const time = { value: 0 };
    clocks.push(time);
    const uniforms = {
      uWindTime: time,
      uWindDir: { value: dir },
      uWindAmp: { value: o.amp ?? 0.15 },
      uWindRate: { value: o.rate ?? 0.9 },
      uWindHeight: { value: Math.max(0.01, o.height ?? 1) },
      uWindFlutter: { value: o.flutter ?? 0 },
      uWindPhase: { value: o.phase ?? 0 },
    };
    material.userData.wind = uniforms;
    material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, uniforms);
      shader.vertexShader = shader.vertexShader.replace(
        '#include <common>',
        `#include <common>\n${WIND_PARS}`
      );
      shader.vertexShader = shader.vertexShader.replace(
        '#include <begin_vertex>',
        `#include <begin_vertex>\n${WIND_BODY}`
      );
    };
    material.customProgramCacheKey = () => 'worldWind';
    material.needsUpdate = true;
    return uniforms;
  }

  return {
    dir,
    apply,
    /** Pure function of `t` — the only per-frame state the wind has. */
    update(t) {
      for (let i = 0; i < clocks.length; i++) clocks[i].value = t;
    },
    get materialCount() {
      return clocks.length;
    },
  };
}
