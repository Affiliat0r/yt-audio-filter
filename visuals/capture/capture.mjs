/**
 * Part B — headless capture harness.
 *
 * Drives a scene page (see `visuals/CONTRACT.md`, Part A) through headless
 * Chrome one discrete frame at a time and writes `frame_%06d.png` into
 * `outDir`. Nothing here reads a clock to decide what to draw: frame `n` is
 * always `renderAt(n / fps)`, so a render is reproducible regardless of how
 * long the machine took.
 *
 * The design bias throughout is "fail loudly". A capture that silently emits
 * 300 black frames costs an overnight encode; a capture that throws on frame
 * 0 costs a second.
 */

import { mkdir, readdir, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import puppeteer from 'puppeteer-core';

import { decodePng, isUniform, readPngHeader } from './png.mjs';

export const DEFAULT_TIMEOUT_MS_PER_FRAME = 30_000;
export const DEFAULT_READY_TIMEOUT_MS = 120_000;
export const DEFAULT_NAV_TIMEOUT_MS = 60_000;

/** Windows fallback; `opts.chromePath` or CHROME_PATH normally supplies this. */
const FALLBACK_CHROME_PATHS = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
];

/**
 * Flags every tier gets.
 *
 * `--allow-file-access-from-files` is load-bearing: the scene is a `file://`
 * page importing ES modules from `../node_modules`, and without it Chrome
 * treats each file as an opaque origin and the module fetch dies on CORS.
 */
const BASE_ARGS = [
  '--hide-scrollbars',
  '--disable-dev-shm-usage',
  '--allow-file-access-from-files',
  '--disable-background-timer-throttling',
  '--disable-renderer-backgrounding',
  '--disable-backgrounding-occluded-windows',
];

/**
 * Launch tiers, tried in order. Tier 1 is the flag set named in the contract.
 *
 * On this class of Windows machine `--use-angle=gl-egl` does not fail — it
 * *succeeds into software* ("Microsoft Basic Render Driver"), which is the
 * exact silent degradation we are trying to avoid. So each tier is validated
 * against the renderer string it actually produced, and a software renderer
 * only counts as success on the tier that deliberately asks for one.
 *
 * `--enable-unsafe-swiftshader` is confined to the last tier on purpose:
 * without it, Chrome cannot quietly drop to SwiftShader behind our back.
 */
const LAUNCH_TIERS = [
  { name: 'angle-gl-egl', args: ['--use-gl=angle', '--use-angle=gl-egl', '--enable-gpu'], software: false },
  { name: 'angle-default', args: ['--use-gl=angle', '--enable-gpu'], software: false },
  { name: 'swiftshader', args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'], software: true },
];

const SOFTWARE_RENDERER_RE =
  /swiftshader|llvmpipe|softpipe|basic render|microsoft basic|generic renderer|software adapter|\bsoftware\b/i;

// ---------------------------------------------------------------------------
// small helpers
// ---------------------------------------------------------------------------

function makeLogger(onLog) {
  if (typeof onLog === 'function') return onLog;
  return (level, message) => {
    const line = `[capture] ${message}`;
    if (level === 'warn' || level === 'error') console.warn(line);
    else if (level === 'debug') console.debug(line);
    else console.log(line);
  };
}

function banner(log, lines) {
  const rule = '='.repeat(72);
  log('warn', rule);
  for (const line of lines) log('warn', line);
  log('warn', rule);
}

function toUrl(sceneUrl) {
  if (typeof sceneUrl !== 'string' || sceneUrl.trim() === '') {
    throw new Error('capture: sceneUrl is required');
  }
  // Two-or-more character scheme, so a Windows drive letter ("C:\...") is
  // still treated as a path rather than a URL scheme.
  if (/^[a-z][a-z0-9+.-]+:/i.test(sceneUrl)) return sceneUrl;
  // Keep any ?query / #hash out of pathToFileURL, which would percent-encode
  // them into the filename and turn the load into ERR_FILE_NOT_FOUND.
  const [, filePath, suffix = ''] = /^([^?#]*)(.*)$/s.exec(sceneUrl);
  return pathToFileURL(path.resolve(filePath)).href + suffix;
}

function positiveInt(value, name) {
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`capture: ${name} must be a positive integer, got ${JSON.stringify(value)}`);
  }
  return value;
}

function positiveNumber(value, name) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    throw new Error(`capture: ${name} must be a positive number, got ${JSON.stringify(value)}`);
  }
  return value;
}

