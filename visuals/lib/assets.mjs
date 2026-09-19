/**
 * lib/assets.mjs — CC0 asset fetch and on-disk cache.
 *
 * Every texture and HDRI in this project comes from Poly Haven, which is CC0:
 * no attribution required, no share-alike, safe on a monetised YouTube channel.
 *
 * The cache is the contract. `ensureAssets()` is a prefetch step that runs once
 * with the network up; after that the renderer only ever touches disk. A warm
 * cache never issues a request — not even to the Poly Haven API — because the
 * per-asset `index.json` written at fetch time records every file's size and
 * md5, so the "is it already here and intact?" question is answered by `stat`.
 *
 * Layout:
 *
 *   visuals/assets/<slug>/<res>/diffuse.jpg
 *                              /nor_gl.jpg
 *                              /arm.jpg          (AO=R, roughness=G, metal=B)
 *                              /disp.jpg
 *                              /index.json       (size + md5 + source url)
 *   visuals/assets/<slug>/<res>/hdri.hdr
 *
 * This module is imported by `materials.mjs`, which runs in the browser, so
 * nothing here may touch a node builtin at module scope. The path helpers are
 * pure string maths; `node:fs` and friends are imported lazily inside the
 * functions that actually download.
 *
 * CLI:
 *   node visuals/lib/assets.mjs                 warm the whole manifest
 *   node visuals/lib/assets.mjs --res 4k        higher resolution
 *   node visuals/lib/assets.mjs --verify md5    re-hash cached files
 *   node visuals/lib/assets.mjs --force         ignore the cache
 */

const IS_NODE = typeof process !== 'undefined' && !!(process.versions && process.versions.node);

const API = 'https://api.polyhaven.com';

// ---------------------------------------------------------------------------
// Cache location
// ---------------------------------------------------------------------------

const ASSETS_URL = new URL('../assets/', import.meta.url);

