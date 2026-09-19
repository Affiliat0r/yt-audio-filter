/**
 * Steam and smoke — the one part of a train that genuinely wants to be a
 * simulation, and the one part that absolutely must not be.
 *
 * The obvious implementation integrates each particle's velocity once per
 * frame. That makes frame 150 depend on frames 0-149, so rendering a frame in
 * isolation, or re-rendering it, gives a different picture. The contract's
 * determinism rule forbids it.
 *
 * So every particle is evaluated **analytically**. A particle's identity is
 * `(exhaust beat number, slot within the beat)`; from `t` we know which beats
 * are still alive, and each particle's position is a closed-form function of
 * its own age:
 *
 *   drag-limited ejection   (v0/k)·(1 - e^(-k·age))
 *   buoyant rise            b·age
 *   inherited train motion  (V/kd)·(1 - e^(-kd·age))   along the track heading
 *                                                      *at the moment it left*
 *   wind                    W·age
 *   turbulent curl          A·(1 - e^(-age))·sin(ω·age + φ)
 *
 * Nothing carries over between calls. `update(150/30)` produces the same image
 * whether or not `update(149/30)` ever ran.
 */

import * as THREE from 'three';
import { smokeSprite } from './textures.mjs';

const UP = new THREE.Vector3(0, 1, 0);

/** Deterministic 32-bit integer hash -> [0, 1). */
function hash01(n) {
  let h = n | 0;
  h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d);
  h = Math.imul(h ^ (h >>> 12), 0x297a2d39);
  h ^= h >>> 15;
  return (h >>> 0) / 4294967296;
}

function buildMaterial(sprite, sootColor, steamColor) {
  const material = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 1.0,
    metalness: 0.0,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    envMapIntensity: 1.0,
  });
  material.name = 'steam';

  material.onBeforeCompile = (shader) => {
    shader.uniforms.uSprite = { value: sprite };
    shader.uniforms.uSoot = { value: new THREE.Color(sootColor) };
    shader.uniforms.uSteam = { value: new THREE.Color(steamColor) };

    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        `#include <common>
         attribute float aOpacity;
         attribute float aRot;
         attribute float aScale;
         attribute float aTint;
         varying float vOpacity;
         varying float vTint;
         varying vec2 vPuff;`
      )
      .replace(
        '#include <project_vertex>',
        `vOpacity = aOpacity;
         vTint = aTint;
         vPuff = position.xy * 2.0;
         vec4 puffCentre = modelViewMatrix * instanceMatrix * vec4( 0.0, 0.0, 0.0, 1.0 );
         float puffCos = cos( aRot );
         float puffSin = sin( aRot );
         vec2 puffQuad = vec2(
           position.x * puffCos - position.y * puffSin,
           position.x * puffSin + position.y * puffCos
         ) * aScale;
         vec4 mvPosition = vec4( puffCentre.xyz + vec3( puffQuad, 0.0 ), 1.0 );
         gl_Position = projectionMatrix * mvPosition;`
      );

    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        `#include <common>
         uniform sampler2D uSprite;
         uniform vec3 uSoot;
         uniform vec3 uSteam;
         varying float vOpacity;
         varying float vTint;
         varying vec2 vPuff;`
      )
      .replace(
        '#include <map_fragment>',
        `vec4 puffTexel = texture2D( uSprite, vPuff * 0.5 + 0.5 );
         float puffAlpha = puffTexel.a * vOpacity;
         if ( puffAlpha < 0.004 ) discard;
         diffuseColor.rgb *= mix( uSoot, uSteam, vTint );
         diffuseColor.a *= puffAlpha;`
      )
      .replace(
        '#include <normal_fragment_begin>',
        `#include <normal_fragment_begin>
         // Shade the billboard as if it were a sphere so the plume takes real
         // light from the scene rather than reading as a flat decal.
         float puffR2 = min( dot( vPuff, vPuff ), 1.0 );
         normal = normalize( vec3( vPuff * 0.85, sqrt( max( 0.05, 1.0 - puffR2 ) ) ) );`
      );
  };
  material.customProgramCacheKey = () => 'train-steam';
  return material;
}

/**
 * One analytic emitter.
 *
 * @param {object} cfg
 * @param {number} cfg.count      particles alive at once
 * @param {number} cfg.life       seconds a particle lives
 * @param {number} cfg.beat       seconds between emission bursts
 * @param {Function} cfg.origin   (spawnTime, outPos, outFwd) -> void
 */