async function resolveChromePath(chromePath) {
  const candidate = chromePath || process.env.CHROME_PATH || process.env.PUPPETEER_EXECUTABLE_PATH;
  if (candidate) return candidate;
  const { existsSync } = await import('node:fs');
  const found = FALLBACK_CHROME_PATHS.find((p) => existsSync(p));
  if (found) return found;
  throw new Error(
    'capture: chromePath is required (no CHROME_PATH env var and no Chrome at the default install location)',
  );
}

/** Remove stale `frame_*.png` so a shorter re-run cannot feed the encoder old frames. */
async function prepareOutDir(outDir, log) {
  await mkdir(outDir, { recursive: true });
  const stale = (await readdir(outDir)).filter((name) => /^frame_\d+\.png$/i.test(name));
  if (stale.length === 0) return 0;
  log('info', `clearing ${stale.length} stale frame(s) from ${outDir}`);
  await Promise.all(stale.map((name) => unlink(path.join(outDir, name))));
  return stale.length;
}

// ---------------------------------------------------------------------------
// in-page probes (serialised into Chrome, so: no closures over Node scope)
// ---------------------------------------------------------------------------

function probeWebGLInPage() {
  const result = { webgl2: false, webgl1: false, renderer: null, vendor: null, version: null, error: null };
  try {
    const c2 = document.createElement('canvas');
    const gl2 = c2.getContext('webgl2');
    result.webgl2 = Boolean(gl2);
    const c1 = document.createElement('canvas');
    const gl1 = result.webgl2 ? null : c1.getContext('webgl');
    result.webgl1 = result.webgl2 || Boolean(gl1);
    const gl = gl2 || gl1;
    if (!gl) {
      result.error = 'neither webgl2 nor webgl could be created';
      return result;
    }
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    result.renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    result.vendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
    result.version = gl.getParameter(gl.VERSION);
    const lose = gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
  } catch (err) {
    result.error = String((err && err.message) || err);
  }
  return result;
}

function measureStageInPage() {
  const el = document.querySelector('#stage');
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  return {
    tagName: el.tagName,
    rectX: rect.x,
    rectY: rect.y,
    rectWidth: rect.width,
    rectHeight: rect.height,
    backingWidth: typeof el.width === 'number' ? el.width : null,
    backingHeight: typeof el.height === 'number' ? el.height : null,
    transform: style.transform,
    devicePixelRatio: window.devicePixelRatio,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
  };
}

// ---------------------------------------------------------------------------
// main entry
// ---------------------------------------------------------------------------

/**
 * Capture a scene page to a numbered PNG sequence.
 *
 * @param {{
 *   sceneUrl: string,
 *   outDir: string,
 *   width: number,
 *   height: number,
 *   fps: number,
 *   durationSeconds: number,
 *   seed: number,
 *   chromePath?: string,
 *   onProgress?: (done: number, total: number) => void,
 *   onLog?: (level: 'debug'|'info'|'warn'|'error', message: string) => void,
 *   timeoutMsPerFrame?: number,
 *   readyTimeoutMs?: number,
 *   allowSoftwareRenderer?: boolean,
 *   allowFlatFirstFrame?: boolean,
 *   optimizeForSpeed?: boolean,
 *   extraArgs?: string[],
 * }} opts
 * @returns {Promise<{
 *   frameCount: number, outDir: string, width: number, height: number,
 *   fps: number, durationSeconds: number, seed: number,
 *   elapsedMs: number, msPerFrame: number, fpsAchieved: number,
 *   renderer: string|null, gpuTier: string, softwareRendering: boolean,
 * }>}
 */