/** file:// URL -> native path, without pulling in node:url (browser-safe). */
function urlToPath(url) {
  let p = decodeURIComponent(new URL(url).pathname);
  if (/^\/[A-Za-z]:/.test(p)) p = p.slice(1);
  if (IS_NODE && process.platform === 'win32') p = p.replace(/\//g, '\\');
  return p;
}

/**
 * Root of the asset cache.
 *
 * Under node this is a filesystem path; in the browser it is the `file://` URL
 * of the same directory, which is what `THREE.TextureLoader` wants. Both forms
 * are produced from `import.meta.url`, so the cache is always
 * `visuals/assets/` regardless of the working directory.
 */
export const CACHE_DIR = IS_NODE ? urlToPath(ASSETS_URL) : ASSETS_URL.href;

/** Join cache-relative segments into whatever form `CACHE_DIR` is in. */
function cacheJoin(root, ...segments) {
  const rel = segments.filter(Boolean).join('/');
  if (!IS_NODE) return new URL(rel, root.endsWith('/') ? root : root + '/').href;
  const sep = process.platform === 'win32' ? '\\' : '/';
  const base = root.replace(/[\\/]+$/, '');
  return base + sep + rel.split('/').join(sep);
}

// ---------------------------------------------------------------------------
// Map naming
// ---------------------------------------------------------------------------

/**
 * Friendly map name -> {file, apiKeys, formats}.
 *
 * `apiKeys` is the priority list of Poly Haven map names to look for; the first
 * that exists wins. Normal maps take `nor_gl` (OpenGL, green-up) because that
 * is three.js's convention — `nor_dx` would need the green channel inverted.
 */
export const MAPS = {
  diffuse: { file: 'diffuse', apiKeys: ['Diffuse', 'diffuse', 'Color', 'albedo'] },
  normal: { file: 'nor_gl', apiKeys: ['nor_gl', 'Normal', 'nor_dx'] },
  arm: { file: 'arm', apiKeys: ['arm'] },
  rough: { file: 'rough', apiKeys: ['Rough', 'roughness'] },
  ao: { file: 'ao', apiKeys: ['AO', 'ao'] },
  metal: { file: 'metal', apiKeys: ['Metal', 'metallic', 'Metalness'] },
  disp: { file: 'disp', apiKeys: ['Displacement', 'Height', 'disp', 'bump'] },
};

const MAP_ALIASES = {
  map: 'diffuse',
  albedo: 'diffuse',
  color: 'diffuse',
  colour: 'diffuse',
  basecolor: 'diffuse',
  nor: 'normal',
  nor_gl: 'normal',
  normalmap: 'normal',
  orm: 'arm',
  roughness: 'rough',
  ambientocclusion: 'ao',
  metalness: 'metal',
  metallic: 'metal',
  displacement: 'disp',
  height: 'disp',
};

/** Normalise any spelling of a map name to a `MAPS` key. */
export function normalizeMapName(name) {
  const k = String(name).toLowerCase().replace(/[\s_-]/g, '');
  if (MAPS[k]) return k;
  const direct = String(name).toLowerCase();
  if (MAPS[direct]) return direct;
  const alias = MAP_ALIASES[k] || MAP_ALIASES[direct];
  if (alias) return alias;
  throw new Error(`assets: unknown map name "${name}" (known: ${Object.keys(MAPS).join(', ')})`);
}

/**
 * Textures are always cached as `.jpg` and HDRIs as `.hdr`, so `texturePath()`
 * and `hdriPath()` can stay synchronous and browser-safe — the filename never
 * depends on what the API happened to offer. Every Poly Haven texture publishes
 * jpg at every resolution; a slug that does not is a hard error rather than a
 * silently differently-named file the loader would fail to find.
 */
const TEXTURE_FORMAT = 'jpg';
const HDRI_FORMAT = 'hdr';

/** The maps every texture in the manifest gets unless it says otherwise. */
export const DEFAULT_MAPS = ['diffuse', 'normal', 'arm', 'disp'];

// ---------------------------------------------------------------------------
// The curated manifest
// ---------------------------------------------------------------------------

/**
 * Role -> Poly Haven texture slug.
 *
 * Every slug below was confirmed present via `GET /assets?t=textures` and
 * `GET /files/<slug>` rather than guessed. Roles are what the world and train
 * modules should reference; the slug is an implementation detail that can be
 * swapped without touching scene code.
 *
 * `repeat` is the suggested tiles-per-metre for `materials.pbr()`, chosen so
 * the real-world feature size of the scan lands at roughly the right scale.
 * Poly Haven textures are mostly 2 m x 2 m scans, hence 0.5 as the baseline.
 */
export const TEX = {
  meadow: 'aerial_grass_rock',
  grass: 'sparse_grass',
  ballast: 'gravel_floor_02',
  dirt: 'dirt_floor',
  rock: 'rock_boulder_dry',
  bark: 'bark_brown_02',
  sleeper: 'weathered_brown_planks',
  planks: 'wood_planks',
  paintedMetal: 'green_metal_rust',
  rustedMetal: 'rusty_metal_02',
  treadPlate: 'metal_plate',
  roofTiles: 'clay_roof_tiles_02',
  plaster: 'painted_plaster_wall',
  brick: 'red_brick_03',
};

/** Role -> Poly Haven HDRI slug. All outdoor, all "puresky" (no baked ground). */
export const HDRI = {
  middayClear: 'kloofendal_43d_clear_puresky',
  middayPartlyCloudy: 'kloofendal_48d_partly_cloudy_puresky',
  goldenHour: 'kloppenheim_06_puresky',
  sunset: 'belfast_sunset_puresky',
};

/**
 * Everything this project needs, in one object. `ensureAssets()` with no
 * argument warms exactly this.
 */
export const MANIFEST = {
  textures: [
    { slug: TEX.meadow, role: 'meadow', repeat: [0.35, 0.35], note: 'primary ground / terrain' },
    { slug: TEX.grass, role: 'grass', repeat: [0.6, 0.6], note: 'grass patches, verges' },
    { slug: TEX.ballast, role: 'ballast', repeat: [0.7, 0.7], note: 'track ballast, ~3 cm stones' },
    { slug: TEX.dirt, role: 'dirt', repeat: [0.7, 0.7], note: 'paths, bare earth' },
    { slug: TEX.rock, role: 'rock', repeat: [0.5, 0.5], note: 'boulders, cuttings' },
    { slug: TEX.bark, role: 'bark', repeat: [1.5, 1.0], note: 'tree trunks' },
    { slug: TEX.sleeper, role: 'sleeper', repeat: [1.0, 1.0], note: 'sleepers, fences' },
    { slug: TEX.planks, role: 'planks', repeat: [0.8, 0.8], note: 'decking, buildings' },
    { slug: TEX.paintedMetal, role: 'paintedMetal', repeat: [1.0, 1.0], note: 'loco bodywork wear' },
    { slug: TEX.rustedMetal, role: 'rustedMetal', repeat: [1.2, 1.2], note: 'ironwork, tanks' },
    {
      slug: TEX.treadPlate,
      role: 'treadPlate',
      repeat: [1.0, 1.0],
      maps: ['diffuse', 'normal', 'arm', 'metal', 'disp'],
      note: 'diamond tread plate: footplates, walkways. Carries a real Metal map. '
        + 'Rails are smooth steel — use materials.metal() for those.',
    },
    { slug: TEX.roofTiles, role: 'roofTiles', repeat: [0.28, 0.28], note: 'roofs, ~28 cm pantiles' },
    { slug: TEX.plaster, role: 'plaster', repeat: [0.4, 0.4], note: 'rendered walls' },
    { slug: TEX.brick, role: 'brick', repeat: [0.8, 0.8], note: 'station, bridge abutments' },
  ],
  hdris: [
    { slug: HDRI.middayPartlyCloudy, role: 'middayPartlyCloudy', note: 'default key light' },
    { slug: HDRI.middayClear, role: 'middayClear', note: 'hard sun, deep shadows' },
    { slug: HDRI.goldenHour, role: 'goldenHour', note: 'low warm sun' },
    { slug: HDRI.sunset, role: 'sunset', note: 'soft dusk' },
  ],
};

/** The manifest entry for a texture slug, or null. */
export function manifestEntry(slug) {
  return MANIFEST.textures.find((t) => t.slug === slug) || null;
}

/** Suggested tiles-per-metre for a slug, from the manifest. */
export function suggestedRepeat(slug) {
  const entry = manifestEntry(slug);
  return entry && entry.repeat ? entry.repeat.slice() : [1, 1];
}

/**
 * Which maps are actually cached for a slug.
 *
 * `materials.pbr()` uses this so it never requests a file the prefetch did not
 * download — a 404 on file:// is a silent black texture, which is exactly the
 * kind of bug that is hard to spot after the fact.
 */
export function manifestMaps(slug) {
  const entry = manifestEntry(slug);
  return (entry && entry.maps ? entry.maps : DEFAULT_MAPS).map(normalizeMapName);
}

// ---------------------------------------------------------------------------
// Path helpers (browser-safe)
// ---------------------------------------------------------------------------

/**
 * Local path of one texture map.
 *
 * Returns a filesystem path under node and a `file://` URL in the browser, so
 * the same call site works in the prefetch CLI and in `THREE.TextureLoader`.
 */
export function texturePath(slug, mapName, res = '2k', cacheDir = CACHE_DIR) {
  const key = normalizeMapName(mapName);
  return cacheJoin(cacheDir, slug, res, `${MAPS[key].file}.jpg`);
}

/** Local path of one HDRI. */
export function hdriPath(slug, res = '2k', cacheDir = CACHE_DIR) {
  return cacheJoin(cacheDir, slug, res, 'hdri.hdr');
}

/** Local path of a slug's cache index. */
export function indexPath(slug, res, cacheDir = CACHE_DIR) {
  return cacheJoin(cacheDir, slug, res, 'index.json');
}

// ---------------------------------------------------------------------------
// Node-only: fetch and cache
// ---------------------------------------------------------------------------

let _node = null;
async function node() {
  if (!IS_NODE) throw new Error('assets: downloading is only available under node');
  if (!_node) {
    const [fs, path, crypto] = await Promise.all([
      import('node:fs/promises'),
      import('node:path'),
      import('node:crypto'),
    ]);
    _node = { fs: fs.default || fs, path: path.default || path, crypto: crypto.default || crypto };
  }
  return _node;
}

async function readJSON(file) {
  const { fs } = await node();
  try {
    return JSON.parse(await fs.readFile(file, 'utf8'));
  } catch {
    return null;
  }
}

async function statSize(file) {
  const { fs } = await node();
  try {
    return (await fs.stat(file)).size;
  } catch {
    return -1;
  }
}

function md5(buffer, crypto) {
  return crypto.createHash('md5').update(buffer).digest('hex');
}

/** Pick the best available resolution, preferring the one asked for. */
function pickRes(tree, res) {
  if (tree[res]) return res;
  for (const fallback of ['2k', '1k', '4k', '8k']) if (tree[fallback]) return fallback;
  return Object.keys(tree)[0];
}

/** Resolve one map of one texture to {url, size, md5, format} from the API tree. */
function resolveTextureFile(files, mapKey, res) {
  const spec = MAPS[mapKey];
  for (const apiKey of spec.apiKeys) {
    const tree = files[apiKey];
    if (!tree || typeof tree !== 'object') continue;
    const actualRes = pickRes(tree, res);
    const byFormat = tree[actualRes];
    const entry = byFormat && byFormat[TEXTURE_FORMAT];
    if (entry && entry.url) {
      return { url: entry.url, size: entry.size, md5: entry.md5, apiKey, res: actualRes };
    }
  }
  return null;
}

function resolveHdriFile(files, res) {
  const tree = files.hdri;
  if (!tree) return null;
  const actualRes = pickRes(tree, res);
  const byFormat = tree[actualRes];
  const entry = byFormat && byFormat[HDRI_FORMAT];
  if (!entry || !entry.url) return null;
  return { url: entry.url, size: entry.size, md5: entry.md5, apiKey: 'hdri', res: actualRes };
}

async function fetchFileTree(slug) {
  const res = await fetch(`${API}/files/${encodeURIComponent(slug)}`);
  if (!res.ok) {
    throw new Error(`assets: Poly Haven API returned ${res.status} for "${slug}"`);
  }
  return res.json();
}

/** Download to a temp name and rename, so a killed prefetch never leaves a half file. */
async function downloadTo(dest, spec, { retries = 3, verifyMd5 = true } = {}) {
  const { fs, crypto } = await node();
  let lastErr = null;
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const res = await fetch(spec.url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = Buffer.from(await res.arrayBuffer());
      if (spec.size && buf.length !== spec.size) {
        throw new Error(`size mismatch: got ${buf.length}, API says ${spec.size}`);
      }
      if (verifyMd5 && spec.md5) {
        const got = md5(buf, crypto);
        if (got !== spec.md5) throw new Error(`md5 mismatch: got ${got}, API says ${spec.md5}`);
      }
      const tmp = `${dest}.part`;
      await fs.writeFile(tmp, buf);
      await fs.rename(tmp, dest);
      return buf.length;
    } catch (err) {
      lastErr = err;
      if (attempt < retries) await sleep(400 * attempt);
    }
  }
  throw new Error(`assets: failed to download ${spec.url} — ${lastErr && lastErr.message}`);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/** Bounded-concurrency map. */
async function pool(items, limit, worker) {
  const results = new Array(items.length);
  let next = 0;
  const runners = new Array(Math.min(limit, items.length)).fill(0).map(async () => {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await worker(items[i], i);
    }
  });
  await Promise.all(runners);
  return results;
}

