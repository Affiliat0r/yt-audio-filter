/**
 * Measure where the harakat actually lands, in pixels, on the real scene.
 *
 * Isolating the mark by colour does not work, and the failed attempt is worth
 * recording: the accent layer draws the base letter *as well as* the mark,
 * hidden under a pixel-identical ink layer. Along the letter's outline the two
 * layers anti-alias against each other, so a nearest-colour split hands back a
 * "mark" box as tall as the whole letter. Every measurement taken that way is
 * noise.
 *
 * So the mark is isolated by difference instead. Each letter is rendered twice
 * -- once bare, once carrying the mark -- and the pixels that changed between
 * the two renders are the mark, exactly, with no colour reasoning at all. The
 * bare render doubles as the letter's own bounding box.
 *
 * Everything works on the scene as shipped: the probe copies scene/index.html
 * and scene/main.js into a scratch directory and writes a synthetic
 * timeline.js beside them, so it exercises the same CSS, the same font stack
 * and the same paint path a real render does.
 */

import { cp, mkdtemp, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { decodePng } from '../../visuals/capture/png.mjs';

const ELIFBA_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const REPO_DIR = path.dirname(ELIFBA_DIR);
const SCENE_DIR = path.join(ELIFBA_DIR, 'scene');

// puppeteer-core is installed under visuals/, so a bare import from this
// directory does not resolve -- Node only walks up from the *importer*. The
// render path never hits this because it imports visuals/capture/capture.mjs,
// which resolves from its own directory. Resolve it the same way by hand
// rather than duplicating a large dependency into a second node_modules.
const requireFromVisuals = createRequire(
  pathToFileURL(path.join(REPO_DIR, 'visuals', 'package.json')),
);
const puppeteer = requireFromVisuals('puppeteer-core');

/** Long enough that the sampled instant sits clear of every fade. */
const SEGMENT_SECONDS = 10;

/** Per-channel difference that counts as "this pixel changed". */
const DIFF_THRESHOLD = 24;

/** Ignore islands smaller than this; they are anti-aliasing, not a shape. */
const MIN_PIXELS = 25;

const CHROME_FALLBACKS = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
];

function emptyBox() {
  return { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity, count: 0 };
}

function grow(box, x, y) {
  if (x < box.minX) box.minX = x;
  if (y < box.minY) box.minY = y;
  if (x > box.maxX) box.maxX = x;
  if (y > box.maxY) box.maxY = y;
  box.count++;
}

function finish(box) {
  if (box.count < MIN_PIXELS) return null;
  return {
    minX: box.minX,
    minY: box.minY,
    maxX: box.maxX,
    maxY: box.maxY,
    count: box.count,
    width: box.maxX - box.minX + 1,
    height: box.maxY - box.minY + 1,
    centreX: (box.minX + box.maxX) / 2,
    centreY: (box.minY + box.maxY) / 2,
  };
}

/**
 * Bounding box of everything that is not the white card.
 *
 * @param {{width:number, height:number, channels:number, data:Buffer}} image
 * @returns {object|null}
 */
export function inkBox(image) {
  const { width, height, channels, data } = image;
  const box = emptyBox();
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * channels;
      if (data[i] > 245 && data[i + 1] > 245 && data[i + 2] > 245) continue;
      grow(box, x, y);
    }
  }
  return finish(box);
}

/**
 * Bounding box of the pixels that differ between two renders of the same card.
 *
 * @param {object} before  decoded PNG without the mark
 * @param {object} after   decoded PNG with it
 * @returns {object|null}
 */
export function diffBox(before, after) {
  if (before.width !== after.width || before.height !== after.height) {
    throw new Error(
      `glyph-probe: cannot diff ${before.width}x${before.height} against ${after.width}x${after.height}`,
    );
  }
  const { width, height, channels } = before;
  const box = emptyBox();
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * channels;
      const d =
        Math.abs(before.data[i] - after.data[i]) +
        Math.abs(before.data[i + 1] - after.data[i + 1]) +
        Math.abs(before.data[i + 2] - after.data[i + 2]);
      if (d >= DIFF_THRESHOLD) grow(box, x, y);
    }
  }
  return finish(box);
}

function segment(glyph, mark, accent, index) {
  return {
    kind: 'harakat',
    glyph,
    mark,
    accent,
    prevAccent: accent,
    // No fade at either edge, so the sampled frame is the settled one.
    fadeIn: false,
    fadeOut: false,
    step: 1,
    start: index * SEGMENT_SECONDS,
    duration: SEGMENT_SECONDS,
  };
}