class Emitter {
  constructor(parent, material, cfg) {
    this.cfg = cfg;
    const beats = Math.max(2, Math.round(cfg.life / cfg.beat));
    this.perBeat = Math.max(1, Math.round(cfg.count / beats));
    this.count = beats * this.perBeat;
    this.beats = beats;

    const geometry = new THREE.PlaneGeometry(1, 1);
    const n = this.count;
    this.aOpacity = new THREE.InstancedBufferAttribute(new Float32Array(n), 1);
    this.aRot = new THREE.InstancedBufferAttribute(new Float32Array(n), 1);
    this.aScale = new THREE.InstancedBufferAttribute(new Float32Array(n), 1);
    this.aTint = new THREE.InstancedBufferAttribute(new Float32Array(n), 1);
    geometry.setAttribute('aOpacity', this.aOpacity);
    geometry.setAttribute('aRot', this.aRot);
    geometry.setAttribute('aScale', this.aScale);
    geometry.setAttribute('aTint', this.aTint);

    this.mesh = new THREE.InstancedMesh(geometry, material, n);
    this.mesh.name = cfg.name || 'steam';
    this.mesh.castShadow = false;
    this.mesh.receiveShadow = false;
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = 12;
    this.mesh.matrixAutoUpdate = false;
    parent.add(this.mesh);

    this._m = new THREE.Matrix4();
    this._p = new THREE.Vector3();
    this._o = new THREE.Vector3();
    this._f = new THREE.Vector3();
    this._r = new THREE.Vector3();
    this.triangles = 2 * n;
  }

  update(t, wind) {
    const c = this.cfg;
    const beatIndex = Math.floor(t / c.beat);
    const n = this.count;
    const per = this.perBeat;
    const op = this.aOpacity.array;
    const rot = this.aRot.array;
    const sc = this.aScale.array;
    const tint = this.aTint.array;

    for (let i = 0; i < n; i++) {
      // Slot 0 holds the oldest generation so the strip draws back-to-front.
      const gen = Math.floor((n - 1 - i) / per);
      const slot = (n - 1 - i) % per;
      const beat = beatIndex - gen;
      const h0 = hash01(beat * 2654435761 + slot * 40503 + c.seed);
      const h1 = hash01(beat * 1103515245 + slot * 12345 + c.seed + 7919);
      const h2 = hash01(beat * 22695477 + slot * 65537 + c.seed + 104729);
      const h3 = hash01(beat * 69069 + slot * 8191 + c.seed + 1299709);

      const spawn = beat * c.beat + h0 * c.beat * c.spread;
      const age = t - spawn;
      if (age < 0 || age >= c.life) {
        // Every attribute *and* the matrix must be written even for a dead
        // particle. Leaving the previous frame's matrix in place would make the
        // buffer contents depend on which frame was rendered last — the exact
        // frame-to-frame coupling this whole design exists to avoid.
        sc[i] = 0;
        op[i] = 0;
        rot[i] = 0;
        tint[i] = 0;
        this._m.makeTranslation(0, -1e4, 0);
        this.mesh.setMatrixAt(i, this._m);
        continue;
      }

      c.origin(spawn, this._o, this._f);
      this._r.crossVectors(UP, this._f).normalize();

      // Drag-limited ejection along the emitter's own axis, plus buoyancy.
      const kv = c.dragUp;
      const rise = (c.speedUp / kv) * (1 - Math.exp(-kv * age)) + c.buoyancy * age;

      // Momentum inherited from a moving chimney, bleeding off into still air.
      const kf = c.dragFwd;
      const carry = (c.carrySpeed / kf) * (1 - Math.exp(-kf * age));

      // Turbulent curl: two out-of-phase oscillations that grow then saturate.
      const swell = 1 - Math.exp(-age * 1.4);
      const w0 = 1.1 + h1 * 1.7;
      const w1 = 0.8 + h2 * 1.5;
      const amp = c.turbulence * swell;
      const curlR = amp * Math.sin(w0 * age + h1 * 6.283) + (h1 - 0.5) * c.spreadOut * Math.pow(age, 0.7);
      const curlU = amp * 0.7 * Math.sin(w1 * age + h2 * 6.283);
      const curlF = amp * 0.8 * Math.cos(w0 * age * 0.8 + h3 * 6.283);

      this._p
        .copy(this._o)
        .addScaledVector(this._f, carry + c.launchFwd + curlF)
        .addScaledVector(UP, rise + curlU + c.launchUp)
        .addScaledVector(this._r, curlR + (h3 - 0.5) * c.jitterLat);
      this._p.x += wind.x * age;
      this._p.y += wind.y * age;
      this._p.z += wind.z * age;

      const u = age / c.life;
      const size = c.size0 + c.growth * Math.pow(age, 0.72);
      const fadeIn = THREE.MathUtils.smoothstep(age, 0, c.fadeIn);
      const fadeOut = Math.pow(1 - u, c.fadePower);
      const density = Math.pow(c.size0 / size, c.thinning);

      sc[i] = size;
      op[i] = c.alpha * fadeIn * fadeOut * density * (0.75 + h2 * 0.5);
      rot[i] = h1 * 6.283 + (h2 - 0.5) * c.spin * age;
      tint[i] = THREE.MathUtils.clamp(c.tint0 + (1 - c.tint0) * THREE.MathUtils.smoothstep(age, 0, c.tintTime) * (0.55 + h3 * 0.6), 0, 1);

      this._m.makeTranslation(this._p.x, this._p.y, this._p.z);
      this.mesh.setMatrixAt(i, this._m);
    }

    this.mesh.instanceMatrix.needsUpdate = true;
    this.aOpacity.needsUpdate = true;
    this.aRot.needsUpdate = true;
    this.aScale.needsUpdate = true;
    this.aTint.needsUpdate = true;
  }
}