/**
 * Make sure every asset in `manifest` is on disk, downloading what is missing.
 *
 * Skips the network entirely when the cache is already complete — that check
 * uses the `index.json` written by a previous run, so a warm cache works with
 * the network off, which is the whole point.
 *
 * @param {object} [manifest=MANIFEST]
 * @param {object} [opts]
 * @param {string} [opts.cacheDir=CACHE_DIR]
 * @param {string} [opts.res='2k']            default resolution
 * @param {string} [opts.hdriRes]             defaults to `res`
 * @param {number} [opts.concurrency=6]
 * @param {boolean} [opts.force=false]        re-download even if cached
 * @param {'size'|'md5'|'none'} [opts.verify='size'] how hard to check cached files
 * @param {(p:object)=>void} [opts.onProgress]
 * @returns {Promise<{files:number, downloaded:number, bytes:number, cached:number, elapsedMs:number, entries:object[]}>}
 */
export async function ensureAssets(manifest = MANIFEST, opts = {}) {
  const {
    cacheDir = CACHE_DIR,
    res = '2k',
    hdriRes = null,
    concurrency = 6,
    force = false,
    verify = 'size',
    onProgress = null,
  } = opts;

  const { fs, path, crypto } = await node();
  const started = Date.now();

  const jobs = [];
  for (const t of manifest.textures || []) {
    jobs.push({
      kind: 'texture',
      slug: t.slug,
      res: t.res || res,
      maps: (t.maps || DEFAULT_MAPS).map(normalizeMapName),
      role: t.role,
    });
  }
  for (const h of manifest.hdris || []) {
    jobs.push({ kind: 'hdri', slug: h.slug, res: h.res || hdriRes || res, role: h.role });
  }

  const total = jobs.reduce((n, j) => n + (j.kind === 'hdri' ? 1 : j.maps.length), 0);
  let done = 0;
  let downloaded = 0;
  let cached = 0;
  let bytes = 0; // transferred this run
  let cachedBytes = 0; // already on disk

  const report = (extra) => {
    if (!onProgress) return;
    onProgress({
      done,
      total,
      downloaded,
      cached,
      bytes,
      cachedBytes,
      elapsedMs: Date.now() - started,
      ...extra,
    });
  };

  const entries = await pool(jobs, Math.max(1, concurrency), async (job) => {
    const dir = cacheJoin(cacheDir, job.slug, job.res);
    await fs.mkdir(dir, { recursive: true });
    const idxFile = indexPath(job.slug, job.res, cacheDir);
    const index = (!force && (await readJSON(idxFile))) || { slug: job.slug, res: job.res, files: {} };
    if (!index.files || typeof index.files !== 'object') index.files = {};

    const wanted =
      job.kind === 'hdri'
        ? [{ key: 'hdri', dest: hdriPath(job.slug, job.res, cacheDir) }]
        : job.maps.map((m) => ({ key: m, dest: texturePath(job.slug, m, job.res, cacheDir) }));

    // -- what is already good? ---------------------------------------------
    const missing = [];
    for (const w of wanted) {
      const rec = index.files[w.key];
      const size = await statSize(w.dest);
      let ok = !force && size > 0 && !!rec;
      if (ok && verify !== 'none' && rec.size && size !== rec.size) ok = false;
      if (ok && verify === 'md5' && rec.md5) {
        const buf = await fs.readFile(w.dest);
        if (md5(buf, crypto) !== rec.md5) ok = false;
      }
      if (ok) {
        cached++;
        done++;
        cachedBytes += size;
        report({ slug: job.slug, map: w.key, status: 'cached' });
      } else {
        missing.push(w);
      }
    }

    if (missing.length === 0) return { ...job, status: 'cached' };

    // -- only now do we need the network -----------------------------------
    let files;
    try {
      files = await fetchFileTree(job.slug);
    } catch (err) {
      throw new Error(
        `assets: "${job.slug}" is not fully cached and the Poly Haven API is unreachable. ` +
          `Run the prefetch once with a connection: node visuals/lib/assets.mjs. (${err.message})`
      );
    }

    for (const w of missing) {
      const spec =
        job.kind === 'hdri'
          ? resolveHdriFile(files, job.res)
          : resolveTextureFile(files, w.key, job.res);
      if (!spec) {
        done++;
        report({ slug: job.slug, map: w.key, status: 'unavailable' });
        continue;
      }
      const n = await downloadTo(w.dest, spec, { verifyMd5: true });
      index.files[w.key] = {
        file: path.basename(w.dest),
        size: spec.size || n,
        md5: spec.md5 || null,
        url: spec.url,
        apiKey: spec.apiKey,
        res: spec.res,
      };
      downloaded++;
      done++;
      bytes += n;
      report({ slug: job.slug, map: w.key, status: 'downloaded', fileBytes: n });
      await fs.writeFile(idxFile, JSON.stringify(index, null, 2));
    }

    await fs.writeFile(idxFile, JSON.stringify(index, null, 2));
    return { ...job, status: 'fetched' };
  });

  return {
    files: total,
    downloaded,
    cached,
    bytes,
    cachedBytes,
    elapsedMs: Date.now() - started,
    entries,
  };
}