/**
 * Render every (letter, mark) pair and measure both shapes.
 *
 * @param {{lesson: object, letterIds?: string[], height?: number,
 *          chromePath?: string}} opts
 * @returns {Promise<object[]>} one row per pair, with `ink`, `mark` and `card`
 */
export async function probeGlyphs(opts) {
  const { lesson, height = 1080, chromePath } = opts;
  const letterIds = opts.letterIds || lesson.letters.map((l) => l.id);
  const byId = new Map(lesson.letters.map((l) => [l.id, l]));
  const width = Math.round((height * 16) / 9 / 2) * 2;

  const segments = [];
  const plan = [];
  for (const id of letterIds) {
    const letter = byId.get(id);
    if (!letter) throw new Error(`glyph-probe: no letter ${id}`);

    // The bare render: same segment, no mark. Its pixels are the letter, and
    // it is the baseline every marked render is differenced against.
    const bareAt = segments.length * SEGMENT_SECONDS + SEGMENT_SECONDS / 2;
    segments.push(segment(letter.glyph, '', '#B08968', segments.length));

    for (const h of lesson.harakat) {
      const markedAt = segments.length * SEGMENT_SECONDS + SEGMENT_SECONDS / 2;
      segments.push(segment(letter.glyph, h.mark, h.accent, segments.length));
      plan.push({
        letterId: id,
        letterName: letter.name,
        glyph: letter.glyph,
        harakat: h.id,
        harakatName: h.name,
        accent: h.accent,
        bareAt,
        markedAt,
      });
    }
  }

  const scratch = await mkdtemp(path.join(os.tmpdir(), 'elifba-probe-'));
  await cp(path.join(SCENE_DIR, 'index.html'), path.join(scratch, 'index.html'));
  await cp(path.join(SCENE_DIR, 'main.js'), path.join(scratch, 'main.js'));
  await writeFile(
    path.join(scratch, 'timeline.js'),
    'window.__timeline = ' +
      JSON.stringify({
        title: 'probe',
        totalSeconds: segments.length * SEGMENT_SECONDS,
        segments,
      }) +
      ';\n',
    'utf8',
  );

  const executablePath = chromePath || process.env.CHROME_PATH || CHROME_FALLBACKS[0];

  const browser = await puppeteer.launch({
    executablePath,
    headless: true,
    args: [
      '--hide-scrollbars',
      '--allow-file-access-from-files',
      `--window-size=${width},${height}`,
    ],
    defaultViewport: { width, height, deviceScaleFactor: 1 },
  });

  try {
    const page = (await browser.pages())[0] || (await browser.newPage());
    const errors = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto(pathToFileURL(path.join(scratch, 'index.html')).href, { waitUntil: 'load' });
    await page.waitForFunction(() => Boolean(window.__movie), { timeout: 30_000 });
    await page.evaluate((cfg) => window.__movie.init(cfg), {
      seed: 0,
      width,
      height,
      durationSeconds: segments.length * SEGMENT_SECONDS,
    });
    await page.waitForFunction(() => window.__movie.ready === true, { timeout: 60_000 });
    if (errors.length) throw new Error(`glyph-probe: scene threw -- ${errors.join('; ')}`);

    const card = await page.$('#card');
    if (!card) throw new Error('glyph-probe: no #card in the scene');
    const cardBox = await card.boundingBox();

    // Inset past the rounded corners and the drop shadow. Both sit at the
    // card's edge and both shift when the accent colour changes, so an
    // element-level screenshot would put that change into every diff.
    const inset = Math.round(Math.min(cardBox.width, cardBox.height) * 0.04);
    const clip = {
      x: Math.round(cardBox.x) + inset,
      y: Math.round(cardBox.y) + inset,
      width: Math.round(cardBox.width) - inset * 2,
      height: Math.round(cardBox.height) - inset * 2,
    };
    const cardSize = { width: clip.width, height: clip.height };

    const fontSizePx = await page.evaluate(
      () => parseFloat(getComputedStyle(document.getElementById('glyphBase')).fontSize),
    );

    const shoot = async (t) => {
      await page.evaluate((time) => window.__movie.renderAt(time), t);
      return decodePng(await page.screenshot({ type: 'png', clip, optimizeForSpeed: true }));
    };

    // Each letter's bare render is shared by its three marks, so cache it.
    const bare = new Map();
    const rows = [];
    for (const item of plan) {
      if (!bare.has(item.bareAt)) bare.set(item.bareAt, await shoot(item.bareAt));
      const before = bare.get(item.bareAt);
      const after = await shoot(item.markedAt);
      rows.push({
        ...item,
        ink: inkBox(before),
        mark: diffBox(before, after),
        card: cardSize,
        fontSizePx,
      });
    }
    return rows;
  } finally {
    await browser.close().catch(() => {});
  }
}
