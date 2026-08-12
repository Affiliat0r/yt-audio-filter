/**
 * Sky, sun and lighting.
 *
 * The shadow camera rides along with the train so a modest shadow map stays
 * sharp over the whole endless loop; its centre is snapped to shadow-texel
 * multiples so the shadows do not swim as it moves.
 */

import * as THREE from 'three';
import { SKY } from './palette.mjs';
import { WORLD_UP } from './geom.mjs';

const SHADOW_HALF_SIZE = 58;
const SHADOW_MAP_SIZE = 4096;
const SUN_DISTANCE = 150;

const SKY_VERT = /* glsl */ `
varying vec3 vDirection;
void main() {
  vDirection = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const SKY_FRAG = /* glsl */ `
uniform vec3 uZenith;
uniform vec3 uMid;
uniform vec3 uHorizon;
varying vec3 vDirection;
void main() {
  float h = normalize(vDirection).y;
  vec3 c = mix(uHorizon, uMid, smoothstep(-0.05, 0.30, h));
  c = mix(c, uZenith, smoothstep(0.22, 0.90, h));
  gl_FragColor = vec4(c, 1.0);
  #include <colorspace_fragment>
}
`;

export function buildSky(rng) {
  const group = new THREE.Group();
  group.name = 'sky';

  // --- dome ---------------------------------------------------------------

  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(800, 32, 20),
    new THREE.ShaderMaterial({
      uniforms: {
        uZenith: { value: new THREE.Color(SKY.zenith) },
        uMid: { value: new THREE.Color(SKY.mid) },
        uHorizon: { value: new THREE.Color(SKY.horizon) },
      },
      vertexShader: SKY_VERT,
      fragmentShader: SKY_FRAG,
      side: THREE.BackSide,
      depthTest: false,
      depthWrite: false,
      fog: false,
    })
  );
  dome.renderOrder = -1000;
  dome.frustumCulled = false;
  group.add(dome);

  // --- sun ----------------------------------------------------------------

  const sunDirection = new THREE.Vector3(0.46, 0.76, 0.46).normalize();

  const sunDisc = new THREE.Mesh(
    new THREE.CircleGeometry(26, 32),
    new THREE.MeshBasicMaterial({ color: SKY.sun, fog: false, depthTest: false, depthWrite: false })
  );
  sunDisc.position.copy(sunDirection).multiplyScalar(680);
  sunDisc.lookAt(0, 0, 0);
  sunDisc.renderOrder = -900;
  sunDisc.frustumCulled = false;

  const sunHalo = new THREE.Mesh(
    new THREE.CircleGeometry(58, 32),
    new THREE.MeshBasicMaterial({
      color: SKY.sunHalo,
      transparent: true,
      opacity: 0.28,
      fog: false,
      depthTest: false,
      depthWrite: false,
    })
  );
  sunHalo.position.copy(sunDirection).multiplyScalar(660);
  sunHalo.lookAt(0, 0, 0);
  sunHalo.renderOrder = -950;
  sunHalo.frustumCulled = false;
  group.add(sunHalo, sunDisc);

  // --- clouds -------------------------------------------------------------

  const cloudLayer = new THREE.Group();
  cloudLayer.name = 'clouds';
  const clusters = 14;
  const puffsPerCluster = 6;
  const cloudMesh = new THREE.InstancedMesh(
    new THREE.IcosahedronGeometry(1, 1),
    new THREE.MeshLambertMaterial({
      color: SKY.cloud,
      emissive: SKY.cloudShade,
      emissiveIntensity: 0.45,
      flatShading: true,
      fog: false,
    }),
    clusters * puffsPerCluster
  );
  cloudMesh.castShadow = false;
  cloudMesh.receiveShadow = false;
  cloudMesh.frustumCulled = false;

  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const pos = new THREE.Vector3();
  const scale = new THREE.Vector3();
  let n = 0;
  for (let c = 0; c < clusters; c++) {
    const angle = (c / clusters) * Math.PI * 2 + rng.range(-0.2, 0.2);
    const radius = rng.range(140, 360);
    const cx = Math.cos(angle) * radius;
    const cz = Math.sin(angle) * radius;
    const cy = rng.range(105, 165);
    const spread = rng.range(9, 22);
    for (let p = 0; p < puffsPerCluster; p++) {
      pos.set(
        cx + rng.range(-spread, spread),
        cy + rng.range(-3, 5),
        cz + rng.range(-spread * 0.7, spread * 0.7)
      );
      const s = rng.range(7, 15);
      scale.set(s, s * rng.range(0.5, 0.75), s * rng.range(0.7, 1.0));
      q.setFromEuler(new THREE.Euler(rng.range(-0.3, 0.3), rng.range(0, Math.PI * 2), rng.range(-0.3, 0.3)));
      m.compose(pos, q, scale);
      cloudMesh.setMatrixAt(n++, m);
    }
  }
  cloudMesh.instanceMatrix.needsUpdate = true;
  cloudLayer.add(cloudMesh);
  group.add(cloudLayer);

  // --- lights -------------------------------------------------------------

  const sun = new THREE.DirectionalLight(0xfff4dd, 2.7);
  sun.castShadow = true;
  sun.shadow.mapSize.set(SHADOW_MAP_SIZE, SHADOW_MAP_SIZE);
  sun.shadow.camera.left = -SHADOW_HALF_SIZE;
  sun.shadow.camera.right = SHADOW_HALF_SIZE;
  sun.shadow.camera.top = SHADOW_HALF_SIZE;
  sun.shadow.camera.bottom = -SHADOW_HALF_SIZE;
  sun.shadow.camera.near = 20;
  sun.shadow.camera.far = SUN_DISTANCE + 140;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.35;
  sun.shadow.camera.updateProjectionMatrix();
  group.add(sun, sun.target);

  const hemi = new THREE.HemisphereLight(0xcbe8ff, 0x6d9450, 1.15);
  group.add(hemi);

  const ambient = new THREE.AmbientLight(0xffffff, 0.25);
  group.add(ambient);

  // --- shadow follow ------------------------------------------------------

  const zAxis = sunDirection.clone();
  const xAxis = new THREE.Vector3().crossVectors(WORLD_UP, zAxis).normalize();
  const yAxis = new THREE.Vector3().crossVectors(zAxis, xAxis).normalize();
  const texel = (2 * SHADOW_HALF_SIZE) / SHADOW_MAP_SIZE;
  const snapped = new THREE.Vector3();

  /**
   * Centre the shadow frustum on `focus`, snapped to whole shadow texels in
   * light space so shadow edges stay put between frames.
   */
  function focusShadow(focus) {
    const cx = Math.round(focus.dot(xAxis) / texel) * texel;
    const cy = Math.round(focus.dot(yAxis) / texel) * texel;
    const cz = focus.dot(zAxis);
    snapped
      .copy(xAxis)
      .multiplyScalar(cx)
      .addScaledVector(yAxis, cy)
      .addScaledVector(zAxis, cz);
    sun.target.position.copy(snapped);
    sun.position.copy(snapped).addScaledVector(zAxis, SUN_DISTANCE);
  }

  function update(t, focus) {
    focusShadow(focus);
    // Very slow drift so the sky is never quite static.
    cloudLayer.rotation.y = t * 0.0022;
  }

  return { group, sun, sunDirection, update };
}