/**
 * True when every file the manifest names is already on disk.
 * Never touches the network — use it to fail fast before a render.
 */
export async function isCacheWarm(manifest = MANIFEST, opts = {}) {
  const { cacheDir = CACHE_DIR, res = '2k', hdriRes = null } = opts;
  const check = async (file) => (await statSize(file)) > 0;
  for (const t of manifest.textures || []) {
    const r = t.res || res;
    for (const m of (t.maps || DEFAULT_MAPS).map(normalizeMapName)) {
      if (!(await check(texturePath(t.slug, m, r, cacheDir)))) return false;
    }
  }
  for (const h of manifest.hdris || []) {
    if (!(await check(hdriPath(h.slug, h.res || hdriRes || res, cacheDir)))) return false;
  }
  return true;
}

/** Total bytes currently in the cache. */
export async function cacheSize(cacheDir = CACHE_DIR) {
  const { fs, path } = await node();
  let total = 0;
  const walk = async (dir) => {
    let list;
    try {
      list = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of list) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) await walk(p);
      else total += (await fs.stat(p)).size;
    }
  };
  await walk(cacheDir);
  return total;
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(2)} GB`;
}

async function main(argv) {
  const args = { res: '2k', concurrency: 6, force: false, verify: 'size' };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--res') args.res = argv[++i];
    else if (a === '--hdri-res') args.hdriRes = argv[++i];
    else if (a === '--concurrency') args.concurrency = Number(argv[++i]);
    else if (a === '--verify') args.verify = argv[++i];
    else if (a === '--force') args.force = true;
    else if (a === '--help' || a === '-h') {
      console.log(
        'usage: node visuals/lib/assets.mjs [--res 2k] [--hdri-res 2k] ' +
          '[--concurrency 6] [--verify size|md5|none] [--force]'
      );
      return 0;
    } else {
      console.error(`unknown argument: ${a}`);
      return 2;
    }
  }

  const total =
    MANIFEST.textures.reduce((n, t) => n + (t.maps || DEFAULT_MAPS).length, 0) +
    MANIFEST.hdris.length;
  console.log(
    `warming ${MANIFEST.textures.length} textures + ${MANIFEST.hdris.length} HDRIs ` +
      `(${total} files) at ${args.res} into ${CACHE_DIR}`
  );

  let lastLine = 0;
  const result = await ensureAssets(MANIFEST, {
    ...args,
    onProgress: (p) => {
      const pct = ((p.done / p.total) * 100).toFixed(0).padStart(3);
      const tag = p.status === 'downloaded' ? 'GET ' : p.status === 'cached' ? 'hit ' : 'MISS';
      const line = `[${pct}%] ${tag} ${p.slug}/${p.map}`;
      lastLine = Math.max(lastLine, line.length);
      console.log(line.padEnd(lastLine));
    },
  });

  const onDisk = await cacheSize();
  console.log('');
  console.log(`files      ${result.files}  (${result.downloaded} downloaded, ${result.cached} already cached)`);
  console.log(`downloaded ${fmtBytes(result.bytes)}`);
  console.log(`cache size ${fmtBytes(onDisk)}  at ${CACHE_DIR}`);
  console.log(`elapsed    ${(result.elapsedMs / 1000).toFixed(1)} s`);
  const warm = await isCacheWarm(MANIFEST, { res: args.res, hdriRes: args.hdriRes });
  console.log(`cache warm ${warm ? 'yes — renders no longer need the network' : 'NO — some files are missing'}`);
  return warm ? 0 : 1;
}

/** True when this file was run as `node visuals/lib/assets.mjs`. */
function invokedDirectly() {
  if (!IS_NODE) return false;
  if (typeof import.meta.main === 'boolean') return import.meta.main;
  const argv1 = process.argv[1];
  if (typeof argv1 !== 'string') return false;
  const norm = (p) => p.replace(/\\/g, '/').toLowerCase();
  return norm(urlToPath(import.meta.url)) === norm(argv1);
}

if (invokedDirectly()) {
  main(process.argv.slice(2))
    .then((code) => {
      process.exitCode = code;
    })
    .catch((err) => {
      console.error(err && err.stack ? err.stack : String(err));
      process.exitCode = 1;
    });
}