export default async function capture(opts = {}) {
  const {
    sceneUrl,
    outDir,
    seed = 0,
    onProgress,
    onLog,
    chromePath,
    timeoutMsPerFrame = DEFAULT_TIMEOUT_MS_PER_FRAME,
    readyTimeoutMs = DEFAULT_READY_TIMEOUT_MS,
    allowSoftwareRenderer = true,
    allowFlatFirstFrame = false,
    optimizeForSpeed = true,
    extraArgs = [],
  } = opts;

  const log = makeLogger(onLog);

  const width = positiveInt(opts.width, 'width');
  const height = positiveInt(opts.height, 'height');
  const fps = positiveNumber(opts.fps, 'fps');
  const durationSeconds = positiveNumber(opts.durationSeconds, 'durationSeconds');
  positiveNumber(timeoutMsPerFrame, 'timeoutMsPerFrame');
  if (typeof seed !== 'number' || !Number.isFinite(seed)) {
    throw new Error(`capture: seed must be a finite number, got ${JSON.stringify(seed)}`);
  }
  if (typeof outDir !== 'string' || outDir.trim() === '') {
    throw new Error('capture: outDir is required');
  }

  const url = toUrl(sceneUrl);
  const resolvedOutDir = path.resolve(outDir);
  const executablePath = await resolveChromePath(chromePath);

  // `- 1e-6` keeps float noise (e.g. 30 * 10.0 === 300.00000000000006) from
  // buying an extra frame; the intent is still ceil(fps * durationSeconds).
  const total = Math.max(1, Math.ceil(fps * durationSeconds - 1e-6));

  await prepareOutDir(resolvedOutDir, log);

  const tiers = LAUNCH_TIERS.filter((tier) => allowSoftwareRenderer || !tier.software);
  const launchFailures = [];

  let browser = null;
  let chosen = null;
  let gpu = null;

  const started = process.hrtime.bigint();

  try {
    // -- launch, escalating until we get a renderer we are willing to use ----
    for (const tier of tiers) {
      const args = [...BASE_ARGS, ...tier.args, `--window-size=${width},${height}`, ...extraArgs];
      let candidate = null;
      try {
        candidate = await puppeteer.launch({
          executablePath,
          headless: true, // "new" headless; puppeteer >= 23 spells it `true`
          args,
          defaultViewport: { width, height, deviceScaleFactor: 1 },
          protocolTimeout: Math.max(180_000, timeoutMsPerFrame * 3),
        });
        const probePage = (await candidate.pages())[0] || (await candidate.newPage());
        const info = await probePage.evaluate(probeWebGLInPage);

        if (!info.webgl2 && !info.webgl1) {
          throw new Error(`no WebGL context: ${info.error || 'unknown reason'}`);
        }
        const isSoftware = SOFTWARE_RENDERER_RE.test(String(info.renderer || ''));
        if (isSoftware && !tier.software) {
          throw new Error(`renderer is software-backed: ${info.renderer}`);
        }

        browser = candidate;
        candidate = null;
        chosen = tier;
        gpu = { ...info, software: isSoftware, args };
        break;
      } catch (err) {
        launchFailures.push(`${tier.name}: ${err && err.message ? err.message : String(err)}`);
        log('warn', `launch tier "${tier.name}" rejected — ${err && err.message ? err.message : err}`);
        if (candidate) {
          await candidate.close().catch(() => {});
        }
      }
    }

    if (!browser) {
      throw new Error(
        'capture: could not obtain a WebGL-capable headless Chrome. Refusing to render black frames.\n' +
          launchFailures.map((line) => `  - ${line}`).join('\n'),
      );
    }

    if (gpu.software) {
      banner(log, [
        'WARNING: falling back to SOFTWARE rendering (SwiftShader).',
        `renderer: ${gpu.renderer}`,
        'This is 10-50x slower than the GPU path — an overnight render may not finish.',
        'Preceding tier failures:',
        ...launchFailures.map((line) => `  - ${line}`),
      ]);
    } else if (chosen !== tiers[0]) {
      banner(log, [
        `WARNING: the contract's flag set did not give a hardware renderer; using tier "${chosen.name}".`,
        `renderer: ${gpu.renderer}`,
        ...launchFailures.map((line) => `  - ${line}`),
      ]);
    } else {
      log('info', `GPU tier "${chosen.name}" — ${gpu.renderer}`);
    }
    log('debug', `launch args: ${gpu.args.join(' ')}`);
    if (!gpu.webgl2) log('warn', 'WebGL2 unavailable; only WebGL1 was obtainable');

    // -- page wiring: every in-page signal must reach the Node side ----------
    const page = (await browser.pages())[0] || (await browser.newPage());
    await page.setViewport({ width, height, deviceScaleFactor: 1 });
    page.setDefaultTimeout(readyTimeoutMs);

    let fatalError = null;
    const fatalWaiters = new Set();
    const raiseFatal = (err) => {
      if (!fatalError) fatalError = err instanceof Error ? err : new Error(String(err));
      for (const reject of [...fatalWaiters]) reject(fatalError);
      fatalWaiters.clear();
    };

    const requestFailures = [];

    page.on('console', (msg) => {
      const type = msg.type();
      const level = type === 'error' ? 'error' : type === 'warning' ? 'warn' : 'debug';
      log(level, `page console.${type}: ${msg.text()}`);
    });
    page.on('pageerror', (err) => {
      log('error', `page error: ${err.stack || err.message || err}`);
      raiseFatal(new Error(`capture: scene threw in the page — ${err.message || err}`, { cause: err }));
    });
    page.on('error', (err) => {
      log('error', `page crashed: ${err.message || err}`);
      raiseFatal(new Error(`capture: the renderer process crashed — ${err.message || err}`, { cause: err }));
    });
    page.on('requestfailed', (req) => {
      const failure = req.failure();
      const line = `${req.url()} (${(failure && failure.errorText) || 'unknown error'})`;
      if (/\/favicon\.ico$/i.test(req.url())) return;
      requestFailures.push(line);
      log('warn', `request failed: ${line}`);
    });

    /** Race a page operation against the frame deadline and any fatal page event. */
    const guard = (promise, ms, label) => {
      if (fatalError) return Promise.reject(fatalError);
      return new Promise((resolve, reject) => {
        const onFatal = (err) => finish(() => reject(err));
        const timer = setTimeout(
          () => finish(() => reject(new Error(`capture: timed out after ${ms} ms during ${label}`))),
          ms,
        );
        const finish = (act) => {
          clearTimeout(timer);
          fatalWaiters.delete(onFatal);
          act();
        };
        fatalWaiters.add(onFatal);
        promise.then(
          (value) => finish(() => resolve(value)),
          (err) => finish(() => reject(fatalError || err)),
        );
      });
    };

    const withRequestContext = (message) =>
      requestFailures.length === 0
        ? message
        : `${message}\nFailed page requests (a missing module or asset is the usual cause):\n` +
          requestFailures.map((line) => `  - ${line}`).join('\n');

    // -- load the scene and hand it the config ------------------------------
    log('info', `loading scene ${url}`);
    await guard(page.goto(url, { waitUntil: 'load', timeout: DEFAULT_NAV_TIMEOUT_MS }), DEFAULT_NAV_TIMEOUT_MS, 'page load');

    try {
      await guard(
        page.waitForFunction(
          () => Boolean(window.__movie) && typeof window.__movie.init === 'function' && typeof window.__movie.renderAt === 'function',
          { polling: 50, timeout: readyTimeoutMs },
        ),
        readyTimeoutMs + 1000,
        'waiting for window.__movie',
      );
    } catch (err) {
      throw new Error(
        withRequestContext(
          `capture: the scene never defined window.__movie with init()/renderAt() (${err.message}).`,
        ),
        { cause: err },
      );
    }

    const config = { seed, width, height, durationSeconds };
    log('info', `init(${JSON.stringify(config)})`);
    await guard(
      page.evaluate((cfg) => window.__movie.init(cfg), config),
      readyTimeoutMs,
      'window.__movie.init()',
    );
    await guard(
      page.waitForFunction(() => window.__movie.ready === true, { polling: 50, timeout: readyTimeoutMs }),
      readyTimeoutMs + 1000,
      'window.__movie.ready',
    );

    // -- geometry: a mis-sized stage corrupts every frame --------------------
    const stageInfo = await guard(page.evaluate(measureStageInPage), 30_000, 'measuring #stage');
    if (!stageInfo) {
      throw new Error('capture: the scene has no #stage element after init() — nothing to screenshot');
    }
    if (Math.round(stageInfo.rectWidth) !== width || Math.round(stageInfo.rectHeight) !== height) {
      throw new Error(
        `capture: #stage is laid out at ${stageInfo.rectWidth}x${stageInfo.rectHeight} CSS px but ` +
          `${width}x${height} was requested. Every frame would be the wrong size; fix the scene's CSS.`,
      );
    }
    if (stageInfo.rectX % 1 !== 0 || stageInfo.rectY % 1 !== 0) {
      log('warn', `#stage sits at a fractional offset (${stageInfo.rectX}, ${stageInfo.rectY}); frames may be resampled`);
    }
    if (stageInfo.transform && stageInfo.transform !== 'none') {
      log('warn', `#stage has a CSS transform (${stageInfo.transform}); frames may not be pixel-exact`);
    }
    if (stageInfo.devicePixelRatio !== 1) {
      log('warn', `devicePixelRatio is ${stageInfo.devicePixelRatio}, expected 1`);
    }
    if (stageInfo.backingWidth && (stageInfo.backingWidth !== width || stageInfo.backingHeight !== height)) {
      log(
        'warn',
        `#stage backing store is ${stageInfo.backingWidth}x${stageInfo.backingHeight}, CSS box is ${width}x${height}`,
      );
    }

    const stage = await page.$('#stage');
    if (!stage) throw new Error('capture: #stage vanished between measurement and capture');

    // -- the frame loop -----------------------------------------------------
    log('info', `capturing ${total} frame(s) at ${fps} fps into ${resolvedOutDir}`);
    const frameStart = process.hrtime.bigint();

    for (let n = 0; n < total; n++) {
      const t = n / fps;

      await guard(
        page.evaluate((time) => window.__movie.renderAt(time), t),
        timeoutMsPerFrame,
        `renderAt(${t.toFixed(6)}) for frame ${n}`,
      );

      // Screenshot the #stage element, never the viewport: if the scene ever
      // grows a margin, an element capture stays correct where a viewport
      // capture would silently shift every frame.
      //
      // `optimizeForSpeed` trades PNG compression effort for wall time. The
      // result is still lossless and still byte-repeatable; it just costs
      // roughly 2x the disk. Measured on this machine at 1280x720: 115 ms
      // -> 83 ms per frame, which is Chrome's surface-capture floor.
      const shot = await guard(
        stage.screenshot({ type: 'png', captureBeyondViewport: false, omitBackground: false, optimizeForSpeed }),
        timeoutMsPerFrame,
        `screenshot of frame ${n}`,
      );

      const header = readPngHeader(shot);
      if (header.width !== width || header.height !== height) {
        throw new Error(
          `capture: frame ${n} came back ${header.width}x${header.height}, expected ${width}x${height}. ` +
            'Refusing to write a wrong-sized frame — the encoder cannot recover from it.',
        );
      }

      if (n === 0 && !allowFlatFirstFrame) {
        try {
          const { flat, color } = isUniform(decodePng(shot));
          if (flat) {
            throw new Error(
              `capture: frame 0 is a single flat colour (${color.join(', ')}). ` +
                `The renderer (${gpu.renderer}) produced no image, or the scene drew nothing. ` +
                'Refusing to emit a black movie. Pass allowFlatFirstFrame: true if the scene really does open on a solid colour.',
            );
          }
        } catch (err) {
          if (/^capture: frame 0 is a single flat colour/.test(err.message)) throw err;
          log('warn', `could not decode frame 0 for the flat-colour check: ${err.message}`);
        }
      }

      await writeFile(path.join(resolvedOutDir, `frame_${String(n).padStart(6, '0')}.png`), shot);

      if (typeof onProgress === 'function') {
        try {
          onProgress(n + 1, total);
        } catch (err) {
          log('warn', `onProgress threw: ${err.message || err}`);
        }
      }
    }

    const frameElapsedMs = Number(process.hrtime.bigint() - frameStart) / 1e6;
    const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;
    const msPerFrame = frameElapsedMs / total;

    log(
      'info',
      `captured ${total} frame(s) in ${(elapsedMs / 1000).toFixed(2)} s — ` +
        `${msPerFrame.toFixed(1)} ms/frame (${(1000 / msPerFrame).toFixed(2)} fps)`,
    );

    return {
      frameCount: total,
      outDir: resolvedOutDir,
      width,
      height,
      fps,
      durationSeconds,
      seed,
      elapsedMs,
      captureMs: frameElapsedMs,
      msPerFrame,
      fpsAchieved: 1000 / msPerFrame,
      renderer: gpu.renderer,
      gpuTier: chosen.name,
      softwareRendering: gpu.software,
      launchArgs: gpu.args,
    };
  } finally {
    // A leaked headless Chrome keeps the GPU pinned, so this is unconditional.
    if (browser) {
      await browser.close().catch((err) => log('warn', `browser.close() failed: ${err.message || err}`));
    }
  }
}
