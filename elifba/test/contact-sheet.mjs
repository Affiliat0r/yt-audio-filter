#!/usr/bin/env node
/**
 * Render every letter under every harakat and tile them into one sheet.
 *
 * A metric tells you a number is wrong; a contact sheet tells you what is
 * wrong with it. This exists so placement can be judged by eye before any
 * threshold is chosen, and then again afterwards to confirm the fix.
 *
 *   node elifba/test/contact-sheet.mjs --out elifba/out/sheet.png
 */

import { execFile } from 'node:child_process';
import { cp, mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const ELIFBA_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const REPO_DIR = path.dirname(ELIFBA_DIR);
const SCENE_DIR = path.join(ELIFBA_DIR, 'scene');

const requireFromVisuals = createRequire(
  pathToFileURL(path.join(REPO_DIR, 'visuals', 'package.json')),
);
const puppeteer = requireFromVisuals('puppeteer-core');

const SEGMENT_SECONDS = 10;

async function main() {
  const outArg = process.argv.indexOf('--out');
  const outPath = path.resolve(
    outArg === -1 ? path.join(ELIFBA_DIR, 'out', 'sheet.png') : process.argv[outArg + 1],
  );
  const lettersArg = process.argv.indexOf('--letters');
  const height = 1080;
  const width = Math.round((height * 16) / 9 / 2) * 2;

  const lesson = JSON.parse(
    await readFile(path.join(ELIFBA_DIR, 'lesson.json'), 'utf8'),
  );
  const ids =
    lettersArg === -1
      ? lesson.letters.map((l) => l.id)
      : process.argv[lettersArg + 1].split(',');
  const byId = new Map(lesson.letters.map((l) => [l.id, l]));

  const segments = [];
  const cells = [];
  for (const id of ids) {
    const letter = byId.get(id);
    if (!letter) throw new Error(`no letter ${id}`);
    for (const h of lesson.harakat) {
      cells.push({
        at: segments.length * SEGMENT_SECONDS + SEGMENT_SECONDS / 2,
        label: `${letter.name} ${h.name}`,
      });
      segments.push({
        kind: 'harakat',
        glyph: letter.glyph,
        mark: h.mark,
        accent: h.accent,
        prevAccent: h.accent,
        fadeIn: false,
        fadeOut: false,
        step: 1,
        start: segments.length * SEGMENT_SECONDS,
        duration: SEGMENT_SECONDS,
      });
    }
  }

  const scratch = await mkdtemp(path.join(os.tmpdir(), 'elifba-sheet-'));
  await cp(path.join(SCENE_DIR, 'index.html'), path.join(scratch, 'index.html'));
  await cp(path.join(SCENE_DIR, 'main.js'), path.join(scratch, 'main.js'));
  await writeFile(
    path.join(scratch, 'timeline.js'),
    `window.__timeline = ${JSON.stringify({ title: 'sheet', totalSeconds: segments.length * SEGMENT_SECONDS, segments })};\n`,
    'utf8',
  );

  const frames = path.join(scratch, 'frames');
  await mkdir(frames, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH ||
      'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    headless: true,
    args: ['--hide-scrollbars', '--allow-file-access-from-files', `--window-size=${width},${height}`],
    defaultViewport: { width, height, deviceScaleFactor: 1 },
  });

  try {
    const page = (await browser.pages())[0] || (await browser.newPage());
    page.on('pageerror', (err) => { throw err; });
    await page.goto(pathToFileURL(path.join(scratch, 'index.html')).href, { waitUntil: 'load' });
    await page.waitForFunction(() => Boolean(window.__movie));
    await page.evaluate((cfg) => window.__movie.init(cfg), {
      seed: 0, width, height, durationSeconds: segments.length * SEGMENT_SECONDS,
    });
    await page.waitForFunction(() => window.__movie.ready === true, { timeout: 60_000 });

    const card = await page.$('#card');
    for (let i = 0; i < cells.length; i++) {
      await page.evaluate((t) => window.__movie.renderAt(t), cells[i].at);
      await card.screenshot({
        path: path.join(frames, `cell_${String(i).padStart(3, '0')}.png`),
        type: 'png',
      });
    }
  } finally {
    await browser.close().catch(() => {});
  }

  // Three columns -- one per harakat -- so a letter's ustun/esre/otre sit side
  // by side and a mark that has wandered is obvious against its neighbours.
  await mkdir(path.dirname(outPath), { recursive: true });
  await execFileAsync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-y',
    '-i', path.join(frames, 'cell_%03d.png'),
    '-filter_complex', `scale=260:-1,tile=3x${Math.ceil(cells.length / 3)}:margin=6:padding=4:color=0x333333`,
    '-frames:v', '1',
    outPath,
  ]);

  console.log(`${cells.length} cells -> ${outPath}`);
  console.log('columns: üstün | esre | ötre');
  console.log('rows:', ids.join(', '));
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
