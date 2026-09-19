# `visuals/` — contract

Procedural three.js movies rendered headlessly to MP4. Our own footage, so
no YouTube bot detection, no SABR wall, no Content ID exposure.

**v2 — quality rebuild.** v1 proved the pipeline with flat-shaded
primitives. It looked like a prototype. v2 replaces the look entirely:
photoscanned PBR materials, HDRI-based lighting, and a full cinematic
post-processing chain.

## The budget insight that drives every decision here

We render **offline**. v1 measured 1 ms/frame to render and ~100 ms/frame
to capture — so there is roughly a **100x per-frame compute budget sitting
unused**. Spend it. Anything a real-time engine has to cut for framerate,
we can afford: supersampling, high-sample ambient occlusion, real depth of
field, many-sample soft shadows. Quality beats speed until a frame costs
more than ~80 ms, and only then is it worth discussing.

## Non-negotiable: determinism

`renderAt(t)` is a pure function of `(seed, t)`. Never `Date.now()`,
`performance.now()`, `THREE.Clock`, `requestAnimationFrame`, or
`Math.random()`. All randomness comes from the seeded PRNG. Frame 150 must
be frame 150 whether the machine took 4 seconds or 40 to reach it, and the
same seed must reproduce a world exactly.

This bit everyone in v1 and it will bite again: **temporal effects break
determinism.** TAA and motion blur that accumulate across frames must
derive their jitter/history from `t`, never from a frame counter that
depends on call order.

## Ownership — do not edit outside your own paths

| Part | Owns | Builds |
|------|------|--------|
| A — assets & materials | `visuals/lib/assets.mjs`, `visuals/lib/materials.mjs`, `visuals/lib/geom.mjs` | CC0 fetch + cache, PBR material factory, beveled geometry helpers |
| B — look pipeline | `visuals/lib/renderer.mjs`, `visuals/lib/lighting.mjs`, `visuals/lib/post.mjs` | renderer config, sun/sky/IBL/CSM, post-processing chain |
| C — world | `visuals/scene/world/` | terrain, track, vegetation, props, characters |
| D — train | `visuals/scene/train/` | locomotive, carriages, suspension, steam |
| (owner) | `visuals/scene/index.html`, `main.mjs`, `world.mjs` | page shell, `window.__movie`, camera, assembly |

`visuals/capture/`, `visuals/encode/`, `visuals/cli.mjs`, `visuals/test/`
are settled — do not touch them.

## Scene API (unchanged from v1)

`visuals/scene/index.html` defines on `window`:

```js
window.__movie = {
  async init({seed, width, height, durationSeconds}) {},
  async renderAt(t) {},   // canvas holds the finished frame when this resolves
  ready: false,
};
```

Canvas is `#stage`, exactly `width x height`, `display:block`, page margin
0, no scrollbars, `setPixelRatio(1)`. Import three via the import map
already in place. Everything loads from disk — **no network at render
time**; assets are fetched in a separate prefetch step.

## Part A — assets, materials, geometry

```js
// lib/assets.mjs
export async function ensureAssets(manifest, {cacheDir, onProgress})
export function texturePath(slug, mapName, res)   // local file path
export function hdriPath(slug, res)
export const CACHE_DIR                            // default visuals/assets
```

Poly Haven (CC0, no attribution required, safe for monetized YouTube).
API: `https://api.polyhaven.com/files/<slug>` returns a JSON tree of
download URLs per map type and resolution. Fetch once, cache on disk keyed
by `<slug>/<res>/<map>.<ext>`, verify size, and **never re-download a
cached file**. Renders must work with the network off once the cache is
warm.

```js
// lib/materials.mjs
export async function pbr(slug, {repeat=[1,1], res='2k', ...overrides})
  // -> THREE.MeshStandardMaterial with map/normalMap/roughnessMap/aoMap/
  //    displacementMap wired, correct colorSpace, repeat wrapping, and
  //    a shared cache so the same slug+res is loaded once.
export function solid({color, roughness=0.8, metalness=0, ...})
export function painted({color, roughness=0.35, clearcoat=true})  // toy paint
```

sRGB for albedo, Linear for normal/roughness/AO — getting this wrong is the
classic washed-out-render bug. AO needs `uv2`; set it up.

```js
// lib/geom.mjs
export function beveledBox(w, h, d, {bevel=0.02, segments=2})
export function beveledCylinder(rTop, rBottom, h, {bevel, radialSegments})
export function roundedProfile(points, {bevel})   // lathe/extrude profiles
export function mergeAll(geometries)              // BufferGeometryUtils
```

**Bevels are the highest-value item in this whole contract.** A perfectly
sharp 90-degree edge never occurs in reality and is the single strongest
"this is CG" tell. Every visible edge gets one, however small — they catch
a highlight and that is what sells the material.

## Part B — the look pipeline

```js
// lib/renderer.mjs
export function createRenderer({canvas, width, height, supersample=2})
  // WebGLRenderer, ACESFilmicToneMapping, correct output colorSpace,
  // shadowMap on with PCFSoft, physically-correct lights.
  // supersample renders at width*ss x height*ss and downsamples — this is
  // the cheapest large quality win we have and the budget is there for it.
```

```js
// lib/lighting.mjs
export async function createLighting(scene, renderer, {hdri, sunElevation,
                                                      sunAzimuth, ...})
  // -> { sun, csm, envMap, update(camera, t) }
```

HDRI environment via `RGBELoader` + `PMREMGenerator` for image-based
lighting — this, not the directional light, is what makes materials read
as real. Cascaded shadow maps (`csm/CSM.js`) so shadows stay sharp near the
camera and still cover the distance. Physical sky (`objects/Sky.js`) driven
by the same sun vector so sky and lighting agree.

```js
// lib/post.mjs
export function createComposer(renderer, scene, camera, {width, height, quality})
  // -> { composer, render(t), setFocus(distance), resize(w,h) }
```

Chain, in order: RenderPass → GTAO → SSR (optional) → Bloom → Bokeh DOF →
TAA/SMAA → OutputPass (ACES + sRGB). Add subtle grain, vignette, and
chromatic aberration as a final shader — subtle, they read as film rather
than as effects. `render(t)` takes `t` so any temporal jitter is derived
from it, never from a frame counter.

## Part C — world

`visuals/scene/world/` exports `createWorld(scene, {rng, materials, ...})`
returning `{ update(t, trainState), track }`. Terrain with real ground
materials and a smooth corridor for the track; ballast, rails, sleepers;
instanced vegetation with wind driven by `t`; rocks, buildings, fences;
characters near the line. Consume Part A's helpers rather than building
raw geometry — every edge beveled, every surface a real material.

## Part D — train

`visuals/scene/train/` exports `createTrain(scene, {rng, materials, track})`
returning `{ group, update(t) -> trainState }` where `trainState` carries at
least `{ position, direction, speed, headPosition }` for the camera to
follow. Detailed locomotive and carriages, painted-metal materials with
clearcoat, correct wheel rotation from ground speed, coupling rods,
suspension and body sway over the sleepers, and simulated steam/smoke.

## Definition of done

`node visuals/cli.mjs --seconds 10` produces a 10.0s MP4 that a viewer
would call polished rather than programmer-art, rendering under ~80
ms/frame at 1280x720, deterministic across runs on the same seed, with the
network disconnected after the asset prefetch.