/**
 * Build the chimney plume and the cylinder wisps.
 *
 * @param {THREE.Object3D} parent  world-space group (NOT the train body — the
 *                                 plume is left behind in world coordinates)
 * @param {object} deps  { rng, track, speed, distanceAt, railTop, locomotive }
 */
export function createSteam(parent, deps) {
  const { rng, track, speed, distanceAt, railTop, chimney, cylinderVents, quality = 1 } = deps;

  const sprite = smokeSprite((rng.next() * 1e9) | 0);
  const material = buildMaterial(sprite, 0x4a4a4e, 0xfbfcfd);

  // Four exhaust beats per revolution of the driving wheels: two cylinders,
  // double acting. This is what gives a steam locomotive its rhythm, and it is
  // the reason the plume comes out in discrete puffs rather than as a stream.
  const wheelPeriod = (Math.PI * 2 * deps.driverRadius) / Math.max(0.1, speed);
  const beat = wheelPeriod / 4;

  const wind = new THREE.Vector3(rng.range(-1.1, 1.1), 0.05, rng.range(-1.1, 1.1));

  const tmpR = new THREE.Vector3();

  /** Where a point fixed to the vehicle was, in world space, at time `ts`. */
  function vehiclePoint(local, ts, outPos, outFwd) {
    const d = distanceAt(ts) + local.z;
    track.pointAt(d, outPos);
    track.forwardAt(d, outFwd);
    tmpR.crossVectors(UP, outFwd).normalize();
    outPos.addScaledVector(UP, railTop + local.y).addScaledVector(tmpR, local.x);
    return outPos;
  }

  const emitters = [];

  emitters.push(
    new Emitter(parent, material, {
      name: 'steam-chimney',
      seed: (rng.next() * 1e9) | 0,
      count: Math.round(384 * quality),
      life: 5.6,
      beat,
      spread: 0.6,
      speedUp: 6.4,
      dragUp: 2.4,
      buoyancy: 0.6,
      carrySpeed: speed,
      dragFwd: 1.1,
      launchUp: 0.16,
      launchFwd: 0,
      turbulence: 0.46,
      spreadOut: 0.5,
      jitterLat: 0.18,
      size0: 0.56,
      growth: 1.15,
      thinning: 0.48,
      alpha: 1.0,
      fadeIn: 0.05,
      fadePower: 1.6,
      spin: 0.55,
      tint0: 0.34,
      tintTime: 0.4,
      origin: (ts, outPos, outFwd) => vehiclePoint(chimney, ts, outPos, outFwd),
    })
  );

  for (let v = 0; v < cylinderVents.length; v++) {
    const vent = cylinderVents[v];
    emitters.push(
      new Emitter(parent, material, {
        name: `steam-cylinder-${v}`,
        seed: ((rng.next() * 1e9) | 0) + v * 77,
        count: Math.round(46 * quality),
        life: 1.15,
        beat: beat * 2,
        spread: 0.3,
        speedUp: -1.6,
        dragUp: 3.2,
        buoyancy: 0.55,
        carrySpeed: speed * 0.55,
        dragFwd: 2.4,
        launchUp: 0,
        launchFwd: 0.1,
        turbulence: 0.34,
        spreadOut: 0.55,
        jitterLat: 0.22,
        size0: 0.26,
        growth: 0.95,
        thinning: 0.5,
        alpha: 0.4,
        fadeIn: 0.05,
        fadePower: 1.5,
        spin: 0.9,
        tint0: 0.85,
        tintTime: 0.2,
        origin: (ts, outPos, outFwd) => vehiclePoint(vent, ts, outPos, outFwd),
      })
    );
  }

  let triangles = 0;
  for (const e of emitters) triangles += e.triangles;

  const gust = new THREE.Vector3();

  return {
    material,
    triangles,
    update(t) {
      // A slow breeze variation, derived from t so it stays reproducible.
      gust.set(
        wind.x + Math.sin(t * 0.21 + 1.3) * 0.45,
        wind.y + Math.sin(t * 0.17) * 0.06,
        wind.z + Math.cos(t * 0.19 + 0.4) * 0.45
      );
      for (const e of emitters) e.update(t, gust);
    },
    dispose() {
      for (const e of emitters) {
        e.mesh.geometry.dispose();
        e.mesh.parent?.remove(e.mesh);
      }
      material.dispose();
      sprite.dispose();
    },
  };
}
